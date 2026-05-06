#!/usr/bin/env python3
"""
harness/verify.py — Verification Gate (deterministic).

Pipeline (any failure short-circuits and is reported back as structured feedback
the LLM can act on next iteration — this is the ReX compiler-feedback loop):

  0. schema       — required hypothesis fields are present and parseable
  1. compile      — PoC file exists, names line up, and targeted `forge build <poc>` succeeds
  2. execute      — exact PoC test path/name passes under Foundry
  3. semantic     — reject PoCs that violate known protocol semantics
  4. state_delta  — matching ClassInvariants evidence event was emitted
  5. econ_check   — actual emitted delta vs threshold and gas cost when priced
  6. dup_check    — on-demand Slither duplicate signal
  7. halmos_check — optional, when hypothesis.invariant.halmos_check=true

Usage:
  python3 harness/verify.py path/to/hypothesis.json

Exit codes:
  0  fully verified — finding is publishable
  1  compile failure   (errors.json populated for next LLM iteration)
  2  execution failure
  3  invariant did not fire / wrong direction
  4  not economically feasible
  5  duplicate
  6  halmos failure (when required)
  7  semantic failure
  97 schema failure
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO / "harness"
SCHEMA_PATH = HARNESS_DIR / "schemas" / "hypothesis.schema.json"

# Per-hypothesis Foundry root override.
# Hypothesis JSON may set "forge_root": "/path/to/scaffolded/case" to redirect
# verify away from the repo's poc-forge. Used for SCONE-mode (Sourcify-fetched
# sources scaffolded by harness/tools/scaffold_forge.py).
def _forge_dir_for(hyp: dict) -> Path:
    fr = hyp.get("forge_root")
    return Path(fr).resolve() if fr else (REPO / "poc-forge")


@dataclass
class GateResult:
    gate: str
    passed: bool
    detail: str = ""
    artifact: str = ""


ALLOWED_CLASS_INVARIANTS = {
    "attacker_eth_increased",
    "attacker_token_increased",
    "victim_token_drained",
    "share_price_collapsed",
    "share_price_inflated",
    "supply_inflated",
    "permission_acquired",
    "function_unexpectedly_callable",
    "invariant_violated_custom",
}

ALLOWED_VULN_CLASSES = {
    "reentrancy",
    "access_control",
    "arithmetic",
    "oracle_manipulation",
    "flashloan_pricing",
    "donation_share_inflation",
    "callback_state_corruption",
    "signature_replay",
    "dos_griefing",
    "init_unprotected",
    "upgradeability_storage",
    "rounding_dust_drain",
    "invariant_break_other",
}

ALLOWED_MODES = {"audit_competition", "immunefi_bounty", "research"}

ECONOMIC_DELTA_CLASSES = {
    "attacker_eth_increased",
    "attacker_token_increased",
    "victim_token_drained",
    "share_price_collapsed",
    "share_price_inflated",
    "supply_inflated",
}


def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout, p.stderr


def parse_uint(value: object, field: str) -> int:
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{field} must be non-negative")
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an integer string")
    s = value.strip().replace("_", "")
    if not s:
        raise ValueError(f"{field} is empty")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"{field} is not numeric: {value!r}") from e
    if d < 0 or d != d.to_integral_value():
        raise ValueError(f"{field} must be a non-negative integer value")
    return int(d)


def solidity_identifier(value: str) -> str:
    """Map arbitrary hypothesis ids to Solidity-safe test/file suffixes."""
    ident = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not ident or not re.match(r"[A-Za-z_]", ident[0]):
        ident = f"_{ident}"
    return ident


def validate_hypothesis(hyp: dict) -> list[str]:
    errors = validate_schema(hyp)
    if not isinstance(hyp, dict):
        return errors + ["hypothesis root must be an object"]
    for key in ("id", "vuln_class", "target", "initial_state", "pre_vuln_state", "post_vuln_state", "invariant"):
        if key not in hyp:
            errors.append(f"missing required field: {key}")
    if not isinstance(hyp.get("id"), str) or not hyp.get("id"):
        errors.append("id must be a non-empty string")
    if hyp.get("mode") is not None and hyp.get("mode") not in ALLOWED_MODES and not _schema_reported(errors, "mode"):
        errors.append(f"mode must be one of {sorted(ALLOWED_MODES)}")
    if hyp.get("vuln_class") is not None and hyp.get("vuln_class") not in ALLOWED_VULN_CLASSES and not _schema_reported(errors, "vuln_class"):
        errors.append(f"vuln_class must be one of {sorted(ALLOWED_VULN_CLASSES)}")
    target = hyp.get("target") or {}
    if not isinstance(target, dict):
        errors.append("target must be an object")
    else:
        for key in ("contract", "function"):
            if not target.get(key):
                errors.append(f"target.{key} is required")
        lines = target.get("lines")
        if lines is not None and not isinstance(lines, str):
            errors.append("target.lines must be a string when provided")
    for key in ("initial_state", "pre_vuln_state", "post_vuln_state", "invariant"):
        if key in hyp and not isinstance(hyp.get(key), dict):
            errors.append(f"{key} must be an object")
    post = hyp.get("post_vuln_state") or {}
    cls = post.get("class_invariant")
    if cls not in ALLOWED_CLASS_INVARIANTS:
        errors.append(f"post_vuln_state.class_invariant must be one of {sorted(ALLOWED_CLASS_INVARIANTS)}")
    if cls == "invariant_violated_custom" and not post.get("custom_assertion"):
        errors.append("custom_assertion is required for invariant_violated_custom")
    for key in ("min_profit_wei", "profit_min_raw", "profit_token_price_wei"):
        if key in post:
            try:
                parse_uint(post[key], f"post_vuln_state.{key}")
            except ValueError as e:
                errors.append(str(e))
    if "profit_token_decimals" in post:
        dec = post["profit_token_decimals"]
        if not isinstance(dec, int) or dec < 0 or dec > 255:
            errors.append("post_vuln_state.profit_token_decimals must be an integer in [0,255]")
    inv = hyp.get("invariant") or {}
    if isinstance(inv, dict):
        if inv.get("halmos_check") and not inv.get("property_function"):
            errors.append("invariant.property_function is required when halmos_check=true")
    return errors


def _schema_reported(errors: list[str], field: str) -> bool:
    return any(e.startswith(f"{field}:") or e.startswith(f"{field}.") for e in errors)


def validate_schema(hyp: dict) -> list[str]:
    """Validate against the checked-in JSON Schema when jsonschema is installed."""
    try:
        import jsonschema  # type: ignore
    except Exception:
        return []
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as e:
        return [f"schema file unreadable: {e}"]
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(hyp), key=lambda e: list(e.path)):
        loc = ".".join(str(x) for x in err.path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def resolve_poc_path(hyp: dict, forge_dir: Path, test_id: str) -> Path:
    value = hyp.get("poc_path")
    if value:
        p = Path(value)
        if p.is_absolute():
            return p
        for base in (forge_dir, REPO):
            candidate = base / p
            if candidate.exists():
                return candidate
        return forge_dir / p
    return forge_dir / "test" / f"AttackHarness_{test_id}.t.sol"


def _forge_relative(path: Path, forge_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(forge_dir.resolve()))
    except ValueError:
        return str(path)


def gate_compile(test_path: Path, forge_dir: Path, test_id: str, test_name: str) -> GateResult:
    if not test_path.exists():
        return GateResult("compile", False, detail=f"missing PoC test file: {test_path}", artifact=str(test_path))
    try:
        poc_text = test_path.read_text(errors="ignore")
    except Exception as e:
        return GateResult("compile", False, detail=f"could not read PoC test file: {e}", artifact=str(test_path))

    expected_contract = f"AttackHarness_{test_id}"
    if not re.search(rf"\bcontract\s+{re.escape(expected_contract)}\b", poc_text):
        return GateResult(
            "compile",
            False,
            detail=f"missing expected PoC contract `{expected_contract}` in {test_path}",
            artifact=str(test_path),
        )
    if not re.search(rf"\bfunction\s+{re.escape(test_name)}\s*\(", poc_text):
        return GateResult(
            "compile",
            False,
            detail=f"missing expected PoC test function `{test_name}()` in {test_path}",
            artifact=str(test_path),
        )

    rel_test_path = _forge_relative(test_path, forge_dir)
    rc, out, err = run(["forge", "build", rel_test_path], forge_dir)
    if rc != 0:
        return GateResult("compile", False, detail=err or out, artifact=out + "\n" + err)
    return GateResult("compile", True, detail=f"targeted build ok: {rel_test_path}", artifact=out + "\n" + err)


def gate_execute(test_name: str, test_path: Path, forge_dir: Path) -> GateResult:
    rel_test_path = _forge_relative(test_path, forge_dir)
    rc, out, err = run(
        ["forge", "test", rel_test_path, "--match-test", test_name, "-vvvv"],
        forge_dir,
    )
    artifact = out + "\n" + err
    if "No tests found" in artifact:
        return GateResult(
            "execute",
            False,
            detail=f"no matching Foundry test named {test_name}",
            artifact=artifact,
        )
    if rc != 0:
        return GateResult("execute", False, detail=_extract_revert(artifact), artifact=artifact)
    return GateResult("execute", True, artifact=artifact)


def _extract_revert(trace: str) -> str:
    # forge -vvvv prints '[FAIL. Reason: ...] testPoC_xxx()'
    m = re.search(r"\[FAIL\.[^\]]*\][^\n]*", trace)
    return m.group(0) if m else trace[-2000:]


def _address_from_foundry_value(value: str) -> str:
    m = re.search(r"\[(0x[0-9a-fA-F]{40})\]", value)
    if m:
        return m.group(1)
    m = re.search(r"\b(0x[0-9a-fA-F]{40})\b", value)
    return m.group(1) if m else ""


def extract_invariant_evidence(trace: str) -> list[dict]:
    evidence = []
    for m in re.finditer(r'emit InvariantEvidence\((.*?)\)', trace):
        payload = m.group(1)
        cls_m = re.search(r'classInvariant:\s*"([^"]+)"', payload)
        before_m = re.search(r"beforeValue:\s*(\d+)", payload)
        after_m = re.search(r"afterValue:\s*(\d+)", payload)
        delta_m = re.search(r"delta:\s*(\d+)", payload)
        threshold_m = re.search(r"threshold:\s*(\d+)", payload)
        subject_m = re.search(r"subject:\s*(.*?),\s*asset:", payload)
        asset_m = re.search(r"asset:\s*(.*?),\s*beforeValue:", payload)
        if not (cls_m and before_m and after_m and delta_m and threshold_m):
            continue
        evidence.append({
            "class_invariant": cls_m.group(1),
            "subject": _address_from_foundry_value(subject_m.group(1)) if subject_m else "",
            "asset": _address_from_foundry_value(asset_m.group(1)) if asset_m else "",
            "before": int(before_m.group(1)),
            "after": int(after_m.group(1)),
            "delta": int(delta_m.group(1)),
            "threshold": int(threshold_m.group(1)),
        })
    return evidence


def gate_state_delta(hyp: dict, execute_artifact: str, test_name: str) -> GateResult:
    pass_re = re.compile(rf"\[PASS\]\s+{re.escape(test_name)}\b")
    if not pass_re.search(execute_artifact):
        return GateResult(
            "state_delta",
            False,
            detail=f"{test_name} did not pass — invariant did not fire correctly",
        )
    expected = hyp.get("post_vuln_state", {}).get("class_invariant")
    evidence = extract_invariant_evidence(execute_artifact)
    matches = [e for e in evidence if e["class_invariant"] == expected]
    if not matches:
        return GateResult(
            "state_delta",
            False,
            detail=(
                f"missing InvariantEvidence event for {expected}; "
                "use ClassInvariants helpers or assertCustomInvariant()"
            ),
            artifact=json.dumps({"evidence": evidence}, indent=2),
        )
    best = max(matches, key=lambda e: e["delta"])
    return GateResult("state_delta", True, detail=f"{expected} delta={best['delta']}", artifact=json.dumps(best))


def gate_semantic(hyp: dict, test_path: Path) -> GateResult:
    """Reject common PoC shapes that pass locally by violating protocol rules."""
    target = hyp.get("target") or {}
    fn = str(target.get("function") or "").lower()
    try:
        poc_text = test_path.read_text(errors="ignore")
    except Exception:
        poc_text = ""
    blob = "\n".join([
        poc_text,
        str(hyp.get("rationale") or ""),
        json.dumps(hyp.get("attack_steps") or []),
        json.dumps(hyp.get("preconditions") or []),
    ]).lower()

    if "validateuserop" in fn or "validatepaymasteruserop" in fn:
        invalid_sig_patterns = [
            r"validationdata\s*==\s*1",
            r"uint160\s*\(\s*validationdata\s*\)\s*==\s*1",
            r"expected .*signature failure",
            r"invalid signature",
            r"signature failure",
            r"does not roll back",
            r"validationdata[^.\n]{0,80}terminal",
            r"caller path[^.\n]{0,120}validationdata",
        ]
        if any(re.search(pat, blob) for pat in invalid_sig_patterns):
            return GateResult(
                "semantic",
                False,
                detail=(
                    "ERC-4337 invalid-signature validationData is terminal for a valid exploit model; "
                    "PoC cannot profit by assuming a caller ignores validationData==1"
                ),
            )
        if "vm.etch" in blob and "entrypoint" in blob:
            return GateResult(
                "semantic",
                False,
                detail="PoC replaces the EntryPoint code with vm.etch; this violates the protocol caller model",
            )
    return GateResult("semantic", True)


def gate_econ(hyp: dict, execute_artifact: str) -> GateResult:
    post = hyp.get("post_vuln_state", {})
    cls = post.get("class_invariant")
    evidence = [e for e in extract_invariant_evidence(execute_artifact) if e["class_invariant"] == cls]
    if not evidence:
        return GateResult("econ", False, detail=f"no invariant evidence for {cls}")
    best = max(evidence, key=lambda e: e["delta"])
    explicit_min = post.get("profit_min_raw", post.get("min_profit_wei"))
    if explicit_min is None and cls not in ECONOMIC_DELTA_CLASSES:
        return GateResult("econ", True, detail=f"{cls} is non-balance evidence; no explicit profit threshold")
    min_profit = parse_uint(explicit_min if explicit_min is not None else "10000000000000000", "profit_min_raw")
    if best["delta"] < min_profit:
        return GateResult("econ", False, detail=f"actual_delta={best['delta']} < min_profit_raw={min_profit}")

    # Foundry prints 'Gas used: <N>' at the end of a passing test.
    gm = re.search(r"gas:\s*(\d+)", execute_artifact, re.IGNORECASE)
    gas_used = int(gm.group(1)) if gm else 500_000

    # Conservative gas price: 20 gwei mainnet base+priority
    gas_price_wei = 20 * 10**9
    gas_cost = gas_used * gas_price_wei

    zero_addr = "0x0000000000000000000000000000000000000000"
    asset = (best.get("asset") or zero_addr).lower()
    is_native = cls == "attacker_eth_increased" or post.get("profit_asset") in ("ETH", "NATIVE", "native", "eth")
    if is_native or asset == zero_addr:
        profit_wei = best["delta"]
    elif post.get("profit_token_price_wei") is not None:
        decimals = int(post.get("profit_token_decimals", 18))
        price_wei = parse_uint(post["profit_token_price_wei"], "profit_token_price_wei")
        profit_wei = best["delta"] * price_wei // (10 ** decimals)
    else:
        if hyp.get("mode") != "research" and os.environ.get("HARNESS_ALLOW_UNPRICED_TOKEN_PROFIT") != "1":
            return GateResult(
                "econ",
                False,
                detail=(
                    f"raw_delta={best['delta']} >= min_profit_raw={min_profit}, "
                    "but profit_token_price_wei is required outside research mode "
                    "to compare token profit against gas cost"
                ),
            )
        return GateResult(
            "econ",
            True,
            detail=(
                f"raw_delta={best['delta']} >= min_profit_raw={min_profit}; "
                "gas-vs-profit skipped because profit_token_price_wei is missing"
            ),
        )

    if gas_cost >= profit_wei:
        return GateResult(
            "econ",
            False,
            detail=f"gas_cost={gas_cost} >= actual_profit_wei={profit_wei} (gas_used={gas_used}@20gwei)",
        )
    return GateResult("econ", True, detail=f"gas_cost={gas_cost} < actual_profit_wei={profit_wei}; raw_delta={best['delta']}")


def gate_dup(hyp: dict) -> GateResult:
    """
    Use slither_dup_check on the hypothesis target. If Slither already flagged
    this exact location, the protocol's CI almost certainly caught it →
    likely a duplicate finding. Skip in audit-comp mode (still pays for dups);
    enforce in Immunefi bounty mode.
    """
    target = hyp.get("target") or {}
    file = target.get("file")
    lines = target.get("lines")
    project_dir = (
        hyp.get("project_dir")
        or hyp.get("forge_root")
        or (hyp.get("initial_state") or {}).get("project_dir")
    )
    if not (file and lines and project_dir):
        return GateResult("dup", True, detail="missing target.file/lines or project_dir; skipped")
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO))
        from harness.tools.slither_tools import slither_dup_check  # type: ignore
        res = slither_dup_check(project_dir, file, lines)
    except Exception as e:
        return GateResult("dup", True, detail=f"slither_tools unavailable: {e}; skipped")
    if not res.get("ok"):
        return GateResult("dup", True, detail=f"slither error: {res.get('error', '?')}; skipped")
    data = res["data"]
    if data.get("is_likely_duplicate"):
        if hyp.get("mode") == "immunefi_bounty":
            return GateResult("dup", False,
                detail=f"likely duplicate of static finding(s): {data.get('matched_detectors')[:3]}")
        return GateResult("dup", True,
            detail=f"warning: matched detectors {data.get('matched_detectors')[:3]} (not hard-failed outside immunefi_bounty)")
    return GateResult("dup", True, detail="no static-analysis match")


def gate_halmos(hyp: dict, forge_dir: Path) -> GateResult:
    if not hyp.get("invariant", {}).get("halmos_check"):
        return GateResult("halmos", True, detail="not requested")
    fn = hyp["invariant"].get("property_function")
    if not fn:
        return GateResult("halmos", False, detail="halmos_check=true but property_function missing")
    rc, out, err = run(["halmos", "--function", fn], forge_dir)
    if rc != 0:
        return GateResult("halmos", False, detail=err or out)
    return GateResult("halmos", True)


GATE_EXIT = {
    "compile": 1,
    "execute": 2,
    "state_delta": 3,
    "econ": 4,
    "dup": 5,
    "halmos": 6,
    "semantic": 7,
    "schema": 97,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: verify.py <hypothesis.json>", file=sys.stderr)
        return 99
    hyp_path = Path(argv[1])
    hyp = json.loads(hyp_path.read_text())
    schema_errors = validate_hypothesis(hyp)
    if schema_errors:
        feedback = {"hypothesis_path": str(hyp_path), "results": [], "forge_dir": "", "schema_errors": schema_errors}
        return _emit(feedback, [GateResult("schema", False, detail="; ".join(schema_errors))], GATE_EXIT["schema"])
    hyp_id = hyp["id"]
    test_id = solidity_identifier(hyp_id)
    test_name = f"testPoC_{test_id}"
    forge_dir = _forge_dir_for(hyp)
    test_path = resolve_poc_path(hyp, forge_dir, test_id)

    results: list[GateResult] = []
    feedback: dict = {
        "hypothesis_id": hyp_id,
        "test_id": test_id,
        "test_name": test_name,
        "results": [],
        "forge_dir": str(forge_dir),
    }

    g = gate_compile(test_path, forge_dir, test_id, test_name); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["compile"])

    g = gate_execute(test_name, test_path, forge_dir); results.append(g)
    exec_artifact = g.artifact
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["execute"])

    g = gate_semantic(hyp, test_path); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["semantic"])

    g = gate_state_delta(hyp, exec_artifact, test_name); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["state_delta"])

    g = gate_econ(hyp, exec_artifact); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["econ"])

    g = gate_dup(hyp); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["dup"])

    g = gate_halmos(hyp, forge_dir); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["halmos"])

    return _emit(feedback, results, 0)


def _emit(feedback: dict, results: list[GateResult], code: int) -> int:
    feedback["results"] = [asdict(r) for r in results]
    feedback["exit_code"] = code
    out_path = HARNESS_DIR / "last_verify.json"
    out_path.write_text(json.dumps(feedback, indent=2))
    print(json.dumps(feedback, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
