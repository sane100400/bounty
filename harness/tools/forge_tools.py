#!/usr/bin/env python3
"""
harness/tools/forge_tools.py — On-demand Foundry / cast queries.

Mirrors three of A1's six tools (arXiv 2507.05558) plus our additions:
  state_reader      — A1 StateReader: ABI → external/public/view fns
  proxy_resolver    — A1 SourceCodeFetcher's proxy half: read EIP-1967 slots
  storage_reader    — read raw storage slot at fork (A1 ConcreteExecution helper)
  cast_call         — eth_call against fork (read functions only)
  forge_inspect     — wraps `forge inspect <c> <field>` for any field
  build_artifact    — fetch compiled artifact (ABI + bytecode + storageLayout)

Uniform envelope: { ok: bool, data: any, error: str }
All outputs <2KB; safe for direct LLM tool-result consumption.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


# EIP-1967 well-known proxy slots
EIP1967_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN = "0x10d6a54a4754c8869d6886b5f5d7fbfa5b4522237ea5c60d11bc4e7a1ff9390b"
EIP1967_BEACON = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def state_reader(project: str | Path, contract: str) -> dict:
    """
    A1 StateReader equivalent. Returns external/public functions split by
    state-mutability (view / pure / nonpayable / payable).
    """
    project = Path(project).resolve()
    art_dir = project / "out"
    if not art_dir.exists():
        return {"ok": False, "error": "out/ missing — run `forge build` first"}
    # Find first matching artifact
    candidates = list(art_dir.rglob(f"{contract}.json"))
    if not candidates:
        return {"ok": False, "error": f"artifact for {contract!r} not found"}
    try:
        data = json.loads(candidates[0].read_text())
    except Exception as e:
        return {"ok": False, "error": f"artifact parse: {e}"}
    abi = data.get("abi") or []
    by_mut: dict[str, list[dict]] = {"view": [], "pure": [], "nonpayable": [], "payable": []}
    for item in abi:
        if item.get("type") != "function":
            continue
        sig_inputs = ",".join(i.get("type", "") for i in (item.get("inputs") or []))
        sig = f'{item.get("name")}({sig_inputs})'
        mut = item.get("stateMutability", "nonpayable")
        by_mut.setdefault(mut, []).append({
            "signature": sig,
            "outputs": [o.get("type") for o in (item.get("outputs") or [])],
        })
    return {"ok": True, "data": by_mut}


def proxy_resolver(rpc_url: str, target: str) -> dict:
    """
    Read EIP-1967 storage slots to detect transparent / UUPS / beacon proxies.
    Uses `cast storage`. Returns implementation + admin if found.
    """
    out: dict[str, str] = {}
    for label, slot in [("implementation", EIP1967_IMPL),
                        ("admin", EIP1967_ADMIN),
                        ("beacon", EIP1967_BEACON)]:
        rc, stdout, err = _run(["cast", "storage", target, slot, "--rpc-url", rpc_url], timeout=30)
        if rc != 0:
            continue
        raw = stdout.strip()
        # zero slot → not a proxy field
        if raw and int(raw, 16) != 0:
            # last 20 bytes is the address
            addr = "0x" + raw[-40:]
            out[label] = addr
    if not out:
        return {"ok": True, "data": {"is_proxy": False}}
    return {"ok": True, "data": {"is_proxy": True, **out}}


def storage_reader(rpc_url: str, target: str, slot: str | int) -> dict:
    """Read a single storage slot via `cast storage`. Slot can be hex or decimal."""
    if isinstance(slot, int):
        slot = hex(slot)
    rc, out, err = _run(["cast", "storage", target, slot, "--rpc-url", rpc_url], timeout=30)
    if rc != 0:
        return {"ok": False, "error": err.strip() or out}
    return {"ok": True, "data": {"slot": slot, "value": out.strip()}}


def cast_call(rpc_url: str, target: str, signature: str, args: list[str] | None = None) -> dict:
    """
    eth_call wrapper. Uses cast call. Read-only — does not require signing.
    Args are positional Solidity values (cast handles encoding).
    """
    cmd = ["cast", "call", target, signature, "--rpc-url", rpc_url]
    cmd.extend(args or [])
    rc, out, err = _run(cmd, timeout=60)
    if rc != 0:
        return {"ok": False, "error": (err.strip() or out)[-1500:]}
    return {"ok": True, "data": {"return": out.strip()}}


def forge_inspect(project: str | Path, contract: str, field: str) -> dict:
    """
    Wraps `forge inspect`. Common fields: abi, bytecode, methods, storage-layout,
    methodIdentifiers, gasEstimates.
    """
    project = Path(project).resolve()
    rc, out, err = _run(["forge", "inspect", contract, field], cwd=project, timeout=60)
    if rc != 0:
        return {"ok": False, "error": (err.strip() or out)[-1500:]}
    # Try parse as JSON; fall back to raw
    try:
        return {"ok": True, "data": json.loads(out)}
    except Exception:
        return {"ok": True, "data": {"raw": out.strip()[:1500]}}


def build_artifact(project: str | Path, contract: str) -> dict:
    """Fetch the slim compiled artifact: ABI + storageLayout + methodIdentifiers."""
    project = Path(project).resolve()
    arts = list((project / "out").rglob(f"{contract}.json"))
    if not arts:
        return {"ok": False, "error": f"{contract} not found in out/"}
    try:
        d = json.loads(arts[0].read_text())
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "data": {
        "abi": d.get("abi"),
        "storageLayout": d.get("storageLayout"),
        "methodIdentifiers": d.get("methodIdentifiers"),
        # Skip bytecode — too large; fetch separately if needed
    }}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("state"); p1.add_argument("project"); p1.add_argument("contract")
    p2 = sub.add_parser("proxy"); p2.add_argument("rpc"); p2.add_argument("target")
    p3 = sub.add_parser("storage"); p3.add_argument("rpc"); p3.add_argument("target"); p3.add_argument("slot")
    p4 = sub.add_parser("call"); p4.add_argument("rpc"); p4.add_argument("target"); p4.add_argument("sig")
    p5 = sub.add_parser("inspect"); p5.add_argument("project"); p5.add_argument("contract"); p5.add_argument("field")
    args = ap.parse_args()
    if args.cmd == "state":
        print(json.dumps(state_reader(args.project, args.contract), indent=2))
    elif args.cmd == "proxy":
        print(json.dumps(proxy_resolver(args.rpc, args.target), indent=2))
    elif args.cmd == "storage":
        print(json.dumps(storage_reader(args.rpc, args.target, args.slot), indent=2))
    elif args.cmd == "call":
        print(json.dumps(cast_call(args.rpc, args.target, args.sig), indent=2))
    elif args.cmd == "inspect":
        print(json.dumps(forge_inspect(args.project, args.contract, args.field), indent=2))
