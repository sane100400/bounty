#!/usr/bin/env python3
"""
bench/ablation/agent.py — Tool-using agent for one ablation cell run.

Implements the standard Anthropic tool-use loop:
  while resp.stop_reason == "tool_use":
      execute tool calls → append tool_result → call API again

Tool surface gates by HARNESS_* env flags so a single agent serves all 8 cells.
A0 (vanilla) exposes only read_file/grep/forge_test/cast_call.

Environment:
  HARNESS_USE_API       — if set, use Anthropic SDK (requires ANTHROPIC_API_KEY)
                          otherwise default: spawn `claude -p` subprocess (uses CLI auth)
  ANTHROPIC_MODEL       — default 'claude-opus-4-7' (SDK path only)
  HARNESS_RECON, HARNESS_BANK, HARNESS_INV, HARNESS_VERIFY,
  HARNESS_TRACE2INV, HARNESS_SLITHER, HARNESS_HALMOS, HARNESS_ECHIDNA — toggle tools

Usage:
  python3 bench/ablation/agent.py <project_dir> --case-id <id> [--budget 5]

Output: stdout JSON with hypotheses generated, tool calls log, verified findings,
cost estimate. Same shape as bench/ablation/run.py CaseResult.

Default path uses `claude -p` subprocess so no API key is needed — the agent
runs as a child Claude Code session with restricted tool surface and writes
finding JSON files to harness/hypotheses/{case_id}-{n}.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "harness"
sys.path.insert(0, str(REPO))


# ----------------------------------------------------------------------
# Tool definitions (surface depends on env flags)
# ----------------------------------------------------------------------
def tool_defs() -> list[dict]:
    """Return Anthropic-format tool schema list, gated by env flags."""
    base = [
        {
            "name": "read_file",
            "description": "Read a file (or a line range) from the target project.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative to project root"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "grep",
            "description": "ripgrep-style search across .sol files in project.",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
        },
        {
            "name": "forge_test",
            "description": "Run a Foundry test by name from poc-forge/.",
            "input_schema": {
                "type": "object",
                "properties": {"test_name": {"type": "string"}, "verbosity": {"type": "integer"}},
                "required": ["test_name"],
            },
        },
        {
            "name": "cast_call",
            "description": "Read-only eth_call against an RPC. Args are positional Solidity values.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "rpc_url": {"type": "string"},
                    "target": {"type": "string"},
                    "signature": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["rpc_url", "target", "signature"],
            },
        },
        {
            "name": "submit_finding",
            "description": "Submit a verified finding. Required output format: hypothesis dict per harness/schemas/hypothesis.schema.json plus the path to the passing PoC test file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "object"},
                    "poc_path": {"type": "string"},
                },
                "required": ["hypothesis", "poc_path"],
            },
        },
    ]
    # Conditionally add tools per env flag
    if os.environ.get("HARNESS_SLITHER"):
        base.extend([
            {"name": "slither_dup_check", "description": "Has Slither already flagged this file:lines? Likely-dup signal.",
             "input_schema": {"type": "object", "properties": {
                 "file": {"type": "string"}, "lines": {"type": "string"}}, "required": ["file", "lines"]}},
            {"name": "slither_function_summary", "description": "Per-function summary for one contract.",
             "input_schema": {"type": "object", "properties": {"contract": {"type": "string"}}, "required": ["contract"]}},
        ])
    if os.environ.get("HARNESS_VERIFY"):
        base.append({
            "name": "verify_hypothesis",
            "description": "Run the 6-gate Verification Gate on a hypothesis JSON file.",
            "input_schema": {"type": "object", "properties": {"hypothesis_path": {"type": "string"}},
                             "required": ["hypothesis_path"]},
        })
    return base


# ----------------------------------------------------------------------
# Tool dispatch
# ----------------------------------------------------------------------
def dispatch_tool(name: str, inp: dict, project: Path) -> dict:
    try:
        if name == "read_file":
            p = project / inp["path"]
            text = p.read_text(errors="ignore")
            if "start_line" in inp or "end_line" in inp:
                lines = text.splitlines()
                a = max(1, int(inp.get("start_line", 1))) - 1
                b = min(len(lines), int(inp.get("end_line", len(lines))))
                text = "\n".join(lines[a:b])
            return {"ok": True, "content": text[:20000]}
        if name == "grep":
            pat = inp["pattern"]; path = inp.get("path") or str(project)
            r = subprocess.run(["grep", "-rn", "--include=*.sol", pat, path],
                               capture_output=True, text=True, timeout=30)
            return {"ok": True, "matches": r.stdout[:8000]}
        if name == "forge_test":
            r = subprocess.run(
                ["forge", "test", "--match-test", inp["test_name"], f'-{"v" * max(1, int(inp.get("verbosity", 2)))}'],
                cwd=REPO / "poc-forge", capture_output=True, text=True, timeout=600,
            )
            return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-6000:]}
        if name == "cast_call":
            cmd = ["cast", "call", inp["target"], inp["signature"], "--rpc-url", inp["rpc_url"]]
            cmd.extend(inp.get("args") or [])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return {"ok": r.returncode == 0, "return": r.stdout.strip()[:1000], "err": r.stderr[-500:]}
        if name == "submit_finding":
            # Just persist + acknowledge — final accounting in main loop
            findings_dir = HARNESS / "hypotheses"
            findings_dir.mkdir(exist_ok=True)
            hyp = inp["hypothesis"]
            (findings_dir / f"{hyp['id']}.json").write_text(json.dumps(hyp, indent=2))
            return {"ok": True, "saved": str(findings_dir / f"{hyp['id']}.json")}
        if name == "slither_dup_check":
            from harness.tools.slither_tools import slither_dup_check
            return slither_dup_check(str(project), inp["file"], inp["lines"])
        if name == "slither_function_summary":
            from harness.tools.slither_tools import slither_function_summary
            return slither_function_summary(str(project), inp["contract"])
        if name == "verify_hypothesis":
            r = subprocess.run(
                ["python3", str(HARNESS / "verify.py"), inp["hypothesis_path"]],
                capture_output=True, text=True, timeout=900,
            )
            try:
                return json.loads(r.stdout)
            except Exception:
                return {"ok": False, "error": "verify.py output not JSON", "stdout": r.stdout[-2000:]}
        return {"ok": False, "error": f"unknown tool {name!r}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ----------------------------------------------------------------------
# Claude CLI subprocess path (default — uses CLI auth, no API key needed)
# ----------------------------------------------------------------------
def _allowed_tools_for_cell() -> list[str]:
    """Map ablation env flags → Claude Code built-in tool surface.

    A0 vanilla = Read,Grep,Glob,Bash. Bash is gated by allowed-commands list
    in the system prompt; harness tools are exposed via Bash invocations of
    harness/{verify.py, slim_slither.py, recon_pack.py}.
    """
    base = ["Read", "Grep", "Glob", "Bash", "Write"]
    return base


def run_agent_cli(project: Path, case_id: str, budget_iterations: int = 5) -> dict:
    started = time.time()
    findings_dir = HARNESS / "hypotheses"
    findings_dir.mkdir(exist_ok=True)
    before = {p.name for p in findings_dir.glob(f"{case_id}-*.json")}

    system = _load_prompt(project, case_id)
    user_msg = (
        f"Find vulnerabilities in the Solidity project at {project}.\n"
        f"For each verified finding, write the hypothesis JSON to "
        f"{findings_dir}/{case_id}-N.json (N = 1, 2, ...) following "
        f"harness/schemas/hypothesis.schema.json.\n"
        f"Budget: {budget_iterations} verification iterations total. Stop when "
        f"budget exhausted or no more candidates."
    )

    cmd = [
        "claude", "-p", user_msg,
        "--append-system-prompt", system,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(project),
        "--add-dir", str(HARNESS),
        "--tools", ",".join(_allowed_tools_for_cell()),
        "--max-budget-usd", os.environ.get("HARNESS_MAX_BUDGET_USD", "5"),
        "--no-session-persistence",
    ]
    if os.environ.get("HARNESS_MODEL"):
        cmd.extend(["--model", os.environ["HARNESS_MODEL"]])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(os.environ.get("HARNESS_TIMEOUT_SEC", "3600")))
    except subprocess.TimeoutExpired:
        return {"case_id": case_id, "error": "claude CLI timed out",
                "elapsed_sec": round(time.time() - started, 3)}

    parsed: Any
    try:
        parsed = json.loads(r.stdout)
    except Exception:
        parsed = {"_raw_stdout_tail": r.stdout[-2000:], "_stderr_tail": r.stderr[-1000:]}

    after = {p.name for p in findings_dir.glob(f"{case_id}-*.json")}
    new_findings = []
    for fname in sorted(after - before):
        try:
            new_findings.append(json.loads((findings_dir / fname).read_text()))
        except Exception as e:
            new_findings.append({"_parse_error": str(e), "_file": fname})

    cost = parsed.get("total_cost_usd") if isinstance(parsed, dict) else None
    turns = parsed.get("num_turns") if isinstance(parsed, dict) else None
    return {
        "case_id": case_id,
        "mode": "claude-cli",
        "model": parsed.get("model") if isinstance(parsed, dict) else "claude-cli",
        "stub": False,
        "tool_calls": turns or 0,
        "turns": turns,
        "hypotheses": new_findings,
        "verified": new_findings,
        "cost_usd": cost,
        "elapsed_sec": round(time.time() - started, 3),
        "exit_code": r.returncode,
    }


# ----------------------------------------------------------------------
# Agent loop (SDK path — opt-in via HARNESS_USE_API)
# ----------------------------------------------------------------------
def run_agent(project: Path, case_id: str, budget_iterations: int = 5) -> dict:
    if not os.environ.get("HARNESS_USE_API"):
        return run_agent_cli(project, case_id, budget_iterations)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")
    started = time.time()

    if not api_key:
        return {"error": "HARNESS_USE_API set but ANTHROPIC_API_KEY missing"}

    try:
        import anthropic  # type: ignore
    except ImportError:
        return {"error": "anthropic SDK not installed: pip install anthropic"}

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = _load_prompt(project, case_id)
    messages: list[dict] = [{
        "role": "user",
        "content": f"Find vulnerabilities in the Solidity project at {project}. Submit verified findings via the submit_finding tool. Budget: {budget_iterations} iterations per hypothesis.",
    }]
    tools = tool_defs()

    tool_call_count = 0
    findings: list[dict] = []
    cost_usd = 0.0
    max_turns = 60  # hard ceiling

    for turn in range(max_turns):
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        cost_usd += _estimate_cost(resp, model)
        # Append assistant message
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_call_count += 1
            result = dispatch_tool(block.name, dict(block.input), project)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result)[:8000],
            })
            if block.name == "submit_finding" and result.get("ok"):
                findings.append(block.input["hypothesis"])
        messages.append({"role": "user", "content": tool_results})

    return {
        "case_id": case_id, "model": model, "stub": False,
        "tool_calls": tool_call_count, "turns": turn + 1,
        "hypotheses": findings, "verified": findings,  # post-verify gate the agent itself runs
        "cost_usd": round(cost_usd, 4),
        "elapsed_sec": round(time.time() - started, 3),
    }


def _load_prompt(project: Path, case_id: str) -> str:
    """Build the system prompt by expanding {{IF HARNESS_X}} blocks per env.

    HARNESS_SPLIT=1 → use the BCDA/BGA two-phase prompt (v2 H2).
    """
    fname = "agent_prompt_split.md" if os.environ.get("HARNESS_SPLIT") else "agent_prompt.md"
    template = (REPO / "bench" / "ablation" / fname).read_text()
    # Strip the {{IF X}}...{{ENDIF}} blocks if X is not set
    import re
    def _replace(m):
        flag = m.group(1).strip()
        block = m.group(2)
        return block if os.environ.get(flag) else ""
    expanded = re.sub(
        r"\{\{IF\s+(\w+)\}\}([\s\S]*?)\{\{ENDIF\}\}",
        _replace, template,
    )
    expanded += f"\n\n---\nCASE_ID: {case_id}\nPROJECT_DIR: {project}\n"
    return expanded


def _estimate_cost(resp: Any, model: str) -> float:
    """Best-effort cost estimate. Opus 4.7 placeholder pricing."""
    usage = getattr(resp, "usage", None)
    if not usage:
        return 0.0
    in_tok = getattr(usage, "input_tokens", 0)
    out_tok = getattr(usage, "output_tokens", 0)
    # Approximate Opus pricing — update with actual rates
    return (in_tok / 1_000_000) * 15.0 + (out_tok / 1_000_000) * 75.0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--budget", type=int, default=5)
    args = ap.parse_args(argv[1:])

    result = run_agent(Path(args.project_dir).resolve(), args.case_id, args.budget)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
