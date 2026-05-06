#!/usr/bin/env python3
"""
bench/ablation/agent.py — Tool-using agent for one ablation cell run.

Implements the standard Anthropic tool-use loop:
  while resp.stop_reason == "tool_use":
      execute tool calls → append tool_result → call API again

Tool surface gates by HARNESS_* env flags so a single agent serves all 8 cells.
A0 (vanilla) exposes only read_file/grep/forge_test/cast_call.

Environment:
  HARNESS_AGENT_BACKEND  — codex (default), claude, or anthropic
  HARNESS_CODEX_MODEL    — optional model for `codex exec`
  HARNESS_USE_API       — if set, use Anthropic SDK (requires ANTHROPIC_API_KEY)
                          legacy alias for HARNESS_AGENT_BACKEND=anthropic
  ANTHROPIC_MODEL       — default 'claude-opus-4-7' (SDK path only)
  HARNESS_RECON, HARNESS_BANK, HARNESS_INV, HARNESS_VERIFY,
  HARNESS_TRACE2INV, HARNESS_SLITHER, HARNESS_HALMOS, HARNESS_ECHIDNA — toggle tools

Usage:
  python3 bench/ablation/agent.py <project_dir> --case-id <id> [--budget 5]

Output: stdout JSON with hypotheses generated, tool calls log, verified findings,
cost estimate. Same shape as bench/ablation/run.py CaseResult.

Default path uses `codex exec` subprocess so Claude tokens are not needed.
Set HARNESS_AGENT_BACKEND=claude to use the previous Claude Code subprocess.
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
            forge_cwd = project if (project / "foundry.toml").exists() else (REPO / "poc-forge")
            r = subprocess.run(
                ["forge", "test", "--match-test", inp["test_name"], f'-{"v" * max(1, int(inp.get("verbosity", 2)))}'],
                cwd=forge_cwd, capture_output=True, text=True, timeout=600,
            )
            return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-6000:]}
        if name == "cast_call":
            cmd = ["cast", "call", inp["target"], inp["signature"], "--rpc-url", inp["rpc_url"]]
            cmd.extend(inp.get("args") or [])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return {"ok": r.returncode == 0, "return": r.stdout.strip()[:1000], "err": r.stderr[-500:]}
        if name == "submit_finding":
            # Just persist + acknowledge — final accounting in main loop
            findings_dir = Path(os.environ.get("HARNESS_FINDINGS_DIR", str(HARNESS / "hypotheses"))).resolve()
            findings_dir.mkdir(parents=True, exist_ok=True)
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
# Claude CLI subprocess path (optional — uses Claude Code CLI auth)
# ----------------------------------------------------------------------
def _allowed_tools_for_cell() -> list[str]:
    """Map ablation env flags → Claude Code built-in tool surface.

    A0 vanilla = Read,Grep,Glob,Bash. Bash is gated by allowed-commands list
    in the system prompt; harness tools are exposed via Bash invocations of
    harness/{verify.py, slim_slither.py, recon_pack.py}.
    """
    base = ["Read", "Grep", "Glob", "Bash", "Write"]
    return base


def _allowed_tool_rules() -> list[str]:
    """Permission allowlist for Claude Code's built-in tools.

    Keep Bash narrow: the agent can inspect/read/write via native tools, and
    can execute only the deterministic harness/Foundry commands needed for
    PoC repair.
    """
    py = sys.executable
    return [
        "Read",
        "Grep",
        "Glob",
        "Write",
        "Edit",
        "Bash(forge *)",
        "Bash(cast call *)",
        f"Bash({py} {HARNESS / 'verify.py'} *)",
        f"Bash({py} {HARNESS / 'tools' / 'slither_tools.py'} *)",
        f"Bash({py} {HARNESS / 'tools' / 'source_fetcher.py'} *)",
        f"Bash({py} {HARNESS / 'tools' / 'scaffold_forge.py'} *)",
        f"Bash(python3 {HARNESS / 'verify.py'} *)",
        f"Bash(python3 {HARNESS / 'tools' / 'slither_tools.py'} *)",
        f"Bash(python3 {HARNESS / 'tools' / 'source_fetcher.py'} *)",
        f"Bash(python3 {HARNESS / 'tools' / 'scaffold_forge.py'} *)",
    ]


def _agent_extra_dirs(project: Path, findings_dir: Path) -> list[Path]:
    dirs = [
        project,
        HARNESS / "schemas",
        HARNESS / "templates",
        findings_dir,
    ]
    return [d for d in dirs if d.exists()]


def _prepare_runtime_harness(findings_dir: Path) -> Path:
    """Build a minimal harness view for child agents.

    This avoids mounting the full repository/harness directory, which contains
    old hypotheses and benchmark artifacts. The runtime copy is enough for
    schema/template reads and local verify.py repair loops.
    """
    runtime = findings_dir / "_runtime" / "harness"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    shutil.copy2(HARNESS / "verify.py", runtime / "verify.py")
    for dirname in ("schemas", "templates", "tools"):
        src = HARNESS / dirname
        dst = runtime / dirname
        if src.exists():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    kg_src = HARNESS / "kg"
    kg_dst = runtime / "kg"
    kg_dst.mkdir(exist_ok=True)
    for name in ("retrieve.py", "index.train.json"):
        src = kg_src / name
        if src.exists():
            shutil.copy2(src, kg_dst / name)
    return runtime


def _coverage_preamble(project: Path) -> str:
    """List in-scope .sol files with size, so the agent has a concrete
    'attack surface inventory' it must work through. Solves the
    "lost in 26 turns on 77-file codebase" problem from docs/09."""
    if os.environ.get("HARNESS_NO_COVERAGE") == "1":
        return ""
    files = []
    for p in project.rglob("*.sol"):
        s = str(p)
        if any(x in s for x in ("/lib/", "/out/", "/cache/", "/node_modules/",
                                 "/.recon/", "/test/", "/tests/",
                                 "/script/", "/scripts/", "_legacy_disabled/",
                                 "forge-std/", "@openzeppelin/", "solmate/",
                                 "@uniswap/")):
            continue
        if p.name.endswith((".t.sol", ".s.sol")):
            continue
        try:
            loc = sum(1 for _ in p.open(errors="ignore"))
        except Exception:
            continue
        files.append((str(p.relative_to(project)), loc))
    files.sort(key=lambda x: -x[1])
    if not files:
        return ""
    total_files = len(files)
    total_loc = sum(f[1] for f in files)
    head = files[:30]  # show top-30 by LOC; the rest get a tail mention
    rows = "\n".join(f"  {p}  ({loc} LOC)" for p, loc in head)
    rest_note = ""
    if total_files > 30:
        rest_note = f"\n  ... and {total_files - 30} smaller files (read after these)"
    return (
        f"## In-scope source inventory ({total_files} .sol files, "
        f"{total_loc:,} LOC total)\n"
        f"Plan coverage from this list. Larger files first; smaller ones often "
        f"contain helper logic with subtle invariants. Track which you've read.\n\n"
        f"{rows}{rest_note}\n\n"
    )


def _scone_scaffold(project: Path) -> str:
    """If <project> looks like a Sourcify-fetched src/ tree (no foundry.toml),
    run scaffold_forge to add minimal Foundry plumbing so verify.py works.
    Returns a preamble line documenting the action (or empty)."""
    if (project / "foundry.toml").exists():
        return ""
    if not (project / "src").is_dir() and not list(project.rglob("*.sol")):
        return ""
    try:
        sys.path.insert(0, str(REPO / "harness" / "tools"))
        from scaffold_forge import scaffold  # type: ignore
        result = scaffold(project)
    except Exception as e:
        return f"(scaffold_forge failed: {type(e).__name__}: {e})\n\n"
    if not result.get("ok"):
        return f"(scaffold_forge build failed; see {project}/foundry.toml — {result.get('build_stderr_tail','')[-300:]})\n\n"
    return (
        f"## Foundry scaffold (auto-applied for SCONE mode)\n"
        f"Project at `{project}` had no `foundry.toml` — minimal scaffold "
        f"applied (forge-std symlink, remappings, build verified). When you "
        f"submit a hypothesis JSON, set `\"forge_root\": \"{project}\"` so "
        f"verify.py runs the gates inside the scaffolded project.\n\n"
    )


def _recon_preamble(project: Path) -> str:
    if not os.environ.get("HARNESS_RECON"):
        return ""
    recon_dir = Path(os.environ.get("HARNESS_RECON_DIR") or project / "recon-pack").resolve()
    if not recon_dir.is_dir():
        return (
            "## Recon Pack\n"
            f"HARNESS_RECON=1, but no recon pack was found at `{recon_dir}`. "
            "Proceed from source inventory and attack-surface ranking.\n\n"
        )

    if os.environ.get("HARNESS_RECON_COMPACT") == "1":
        return _compact_recon_preamble(recon_dir)

    sections = []
    for name, limit in (
        ("meta.json", 2000),
        ("inscope.json", 5000),
        ("entry_points.json", 8000),
        ("storage.json", 6000),
        ("mcga_sinks.json", 8000),
        ("diff.patch", 6000),
    ):
        path = recon_dir / name
        if not path.exists():
            continue
        text = path.read_text(errors="ignore")
        if len(text) > limit:
            text = text[:limit] + "\n... <truncated; read the file for more>"
        lang = "diff" if name.endswith(".patch") else "json"
        sections.append(f"### {name}\n```{lang}\n{text}\n```")

    if not sections:
        return f"## Recon Pack\nRecon directory: `{recon_dir}` (no readable summary files found).\n\n"
    return (
        "## Recon Pack (precomputed)\n"
        f"Recon directory: `{recon_dir}`. Use this before ad hoc source search; "
        "read the files directly if a section is truncated.\n\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def _compact_recon_preamble(recon_dir: Path) -> str:
    sections = []

    def _load_json(name: str) -> Any:
        path = recon_dir / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(errors="ignore"))
        except Exception:
            return None

    inscope = _load_json("inscope.json")
    inscope_rows = []
    if isinstance(inscope, dict):
        inscope_rows = inscope.get("files") or []
    elif isinstance(inscope, list):
        inscope_rows = inscope
    if inscope_rows:
        files = []
        for row in inscope_rows[:40]:
            if isinstance(row, dict):
                files.append(f"  - {row.get('path') or row.get('file')} ({row.get('loc', '?')} LOC)")
            else:
                files.append(f"  - {row}")
        sections.append("### In-scope files\n" + "\n".join(files))

    entry = _load_json("entry_points.json")
    if isinstance(entry, dict):
        rows = []
        for contract, funcs in list(entry.items())[:35]:
            if not isinstance(funcs, list):
                continue
            for fn in funcs[:12]:
                if isinstance(fn, dict):
                    name = fn.get("name") or fn.get("signature")
                    mut = fn.get("stateMutability") or fn.get("visibility") or "?"
                    rows.append(f"  - {contract}.{name} ({mut})")
                else:
                    rows.append(f"  - {contract}.{fn}")
                if len(rows) >= 60:
                    break
            if len(rows) >= 60:
                break
        if rows:
            sections.append("### Entry points\n" + "\n".join(rows))
    elif isinstance(entry, list):
        rows = []
        for item in entry[:35]:
            if not isinstance(item, dict):
                continue
            contract = item.get("contract", "")
            for fn in (item.get("functions") or [])[:12]:
                if isinstance(fn, dict):
                    rows.append(f"  - {contract}.{fn.get('name') or fn.get('signature')} ({fn.get('visibility', '?')})")
                else:
                    rows.append(f"  - {contract}.{fn}")
                if len(rows) >= 60:
                    break
            if len(rows) >= 60:
                break
        if rows:
            sections.append("### Entry points\n" + "\n".join(rows))

    mcga = _load_json("mcga_sinks.json")
    if isinstance(mcga, dict):
        rows = []
        for key in ("top_external_functions", "top_internal_callees"):
            for item in (mcga.get(key) or [])[:12]:
                if not isinstance(item, dict):
                    continue
                sinks = ", ".join(sorted(item.get("sinks") or {}))
                rows.append(
                    f"  - {item.get('contract')}.{item.get('function')} "
                    f"@ {item.get('file')}:{item.get('line')} sinks={sinks}"
                )
        if rows:
            sections.append("### Sink-ranked functions\n" + "\n".join(rows[:35]))

    if not sections:
        return (
            "## Compact Recon Pack\n"
            f"Recon directory: `{recon_dir}`. Compact summary unavailable; inspect files directly if needed.\n\n"
        )
    return (
        "## Compact Recon Pack\n"
        f"Recon directory: `{recon_dir}`. This is a compact orientation only; "
        "do not let it replace source reading.\n\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def _mcga_preamble(project: Path) -> str:
    """When HARNESS_MCGA=1, run sink tagger and inject top external + top
    internal high-density functions as a focused attack-surface block."""
    if not os.environ.get("HARNESS_MCGA"):
        return ""
    try:
        sys.path.insert(0, str(REPO / "harness"))
        from mcga_sinks import build  # type: ignore
        result = build(project)
    except Exception as e:
        return f"(MCGA failed: {type(e).__name__}: {e})\n\n"

    ext = result.get("top_external_functions", [])[:10]
    intl = result.get("top_internal_callees", [])[:10]
    if not ext and not intl:
        return ""

    def _fmt(rows: list[dict]) -> str:
        out = []
        for r in rows:
            sks = ", ".join(sorted(r.get("sinks") or {}))
            out.append(
                f"  {r['contract']}.{r['function']}  "
                f"[{r['visibility']}, {r['file']}:{r['line']}, "
                f"sinks={r['sink_count']}: {sks}]"
            )
        return "\n".join(out)

    return (
        "## MCGA sink-tagged attack surface (hard-injected, top-10 each)\n"
        "External / public functions are direct attack entry points. "
        "Internal callees with high sink density are the **bug-bearing primitives** "
        "that external callers reach via call graph — trace them back to their "
        "external entry points.\n\n"
        f"### Top external/public functions by sink density\n{_fmt(ext)}\n\n"
        f"### Top internal callees by sink density (call-graph hot spots)\n{_fmt(intl)}\n\n"
        "Sink categories: external_call, delegatecall, balance_write, share_write, "
        "supply_write, oracle_read, lp_sync, flash_loan, unchecked_arith, "
        "transfer_token, fee_on_transfer, approve_inf, tx_origin, block_dep, "
        "selfdestruct, low_level_send.\n\n"
    )


def _attack_surface_preamble(project: Path) -> str:
    """Inject a deterministic CPUA-style ranked coverage/tracing plan.

    This is the coverage tracker upgraded from file-level orientation to
    function-level attack-surface prioritization. It is enabled by default for
    the harness path because docs/10 showed orientation is the load-bearing
    lift. Set HARNESS_NO_ATTACK_SURFACE=1 only for purity ablations.
    """
    if os.environ.get("HARNESS_NO_ATTACK_SURFACE") == "1":
        return ""
    try:
        sys.path.insert(0, str(REPO / "harness"))
        from attack_surface import build  # type: ignore
        result = build(project)
    except Exception as e:
        return f"(attack-surface ranking failed: {type(e).__name__}: {e})\n\n"

    top_files = result.get("ranked_files", [])[:12]
    top_functions = result.get("ranked_functions", [])[:18]
    if not top_files and not top_functions:
        return ""

    file_rows = "\n".join(
        f"  {i+1}. {row['file']} "
        f"(score={row['score']}, sinks={row['sinks_total']}, "
        f"entries={row['external_or_public_functions']})"
        for i, row in enumerate(top_files)
    )
    fn_rows = []
    for i, fn in enumerate(top_functions):
        sinks = ",".join(sorted((fn.get("sinks") or {}).keys())) or "entry"
        reasons = ",".join(fn.get("reasons") or [])
        fn_rows.append(
            f"  {i+1}. {fn['contract']}.{fn['function']} "
            f"@ {fn['file']}:{fn['line']} "
            f"(score={fn['score']}, sinks={sinks}, reasons={reasons})"
        )

    return (
        "## CPUA ranked attack-surface plan (hard-injected)\n"
        "Use this as the reading and tracing order. For each top function, "
        "trace caller/preconditions, value flow, state writes, and the matching "
        "class invariant before moving on. This ranking is not a finding list; "
        "it is a coverage plan.\n\n"
        "### Ranked files\n"
        f"{file_rows}\n\n"
        "### Ranked functions\n"
        f"{chr(10).join(fn_rows)}\n\n"
    )


def _output_contract(case_id: str, findings_dir: Path, harness_dir: Path) -> str:
    return (
        "## Output Contract — Mandatory\n"
        "A finding only counts if all of these names line up exactly:\n"
        f"- Hypothesis JSON path: `{findings_dir}/{case_id}-N.json`\n"
        f"- Hypothesis `id`: `{case_id}-N`\n"
        f"- Solidity-safe id: replace non `[A-Za-z0-9_]` chars with `_`, so `{case_id}-1` becomes `{case_id}_1`\n"
        f"- PoC file path: `test/AttackHarness_<solidity_safe_id>.t.sol`\n"
        f"- PoC contract: `AttackHarness_<solidity_safe_id>`\n"
        f"- PoC test function: `testPoC_<solidity_safe_id>()`\n"
        "- Do not bundle multiple findings in one Solidity file.\n"
        "- Do not write only a `.t.sol` file. Write the hypothesis JSON before verification.\n"
        "- Set `poc_path` in the JSON to the PoC file path.\n"
        "- Set `forge_root` in the JSON to the target Foundry root.\n"
        f"- Verify with `{sys.executable} {harness_dir / 'verify.py'} {findings_dir}/{case_id}-N.json`.\n\n"
    )


def _score_only_output_contract(case_id: str, findings_dir: Path) -> str:
    return (
        "## Output Contract — Mandatory\n"
        "This baseline cell is score-only: write candidate artifacts, but do not "
        "use any repository harness verifier, recon pack, invariant template, "
        "incident corpus, or prior benchmark output even if you discover one. "
        "Use source reading plus normal Foundry build/test commands only.\n\n"
        "A candidate only enters scoring if these names line up exactly:\n"
        f"- Hypothesis JSON path: `{findings_dir}/{case_id}-N.json`\n"
        f"- Hypothesis `id`: `{case_id}-N`\n"
        f"- Solidity-safe id: replace non `[A-Za-z0-9_]` chars with `_`, so `{case_id}-1` becomes `{case_id}_1`\n"
        f"- PoC file path: `test/AttackHarness_<solidity_safe_id>.t.sol`\n"
        f"- PoC contract: `AttackHarness_<solidity_safe_id>`\n"
        f"- PoC test function: `testPoC_<solidity_safe_id>()`\n"
        "- Do not bundle multiple findings in one Solidity file.\n"
        "- Do not write only a `.t.sol` file. Write the hypothesis JSON too.\n"
        "- Set `poc_path` in the JSON to the PoC file path.\n"
        "- Set `forge_root` in the JSON to the target Foundry root.\n\n"
        "JSON field constraints that are easy to get wrong:\n"
        "- `initial_state.actors` must be `[]` or objects like "
        "`{\"name\":\"attacker\",\"role\":\"attacker\"}`; never use bare strings.\n"
        "- actor `role` must be one of `attacker`, `victim`, `admin`, `user`, `oracle`, `whale`.\n"
        "- `target.function` should include the Solidity signature when known, e.g. `_transfer(address,address,uint256)`.\n"
        "- `post_vuln_state.class_invariant` must be one of the values shown in the example.\n\n"
        "Use this compact JSON shape:\n"
        "```json\n"
        "{\n"
        f"  \"id\": \"{case_id}-1\",\n"
        "  \"mode\": \"research\",\n"
        "  \"forge_root\": \"<absolute project path>\",\n"
        "  \"poc_path\": \"test/AttackHarness_<id>.t.sol\",\n"
        "  \"vuln_class\": \"reentrancy|access_control|arithmetic|oracle_manipulation|flashloan_pricing|donation_share_inflation|callback_state_corruption|signature_replay|dos_griefing|init_unprotected|upgradeability_storage|rounding_dust_drain|invariant_break_other\",\n"
        "  \"target\": {\"contract\": \"...\", \"function\": \"...\", \"file\": \"src/Target.sol\", \"lines\": \"L1-L2\"},\n"
        "  \"initial_state\": {\"fork\": null, \"actors\": [{\"name\": \"attacker\", \"role\": \"attacker\"}], \"deployments\": []},\n"
        "  \"pre_vuln_state\": {\"assertions\": [], \"setup_calls\": []},\n"
        "  \"post_vuln_state\": {\"class_invariant\": \"invariant_violated_custom\", \"custom_assertion\": \"...\", \"profit_min_raw\": \"0\", \"profit_asset\": \"NATIVE\"},\n"
        "  \"invariant\": {\"halmos_check\": false},\n"
        "  \"attack_steps\": [\"...\"],\n"
        "  \"preconditions\": [\"...\"],\n"
        "  \"rationale\": \"...\"\n"
        "}\n"
        "```\n\n"
    )


def _budget_contract(budget_iterations: int, max_usd: float) -> str:
    target_verified = max(1, min(3, budget_iterations))
    if budget_iterations <= 1:
        return (
            "## Budget — Smoke Run\n"
            f"You have {budget_iterations} verification iteration and about "
            f"${max_usd:.0f} USD of model spend available. This is an intentionally "
            "small benchmark cell: prioritize one high-confidence vulnerability, "
            "write exactly one hypothesis JSON first, write one matching PoC test, "
            "run verify.py, repair once if needed, then stop. Do not attempt a "
            "second candidate. If no high-confidence candidate emerges after "
            "reading the top-ranked file plus at most two supporting files, write "
            "no finding and stop. Do not broaden into full-codebase coverage after "
            "one candidate is attempted.\n\n"
        )
    if os.environ.get("HARNESS_DEEP") == "1":
        min_attempts = max(2, min(5, budget_iterations))
        return (
            "## Budget — Deep Harness Run\n"
            f"You have {budget_iterations} verification iterations and about "
            f"${max_usd:.0f} USD of model spend available.\n"
            f"- Target at least {target_verified} verified findings.\n"
            f"- Before stopping with fewer than {target_verified} verified findings, "
            f"you must write and run verify.py on at least {min_attempts} distinct "
            "candidate JSONs from different root causes or different functions.\n"
            "- Do not require bounty-grade certainty before attempting a candidate. "
            "The verifier is the filter: source-backed, plausible, economically "
            "testable candidates should be tried.\n"
            "- If one candidate fails, read the failing gate, repair once or twice, "
            "then move to the next queued candidate. Avoid spending the entire run "
            "on one hard PoC.\n"
            "- Stop only when target verified findings are reached, candidate "
            "attempt minimum is satisfied with no remaining plausible queue items, "
            "or the wall budget is genuinely exhausted.\n\n"
        )
    return (
        "## Budget — USE IT\n"
        f"You have {budget_iterations} verification iterations and about "
        f"${max_usd:.0f} USD of model spend available.\n"
        f"- Do not stop until: (a) you have >= {target_verified} verified "
        "findings, or (b) you've read >=80% of files in the inventory above "
        "and submitted at least one candidate per major attack-surface category, "
        "or (c) the wall budget is genuinely exhausted.\n"
        "- If a hypothesis fails verify.py, read the failure JSON, fix the named "
        "gate, and retry. Max 5 retries per hypothesis; then move on.\n\n"
    )


def _score_only_budget_contract(budget_iterations: int, max_usd: float) -> str:
    target_candidates = max(1, min(3, budget_iterations))
    return (
        "## Budget — Score-Only Baseline\n"
        f"You have {budget_iterations} candidate attempts and about "
        f"${max_usd:.0f} USD of model spend available. Try to produce up to "
        f"{target_candidates} source-backed vulnerabilities with passing Foundry "
        "PoC tests. Do not run any hidden benchmark verifier or repair loop. "
        "Use `forge build` and `forge test` only to check your own PoC. Stop when "
        "you have written the candidate JSON/PoC pairs or no source-backed "
        "candidate remains under the budget.\n\n"
    )


def _raw_codex_system(score_only: bool = False) -> str:
    if score_only:
        return (
            "You are Codex acting as a smart-contract security researcher in a "
            "strict score-only baseline cell. Inspect only the target project "
            "source and tests. Do not use injected recon summaries, ranked "
            "attack-surface plans, harness verifier scripts, invariant helper "
            "templates, prior benchmark outputs, old hypotheses, or incident "
            "corpora. Produce ordinary Foundry PoC tests plus the requested JSON "
            "candidate files; an external scorer will evaluate them after you stop."
        )
    return (
        "You are Codex acting as a smart-contract security researcher. "
        "This is the raw baseline cell: do not use injected recon summaries, "
        "ranked attack-surface plans, prior benchmark outputs, old hypotheses, "
        "or incident corpora. Inspect only the target project source and tests. "
        "A finding only counts when backed by a Foundry PoC and the required "
        "hypothesis JSON."
    )


def _deep_harness_preamble(project: Path, budget_iterations: int) -> str:
    if os.environ.get("HARNESS_DEEP") != "1":
        return ""
    target_verified = max(1, min(3, budget_iterations))
    candidate_floor = max(2, min(5, budget_iterations))
    try:
        sys.path.insert(0, str(REPO / "harness"))
        from attack_surface import build  # type: ignore
        result = build(project)
    except Exception:
        result = {}

    fn_rows = []
    for fn in (result.get("ranked_functions") or [])[:18]:
        sinks = ",".join(sorted((fn.get("sinks") or {}).keys())) or "entry"
        fn_rows.append(
            f"  - {fn.get('contract')}.{fn.get('function')} "
            f"@ {fn.get('file')}:{fn.get('line')} "
            f"(score={fn.get('score')}, sinks={sinks})"
        )

    patterns = [
        ("authorization", ("init", "initialize", "set", "owner", "admin", "manager", "permission", "role")),
        ("reservation/accounting", ("validate", "postOp", "nonce", "withdrawable", "reserved", "balance", "totalAssets")),
        ("cap/position transfer", ("cap", "limit", "max", "position", "tokenId", "transferFrom", "ownerOf")),
        ("rounding/precision", ("mulDiv", "sqrt", "ln", "div", "round", "precision", "share", "rate")),
        ("external callback/CEI", ("call", "transfer", "safeTransfer", "onERC", "hook", "callback", "delegatecall")),
        ("oracle/NAV/staleness", ("oracle", "price", "NAV", "TWAP", "slot0", "sqrtPrice", "rate")),
    ]
    hits: dict[str, list[str]] = {name: [] for name, _ in patterns}
    for p in project.rglob("*.sol"):
        s = str(p)
        if any(x in s for x in ("/lib/", "/out/", "/cache/", "/node_modules/", "/test/", "/script/")):
            continue
        if p.name.endswith((".t.sol", ".s.sol")):
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        rel = str(p.relative_to(project))
        for idx, line in enumerate(lines, 1):
            low = line.lower()
            if not any(marker in low for marker in ("function ", "modifier ", "unchecked", "safeTransfer".lower(), "transfer(", "call(")):
                continue
            for name, words in patterns:
                if len(hits[name]) >= 8:
                    continue
                if any(w.lower() in low for w in words):
                    hits[name].append(f"  - {rel}:{idx}: {line.strip()[:150]}")

    hit_sections = []
    for name, rows in hits.items():
        if rows:
            hit_sections.append(f"### {name}\n" + "\n".join(rows))

    return (
        "## Deep Harness Strategy\n"
        "This run is optimized for recall, not for a single polished report. "
        f"Build a queue of at least {candidate_floor} source-backed candidates "
        f"and try to verify at least {target_verified}. A lower-confidence but "
        "mechanically testable candidate is better than stopping with no JSON.\n\n"
        "Use this candidate order:\n"
        "1. Authorization or initialization gaps on high-impact entry points.\n"
        "2. Accounting/reservation bugs where validation and settlement are split.\n"
        "3. Cap, quota, or per-user limit bypasses via transferable positions.\n"
        "4. Rounding/precision/domain bugs in math code that can become DoS or value drift.\n"
        "5. External callback, token hook, CEI, or untrusted call bugs.\n"
        "6. Oracle/NAV/stale-price bugs with direct value extraction.\n\n"
        "ERC-4337/paymaster rules: `validationData == 1` means invalid "
        "signature and is not an exploitable success path by itself. Do not "
        "report a candidate that profits only because a mocked caller ignores "
        "`validationData == 1`, and do not replace EntryPoint code with `vm.etch`. "
        "Valid paymaster candidates should use valid signatures and real ordering "
        "issues such as aggregate reservation across multiple validations before "
        "postOp settlement.\n\n"
        "For each queued candidate, write the JSON first, then a minimal Foundry "
        "PoC. Use test-local mocks when the production integration is heavy, but "
        "the failing behavior must come from the target source code. If a PoC "
        "fails because the candidate is wrong, keep the JSON as a failed attempt "
        "only if it is parseable, then move on to the next candidate id.\n\n"
        "### Ranked candidate functions\n"
        + ("\n".join(fn_rows) if fn_rows else "  - no ranked functions available")
        + "\n\n"
        + ("\n\n".join(hit_sections) if hit_sections else "### Pattern hits\n  - no pattern hits collected")
        + "\n\n"
    )


def _kg_preamble(project: Path) -> str:
    """When HARNESS_KG=1, run KG retrieval and return a preamble block to
    prepend to the user message. Hard-wired so the agent cannot skip it."""
    if not os.environ.get("HARNESS_KG"):
        return ""
    try:
        # Extract interface symbols from project sources (grep-based, fast)
        r = subprocess.run(
            ["grep", "-rho", "--include=*.sol", "-E", r"\bI[A-Z][A-Za-z0-9_]+\b", str(project)],
            capture_output=True, text=True, timeout=30,
        )
        ifaces = sorted({s.strip() for s in r.stdout.splitlines() if len(s.strip()) > 2})[:60]
        if not ifaces:
            return ""
        sys.path.insert(0, str(REPO / "harness" / "kg"))
        from retrieve import retrieve  # type: ignore
        hits = retrieve({"interfaces": ifaces}, top_k=5)
        if not hits:
            return ""
        return (
            "## DeFiHackLabs KG retrieval (hard-injected, top-5 by interface Jaccard)\n"
            "These are past incidents whose external interface set overlaps with "
            "the current target. Treat each as a *candidate hypothesis seed* — "
            "verify or rule out the corresponding pattern against this code. "
            "Cite the incident `id` in your hypothesis `rationale`.\n\n"
            f"```json\n{json.dumps(hits, indent=2)}\n```\n\n"
            f"Target's extracted interface set ({len(ifaces)}): {', '.join(ifaces[:30])}\n\n"
        )
    except Exception as e:
        return f"(KG retrieval failed: {type(e).__name__}: {e})\n\n"


def _post_verify_candidates(candidates: list[tuple[Path, dict]]) -> tuple[list[dict], list[dict]]:
    """Run verify.py after the agent exits and split candidates from verified.

    The agent is instructed to verify before writing findings, but experiments
    need an independent accounting path: `hypotheses` means candidate JSONs
    emitted, while `verified` means the deterministic gate returned exit 0.
    """
    if os.environ.get("HARNESS_SKIP_POST_VERIFY") == "1":
        return [], [
            {
                "hypothesis_id": hyp.get("id"),
                "hypothesis_path": str(path),
                "skipped": True,
                "reason": "HARNESS_SKIP_POST_VERIFY=1",
            }
            for path, hyp in candidates
        ]

    verified: list[dict] = []
    verification_results: list[dict] = []
    for hyp_path, hyp in candidates:
        hyp_id = hyp.get("id")
        started = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(HARNESS / "verify.py"), str(hyp_path)],
                capture_output=True, text=True,
                timeout=int(os.environ.get("HARNESS_VERIFY_TIMEOUT_SEC", "900")),
            )
            try:
                parsed = json.loads(r.stdout)
            except Exception:
                parsed = {
                    "hypothesis_id": hyp_id,
                    "exit_code": r.returncode,
                    "error": "verify.py output not JSON",
                    "stdout_tail": r.stdout[-2000:],
                    "stderr_tail": r.stderr[-1000:],
                }
            parsed.setdefault("hypothesis_id", hyp_id)
            parsed["hypothesis_path"] = str(hyp_path)
            parsed["elapsed_sec"] = round(time.time() - started, 3)
            verification_results.append(parsed)
            if parsed.get("exit_code") == 0:
                verified.append(hyp)
        except subprocess.TimeoutExpired:
            verification_results.append({
                "hypothesis_id": hyp_id,
                "hypothesis_path": str(hyp_path),
                "exit_code": 98,
                "error": "verify.py timed out",
                "elapsed_sec": round(time.time() - started, 3),
            })
    return verified, verification_results


def run_agent_cli(project: Path, case_id: str, budget_iterations: int = 5) -> dict:
    started = time.time()
    default_findings_dir = HARNESS / "hypotheses" / "_runs" / case_id
    findings_dir = Path(os.environ.get("HARNESS_FINDINGS_DIR", str(default_findings_dir))).resolve()
    findings_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name: p.stat().st_mtime_ns for p in findings_dir.glob(f"{case_id}-*.json")}

    system = _load_prompt(project, case_id)
    scone_preamble = _scone_scaffold(project)
    recon_preamble = _recon_preamble(project)
    coverage_preamble = _coverage_preamble(project)
    attack_surface_preamble = _attack_surface_preamble(project)
    mcga_preamble = _mcga_preamble(project)
    kg_preamble = _kg_preamble(project)
    max_usd = float(os.environ.get("HARNESS_MAX_BUDGET_USD", "5"))
    user_msg = (
        f"{scone_preamble}"
        f"{recon_preamble}"
        f"{coverage_preamble}"
        f"{attack_surface_preamble}"
        f"{mcga_preamble}"
        f"{kg_preamble}"
        f"## Task\n"
        f"Find vulnerabilities in the Solidity project at {project}.\n"
        f"For each {'candidate' if score_only else 'verified'} finding, write the hypothesis JSON to "
        f"{findings_dir}/{case_id}-N.json (N = 1, 2, ...) following "
        f"{HARNESS / 'schemas' / 'hypothesis.schema.json'}.\n"
        f"Set `poc_path` to the PoC test file you wrote and set `forge_root` "
        f"to the Foundry root when it is not the default poc-forge workspace. "
        f"Run verification with `{sys.executable} {HARNESS / 'verify.py'} <hypothesis.json>`.\n\n"
        f"{_output_contract(case_id, findings_dir, HARNESS)}"
        f"## Isolation\n"
        f"Do not inspect prior benchmark outputs, repo docs, old hypotheses, or "
        f"the DeFiHackLabs corpus. Use only the target project, the hard-injected "
        f"blocks above, and the harness schema/templates explicitly provided.\n\n"
        f"{_budget_contract(budget_iterations, max_usd)}"
    )

    cmd = [
        "claude", "-p", user_msg,
        "--append-system-prompt", system,
        "--output-format", "json",
        "--permission-mode", os.environ.get("HARNESS_PERMISSION_MODE", "dontAsk"),
        "--tools", ",".join(_allowed_tools_for_cell()),
        "--allowedTools", ",".join(_allowed_tool_rules()),
        "--max-budget-usd", os.environ.get("HARNESS_MAX_BUDGET_USD", "5"),
        "--no-session-persistence",
    ]
    for d in _agent_extra_dirs(project, findings_dir):
        cmd.extend(["--add-dir", str(d)])
    if os.environ.get("HARNESS_MODEL"):
        cmd.extend(["--model", os.environ["HARNESS_MODEL"]])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(os.environ.get("HARNESS_TIMEOUT_SEC", "3600")),
                           cwd=project)
    except subprocess.TimeoutExpired:
        return {"case_id": case_id, "error": "claude CLI timed out",
                "elapsed_sec": round(time.time() - started, 3)}

    parsed: Any
    try:
        parsed = json.loads(r.stdout)
    except Exception:
        parsed = {"_raw_stdout_tail": r.stdout[-2000:], "_stderr_tail": r.stderr[-1000:]}

    after = {p.name: p.stat().st_mtime_ns for p in findings_dir.glob(f"{case_id}-*.json")}
    candidate_records: list[tuple[Path, dict]] = []
    changed = [name for name, mtime in after.items() if name not in before or before[name] != mtime]
    for fname in sorted(changed):
        hyp_path = findings_dir / fname
        try:
            candidate_records.append((hyp_path, json.loads(hyp_path.read_text())))
        except Exception as e:
            candidate_records.append((hyp_path, {"_parse_error": str(e), "_file": fname}))

    new_findings = [hyp for _, hyp in candidate_records]
    verifiable_records = [(path, hyp) for path, hyp in candidate_records if "id" in hyp]
    verified_findings, verification_results = _post_verify_candidates(verifiable_records)

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
        "verified": verified_findings,
        "verification_results": verification_results,
        "cost_usd": cost,
        "elapsed_sec": round(time.time() - started, 3),
        "exit_code": r.returncode,
    }


def run_agent_codex_cli(project: Path, case_id: str, budget_iterations: int = 5) -> dict:
    started = time.time()
    # Keep Codex writes inside its primary workspace so workspace-write
    # sandboxing can persist PoCs, hypotheses, and the runtime harness copy.
    cell_id = os.environ.get("HARNESS_CELL_ID")
    default_findings_dir = (
        project / ".harness_runs" / cell_id / case_id
        if cell_id
        else project / ".harness_runs" / case_id
    )
    findings_dir = Path(os.environ.get("HARNESS_FINDINGS_DIR", str(default_findings_dir))).resolve()
    findings_dir.mkdir(parents=True, exist_ok=True)
    score_only = os.environ.get("HARNESS_SCORE_ONLY") == "1"
    if score_only:
        shutil.rmtree(findings_dir / "_runtime", ignore_errors=True)
        runtime_harness: Path | None = None
    else:
        runtime_harness = _prepare_runtime_harness(findings_dir)
    before = {p.name: p.stat().st_mtime_ns for p in findings_dir.glob(f"{case_id}-*.json")}

    raw_baseline = os.environ.get("HARNESS_RAW_CODEX") == "1"
    if raw_baseline:
        system = _raw_codex_system(score_only=score_only)
        scone_preamble = ""
        recon_preamble = ""
        coverage_preamble = ""
        attack_surface_preamble = ""
        mcga_preamble = ""
        deep_preamble = ""
        kg_preamble = ""
    else:
        system = _load_prompt(project, case_id)
        # Codex gets a per-run harness copy. Keep prompt references pointed there.
        if runtime_harness is not None:
            system = system.replace(str(HARNESS), str(runtime_harness))
        scone_preamble = _scone_scaffold(project)
        recon_preamble = _recon_preamble(project)
        coverage_preamble = _coverage_preamble(project)
        attack_surface_preamble = _attack_surface_preamble(project)
        mcga_preamble = _mcga_preamble(project)
        deep_preamble = _deep_harness_preamble(project, budget_iterations)
        kg_preamble = _kg_preamble(project)
    max_usd = float(os.environ.get("HARNESS_MAX_BUDGET_USD", "5"))
    output_contract = (
        _score_only_output_contract(case_id, findings_dir)
        if score_only
        else _output_contract(case_id, findings_dir, runtime_harness if runtime_harness is not None else HARNESS)
    )
    budget_contract = (
        _score_only_budget_contract(budget_iterations, max_usd)
        if score_only
        else _budget_contract(budget_iterations, max_usd)
    )
    isolation_scope = (
        "the target project source and tests"
        if score_only
        else "the target project and this runtime harness copy"
        if raw_baseline
        else "the target project, the injected blocks above, and this runtime harness copy"
    )
    prompt = (
        f"{system}\n\n"
        f"{scone_preamble}"
        f"{recon_preamble}"
        f"{coverage_preamble}"
        f"{attack_surface_preamble}"
        f"{mcga_preamble}"
        f"{deep_preamble}"
        f"{kg_preamble}"
        f"## Task\n"
        f"Find vulnerabilities in the Solidity project at {project}.\n"
        f"For each {'candidate' if score_only else 'verified'} finding, write the hypothesis JSON to "
        f"{findings_dir}/{case_id}-N.json (N = 1, 2, ...) following "
        f"the output contract below.\n"
        f"Set `forge_root` to `{project}` in every hypothesis JSON. Set "
        f"`poc_path` to the PoC test file you wrote."
        f"{' Run the verifier while repairing.' if not score_only else ' Do not run any hidden benchmark verifier.'}\n\n"
        f"{output_contract}"
        f"## Isolation\n"
        f"Do not inspect prior benchmark outputs, repo docs, old hypotheses, or "
        f"the DeFiHackLabs corpus. Use only {isolation_scope}.\n"
        f"Do not modify target source contracts except for adding Foundry test "
        f"files and minimal test-only scaffolding needed for PoCs.\n\n"
        f"{budget_contract}"
        f"## Final Response\n"
        f"Keep the final message short. The benchmark reads the JSON files from "
        f"{findings_dir}; the final prose is not used for scoring.\n"
    )
    if os.environ.get("HARNESS_DUMP_PROMPT") == "1" or os.environ.get("HARNESS_DRY_RUN_PROMPT") == "1":
        (findings_dir / "_prompt.md").write_text(prompt)
    if os.environ.get("HARNESS_DRY_RUN_PROMPT") == "1":
        return {
            "case_id": case_id,
            "mode": "codex-cli",
            "model": os.environ.get("HARNESS_CODEX_MODEL") or os.environ.get("HARNESS_MODEL") or "codex-default",
            "stub": False,
            "dry_run_prompt": True,
            "prompt_path": str(findings_dir / "_prompt.md"),
            "score_only": score_only,
            "elapsed_sec": round(time.time() - started, 3),
        }

    last_msg = findings_dir / "_codex_last_message.txt"
    cmd = [
        "codex", "exec",
        "--cd", str(project),
        "--sandbox", os.environ.get("HARNESS_CODEX_SANDBOX", "workspace-write"),
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "--output-last-message", str(last_msg),
        "--add-dir", str(findings_dir),
    ]
    model = os.environ.get("HARNESS_CODEX_MODEL") or os.environ.get("HARNESS_MODEL")
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")

    child_env = os.environ.copy()
    child_env["HARNESS_FINDINGS_DIR"] = str(findings_dir)
    if runtime_harness is not None:
        child_env["HARNESS_RUNTIME_DIR"] = str(runtime_harness)
    else:
        child_env.pop("HARNESS_RUNTIME_DIR", None)
    try:
        r = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("HARNESS_TIMEOUT_SEC", "3600")),
            cwd=project,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        return {
            "case_id": case_id,
            "mode": "codex-cli",
            "error": "codex CLI timed out",
            "elapsed_sec": round(time.time() - started, 3),
        }

    after = {p.name: p.stat().st_mtime_ns for p in findings_dir.glob(f"{case_id}-*.json")}
    candidate_records: list[tuple[Path, dict]] = []
    changed = [name for name, mtime in after.items() if name not in before or before[name] != mtime]
    for fname in sorted(changed):
        hyp_path = findings_dir / fname
        try:
            candidate_records.append((hyp_path, json.loads(hyp_path.read_text())))
        except Exception as e:
            candidate_records.append((hyp_path, {"_parse_error": str(e), "_file": fname}))

    new_findings = [hyp for _, hyp in candidate_records]
    verifiable_records = [(path, hyp) for path, hyp in candidate_records if "id" in hyp]
    verified_findings, verification_results = _post_verify_candidates(verifiable_records)
    events = _parse_jsonl_events(r.stdout)
    last_text = last_msg.read_text(errors="ignore") if last_msg.exists() else ""

    return {
        "case_id": case_id,
        "mode": "codex-cli",
        "model": model or "codex-default",
        "stub": False,
        "tool_calls": _count_codex_tool_events(events),
        "turns": _count_codex_turns(events),
        "hypotheses": new_findings,
        "verified": verified_findings,
        "verification_results": verification_results,
        "cost_usd": None,
        "elapsed_sec": round(time.time() - started, 3),
        "exit_code": r.returncode,
        "codex_last_message": last_text[-4000:],
        "codex_stderr_tail": r.stderr[-2000:],
    }


def _parse_jsonl_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _count_codex_tool_events(events: list[dict]) -> int:
    count = 0
    for event in events:
        blob = json.dumps(event, sort_keys=True)
        if "exec_command" in blob or "tool_call" in blob or "command" in blob:
            count += 1
    return count


def _count_codex_turns(events: list[dict]) -> int | None:
    turns = 0
    for event in events:
        blob = json.dumps(event, sort_keys=True)
        if "turn" in blob.lower() and ("completed" in blob.lower() or "started" in blob.lower()):
            turns += 1
    return turns or None


# ----------------------------------------------------------------------
# Agent loop (SDK path — opt-in via HARNESS_USE_API)
# ----------------------------------------------------------------------
def run_agent(project: Path, case_id: str, budget_iterations: int = 5) -> dict:
    backend = os.environ.get("HARNESS_AGENT_BACKEND", "codex").strip().lower()
    if os.environ.get("HARNESS_USE_API"):
        backend = "anthropic"
    if backend in ("codex", "codex-cli"):
        return run_agent_codex_cli(project, case_id, budget_iterations)
    if backend in ("claude", "claude-cli"):
        return run_agent_cli(project, case_id, budget_iterations)
    if backend not in ("anthropic", "api", "sdk"):
        return {"error": f"unknown HARNESS_AGENT_BACKEND={backend!r}; expected codex, claude, or anthropic"}

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

    # SDK path uses the submit_finding tool instead of watching the hypotheses
    # directory, so only post-verify records that were successfully submitted.
    candidate_records = []
    sdk_findings_dir = Path(os.environ.get("HARNESS_FINDINGS_DIR", str(HARNESS / "hypotheses"))).resolve()
    for hyp in findings:
        hyp_path = sdk_findings_dir / f"{hyp.get('id')}.json"
        if hyp.get("id") and hyp_path.exists():
            candidate_records.append((hyp_path, hyp))
    verified_findings, verification_results = _post_verify_candidates(candidate_records)

    return {
        "case_id": case_id, "model": model, "stub": False,
        "tool_calls": tool_call_count, "turns": turn + 1,
        "hypotheses": findings, "verified": verified_findings,
        "verification_results": verification_results,
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
