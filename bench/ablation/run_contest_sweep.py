#!/usr/bin/env python3
"""
bench/ablation/run_contest_sweep.py — sweep over post-cutoff audit-contest
GitHub repos (Sherlock, Code4rena). Different from run_holdout.py which
takes Sourcify-fetchable contract addresses; this one consumes already-
cloned Foundry projects.

Add new targets to CONTESTS list. Each run goes baseline + full v2 in
parallel-ish (sequential per-case, but parallel-able per-mode).

Usage:
  python3 bench/ablation/run_contest_sweep.py [--budget 15] [--max-budget-usd 20]
  python3 bench/ablation/run_contest_sweep.py --only fluid    # one case
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "bench" / "ablation" / "results"

# (case_id, project_dir) — clone these manually before running.
# All entries below are confirmed post-Opus-4.7-cutoff (>= 2026-02 disclosure).
CONTESTS = [
    ("fluid_dex_v2",  Path("/tmp/fluid_dex/fluid-contracts")),
    ("chainlink",     Path("/tmp/c4_chainlink")),
]


def run_mode(case_dir: Path, case_id: str, mode: str, budget: int,
             max_budget_usd: float, timeout_sec: int) -> dict:
    env = os.environ.copy()
    if mode == "full":
        env["HARNESS_KG"] = "1"
        env["HARNESS_MCGA"] = "1"
    elif mode == "baseline":
        env.pop("HARNESS_KG", None); env.pop("HARNESS_MCGA", None)
    env["HARNESS_MAX_BUDGET_USD"] = str(max_budget_usd)
    env["HARNESS_TIMEOUT_SEC"] = str(timeout_sec)

    started = time.time()
    out_path = RESULTS / "contest_sweep" / f"{case_id}_{mode}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path = out_path.with_suffix(".err")

    with out_path.open("w") as out, err_path.open("w") as err:
        r = subprocess.run(
            [sys.executable, str(REPO / "bench" / "ablation" / "agent.py"),
             str(case_dir), "--case-id", f"contest_{case_id}_{mode}",
             "--budget", str(budget)],
            stdout=out, stderr=err,
            timeout=timeout_sec + 60, env=env,
        )
    wall = time.time() - started
    try:
        result = json.loads(out_path.read_text())
    except Exception:
        result = {"_parse_error": True,
                  "stdout_path": str(out_path),
                  "stderr_path": str(err_path)}
    result["_cell_mode"] = mode
    result["_case"] = case_id
    result["_wall_outer_sec"] = round(wall, 2)
    result["_exit_code"] = r.returncode
    return result


def summarize(rows: list[dict]) -> str:
    lines = ["# Contest Sweep Results", "",
             "| Case | Mode | Cost | Wall | Turns | Findings |",
             "|---|---|---|---|---|---|"]
    by = {}
    for r in rows:
        c = r.get("_case"); m = r.get("_cell_mode")
        by.setdefault(c, {})[m] = r
    for case in sorted(by):
        for m in ("baseline", "full"):
            d = by[case].get(m)
            if not d: continue
            cost = d.get("cost_usd")
            wall = d.get("elapsed_sec")
            n = len(d.get("hypotheses", []) or [])
            t = d.get("turns")
            cs = f"${cost:.2f}" if isinstance(cost, (int, float)) else "?"
            ws = f"{wall:.0f}s" if isinstance(wall, (int, float)) else "?"
            lines.append(f"| {case} | {m} | {cs} | {ws} | {t} | {n} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=15)
    ap.add_argument("--max-budget-usd", type=float, default=20.0)
    ap.add_argument("--timeout-sec", type=int, default=5400)
    ap.add_argument("--modes", default="baseline,full")
    ap.add_argument("--only")
    args = ap.parse_args(argv[1:])

    targets = CONTESTS
    if args.only:
        keep = [s.strip() for s in args.only.split(",")]
        targets = [t for t in targets if any(k in t[0] for k in keep)]

    modes = [m.strip() for m in args.modes.split(",")]
    rows = []
    for case_id, case_dir in targets:
        if not case_dir.is_dir():
            print(f"SKIP {case_id}: {case_dir} not found")
            continue
        print(f"\n=== {case_id} at {case_dir} ===")
        for mode in modes:
            print(f"  running mode={mode}...")
            r = run_mode(case_dir, case_id, mode, args.budget,
                         args.max_budget_usd, args.timeout_sec)
            print(f"    cost={r.get('cost_usd')} findings={len(r.get('hypotheses',[]) or [])}")
            rows.append(r)

    out = RESULTS / "contest_sweep" / "summary.json"
    out.write_text(json.dumps(rows, indent=2, default=str))
    md = RESULTS / "contest_sweep" / "summary.md"
    md.write_text(summarize(rows))
    print(f"\nSummary: {md}\nFull: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
