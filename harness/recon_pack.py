#!/usr/bin/env python3
"""
harness/recon_pack.py — Pre-LLM static analysis pack.

Goal: extract everything the LLM should NOT have to grep for, in deterministic
JSON files written to disk. The LLM's first step in every hunt is `cat`-ing
this directory rather than reading source files blindly.

Outputs (all under <pack_dir>/):
  inscope.json       — files in scope, LOC, SLOC, complexity (LOC printer)
  callgraph.json     — function → callees + callers (parsed from .dot)
  storage.json       — per-contract storage layout (forge inspect)
  perms.json         — modifier matrix + auth state-vars (vars-and-auth)
  entry_points.json  — externally callable state-changing fns (entry-points)
  attack_surface.json — ranked files/functions for coverage and tracing (CPUA)
  function_summary.json — per-function inputs/outputs/modifiers/state-vars
  slither.json       — standard slither findings (raw)
  diff.patch         — git diff vs --base-ref (post-audit changes)
  meta.json          — manifest + tool versions + timestamps

Usage:
  python3 harness/recon_pack.py <project_dir> [--base-ref <commit>] [--out <pack_dir>]

Design notes:
  - Run ONCE per target. Cached on disk; cheap re-runs.
  - LLM never re-runs slither — it reads the JSON.
  - Failures are non-fatal per-step; meta.json records which steps succeeded.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SKIP_DIR_PARTS = {
    ".git",
    ".recon",
    "cache",
    "lib",
    "node_modules",
    "out",
    "script",
    "scripts",
    "test",
    "tests",
}


def is_target_sol_path(path: str | Path) -> bool:
    """Return true for target source files, false for tests/scripts/deps."""
    p = Path(path)
    parts = set(p.parts[:-1])
    if parts & SKIP_DIR_PARTS:
        return False
    name = p.name
    if name.endswith((".t.sol", ".s.sol")):
        return False
    s = str(p)
    if any(x in s for x in ("forge-std/", "@openzeppelin/", "solmate/", "@uniswap/")):
        return False
    return name.endswith(".sol")


def source_path_exists(project: Path, source_path: str) -> bool:
    return resolve_source_path(project, source_path) is not None


def resolve_source_path(project: Path, source_path: str) -> str | None:
    if not source_path:
        return None
    p = Path(source_path)
    if p.is_absolute():
        return str(p) if p.exists() else None
    for candidate in (project / p, project / "src" / p, project / "contracts" / p):
        if candidate.exists():
            return str(candidate.relative_to(project))
    return None


def concrete_contract_kind(artifact: dict, contract_name: str) -> str:
    nodes = (artifact.get("ast") or {}).get("nodes") or []
    for node in nodes:
        if node.get("nodeType") != "ContractDefinition":
            continue
        if node.get("name") == contract_name:
            if node.get("abstract"):
                return "abstract"
            return node.get("contractKind") or ""
    if not nodes:
        bytecode = ((artifact.get("bytecode") or {}).get("object") or "").strip()
        deployed = ((artifact.get("deployedBytecode") or {}).get("object") or "").strip()
        if bytecode not in ("", "0x") or deployed not in ("", "0x"):
            return "contract"
    return ""


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300, mem_mb: int = 4096) -> tuple[int, str, str]:
    """Run with timeout + RLIMIT_AS memory cap (Linux). Prevents slither/forge OOM."""
    import resource
    def _limit():
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        except (ValueError, OSError):
            pass
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            preexec_fn=_limit,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)
    except MemoryError:
        return 137, "", f"OOM (exceeded {mem_mb}MB)"


@dataclass
class Step:
    name: str
    ok: bool = False
    detail: str = ""
    artifact: str = ""


def step_inscope(project: Path, out_dir: Path) -> Step:
    sols = sorted(
        p for p in project.rglob("*.sol")
        if is_target_sol_path(p.relative_to(project))
    )
    files = []
    for p in sols:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        loc = len(text.splitlines())
        sloc = sum(1 for ln in text.splitlines()
                   if ln.strip() and not ln.strip().startswith("//"))
        files.append({
            "path": str(p.relative_to(project)),
            "loc": loc,
            "sloc": sloc,
        })
    out = {"project_dir": str(project), "files": files, "total": len(files)}
    (out_dir / "inscope.json").write_text(json.dumps(out, indent=2))
    return Step("inscope", ok=True, detail=f"{len(files)} .sol files")


def step_slither_json(project: Path, out_dir: Path) -> Step:
    out_path = out_dir / "slither.json"
    rc, _, err = run(
        ["slither", str(project), "--json", str(out_path), "--exclude-informational"],
        timeout=600,
    )
    # Slither returns non-zero when findings exist — still produces JSON.
    if out_path.exists() and out_path.stat().st_size > 0:
        return Step("slither_findings", ok=True, artifact=str(out_path))
    return Step("slither_findings", ok=False, detail=err[-1500:])


def step_slither_printer(project: Path, out_dir: Path, printer: str, fname: str) -> Step:
    rc, out, err = run(
        ["slither", str(project), "--print", printer],
        timeout=600,
    )
    raw_path = out_dir / f"_raw_{fname}.txt"
    raw_path.write_text(out + "\n---STDERR---\n" + err)
    # Real success requires actual stdout content AND non-fatal compilation.
    # crytic_compile InvalidCompilation in stderr means we got nothing useful.
    fatal = "InvalidCompilation" in err or "Traceback (most recent call last)" in err
    if fatal or not out.strip():
        return Step(f"printer_{printer}", ok=False, detail=err.splitlines()[-1] if err else "empty output", artifact=str(raw_path))
    return Step(f"printer_{printer}", ok=True, artifact=str(raw_path))


def step_slither_combined(project: Path, out_dir: Path) -> list[Step]:
    """
    Run all needed printers in ONE slither invocation (one compile, big speedup).
    Splits the combined output back into per-printer files heuristically.
    Falls back to per-printer calls if combined fails.
    """
    printers = ["call-graph", "modifiers", "vars-and-auth", "entry-points", "function-summary"]
    rc, out, err = run(
        ["slither", str(project), "--print", ",".join(printers)],
        cwd=out_dir,  # so .dot files land in out_dir
        timeout=900, mem_mb=6144,
    )
    combined_path = out_dir / "_raw_combined.txt"
    combined_path.write_text(out + "\n---STDERR---\n" + err)
    fatal = "InvalidCompilation" in err or "Traceback (most recent call last)" in err
    if fatal or not out.strip():
        # Fallback to per-printer (slow path)
        return [
            step_slither_printer(project, out_dir, "modifiers", "modifiers"),
            step_slither_printer(project, out_dir, "vars-and-auth", "vars_and_auth"),
            step_slither_printer(project, out_dir, "entry-points", "entry_points"),
            step_slither_printer(project, out_dir, "function-summary", "function_summary"),
        ]
    # Heuristic split — slither prints printer name as section header
    sections = re.split(r"\nPrinter:\s*([\w-]+)\n", "\n" + out)
    section_map: dict[str, str] = {}
    for i in range(1, len(sections) - 1, 2):
        section_map[sections[i]] = sections[i + 1]
    # Persist sections to expected filenames
    name_map = {
        "modifiers": "_raw_modifiers.txt",
        "vars-and-auth": "_raw_vars_and_auth.txt",
        "entry-points": "_raw_entry_points.txt",
        "function-summary": "_raw_function_summary.txt",
    }
    for sect, fname in name_map.items():
        if sect in section_map:
            (out_dir / fname).write_text(section_map[sect])
    # Build steps
    return [
        Step(f"printer_{p}", ok=(p in section_map or out.strip() != ""), artifact=str(out_dir / name_map.get(p, f"_raw_{p}.txt")))
        for p in ["modifiers", "vars-and-auth", "entry-points", "function-summary"]
    ]


def step_callgraph(project: Path, out_dir: Path) -> Step:
    """
    Parse .dot files produced by the slither call-graph printer.
    Run inside step_slither_combined (no separate slither call).
    Format: {edges: [{from: 'C.fn(args)', to: 'D.gn(args)'}], nodes: [...]}
    """
    err = ""
    dots = list(out_dir.glob("*.dot")) + list(project.glob("*.dot"))
    edges: list[dict] = []
    nodes: set[str] = set()
    edge_re = re.compile(r'"([^"]+)"\s*->\s*"([^"]+)"')
    node_re = re.compile(r'"([^"]+)"\s*\[')
    for dot in dots:
        try:
            text = dot.read_text()
        except Exception:
            continue
        for m in edge_re.finditer(text):
            edges.append({"from": m.group(1), "to": m.group(2)})
        for m in node_re.finditer(text):
            nodes.add(m.group(1))
        try:
            dot.unlink()
        except OSError:
            pass
    callers: dict[str, list[str]] = {}
    callees: dict[str, list[str]] = {}
    for e in edges:
        callees.setdefault(e["from"], []).append(e["to"])
        callers.setdefault(e["to"], []).append(e["from"])
    out = {
        "nodes": sorted(nodes),
        "edges": edges,
        "callers": callers,
        "callees": callees,
    }
    (out_dir / "callgraph.json").write_text(json.dumps(out, indent=2))
    if not edges:
        return Step("callgraph", ok=False, detail="no .dot files produced by combined printer pass")
    return Step("callgraph", ok=True, detail=f"{len(edges)} edges, {len(nodes)} nodes")


def detect_project_type(project: Path) -> str:
    if (project / "foundry.toml").exists() and (project / "src").exists():
        return "foundry"
    if (project / "hardhat.config.ts").exists() or (project / "hardhat.config.js").exists():
        return "hardhat"
    if (project / "foundry.toml").exists():
        return "foundry-mixed"  # foundry.toml but contracts in non-standard dir
    return "bare"


def step_storage_layouts(project: Path, out_dir: Path) -> Step:
    """
    forge inspect storage-layout for each compiled contract.
    For hardhat projects, requires `npm i && npx hardhat compile` first.
    """
    ptype = detect_project_type(project)
    if ptype == "hardhat":
        return Step("storage", ok=False, detail="hardhat project — run `npm i && npx hardhat compile` first; forge build skipped")

    # Force storage-layout extra output via env var (works without modifying foundry.toml)
    import os
    env_extra = os.environ.copy()
    env_extra["FOUNDRY_EXTRA_OUTPUT"] = '["storageLayout"]'
    try:
        p = subprocess.run(
            ["forge", "build", "--silent", "--extra-output", "storageLayout"],
            cwd=project, capture_output=True, text=True, timeout=900, env=env_extra,
        )
        rc, _, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return Step("storage", ok=False, detail="forge build timed out (900s)")
    if rc != 0:
        return Step("storage", ok=False, detail=f"forge build failed ({ptype}): {err[-800:]}")

    out_dir_artifacts = project / "out"
    contracts: dict[str, Any] = {}
    if out_dir_artifacts.exists():
        for art in out_dir_artifacts.rglob("*.json"):
            if art.name.startswith("build-info") or "/build-info/" in str(art):
                continue
            try:
                data = json.loads(art.read_text())
            except Exception:
                continue
            source_unit = art.relative_to(out_dir_artifacts).parts[0] if art.is_relative_to(out_dir_artifacts) else ""
            source_path = ((data.get("ast") or {}).get("absolutePath") or source_unit).strip()
            resolved_source_path = resolve_source_path(project, source_path)
            if not resolved_source_path:
                continue
            if not is_target_sol_path(resolved_source_path):
                continue
            if concrete_contract_kind(data, art.stem) != "contract":
                continue
            sl = data.get("storageLayout")
            if sl and isinstance(sl, dict) and sl.get("storage"):
                cname = art.stem
                contracts[cname] = sl
    (out_dir / "storage.json").write_text(json.dumps(contracts, indent=2))
    return Step("storage", ok=True, detail=f"{len(contracts)} contracts with storage layout")


def _unused_step_perms(project: Path, out_dir: Path) -> Step:  # superseded by step_slither_combined
    """
    Combine modifiers + vars-and-auth printers into a permissions matrix.
    Output is a structured JSON hand-rolled from text parsing of slither output.
    """
    mods_step = step_slither_printer(project, out_dir, "modifiers", "modifiers")
    auth_step = step_slither_printer(project, out_dir, "vars-and-auth", "vars_and_auth")

    perms = {
        "modifiers_raw": mods_step.artifact,
        "vars_and_auth_raw": auth_step.artifact,
        "note": "Raw printer output. Future: parse into {function: {modifiers: [], state_vars_written: [], auth_required: bool}}.",
    }
    (out_dir / "perms.json").write_text(json.dumps(perms, indent=2))
    return Step("perms", ok=mods_step.ok and auth_step.ok)


def step_entry_points(project: Path, out_dir: Path) -> Step:
    s = step_slither_printer(project, out_dir, "entry-points", "entry_points")
    if s.ok:
        # Entry-points printer output is text; copy artifact path as the index.
        (out_dir / "entry_points.json").write_text(json.dumps({
            "raw_path": s.artifact,
            "note": "Raw text. Externally-callable state-changing functions.",
        }, indent=2))
    return Step("entry_points", ok=s.ok, detail=s.detail)


def step_function_summary(project: Path, out_dir: Path) -> Step:
    s = step_slither_printer(project, out_dir, "function-summary", "function_summary")
    if s.ok:
        (out_dir / "function_summary.json").write_text(json.dumps({
            "raw_path": s.artifact,
            "note": "Per-function: visibility, modifiers, state vars read/written, calls.",
        }, indent=2))
    return Step("function_summary", ok=s.ok)


def step_diff(project: Path, out_dir: Path, base_ref: str | None) -> Step:
    if not base_ref:
        return Step("diff", ok=True, detail="no --base-ref provided; skipped")
    rc, out, err = run(
        ["git", "diff", f"{base_ref}..HEAD", "--", "*.sol"],
        cwd=project, timeout=120,
    )
    if rc != 0:
        return Step("diff", ok=False, detail=err[-500:])
    (out_dir / "diff.patch").write_text(out)
    return Step("diff", ok=True, detail=f"{len(out.splitlines())} lines of diff")


def step_mcga_sinks(project: Path, out_dir: Path) -> Step:
    """MLLA-style MCGA: tag every function with attack-surface sink categories,
    rank externals by sink density, surface high-density internal callees."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mcga_sinks import build  # type: ignore
        result = build(project)
        (out_dir / "mcga_sinks.json").write_text(json.dumps(result, indent=2))
        return Step("mcga_sinks", ok=True,
                    detail=f"{result['files_scanned']} files, "
                           f"{len(result['top_external_functions'])} ext sinks, "
                           f"{len(result['top_internal_callees'])} int sinks")
    except Exception as e:
        return Step("mcga_sinks", ok=False, detail=f"{type(e).__name__}: {e}")


def step_attack_surface(project: Path, out_dir: Path) -> Step:
    """CPUA-style ranked reading/tracing plan from MCGA sink tags."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from attack_surface import build  # type: ignore
        result = build(project)
        (out_dir / "attack_surface.json").write_text(json.dumps(result, indent=2))
        return Step(
            "attack_surface",
            ok=True,
            detail=f"{len(result['ranked_files'])} files, "
                   f"{len(result['ranked_functions'])} functions ranked",
        )
    except Exception as e:
        return Step("attack_surface", ok=False, detail=f"{type(e).__name__}: {e}")


def step_entry_points_forge(project: Path, out_dir: Path) -> Step:
    """
    Extract externally-callable functions per contract via `forge inspect <c> abi`.
    Replaces the slither entry-points printer — purely deterministic, no compile reuse needed
    beyond the `forge build` that step_storage_layouts already performed.
    """
    artifacts = project / "out"
    if not artifacts.exists():
        return Step("entry_points", ok=False, detail="forge out/ missing — run storage step first")
    entries: dict[str, list[dict]] = {}
    for art in artifacts.rglob("*.json"):
        if "/build-info/" in str(art):
            continue
        try:
            data = json.loads(art.read_text())
        except Exception:
            continue
        source_unit = art.relative_to(artifacts).parts[0] if art.is_relative_to(artifacts) else ""
        source_path = ((data.get("ast") or {}).get("absolutePath") or source_unit).strip()
        resolved_source_path = resolve_source_path(project, source_path)
        if not resolved_source_path:
            continue
        if not is_target_sol_path(resolved_source_path):
            continue
        if concrete_contract_kind(data, art.stem) != "contract":
            continue
        abi = data.get("abi") or []
        funcs = [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "stateMutability": item.get("stateMutability"),
                "inputs": [i.get("type") for i in (item.get("inputs") or [])],
                "source_file": resolved_source_path,
            }
            for item in abi
            if (
                item.get("type") in ("fallback", "receive")
                or (
                    item.get("type") == "function"
                    and item.get("stateMutability") in ("nonpayable", "payable")
                )
            )
        ]
        if funcs:
            entries[art.stem] = funcs
    (out_dir / "entry_points.json").write_text(json.dumps(entries, indent=2))
    return Step("entry_points", ok=True, detail=f"{len(entries)} contracts indexed")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Recon Pack: lightweight pre-LLM static dump")
    ap.add_argument("project_dir", help="Path to Solidity project")
    ap.add_argument("--base-ref", help="Git ref for post-audit diff (e.g. audit-commit hash)")
    ap.add_argument("--out", help="Output pack directory (default: <project>/.recon)")
    ap.add_argument("--with-slither", action="store_true",
                    help="Include legacy slither bulk pre-pass (DEPRECATED — use harness/tools/slither_tools.py on-demand instead)")
    args = ap.parse_args(argv[1:])

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"not a directory: {project}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else (project / ".recon")
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    steps: list[Step] = []
    # Layer 1 (mandatory, lightweight, deterministic)
    steps.append(step_inscope(project, out_dir))
    steps.append(step_storage_layouts(project, out_dir))      # forge build — also produces ABI artifacts
    steps.append(step_entry_points_forge(project, out_dir))   # ABI → external/public funcs (no slither)
    steps.append(step_mcga_sinks(project, out_dir))           # MLLA MCGA — sink-tagged attack surface
    steps.append(step_attack_surface(project, out_dir))       # CPUA — ranked coverage/tracing plan
    steps.append(step_diff(project, out_dir, args.base_ref))

    # Layer 2 (opt-in legacy bulk slither pass — XINT-style on-demand is preferred)
    if args.with_slither:
        steps.append(step_slither_json(project, out_dir))
        steps.extend(step_slither_combined(project, out_dir))
        steps.append(step_callgraph(project, out_dir))

    forge_v = run(["forge", "--version"])[1].splitlines()[0] if shutil.which("forge") else "missing"
    slither_v = run(["slither", "--version"])[1].strip() if shutil.which("slither") else "missing"

    meta = {
        "project_dir": str(project),
        "project_type": detect_project_type(project),
        "out_dir": str(out_dir),
        "base_ref": args.base_ref,
        "elapsed_sec": round(time.time() - started, 2),
        "tools": {"forge": forge_v, "slither": slither_v},
        "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in steps],
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))

    failed = [s for s in steps if not s.ok and s.name != "diff"]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
