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
             max_budget_usd: float = 20.0, timeout_sec: int = 5400,
             backend: str = "codex") -> dict:
    env = benchmark_env(backend)
    try:
        apply_mode_env(env, mode)
    except ValueError as e:
        return {"_mode": mode, "_agent_backend": backend, "error": str(e)}
    env["HARNESS_MAX_BUDGET_USD"] = str(max_budget_usd)
    env["HARNESS_TIMEOUT_SEC"] = str(timeout_sec)
    recon_note = prepare_recon(case_dir, case_id, mode, env)
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
    result["_agent_backend"] = backend
    if recon_note:
        result["_recon"] = recon_note
    result["_wall_outer_sec"] = round(wall, 2)
    return result


def summarize(rows: list[dict]) -> str:
    out = ["# Holdout Sweep Results", "",
           "| Case | Mode | Backend | Cost | Wall | Candidates | Verified (current gate) | Notes |",
           "|---|---|---|---|---|---|---|---|"]
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
            backend = m.get("_agent_backend") or m.get("mode") or "?"
            candidates = len(m.get("hypotheses", []) or [])
            verified = verified_count(m)
            notes = ""
            if "verification_results" not in m:
                notes = "legacy result: no verification_results"
            cs = f"${cost:.3f}" if isinstance(cost, (int, float)) else "?"
            ws = f"{wall:.0f}s" if isinstance(wall, (int, float)) else "?"
            out.append(f"| {case} | {mode} | {backend} | {cs} | {ws} | {candidates} | {verified} | {notes} |")
    out.append("")
    # Aggregates
    full_candidates = sum(len(m["full"].get("hypotheses", [])) for m in by_case.values()
                          if "full" in m and m["full"].get("hypotheses"))
    full_verified = sum(verified_count(m["full"]) for m in by_case.values() if "full" in m)
    out.append(f"Total candidate findings (full mode): {full_candidates}")
    out.append(f"Total verified findings by current gate (full mode): {full_verified}")
    return "\n".join(out)


def verified_count(result: dict) -> int:
    """Count only deterministic verifier passes from current agent output."""
    if "verification_results" not in result:
        return 0
    return sum(1 for vr in result.get("verification_results") or [] if vr.get("exit_code") == 0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--max-budget-usd", type=float, default=20.0)
    ap.add_argument("--timeout-sec", type=int, default=5400)
    ap.add_argument("--modes", default="baseline,full")
    ap.add_argument(
        "--backend",
        default="codex",
        choices=["codex", "codex-cli", "claude", "claude-cli", "anthropic", "api", "sdk"],
        help="agent backend to benchmark; defaults to codex after resetting inherited HARNESS_* flags",
    )
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
                               timeout_sec=args.timeout_sec,
                               backend=args.backend)
            cost = result.get("cost_usd")
            candidates = len(result.get("hypotheses", []) or [])
            verified = verified_count(result)
            print(f"    cost={cost} candidates={candidates} verified={verified}")
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
