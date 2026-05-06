#!/usr/bin/env python3
"""
harness/mcga_sinks.py — MCGA (Make Call Graph Agent) sink tagger.

The MLLA-pattern equivalent of "cartographer that flags high-value
sinks". Walks every .sol in scope and tags each function with the
attack-surface categories present, then ranks contracts and functions
by total sink density.

Output: <out_dir>/mcga_sinks.json — per-contract, per-function tagged
sinks. The recon-pack's CPUA stage (entry_points.json) crosses with
this output to surface "externally-callable AND sink-rich" functions.

Run standalone or via recon_pack.py.

Usage:
  python3 harness/mcga_sinks.py <project_dir> [--out <out_dir>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Sink categories (regex per category). Each pattern is intentionally
# conservative — false positives are tolerable, false negatives lose recall.
SINK_PATTERNS: dict[str, list[str]] = {
    "external_call":   [r"\.call\s*[({]", r"\.staticcall\s*[({]"],
    "delegatecall":    [r"\.delegatecall\s*[({]"],
    "selfdestruct":    [r"\bselfdestruct\s*\("],
    "balance_write":   [r"balances?\s*\[[^\]]+\]\s*[+\-]?=",
                        r"balanceOf\s*\[[^\]]+\]\s*[+\-]?="],
    "share_write":     [r"shares?\s*\[[^\]]+\]\s*[+\-]?=",
                        r"\btotalShares?\s*[+\-]?="],
    "supply_write":    [r"\btotalSupply\s*[+\-]?=", r"_mint\s*\(", r"_burn\s*\("],
    "oracle_read":     [r"latestAnswer", r"latestRoundData",
                        r"\bgetPrice\b", r"\bconsult\b", r"AggregatorV3"],
    "lp_sync":         [r"\.sync\s*\(\)", r"\.skim\s*\("],
    "flash_loan":      [r"\bflashLoan\b", r"\bflash\s*\("],
    "unchecked_arith": [r"\bunchecked\s*\{"],
    "transfer_token": [r"\.transfer(From)?\s*\(",
                       r"safeTransfer(From)?\s*\("],
    "low_level_send": [r"\.send\s*\(", r"transfer\s*\(\s*\)"],
    "fee_on_transfer": [r"swapExactTokensForTokensSupportingFeeOnTransferTokens",
                        r"removeLiquidity.*SupportingFeeOnTransferTokens"],
    "approve_inf":    [r"approve\s*\([^,]+,\s*type\s*\(\s*uint(?:256)?\s*\)\s*\.\s*max\s*\)"],
    "tx_origin":      [r"\btx\.origin\b"],
    "block_dep":      [r"\bblock\.timestamp\b", r"\bblock\.number\b",
                       r"\bblockhash\s*\("],
}

# Compiled once for speed
COMPILED = {k: [re.compile(p) for p in pats] for k, pats in SINK_PATTERNS.items()}

# Function header regex — captures `function NAME(...) visibility ...`
FUNC_HDR = re.compile(
    r"function\s+(?P<name>\w+)\s*\(([^)]*)\)\s*(?P<rest>[^;{]*)\{",
    re.MULTILINE,
)
CONTRACT_HDR = re.compile(r"\b(?:interface|contract|library|abstract\s+contract)\s+(\w+)")

SKIP_DIR_PARTS = {
    ".git",
    ".recon",
    "cache",
    "lib",
    "node_modules",
    "out",
    "script",
    "scripts",
    "test",
    "tests",
}


def _is_target_source(path: Path, project_root: Path) -> bool:
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        rel = path
    if set(rel.parts[:-1]) & SKIP_DIR_PARTS:
        return False
    if rel.name.endswith((".t.sol", ".s.sol")):
        return False
    s = str(rel)
    if any(x in s for x in ("forge-std/", "@openzeppelin/", "solmate/", "@uniswap/")):
        return False
    return rel.name.endswith(".sol")


def _iter_func_bodies(text: str):
    """Yield (function_name, header_match, body_text) by brace matching."""
    for m in FUNC_HDR.finditer(text):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            c = text[i]
            if c == "{": depth += 1
            elif c == "}": depth -= 1
            i += 1
        yield m.group("name"), m, text[start : i - 1]


def _classify_visibility(rest: str) -> str:
    for v in ("external", "public", "internal", "private"):
        if re.search(rf"\b{v}\b", rest):
            return v
    return "default"


def tag_file(path: Path, project_root: Path) -> dict:
    text = path.read_text(errors="ignore")
    rec = {
        "file": str(path.relative_to(project_root)),
        "loc": len(text.splitlines()),
        "contracts": [],
    }
    cur_contract: dict | None = None
    contract_positions = [(m.group(1), m.start()) for m in CONTRACT_HDR.finditer(text)]
    if not contract_positions:
        contract_positions = [("(top-level)", 0)]

    # Per-contract function lists by position (closest-preceding contract wins)
    contracts = {name: {"name": name, "functions": [], "sinks_total": 0}
                 for name, _ in contract_positions}

    for fname, hm, body in _iter_func_bodies(text):
        body_pos = hm.start()
        contract_name = "(top-level)"
        for cname, cpos in contract_positions:
            if cpos <= body_pos:
                contract_name = cname
        vis = _classify_visibility(hm.group("rest"))
        sinks: dict[str, int] = {}
        for cat, regs in COMPILED.items():
            cnt = sum(len(r.findall(body)) for r in regs)
            if cnt:
                sinks[cat] = cnt
        line = text[: body_pos].count("\n") + 1
        contracts.setdefault(contract_name, {"name": contract_name, "functions": [], "sinks_total": 0})
        contracts[contract_name]["functions"].append({
            "name": fname,
            "line": line,
            "visibility": vis,
            "loc_body": body.count("\n") + 1,
            "sinks": sinks,
            "sink_count": sum(sinks.values()),
        })
        contracts[contract_name]["sinks_total"] += sum(sinks.values())

    rec["contracts"] = sorted(contracts.values(), key=lambda c: -c["sinks_total"])
    rec["sinks_total"] = sum(c["sinks_total"] for c in rec["contracts"])
    return rec


def build(project: Path) -> dict:
    files = []
    for p in project.rglob("*.sol"):
        if not _is_target_source(p, project):
            continue
        files.append(tag_file(p, project))

    files.sort(key=lambda f: -f["sinks_total"])
    top_funcs: list[dict] = []
    for f in files:
        for c in f["contracts"]:
            for fn in c["functions"]:
                if fn["sink_count"] == 0:
                    continue
                top_funcs.append({
                    "file": f["file"],
                    "contract": c["name"],
                    "function": fn["name"],
                    "line": fn["line"],
                    "visibility": fn["visibility"],
                    "sink_count": fn["sink_count"],
                    "sinks": fn["sinks"],
                })
    # Two ranked lists. External/public are direct attack surface;
    # high-sink internal funcs are call-graph hot spots worth tracing
    # back to their entry-point callers.
    external_pub = [f for f in top_funcs if f["visibility"] in ("external", "public")]
    external_pub.sort(key=lambda x: -x["sink_count"])
    internal = [f for f in top_funcs if f["visibility"] in ("internal", "private", "default")]
    internal.sort(key=lambda x: -x["sink_count"])

    return {
        "project": str(project),
        "files_scanned": len(files),
        "files": files,
        "top_external_functions": external_pub[:30],
        "top_internal_callees": internal[:20],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("--out", help="output dir (default: <project>/.recon)")
    args = ap.parse_args(argv[1:])

    project = Path(args.project_dir).resolve()
    out_dir = Path(args.out) if args.out else (project / ".recon")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = build(project)
    out_path = out_dir / "mcga_sinks.json"
    out_path.write_text(json.dumps(result, indent=2))
    summary = {
        "out": str(out_path),
        "files_scanned": result["files_scanned"],
        "top_5_external": [
            {k: v for k, v in fn.items() if k != "sinks"}
            | {"sink_classes": sorted(fn["sinks"])}
            for fn in result["top_external_functions"][:5]
        ],
        "top_5_internal": [
            {k: v for k, v in fn.items() if k != "sinks"}
            | {"sink_classes": sorted(fn["sinks"])}
            for fn in result["top_internal_callees"][:5]
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
