#!/usr/bin/env python3
"""
harness/tools/source_fetcher.py — Fetch verified Solidity source for an
on-chain address. Used by SCONE-mode ablation cells where the case spec
provides only (chain, address, fork_block).

Resolution order:
  1. Sourcify (no API key, full + partial match)        https://sourcify.dev
  2. forge clone (requires ETHERSCAN_API_KEY)           Etherscan API v2

Output: writes source files to <out_dir>/, returns {ok, files, source} dict.

Usage (CLI):
  python3 harness/tools/source_fetcher.py mainnet 0xAbC... ./fetched/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHAIN_IDS = {
    "mainnet": 1, "ethereum": 1, "eth": 1,
    "bsc": 56, "binance": 56,
    "polygon": 137, "matic": 137,
    "arbitrum": 42161, "arb": 42161,
    "optimism": 10, "op": 10,
    "base": 8453,
    "avalanche": 43114, "avax": 43114,
}


def _normalize_chain(chain: str) -> int:
    c = chain.strip().lower()
    if c in CHAIN_IDS:
        return CHAIN_IDS[c]
    if c.isdigit():
        return int(c)
    raise ValueError(f"unknown chain: {chain!r}")


def fetch_sourcify(chain_id: int, address: str, out_dir: Path) -> dict:
    """Try Sourcify full match, then partial. Returns {ok, files} or {ok:False, error}."""
    address = address.lower()
    if not address.startswith("0x"):
        address = "0x" + address

    for match_type in ("full_match", "partial_match"):
        url = f"https://sourcify.dev/server/files/any/{chain_id}/{address}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "harness/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            return {"ok": False, "error": f"sourcify HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"ok": False, "error": f"sourcify request failed: {type(e).__name__}: {e}"}

        files_written = []
        for entry in payload.get("files", []):
            rel = entry.get("path", "").split(f"{address}/", 1)[-1] or entry.get("name", "")
            if not rel:
                continue
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(entry.get("content", ""))
            files_written.append(str(dest.relative_to(out_dir)))

        if files_written:
            return {
                "ok": True,
                "source": "sourcify",
                "match": payload.get("status", match_type),
                "files": files_written,
            }
    return {"ok": False, "error": "sourcify: no match (full or partial)"}


def fetch_forge_clone(chain_id: int, address: str, out_dir: Path) -> dict:
    """Use `forge clone` for Etherscan-verified sources. Requires ETHERSCAN_API_KEY."""
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return {"ok": False, "error": "ETHERSCAN_API_KEY not set"}

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["forge", "clone", "--chain", str(chain_id), "--no-commit", address, str(out_dir)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return {"ok": False, "error": f"forge clone failed: {r.stderr[-500:]}"}

    files = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*.sol"))
    return {"ok": True, "source": "forge_clone", "files": files}


def fetch(chain: str, address: str, out_dir: Path) -> dict:
    chain_id = _normalize_chain(chain)
    out_dir.mkdir(parents=True, exist_ok=True)

    sf = fetch_sourcify(chain_id, address, out_dir)
    if sf["ok"]:
        return sf

    fc = fetch_forge_clone(chain_id, address, out_dir)
    if fc["ok"]:
        return fc

    return {"ok": False, "error": "both sourcify and forge clone failed",
            "sourcify": sf, "forge_clone": fc}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chain", help="chain name (mainnet|bsc|...) or numeric id")
    ap.add_argument("address")
    ap.add_argument("out_dir")
    args = ap.parse_args(argv[1:])

    result = fetch(args.chain, args.address, Path(args.out_dir).resolve())
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
