#!/usr/bin/env python3
"""
harness/tools/slither_tools.py — On-demand Slither queries.

XINT-style: Slither is a SECONDARY tool the LLM dispatches when needed,
not a Layer-1 bulk pre-pass. Each function here is a narrow query with
small, structured output that fits in a single LLM context call.

Why narrow tools (not bulk dump):
  - Slither detector findings are mostly known patterns → already in
    auditors' first pass → duplicate-bait. Bulk import wastes context.
  - Compilation is cached after first call (crytic-export reuse) so
    follow-up queries are cheap.
  - Output is always <2KB, suitable for direct inclusion in an LLM
    response or tool-result message.

Functions exposed:
  slither_dup_check(project, file, lines)
      → { "matched_detectors": [...], "is_likely_duplicate": bool }
      Use AFTER hypothesis generation to deprioritize already-flagged spots.

  slither_function_summary(project, contract)
      → modifiers, state vars read/written, external calls — for one contract.

  slither_callers(project, function_signature)
      → list of {file, line, caller_function} — exact callers.

  slither_taint(project, contract, var)
      → { sources: [...], sinks: [...] } — data dependency for one var.

All functions:
  - Cache compilation in <project>/.harness_slither_cache/ (auto-created).
  - Return { "ok": bool, "data": ..., "error": "..." } envelope.
  - Capped at 60s wall, 2GB RSS.
"""

from __future__ import annotations

import json
import os
import re
import resource
import subprocess
from pathlib import Path
from typing import Any


CACHE_DIRNAME = ".harness_slither_cache"


def _run(cmd: list[str], cwd: Path, timeout: int = 60, mem_mb: int = 2048) -> tuple[int, str, str]:
    def _limit():
        try:
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        except (ValueError, OSError):
            pass
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, preexec_fn=_limit)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def _cache_path(project: Path) -> Path:
    p = project / CACHE_DIRNAME
    p.mkdir(exist_ok=True)
    return p


def _ensure_findings(project: Path) -> Path | None:
    """Run `slither --json` once; cache result."""
    cache = _cache_path(project) / "findings.json"
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    rc, _, err = _run(
        ["slither", str(project), "--json", str(cache), "--exclude-informational"],
        cwd=project, timeout=600, mem_mb=4096,
    )
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    return None


def slither_dup_check(project: str | Path, file: str, lines: str) -> dict:
    """
    Did Slither already flag this file/line range? If so, the LLM should
    deprioritize it — protocol's CI almost certainly ran Slither, so any
    detector hit at this location is a probable duplicate.

    Args:
      file:  path relative to project root (e.g. "src/loan/LendingTerm.sol")
      lines: "725-825" or "725" — inclusive range to test for overlap.

    Returns:
      { ok, data: { matched_detectors: [{check, impact, lines}], is_likely_duplicate } }
    """
    project = Path(project).resolve()
    findings_path = _ensure_findings(project)
    if not findings_path:
        return {"ok": False, "error": "slither did not produce findings.json"}

    try:
        a, b = (int(x) for x in (lines.split("-") + [lines])[:2])
    except ValueError:
        return {"ok": False, "error": f"bad lines spec: {lines!r}"}

    data = json.loads(findings_path.read_text())
    detectors = data.get("results", {}).get("detectors", [])
    matches = []
    for d in detectors:
        for el in d.get("elements", []):
            sm = el.get("source_mapping") or {}
            fname = sm.get("filename_relative", "")
            if file != fname and not fname.endswith("/" + file):
                continue
            ls = sm.get("lines") or []
            if not ls:
                continue
            if max(ls[0], a) <= min(ls[-1], b):
                matches.append({
                    "check": d.get("check"),
                    "impact": d.get("impact"),
                    "lines": f"{ls[0]}-{ls[-1]}" if len(ls) > 1 else str(ls[0]),
                })
                break  # one match per detector is enough
    return {
        "ok": True,
        "data": {
            "matched_detectors": matches[:10],
            "is_likely_duplicate": len(matches) > 0,
        },
    }


def slither_function_summary(project: str | Path, contract: str) -> dict:
    """
    Per-function summary for one contract: visibility, modifiers, state vars
    read/written, external calls. Output capped to ~50 functions.
    """
    project = Path(project).resolve()
    cache = _cache_path(project) / f"funsum_{contract}.txt"
    if not cache.exists():
        rc, out, err = _run(
            ["slither", str(project), "--print", "function-summary",
             "--filter-paths", f"!{contract}"],
            cwd=project, timeout=120, mem_mb=2048,
        )
        cache.write_text(out)
    raw = cache.read_text()
    # Cheap heuristic — just slice the section that mentions the contract.
    idx = raw.find(f"Contract {contract}")
    if idx < 0:
        return {"ok": False, "error": f"contract {contract!r} not in summary"}
    snippet = raw[idx: idx + 8000]
    return {"ok": True, "data": {"text": snippet}}


def slither_callers(project: str | Path, function_signature: str) -> dict:
    """
    Who calls <function_signature>? Returns a list of {file, line, caller}.
    """
    project = Path(project).resolve()
    work = _cache_path(project) / "dot"
    work.mkdir(exist_ok=True)
    rc, out, err = _run(
        ["slither", str(project), "--print", "call-graph"],
        cwd=work, timeout=300, mem_mb=4096,
    )
    edges = []
    for dot in work.glob("*.dot"):
        try:
            text = dot.read_text()
        except Exception:
            continue
        for m in re.finditer(r'"([^"]+)"\s*->\s*"([^"]+)"', text):
            edges.append((m.group(1), m.group(2)))
    matched = [src for src, dst in edges if function_signature in dst]
    return {"ok": True, "data": {"callers": matched[:50], "total_edges": len(edges)}}


def slither_taint(project: str | Path, contract: str, var: str) -> dict:
    """
    Data-dependency for <contract>.<var> via slither's data-dependency printer.
    Returns the printer's text section relevant to the variable.
    """
    project = Path(project).resolve()
    cache = _cache_path(project) / f"taint_{contract}_{var}.txt"
    if not cache.exists():
        rc, out, err = _run(
            ["slither", str(project), "--print", "data-dependency"],
            cwd=project, timeout=300, mem_mb=4096,
        )
        cache.write_text(out)
    raw = cache.read_text()
    pat = re.escape(f"{contract}.{var}")
    m = re.search(pat + r"[\s\S]{0,3000}", raw)
    if not m:
        return {"ok": False, "error": f"{contract}.{var} not found in data-dependency output"}
    return {"ok": True, "data": {"text": m.group(0)}}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s_dup = sub.add_parser("dup")
    s_dup.add_argument("file"); s_dup.add_argument("lines")
    s_fun = sub.add_parser("funsum"); s_fun.add_argument("contract")
    s_call = sub.add_parser("callers"); s_call.add_argument("signature")
    s_taint = sub.add_parser("taint"); s_taint.add_argument("contract"); s_taint.add_argument("var")
    args = ap.parse_args()
    if args.cmd == "dup":
        print(json.dumps(slither_dup_check(args.project, args.file, args.lines), indent=2))
    elif args.cmd == "funsum":
        print(json.dumps(slither_function_summary(args.project, args.contract), indent=2))
    elif args.cmd == "callers":
        print(json.dumps(slither_callers(args.project, args.signature), indent=2))
    elif args.cmd == "taint":
        print(json.dumps(slither_taint(args.project, args.contract, args.var), indent=2))
