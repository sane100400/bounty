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

NOTE: This is a SKELETON. The actual agent loop (LLM calls + tool dispatch)
is not implemented here yet — that goes in `agent.py` and is wired in via
`run_one_case()`. The skeleton defines the data model and CLI surface so
downstream pieces can hook in cleanly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------
# Cell presets — the 8 ablation axes
# ----------------------------------------------------------------------
CELL_PRESETS: dict[str, dict[str, str]] = {
    "A0_vanilla":   {},
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
    error: str = ""
    notes: list[str] = field(default_factory=list)


def load_dataset(spec_path: Path) -> list[dict]:
    """
    Dataset spec is a JSON array of:
      { "case_id": "...", "project_dir": "...", "ground_truth": [{"file":..,"vuln_class":..}], "base_ref": "..." }
    """
    return json.loads(spec_path.read_text())


def run_one_case(case: dict, cell_id: str, env: dict[str, str], out_dir: Path) -> CaseResult:
    """
    Stub. Real implementation will:
      1. Build the LLM agent process with `env` exported
      2. Initialize harness/recon_pack.py if HARNESS_RECON
      3. Run the agent loop with a budget (e.g. 5 iterations a la A1)
      4. Capture every tool call + every hypothesis emitted
      5. For each hypothesis, run harness/verify.py if HARNESS_VERIFY
      6. Emit a CaseResult with all counters populated

    Until the agent is wired, this stub records that the case was scheduled.
    """
    res = CaseResult(
        cell_id=cell_id,
        case_id=case["case_id"],
        started_at=time.time(),
    )
    res.notes.append("agent loop not yet implemented — scheduled only")
    res.elapsed_sec = round(time.time() - res.started_at, 4)
    return res


def aggregate(cell_id: str, results: list[CaseResult]) -> dict:
    n = len(results)
    if n == 0:
        return {"cell_id": cell_id, "n": 0}
    valid = sum(r.findings_judged_valid for r in results)
    invalid = sum(r.findings_judged_invalid for r in results)
    compiled = sum(r.poc_compile_count for r in results)
    passed = sum(r.poc_pass_count for r in results)
    tool_calls = sorted(r.tool_calls for r in results)
    median_tc = tool_calls[n // 2] if n else 0
    return {
        "cell_id": cell_id,
        "n_cases": n,
        "valid_findings": valid,
        "invalid_findings": invalid,
        "valid_recall": (valid / n) if n else 0,
        "poc_compile_rate": (compiled / n) if n else 0,
        "poc_pass_rate": (passed / n) if n else 0,
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
        # Reset all HARNESS_* first, then apply cell preset
        for k in list(env):
            if k.startswith("HARNESS_"):
                env.pop(k, None)
        env.update(env_overrides)

        res = run_one_case(case, args.cell, env, out_dir)
        (out_dir / f"{res.case_id}.json").write_text(json.dumps(asdict(res), indent=2))
        results.append(res)

    summary = aggregate(args.cell, results)
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
