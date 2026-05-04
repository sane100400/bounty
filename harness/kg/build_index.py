#!/usr/bin/env python3
"""
harness/kg/build_index.py — Build the DeFiHackLabs Knowledge Graph index.

Parses every src/test/<YYYY-MM>/<Name>_exp.sol PoC, extracts the @KeyInfo,
@Info, @Analysis metadata blocks plus structural features (imports, state
variables, function signatures touched), and emits two JSON artifacts:

  harness/kg/index.train.json    incidents with date < cutoff
  harness/kg/index.holdout.json  incidents with date >= cutoff

Default cutoff: 2026-02-01 (Opus 4.7 knowledge buffer)

Usage:
  python3 harness/kg/build_index.py [--cutoff 2026-02-01]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DHL = REPO / "harness" / "kg" / "defi-hack-labs" / "src" / "test"
OUT_DIR = REPO / "harness" / "kg"

KEY_INFO_FIELDS = {
    "Total Lost": "total_lost",
    "Attacker": "attacker_addr",
    "Attack Contract": "attack_contract_addr",
    "Vulnerable Contract": "vuln_contract_addr",
    "Attack Tx": "attack_tx",
}

ANALYSIS_RE = re.compile(r"//\s*@Analysis\s*\n((?://.*\n)+)", re.MULTILINE)
INFO_RE = re.compile(r"//\s*@Info\s*\n((?://.*\n)+)", re.MULTILINE)
URL_RE = re.compile(r"https?://[^\s]+")
IMPORT_RE = re.compile(r'import\s+["\']([^"\']+)["\']')
INTERFACE_RE = re.compile(r"\b(I[A-Z]\w+)\b")


def parse_keyinfo(text: str) -> dict:
    """Pull // [@KeyInfo - ]Field : Value lines (multiple occurrences allowed)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*//\s*(?:@KeyInfo\s*-\s*)?([\w ]+?)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        label = m.group(1).strip()
        if label in KEY_INFO_FIELDS:
            out[KEY_INFO_FIELDS[label]] = m.group(2).strip()
    # Total-lost normalization (e.g. "~137K US$" → 137000)
    tl = out.get("total_lost", "")
    m = re.search(r"(\d[\d,\.]*)\s*([KMB])?", tl.replace("$", "").replace(",", ""))
    if m:
        n = float(m.group(1))
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2) or "", 1)
        out["total_lost_usd"] = int(n * mult)
    return out


def parse_one(path: Path, month: str) -> dict | None:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return None

    rec = {
        "id": f"{month}__{path.stem}",
        "date": f"{month}-01",  # month precision; pin to first-of-month
        "month": month,
        "file": str(path.relative_to(REPO)),
        "name": path.stem.replace("_exp", ""),
    }
    rec.update(parse_keyinfo(text))

    am = ANALYSIS_RE.search(text)
    rec["analysis_urls"] = sorted(set(URL_RE.findall(am.group(1)))) if am else []

    im = INFO_RE.search(text)
    rec["info_urls"] = sorted(set(URL_RE.findall(im.group(1)))) if im else []

    rec["imports"] = sorted(set(IMPORT_RE.findall(text)))
    rec["interfaces_used"] = sorted({i for i in INTERFACE_RE.findall(text) if len(i) > 2})[:40]
    rec["loc"] = len(text.splitlines())

    return rec


def build(cutoff: str) -> tuple[list, list]:
    train, holdout = [], []
    if not DHL.is_dir():
        print(f"ERROR: {DHL} not found — clone DeFiHackLabs first", file=sys.stderr)
        sys.exit(2)

    for month_dir in sorted(DHL.iterdir()):
        if not month_dir.is_dir():
            continue
        month = month_dir.name
        if not re.match(r"^\d{4}-\d{2}$", month):
            continue
        for sol in sorted(month_dir.glob("*_exp.sol")):
            rec = parse_one(sol, month)
            if rec is None:
                continue
            (holdout if rec["date"] >= cutoff else train).append(rec)
    return train, holdout


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-02-01",
                    help="incidents with date >= this go to holdout (default: %(default)s)")
    args = ap.parse_args(argv[1:])

    train, holdout = build(args.cutoff)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    train_path = OUT_DIR / "index.train.json"
    holdout_path = OUT_DIR / "index.holdout.json"
    train_path.write_text(json.dumps(train, indent=2))
    holdout_path.write_text(json.dumps(holdout, indent=2))

    print(json.dumps({
        "cutoff": args.cutoff,
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_path": str(train_path.relative_to(REPO)),
        "holdout_path": str(holdout_path.relative_to(REPO)),
        "holdout_ids": [r["id"] for r in holdout],
        "train_total_loss_usd": sum(r.get("total_lost_usd", 0) for r in train),
        "holdout_total_loss_usd": sum(r.get("total_lost_usd", 0) for r in holdout),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
