#!/usr/bin/env python3
"""Summarize ablation outputs using only current verifier evidence.

This intentionally ignores legacy `verified` arrays unless the result also
contains `verification_results` with exit_code=0. Older experiment files often
used `verified` to mean "agent-submitted candidate", which is not strong enough
for a performance claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*.json")):
                if child.name.startswith("_"):
                    rows.extend(load_file(child))
        else:
            rows.extend(load_file(path))
    return rows


def load_file(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return [{"source": str(path), "error": f"json parse failed: {type(e).__name__}: {e}"}]
    if isinstance(data, list):
        out = []
        for idx, row in enumerate(data):
            if isinstance(row, dict):
                row = dict(row)
                row.setdefault("_source", f"{path}#{idx}")
                out.append(row)
        return out
    if isinstance(data, dict):
        data = dict(data)
        data.setdefault("_source", str(path))
        return [data]
    return [{"source": str(path), "error": "json root is not object/list"}]


def verified_count(row: dict[str, Any]) -> int:
    if is_verify_output(row):
        return 1 if row.get("exit_code") == 0 else 0
    if is_cell_summary(row):
        return int(row.get("verified_findings") or 0)
    return sum(1 for vr in row.get("verification_results") or [] if isinstance(vr, dict) and vr.get("exit_code") == 0)


def candidate_count(row: dict[str, Any]) -> int:
    if is_verify_output(row):
        return 1
    if is_cell_summary(row):
        return int(row.get("candidate_hypotheses") or 0)
    return len(row.get("hypotheses") or [])


def compile_pass_count(row: dict[str, Any]) -> int:
    if is_verify_output(row):
        return 1 if any(g.get("gate") == "compile" and g.get("passed") for g in row.get("results") or []) else 0
    if is_cell_summary(row):
        return int(row.get("poc_compile_count") or 0)
    count = 0
    for vr in row.get("verification_results") or []:
        for gate in vr.get("results") or []:
            if gate.get("gate") == "compile" and gate.get("passed"):
                count += 1
                break
    return count


def is_verify_output(row: dict[str, Any]) -> bool:
    return "hypothesis_id" in row and "results" in row and "exit_code" in row


def is_cell_summary(row: dict[str, Any]) -> bool:
    return "cell_id" in row and "candidate_hypotheses" in row and "verified_findings" in row


def case_name(row: dict[str, Any]) -> str:
    return str(row.get("_case") or row.get("case") or row.get("case_id") or row.get("hypothesis_id") or "?")


def mode_name(row: dict[str, Any]) -> str:
    return str(row.get("_cell_mode") or row.get("cell_mode") or row.get("cell_id") or row.get("mode") or "?")


def backend_name(row: dict[str, Any]) -> str:
    backends = row.get("agent_backends")
    if isinstance(backends, list):
        return ",".join(str(b) for b in backends) or "?"
    return str(row.get("_agent_backend") or row.get("agent_backend") or row.get("mode") or "?")


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Harness Result Summary",
        "",
        "Counts use `verification_results.exit_code == 0` only. Current cell summaries are accepted; other rows without `verification_results` are legacy and count as 0 verified.",
        "",
        "| Source | Case | Mode | Backend | Candidates | Compile-pass | Verified | Cost | Wall | Notes |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    totals = {"candidates": 0, "compile": 0, "verified": 0, "cost": 0.0}
    for row in rows:
        source = str(row.get("_source") or row.get("source") or "")
        if row.get("error"):
            lines.append(f"| {source} | ? | ? | ? | 0 | 0 | 0 | ? | ? | {row['error']} |")
            continue
        cand = candidate_count(row)
        comp = compile_pass_count(row)
        ver = verified_count(row)
        totals["candidates"] += cand
        totals["compile"] += comp
        totals["verified"] += ver
        cost = row.get("cost_usd")
        if isinstance(cost, (int, float)):
            totals["cost"] += float(cost)
            cost_s = f"{cost:.3f}"
        else:
            cost_s = "?"
        wall = row.get("elapsed_sec") or row.get("_wall_outer_sec")
        wall_s = f"{wall:.0f}s" if isinstance(wall, (int, float)) else "?"
        notes = []
        if "verification_results" not in row:
            if is_verify_output(row):
                notes.append("direct verify.py output")
            elif is_cell_summary(row):
                notes.append("cell summary")
            else:
                notes.append("legacy:no verification_results")
        if row.get("exit_code") not in (None, 0):
            notes.append(f"agent_exit={row.get('exit_code')}")
        lines.append(
            f"| {source} | {case_name(row)} | {mode_name(row)} | {backend_name(row)} | {cand} | {comp} | {ver} | {cost_s} | {wall_s} | {', '.join(notes)} |"
        )
    lines.extend([
        "",
        f"Total candidates: {totals['candidates']}",
        f"Total compile-pass PoCs: {totals['compile']}",
        f"Total verified by current gate: {totals['verified']}",
        f"Total cost (where reported): ${totals['cost']:.3f}",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="result JSON file(s) or directories")
    ap.add_argument("--out", help="optional markdown output path")
    args = ap.parse_args(argv[1:])

    rows = load_rows([Path(p) for p in args.paths])
    md = render_markdown(rows)
    if args.out:
        Path(args.out).write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
