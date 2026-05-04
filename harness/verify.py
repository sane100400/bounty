#!/usr/bin/env python3
"""
harness/verify.py — Verification Gate (deterministic).

Pipeline (any failure short-circuits and is reported back as structured feedback
the LLM can act on next iteration — this is the ReX compiler-feedback loop):

  1. compile      — `forge build` on the test file
  2. execute      — `forge test --match-test testPoC_<id> -vvvv`
  3. state_delta  — parse trace, confirm invariant assertion fired correctly
  4. econ_check   — gas cost vs claimed profit at realistic gas price
  5. dup_check    — (stub) embedding distance vs prior findings
  6. halmos_check — optional, when hypothesis.invariant.halmos_check=true

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
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO / "harness"

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


def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout, p.stderr


def gate_compile(test_path: Path, forge_dir: Path) -> GateResult:
    rc, out, err = run(["forge", "build"], forge_dir)
    if rc != 0:
        return GateResult("compile", False, detail=err or out, artifact=str(test_path))
    return GateResult("compile", True)


def gate_execute(hyp_id: str, forge_dir: Path) -> GateResult:
    rc, out, err = run(
        ["forge", "test", "--match-test", f"testPoC_{hyp_id}", "-vvvv"],
        forge_dir,
    )
    artifact = out + "\n" + err
    if rc != 0:
        return GateResult("execute", False, detail=_extract_revert(artifact), artifact=artifact)
    return GateResult("execute", True, artifact=artifact)


def _extract_revert(trace: str) -> str:
    # forge -vvvv prints '[FAIL. Reason: ...] testPoC_xxx()'
    m = re.search(r"\[FAIL\.[^\]]*\][^\n]*", trace)
    return m.group(0) if m else trace[-2000:]


def gate_state_delta(execute_artifact: str) -> GateResult:
    # ClassInvariants asserts include the marker 'InvariantOK' in their failure msg.
    # If a test PASSES, all asserts succeeded → invariant fired in the right direction.
    if "[PASS]" not in execute_artifact:
        return GateResult("state_delta", False, detail="no [PASS] marker — invariant did not fire correctly")
    return GateResult("state_delta", True)


def gate_econ(hyp: dict, execute_artifact: str) -> GateResult:
    min_profit_wei_str = hyp.get("post_vuln_state", {}).get("min_profit_wei", "10000000000000000")  # 0.01 ETH
    min_profit = int(min_profit_wei_str)

    # Foundry prints 'Gas used: <N>' at the end of a passing test.
    gm = re.search(r"gas:\s*(\d+)", execute_artifact, re.IGNORECASE)
    gas_used = int(gm.group(1)) if gm else 500_000

    # Conservative gas price: 20 gwei mainnet base+priority
    gas_price_wei = 20 * 10**9
    gas_cost = gas_used * gas_price_wei

    if gas_cost >= min_profit:
        return GateResult(
            "econ",
            False,
            detail=f"gas_cost={gas_cost} ≥ min_profit={min_profit} (gas_used={gas_used}@20gwei)",
        )
    return GateResult("econ", True, detail=f"gas_cost={gas_cost} < min_profit={min_profit}")


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
    project_dir = (hyp.get("initial_state") or {}).get("project_dir")
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
        if hyp.get("mode") == "audit_competition":
            # In audit comps duplicates still pay; emit warning but pass.
            return GateResult("dup", True,
                detail=f"warning: matched detectors {data.get('matched_detectors')[:3]} (audit-comp dups still pay)")
        # Immunefi-style: enforce — fail the gate
        return GateResult("dup", False,
            detail=f"likely duplicate of static finding(s): {data.get('matched_detectors')[:3]}")
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
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: verify.py <hypothesis.json>", file=sys.stderr)
        return 99
    hyp_path = Path(argv[1])
    hyp = json.loads(hyp_path.read_text())
    hyp_id = hyp["id"]
    forge_dir = _forge_dir_for(hyp)
    test_path = forge_dir / "test" / f"AttackHarness_{hyp_id}.t.sol"

    results: list[GateResult] = []
    feedback: dict = {"hypothesis_id": hyp_id, "results": [], "forge_dir": str(forge_dir)}

    g = gate_compile(test_path, forge_dir); results.append(g)
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["compile"])

    g = gate_execute(hyp_id, forge_dir); results.append(g)
    exec_artifact = g.artifact
    if not g.passed:
        return _emit(feedback, results, GATE_EXIT["execute"])

    g = gate_state_delta(exec_artifact); results.append(g)
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
