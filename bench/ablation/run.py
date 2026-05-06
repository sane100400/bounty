#!/usr/bin/env python3
"""
bench/ablation/run.py — Per-cell ablation runner.

A "cell" is one (dataset, model, harness configuration) combination.
This script runs every case in the dataset for that cell, produces per-case
JSON results, and aggregates into a single cell summary.

Harness components are flagged via env var so the agent process sees a
consistent config:
  HARNESS_RECON=1     → recon pack loaded into prompt context
  HARNESS_BANK=1      → hypothesis bank externalized to disk
  HARNESS_INV=1       → ClassInvariants + AttackInvariants imports allowed
  HARNESS_VERIFY=1    → Verification Gate strictly required (no PoC = no finding)
  HARNESS_TRACE2INV=1 → 23-template oracle library exposed
  HARNESS_SLITHER=1   → on-demand slither_tools available
  HARNESS_HALMOS=1    → Halmos symbolic gate enabled
  HARNESS_ECHIDNA=1   → Echidna closed loop enabled

The eight pre-defined ablation axes (A0..A8) are encoded as preset configs
in CELL_PRESETS below.

Per-case outputs:
  results/<cell_id>/<case_id>.json with:
    - cell_id, case_id, started_at, elapsed_sec
    - tool_calls (count + log)
    - hypotheses (list of generated)
    - verified_findings (those passing Verification Gate)
    - judge_decision (valid/invalid/duplicate per finding)
    - cost_usd (best-effort estimate)

Cell summary:
  results/<cell_id>/_summary.json with:
    - per-case stats aggregated
    - valid_recall = verified_valid / total_cases
    - poc_compile_rate, poc_pass_rate
    - median_tool_calls, median_time_to_first_finding
    - cost_per_attempt, cost_per_finding

`run_one_case()` shells out to `agent.py`, captures the raw agent JSON beside
the compact per-case summary, and post-verification results decide the
`findings_verified` counters.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------
# Cell presets — the 8 ablation axes
# ----------------------------------------------------------------------
CELL_PRESETS: dict[str, dict[str, str]] = {
    "R0_codex_raw": {
        "HARNESS_RAW_CODEX": "1",
        "HARNESS_SCORE_ONLY": "1",
        "HARNESS_NO_COVERAGE": "1",
        "HARNESS_NO_ATTACK_SURFACE": "1",
    },
    "H1_codex_harness": {
        "HARNESS_RECON": "1",
        "HARNESS_BANK": "1",
        "HARNESS_INV": "1",
        "HARNESS_VERIFY": "1",
        "HARNESS_TRACE2INV": "1",
        "HARNESS_SLITHER": "1",
        "HARNESS_MCGA": "1",
    },
    "H2_codex_deep": {
        "HARNESS_RECON": "1",
        "HARNESS_RECON_COMPACT": "1",
        "HARNESS_BANK": "1",
        "HARNESS_INV": "1",
        "HARNESS_VERIFY": "1",
        "HARNESS_TRACE2INV": "1",
        "HARNESS_SLITHER": "1",
        "HARNESS_DEEP": "1",
    },
    "A0_vanilla":   {"HARNESS_NO_COVERAGE": "1", "HARNESS_NO_ATTACK_SURFACE": "1"},
    "A1_recon":     {"HARNESS_RECON": "1"},
    "A2_bank":      {"HARNESS_RECON": "1", "HARNESS_BANK": "1"},
    "A3_invariants": {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1"},
    "A4_verify":    {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1", "HARNESS_VERIFY": "1"},
    "A5_trace2inv": {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1", "HARNESS_VERIFY": "1", "HARNESS_TRACE2INV": "1"},
    "A6_slither":   {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1", "HARNESS_VERIFY": "1", "HARNESS_TRACE2INV": "1", "HARNESS_SLITHER": "1"},
    "A7_halmos":    {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1", "HARNESS_VERIFY": "1", "HARNESS_TRACE2INV": "1", "HARNESS_SLITHER": "1", "HARNESS_HALMOS": "1"},
    "A8_echidna":   {"HARNESS_RECON": "1", "HARNESS_BANK": "1", "HARNESS_INV": "1", "HARNESS_VERIFY": "1", "HARNESS_TRACE2INV": "1", "HARNESS_SLITHER": "1", "HARNESS_HALMOS": "1", "HARNESS_ECHIDNA": "1"},
}


@dataclass
class CaseResult:
    cell_id: str
    case_id: str
    agent_backend: str
    started_at: float
    elapsed_sec: float = 0.0
    tool_calls: int = 0
    hypotheses_generated: int = 0
    findings_verified: int = 0
    findings_judged_valid: int = 0
    findings_judged_invalid: int = 0
    poc_compile_count: int = 0
    poc_pass_count: int = 0
    cost_usd_estimate: float = 0.0
    has_ground_truth: bool = False
    error: str = ""
    notes: list[str] = field(default_factory=list)


def load_dataset(spec_path: Path) -> list[dict]:
    """
    Dataset spec is a JSON array of:
      { "case_id": "...", "project_dir": "...", "ground_truth": [{"file":..,"vuln_class":..}], "base_ref": "..." }
    """
    return json.loads(spec_path.read_text())


def run_one_case(case: dict, cell_id: str, env: dict[str, str], out_dir: Path) -> CaseResult:
    """Run one benchmark case through bench/ablation/agent.py and summarize it."""
    started = time.time()
    res = CaseResult(
        cell_id=cell_id,
        case_id=case["case_id"],
        agent_backend=env.get("HARNESS_AGENT_BACKEND", "codex"),
        started_at=started,
        has_ground_truth=bool(case.get("ground_truth")),
    )
    project = Path(case["project_dir"]).expanduser().resolve()
    if not project.is_dir():
        res.error = f"project_dir not found: {project}"
        res.elapsed_sec = round(time.time() - started, 4)
        return res

    if env.get("HARNESS_RECON"):
        recon_out = out_dir / f"{res.case_id}.recon"
        cmd = [sys.executable, str(REPO / "harness" / "recon_pack.py"), str(project), "--out", str(recon_out)]
        if case.get("base_ref"):
            cmd.extend(["--base-ref", str(case["base_ref"])])
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=int(env.get("HARNESS_RECON_TIMEOUT_SEC", "1200")))
        if rr.returncode != 0:
            res.notes.append(f"recon_pack failed: {(rr.stdout + rr.stderr)[-1000:]}")
        else:
            res.notes.append(f"recon_pack: {recon_out}")
            env["HARNESS_RECON_DIR"] = str(recon_out.resolve())

    budget = int(case.get("budget") or env.get("HARNESS_BUDGET") or "5")
    cmd = [
        sys.executable,
        str(REPO / "bench" / "ablation" / "agent.py"),
        str(project),
        "--case-id",
        res.case_id,
        "--budget",
        str(budget),
    ]
    stdout_path = out_dir / f"{res.case_id}.agent.json"
    stderr_path = out_dir / f"{res.case_id}.agent.err"
    timeout = int(env.get("HARNESS_TIMEOUT_SEC", "3600")) + 60
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        res.error = f"agent timed out after {timeout}s"
        res.elapsed_sec = round(time.time() - started, 4)
        return res

    try:
        agent_result = json.loads(stdout_path.read_text())
    except Exception as e:
        res.error = f"agent output not JSON: {type(e).__name__}"
        res.notes.append(f"stdout={stdout_path}")
        res.notes.append(f"stderr={stderr_path}")
        res.elapsed_sec = round(time.time() - started, 4)
        return res

    res.tool_calls = int(agent_result.get("tool_calls") or agent_result.get("turns") or 0)
    res.hypotheses_generated = len(agent_result.get("hypotheses") or [])
    verified_hypotheses = _verified_hypotheses(agent_result)
    res.findings_verified = len(verified_hypotheses)
    res.poc_compile_count = _count_gate_pass(agent_result, "compile")
    res.poc_pass_count = _count_verified(agent_result)
    res.cost_usd_estimate = float(agent_result.get("cost_usd") or 0.0)
    valid, invalid = _judge_verified(case, verified_hypotheses)
    res.findings_judged_valid = valid
    res.findings_judged_invalid = invalid
    if not case.get("ground_truth"):
        res.notes.append("no ground_truth in dataset; verified findings left unjudged")
    if proc.returncode != 0:
        res.error = f"agent exit_code={proc.returncode}"
    if agent_result.get("error"):
        res.error = str(agent_result["error"])
    res.notes.append(f"agent_result={stdout_path}")
    res.elapsed_sec = round(time.time() - started, 4)
    return res


def _count_gate_pass(agent_result: dict, gate: str) -> int:
    count = 0
    for vr in agent_result.get("verification_results") or []:
        for gr in vr.get("results") or []:
            if gr.get("gate") == gate and gr.get("passed"):
                count += 1
                break
    return count


def _count_verified(agent_result: dict) -> int:
    return sum(1 for vr in agent_result.get("verification_results") or [] if vr.get("exit_code") == 0)


def _verified_hypotheses(agent_result: dict) -> list[dict]:
    """Return hypotheses that passed the current deterministic verifier."""
    verification_results = agent_result.get("verification_results") or []
    if not verification_results:
        return []
    ok_ids = {vr.get("hypothesis_id") for vr in verification_results if vr.get("exit_code") == 0}
    return [h for h in (agent_result.get("hypotheses") or []) if h.get("id") in ok_ids]


def _judge_verified(case: dict, verified: list[dict]) -> tuple[int, int]:
    ground_truth = case.get("ground_truth") or []
    if not ground_truth:
        return 0, 0
    valid = 0
    invalid = 0
    for hyp in verified:
        target = hyp.get("target") or {}
        hyp_file = str(target.get("file") or "")
        hyp_class = hyp.get("vuln_class")
        matched = False
        for gt in ground_truth:
            gt_file = str(gt.get("file") or "")
            gt_class = gt.get("vuln_class")
            file_ok = not gt_file or gt_file in hyp_file or hyp_file in gt_file
            class_ok = not gt_class or gt_class == hyp_class
            if file_ok and class_ok:
                matched = True
                break
        if matched:
            valid += 1
        else:
            invalid += 1
    return valid, invalid


def aggregate(cell_id: str, results: list[CaseResult]) -> dict:
    n = len(results)
    if n == 0:
        return {"cell_id": cell_id, "n": 0}
    valid = sum(r.findings_judged_valid for r in results)
    invalid = sum(r.findings_judged_invalid for r in results)
    compiled = sum(r.poc_compile_count for r in results)
    passed = sum(r.poc_pass_count for r in results)
    verified = sum(r.findings_verified for r in results)
    hypotheses = sum(r.hypotheses_generated for r in results)
    tool_calls = sorted(r.tool_calls for r in results)
    median_tc = tool_calls[n // 2] if n else 0
    cases_with_valid = sum(1 for r in results if r.findings_judged_valid > 0)
    ground_truth_cases = sum(1 for r in results if r.has_ground_truth)
    # Only cases with ground_truth contribute to valid_recall. If a dataset has
    # no ground truth, keep the denominator at n so the metric is visibly zero
    # rather than undefined.
    recall_denominator = ground_truth_cases or n
    return {
        "cell_id": cell_id,
        "agent_backends": sorted({r.agent_backend for r in results if r.agent_backend}),
        "n_cases": n,
        "valid_findings": valid,
        "invalid_findings": invalid,
        "cases_with_valid_finding": cases_with_valid,
        "candidate_hypotheses": hypotheses,
        "verified_findings": verified,
        "poc_compile_count": compiled,
        "poc_pass_count": passed,
        "valid_recall": (cases_with_valid / recall_denominator) if recall_denominator else 0,
        "poc_compile_rate": (compiled / max(1, hypotheses)) if hypotheses else 0,
        "poc_pass_rate": (passed / max(1, hypotheses)) if hypotheses else 0,
        "median_tool_calls": median_tc,
        "total_cost_usd": round(sum(r.cost_usd_estimate for r in results), 4),
        "errors": [r.error for r in results if r.error],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", required=True, choices=list(CELL_PRESETS.keys()))
    ap.add_argument("--dataset", required=True, help="path to dataset spec JSON")
    ap.add_argument("--out", default="bench/ablation/results")
    ap.add_argument("--limit", type=int, default=0, help="cap number of cases (0=all)")
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument(
        "--backend",
        default="codex",
        choices=["codex", "codex-cli", "claude", "claude-cli", "anthropic", "api", "sdk"],
        help="agent backend to benchmark; defaults to codex after resetting inherited HARNESS_* flags",
    )
    ap.add_argument("--max-budget-usd", type=float, default=5.0)
    ap.add_argument("--timeout-sec", type=int, default=3600)
    args = ap.parse_args(argv[1:])

    dataset = load_dataset(Path(args.dataset))
    if args.limit:
        dataset = dataset[: args.limit]

    env_overrides = CELL_PRESETS[args.cell]
    out_dir = Path(args.out) / args.cell
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []
    for case in dataset:
        # Snapshot environment with cell-specific flags
        env = os.environ.copy()
        preserved_harness_env = {
            k: v for k, v in env.items()
            if k in {
                "HARNESS_CODEX_MODEL",
                "HARNESS_CODEX_SANDBOX",
                "HARNESS_MODEL",
            }
        }
        # Reset all HARNESS_* first, then apply cell preset
        for k in list(env):
            if k.startswith("HARNESS_"):
                env.pop(k, None)
        env.update(preserved_harness_env)
        env["HARNESS_AGENT_BACKEND"] = args.backend
        env.update(env_overrides)
        env["HARNESS_CELL_ID"] = args.cell
        env["HARNESS_BUDGET"] = str(args.budget)
        env["HARNESS_MAX_BUDGET_USD"] = str(args.max_budget_usd)
        env["HARNESS_TIMEOUT_SEC"] = str(args.timeout_sec)

        res = run_one_case(case, args.cell, env, out_dir)
        (out_dir / f"{res.case_id}.json").write_text(json.dumps(asdict(res), indent=2))
        results.append(res)

    summary = aggregate(args.cell, results)
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
