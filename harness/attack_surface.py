#!/usr/bin/env python3
"""
harness/attack_surface.py — deterministic CPUA-style attack-surface ranker.

Turns MCGA sink tags into a compact reading and tracing plan for the agent.
This is intentionally not a vulnerability detector. It answers a narrower
question: "which files/functions should be covered first, and why?"

Output: <out_dir>/attack_surface.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SINK_WEIGHTS = {
    "delegatecall": 14,
    "external_call": 10,
    "oracle_read": 10,
    "lp_sync": 10,
    "flash_loan": 9,
    "share_write": 8,
    "balance_write": 7,
    "supply_write": 7,
    "transfer_token": 6,
    "unchecked_arith": 5,
    "fee_on_transfer": 5,
    "approve_inf": 4,
    "tx_origin": 4,
    "block_dep": 3,
    "low_level_send": 3,
    "selfdestruct": 3,
}

VISIBILITY_BONUS = {
    "external": 8,
    "public": 5,
    "internal": 1,
    "private": 0,
    "default": 0,
}


def _sink_score(sinks: dict[str, int]) -> int:
    return sum(SINK_WEIGHTS.get(name, 1) * int(count) for name, count in sinks.items())


def _reasons(sinks: dict[str, int], visibility: str) -> list[str]:
    reasons = []
    if visibility in ("external", "public"):
        reasons.append("direct_entry")
    for name in sorted(sinks, key=lambda k: (-SINK_WEIGHTS.get(k, 1), k)):
        reasons.append(name)
    return reasons[:6]


def build(project: Path, mcga_result: dict[str, Any] | None = None) -> dict[str, Any]:
    if mcga_result is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mcga_sinks import build as build_mcga  # type: ignore
        mcga_result = build_mcga(project)

    ranked_functions: list[dict[str, Any]] = []
    ranked_files: list[dict[str, Any]] = []

    for file_rec in mcga_result.get("files", []):
        hot_functions = []
        ext_count = 0
        max_fn_score = 0
        for contract in file_rec.get("contracts", []):
            if contract.get("name", "").startswith("I") and not contract.get("functions"):
                continue
            for fn in contract.get("functions", []):
                visibility = fn.get("visibility", "default")
                if visibility in ("external", "public"):
                    ext_count += 1
                sinks = fn.get("sinks") or {}
                sink_count = int(fn.get("sink_count") or 0)
                if sink_count == 0 and visibility not in ("external", "public"):
                    continue
                score = _sink_score(sinks) + VISIBILITY_BONUS.get(visibility, 0)
                if sink_count == 0:
                    score = max(score, VISIBILITY_BONUS.get(visibility, 0))
                max_fn_score = max(max_fn_score, score)
                item = {
                    "file": file_rec["file"],
                    "contract": contract.get("name"),
                    "function": fn.get("name"),
                    "line": fn.get("line"),
                    "visibility": visibility,
                    "sink_count": sink_count,
                    "sinks": sinks,
                    "score": score,
                    "reasons": _reasons(sinks, visibility),
                }
                ranked_functions.append(item)
                if sink_count:
                    hot_functions.append(item)

        file_score = int(file_rec.get("sinks_total") or 0) * 6 + ext_count * 3 + max_fn_score
        ranked_files.append({
            "file": file_rec["file"],
            "loc": file_rec.get("loc"),
            "sinks_total": file_rec.get("sinks_total", 0),
            "external_or_public_functions": ext_count,
            "score": file_score,
            "hot_functions": sorted(
                hot_functions,
                key=lambda x: (-x["score"], x["file"], x["line"] or 0),
            )[:8],
        })

    ranked_functions.sort(key=lambda x: (-x["score"], x["file"], x["line"] or 0))
    ranked_files.sort(key=lambda x: (-x["score"], x["file"]))

    return {
        "project": str(project),
        "ranking_version": "cpua_v1",
        "notes": [
            "Scores prioritize direct external/public entry points with high-value sinks.",
            "Use ranked_files for coverage order; use ranked_functions for trace targets.",
            "This is a prioritizer, not a detector. Still read every in-scope file.",
        ],
        "ranked_files": ranked_files,
        "ranked_functions": ranked_functions[:80],
        "coverage_order": [f["file"] for f in ranked_files],
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
    out_path = out_dir / "attack_surface.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps({
        "out": str(out_path),
        "files_ranked": len(result["ranked_files"]),
        "functions_ranked": len(result["ranked_functions"]),
        "top_files": result["coverage_order"][:5],
        "top_functions": [
            f"{f['contract']}.{f['function']}@{f['file']}:{f['line']}"
            for f in result["ranked_functions"][:5]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
