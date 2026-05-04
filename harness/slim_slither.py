#!/usr/bin/env python3
"""
harness/slim_slither.py — Convert raw slither.json (often >10MB) into a
LLM-friendly slim view that fits in a single context call.

Strips: AST dumps, full source, child-of-child node trees.
Keeps: detector name, impact, confidence, file:line, one-line description.

Outputs three tiers:
  slither_high.json       — high+critical impact only (the must-look-at list)
  slither_medium.json     — high+medium combined
  slither_by_file.json    — index by file → list of {detector, lines, impact}

Usage:
  python3 harness/slim_slither.py <recon_pack_dir>
  → writes slither_high/medium/by_file.json into the same dir
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def slim_one(detector: dict) -> dict:
    """Return a minimal record for a single Slither detector finding."""
    elements = detector.get("elements", [])
    file = ""
    lines = ""
    for el in elements:
        sm = el.get("source_mapping") or {}
        if sm.get("filename_relative"):
            file = sm["filename_relative"]
            lns = sm.get("lines") or []
            if lns:
                lines = f"{lns[0]}-{lns[-1]}" if len(lns) > 1 else str(lns[0])
            break
    return {
        "check": detector.get("check"),
        "impact": detector.get("impact"),
        "confidence": detector.get("confidence"),
        "file": file,
        "lines": lines,
        "description": (detector.get("description") or "").strip().split("\n")[0][:240],
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: slim_slither.py <recon_pack_dir>", file=sys.stderr)
        return 2
    pack = Path(argv[1])
    raw = pack / "slither.json"
    if not raw.exists():
        print(f"missing: {raw}", file=sys.stderr)
        return 1
    data = json.loads(raw.read_text())
    detectors = data.get("results", {}).get("detectors", [])

    high: list[dict] = []
    medium: list[dict] = []
    by_file: dict[str, list[dict]] = defaultdict(list)

    for d in detectors:
        slim = slim_one(d)
        impact = (slim["impact"] or "").lower()
        if impact in ("high", "critical"):
            high.append(slim)
        if impact in ("high", "critical", "medium"):
            medium.append(slim)
        if slim["file"]:
            by_file[slim["file"]].append({
                "check": slim["check"],
                "lines": slim["lines"],
                "impact": slim["impact"],
            })

    (pack / "slither_high.json").write_text(json.dumps({"count": len(high), "findings": high}, indent=2))
    (pack / "slither_medium.json").write_text(json.dumps({"count": len(medium), "findings": medium}, indent=2))
    (pack / "slither_by_file.json").write_text(json.dumps(dict(by_file), indent=2))

    print(f"slim done: high={len(high)} medium={len(medium)} files={len(by_file)}")
    print(f"  → {pack / 'slither_high.json'}")
    print(f"  → {pack / 'slither_medium.json'}")
    print(f"  → {pack / 'slither_by_file.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
