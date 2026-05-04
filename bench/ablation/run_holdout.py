#!/usr/bin/env python3
"""
bench/ablation/run_holdout.py — full sweep over 6 post-cutoff DeFiHackLabs
incidents. For each: fetch source via Sourcify, scaffold Foundry project,
run agent in two modes (baseline vs full v2 stack with KG+MCGA), record
findings, cost, time.

Output: bench/ablation/results/holdout_sweep.json + Markdown summary.

Usage:
  python3 bench/ablation/run_holdout.py [--budget 3] [--modes baseline,full]
  python3 bench/ablation/run_holdout.py --only laxo  # one case
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "bench" / "ablation" / "results"

# (case_id, chain, vuln_contract_addr, confirmed_from_keyinfo)
# Cases where @KeyInfo unambiguously names the vulnerable contract.
# Moonwell/Curve_LlamaLend/Venus_THE not in the auto-confirmed set —
# add manually after reading the PoC body.
HOLDOUT_TARGETS = [
    ("laxo",     "bsc",      "0x62951CaD7659393BF07fbe790cF898A3B6d317CB", True),
    ("alkemi",   "ethereum", "0x4822D9172e5b76b9Db37B75f5552F9988F98a888", True),
    ("est",      "bsc",      "0xD4524Be41cd452576aB9FF7b68a0b89aF8498a91", True),
    # Manual additions (commented out — verify the address before enabling):
    # ("moonwell", "base", "0x...", False),
    # ("curve_llamalend", "ethereum", "0x...", False),
    # ("venus_the", "bsc", "0x...", False),
]


def fetch_source(chain: str, addr: str, work: Path) -> dict:
    out = work / "raw"
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, str(REPO / "harness" / "tools" / "source_fetcher.py"),
         chain, addr, str(out)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"ok": False, "stderr": r.stderr[-500:], "stdout_tail": r.stdout[-500:]}


def assemble_case(case_id: str, fetch_result: dict, work: Path) -> Path | None:
    """Move fetched files into a clean <case>/src/ root."""
    case = work / "case"
    if case.exists():
        shutil.rmtree(case)
    case.mkdir(parents=True)
    raw = work / "raw"
    # Sourcify lays files at contracts/{full,partial}_match/<chain>/<addr>/sources/...
    sources_dirs = list(raw.rglob("sources"))
    if sources_dirs:
        shutil.copytree(sources_dirs[0], case / "src")
        return case
    # Fallback: if raw has *.sol directly
    sols = list(raw.rglob("*.sol"))
    if sols:
        (case / "src").mkdir()
        for s in sols:
            (case / "src" / s.name).write_text(s.read_text())
        return case
    return None


def run_mode(case_dir: Path, case_id: str, mode: str, budget: int,
             max_budget_usd: float = 20.0, timeout_sec: int = 5400) -> dict:
    env = os.environ.copy()
    if mode == "full":
        env["HARNESS_KG"] = "1"
        env["HARNESS_MCGA"] = "1"
    elif mode == "baseline":
        env.pop("HARNESS_KG", None); env.pop("HARNESS_MCGA", None)
    env["HARNESS_MAX_BUDGET_USD"] = str(max_budget_usd)
    env["HARNESS_TIMEOUT_SEC"] = str(timeout_sec)
    started = time.time()
    r = subprocess.run(
        [sys.executable, str(REPO / "bench" / "ablation" / "agent.py"),
         str(case_dir), "--case-id", f"holdout_{case_id}_{mode}",
         "--budget", str(budget)],
        capture_output=True, text=True, timeout=timeout_sec + 60, env=env,
    )
    wall = time.time() - started
    try:
        result = json.loads(r.stdout)
    except Exception:
        result = {"_parse_error": True, "stdout_tail": r.stdout[-1500:],
                  "stderr_tail": r.stderr[-1500:]}
    result["_mode"] = mode
    result["_wall_outer_sec"] = round(wall, 2)
    return result


def summarize(rows: list[dict]) -> str:
    out = ["# Holdout Sweep Results", "",
           "| Case | Mode | Cost | Wall | Findings |",
           "|---|---|---|---|---|"]
    by_case = {}
    for r in rows:
        c = r["case"]
        by_case.setdefault(c, {})[r.get("cell_mode") or r.get("mode")] = r
    for case, modes in by_case.items():
        for mode in ("baseline", "full"):
            m = modes.get(mode)
            if not m:
                continue
            cost = m.get("cost_usd")
            wall = m.get("elapsed_sec")
            n = len(m.get("hypotheses", []) or [])
            cs = f"${cost:.3f}" if isinstance(cost, (int, float)) else "?"
            ws = f"{wall:.0f}s" if isinstance(wall, (int, float)) else "?"
            out.append(f"| {case} | {mode} | {cs} | {ws} | {n} |")
    out.append("")
    # Aggregates
    base_n = sum(len(m["full"].get("hypotheses", [])) for m in by_case.values()
                 if "full" in m and m["full"].get("hypotheses"))
    out.append(f"Total candidate findings (full mode): {base_n}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--max-budget-usd", type=float, default=20.0)
    ap.add_argument("--timeout-sec", type=int, default=5400)
    ap.add_argument("--modes", default="baseline,full")
    ap.add_argument("--only", help="comma-separated case_id substring filter")
    ap.add_argument("--work", default="/tmp/holdout_sweep")
    args = ap.parse_args(argv[1:])

    RESULTS.mkdir(parents=True, exist_ok=True)
    work_root = Path(args.work)
    work_root.mkdir(parents=True, exist_ok=True)

    targets = HOLDOUT_TARGETS
    if args.only:
        keep = [s.strip() for s in args.only.split(",")]
        targets = [t for t in targets if any(k in t[0] for k in keep)]

    # Strip the confirmed-flag column for downstream loop
    targets = [(c, ch, a) for (c, ch, a, *_) in targets]

    modes = [m.strip() for m in args.modes.split(",")]
    rows = []
    for case_id, chain, addr in targets:
        case_work = work_root / case_id
        case_work.mkdir(parents=True, exist_ok=True)

        print(f"\n=== {case_id} ({chain}, {addr}) ===")
        fetch = fetch_source(chain, addr, case_work)
        if not fetch.get("ok"):
            print(f"  fetch failed: {fetch.get('error') or fetch}")
            rows.append({"case": case_id, "mode": "fetch", "error": fetch})
            continue

        case_dir = assemble_case(case_id, fetch, case_work)
        if not case_dir:
            print(f"  assemble failed")
            rows.append({"case": case_id, "mode": "assemble", "error": "no source files"})
            continue
        print(f"  assembled at {case_dir}")

        for mode in modes:
            print(f"  running mode={mode}...")
            result = run_mode(case_dir, case_id, mode, args.budget,
                               max_budget_usd=args.max_budget_usd,
                               timeout_sec=args.timeout_sec)
            cost = result.get("cost_usd")
            n = len(result.get("hypotheses", []) or [])
            print(f"    cost={cost} findings={n}")
            row = {**result, "case": case_id, "cell_mode": mode}  # cell_mode last so it wins
            rows.append(row)

    out_path = RESULTS / "holdout_sweep.json"
    out_path.write_text(json.dumps(rows, indent=2, default=str))
    summary_path = RESULTS / "holdout_sweep.md"
    summary_path.write_text(summarize(rows))
    print(f"\nResults: {out_path}\nSummary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
