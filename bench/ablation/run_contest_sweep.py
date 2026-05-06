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


def benchmark_env(backend: str) -> dict[str, str]:
    env = os.environ.copy()
    preserved_harness_env = {
        k: v for k, v in env.items()
        if k in {
            "HARNESS_CODEX_MODEL",
            "HARNESS_CODEX_SANDBOX",
            "HARNESS_MODEL",
        }
    }
    for k in list(env):
        if k.startswith("HARNESS_"):
            env.pop(k, None)
    env.update(preserved_harness_env)
    env["HARNESS_AGENT_BACKEND"] = backend
    return env


def apply_mode_env(env: dict[str, str], mode: str) -> None:
    """Apply benchmark mode flags after benchmark_env has reset HARNESS_*."""
    if mode == "baseline":
        env["HARNESS_RAW_CODEX"] = "1"
        env["HARNESS_SCORE_ONLY"] = "1"
        env["HARNESS_NO_COVERAGE"] = "1"
        env["HARNESS_NO_ATTACK_SURFACE"] = "1"
    elif mode == "full":
        env["HARNESS_RECON"] = "1"
        env["HARNESS_BANK"] = "1"
        env["HARNESS_INV"] = "1"
        env["HARNESS_VERIFY"] = "1"
        env["HARNESS_TRACE2INV"] = "1"
        env["HARNESS_SLITHER"] = "1"
        env["HARNESS_MCGA"] = "1"
    else:
        raise ValueError(f"unknown mode {mode!r}; expected baseline or full")


def prepare_recon(case_dir: Path, case_id: str, mode: str, env: dict[str, str]) -> str:
    if not env.get("HARNESS_RECON"):
        return ""
    recon_out = case_dir / ".harness_recon" / f"{case_id}_{mode}"
    cmd = [sys.executable, str(REPO / "harness" / "recon_pack.py"), str(case_dir), "--out", str(recon_out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=int(env.get("HARNESS_RECON_TIMEOUT_SEC", "1200")))
    if r.returncode != 0:
        return f"recon_pack failed: {(r.stdout + r.stderr)[-1000:]}"
    env["HARNESS_RECON_DIR"] = str(recon_out.resolve())
    return str(recon_out)


def run_mode(case_dir: Path, case_id: str, mode: str, budget: int,
             max_budget_usd: float, timeout_sec: int, backend: str) -> dict:
    env = benchmark_env(backend)
    try:
        apply_mode_env(env, mode)
    except ValueError as e:
        return {"_cell_mode": mode, "_case": case_id, "_agent_backend": backend, "error": str(e)}
    env["HARNESS_MAX_BUDGET_USD"] = str(max_budget_usd)
    env["HARNESS_TIMEOUT_SEC"] = str(timeout_sec)
    recon_note = prepare_recon(case_dir, case_id, mode, env)

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
    result["_agent_backend"] = backend
    if recon_note:
        result["_recon"] = recon_note
    result["_wall_outer_sec"] = round(wall, 2)
    result["_exit_code"] = r.returncode
    return result


def summarize(rows: list[dict]) -> str:
    lines = ["# Contest Sweep Results", "",
             "| Case | Mode | Backend | Cost | Wall | Turns | Candidates | Verified (current gate) | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
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
            backend = d.get("_agent_backend") or d.get("mode") or "?"
            candidates = len(d.get("hypotheses", []) or [])
            verified = verified_count(d)
            notes = ""
            if "verification_results" not in d:
                notes = "legacy result: no verification_results"
            t = d.get("turns")
            cs = f"${cost:.2f}" if isinstance(cost, (int, float)) else "?"
            ws = f"{wall:.0f}s" if isinstance(wall, (int, float)) else "?"
            lines.append(f"| {case} | {m} | {backend} | {cs} | {ws} | {t} | {candidates} | {verified} | {notes} |")
    return "\n".join(lines) + "\n"


def verified_count(result: dict) -> int:
    """Count only deterministic verifier passes from current agent output."""
    if "verification_results" not in result:
        return 0
    return sum(1 for vr in result.get("verification_results") or [] if vr.get("exit_code") == 0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=15)
    ap.add_argument("--max-budget-usd", type=float, default=20.0)
    ap.add_argument("--timeout-sec", type=int, default=5400)
    ap.add_argument("--modes", default="baseline,full")
    ap.add_argument(
        "--backend",
        default="codex",
        choices=["codex", "codex-cli", "claude", "claude-cli", "anthropic", "api", "sdk"],
        help="agent backend to benchmark; defaults to codex after resetting inherited HARNESS_* flags",
    )
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
                         args.max_budget_usd, args.timeout_sec, args.backend)
            print(
                f"    cost={r.get('cost_usd')} "
                f"candidates={len(r.get('hypotheses', []) or [])} "
                f"verified={verified_count(r)}"
            )
            rows.append(r)

    out = RESULTS / "contest_sweep" / "summary.json"
    out.write_text(json.dumps(rows, indent=2, default=str))
    md = RESULTS / "contest_sweep" / "summary.md"
    md.write_text(summarize(rows))
    print(f"\nSummary: {md}\nFull: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
