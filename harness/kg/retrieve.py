#!/usr/bin/env python3
"""
harness/kg/retrieve.py — KG retrieval API for the BCDA stage.

Given a target's interface usage (extracted from recon_pack ECG), return
top-K most-similar past incidents from harness/kg/index.train.json.

Similarity v0: Jaccard over the interface symbol set (cheap, no embeddings).
The point is to surface "we've seen this shape attacked before — here are
the historical PoCs to read."

Usage (CLI):
  echo '{"interfaces": ["IERC20", "IPancakeRouter", "IFlashLoan"]}' | \
    python3 harness/kg/retrieve.py --top-k 5

Usage (library):
  from harness.kg.retrieve import retrieve
  hits = retrieve({"interfaces": [...]}, top_k=5)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX_TRAIN = REPO / "harness" / "kg" / "index.train.json"


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def retrieve(target: dict, top_k: int = 5,
             index_path: Path = INDEX_TRAIN) -> list[dict]:
    target_ifaces = set(target.get("interfaces") or [])
    if not target_ifaces:
        return []
    if not index_path.exists():
        raise FileNotFoundError(
            f"{index_path} missing — run harness/kg/build_index.py first")

    incidents = json.loads(index_path.read_text())
    scored = []
    for r in incidents:
        ri = set(r.get("interfaces_used") or [])
        if not ri:
            continue
        score = jaccard(target_ifaces, ri)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, r in scored[:top_k]:
        out.append({
            "score": round(score, 4),
            "id": r["id"],
            "name": r["name"],
            "date": r["date"],
            "total_lost_usd": r.get("total_lost_usd", 0),
            "vuln_contract_addr": r.get("vuln_contract_addr"),
            "file": r["file"],
            "shared_interfaces": sorted(target_ifaces & set(r["interfaces_used"])),
            "analysis_urls": r.get("analysis_urls", [])[:2],
        })
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--target-file",
                    help="JSON file with {interfaces: [...]} — defaults to stdin")
    args = ap.parse_args(argv[1:])

    raw = (Path(args.target_file).read_text()
           if args.target_file else sys.stdin.read())
    target = json.loads(raw)
    hits = retrieve(target, top_k=args.top_k)
    print(json.dumps(hits, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
