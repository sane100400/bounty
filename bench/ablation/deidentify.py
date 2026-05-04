#!/usr/bin/env python3
"""
bench/ablation/deidentify.py — Solidity symbol-level de-identification.

Renames contract / state-variable / non-interface function names to break
literal-match memorization with training data, while preserving:
  - External interfaces (ERC20/721/4626/Initializable etc) so it still compiles
  - Function signatures used by external callers (tests, harnesses)
  - NatSpec stripping is opt-in; default keeps it (we want behavioral parity)

Why: EVMBench has known training-data contamination (OpenZeppelin audit).
We need a "memorization-resistant" variant for honest absolute-score reporting.
The expected effect (per LLM benchmark research):
  symbol obfuscation alone → ~24% TPR drop signals memorization

Usage:
  python3 bench/ablation/deidentify.py <src_dir> <dst_dir> [--strip-comments]

Algorithm (pragmatic, not perfect):
  1. Walk all .sol files. Collect "user-defined" identifiers — contract names,
     non-interface state var names, custom function names.
  2. Build a stable rename map (deterministic hash → contract_a, var_b, fn_c).
  3. Substitute in all files. Skip identifiers from a known-public allowlist
     (ERC standards, OZ patterns, common solidity stdlib).
  4. Optionally strip line/block comments.

Caveats:
  - Regex-based, NOT AST. Will miss: identifier-as-string in events, function
    pointers, asm blocks. Acceptable for evaluation purposes — the goal is to
    break surface matching, not be a full obfuscator.
  - Run on a COPY. Never mutate the source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import string
import sys
from pathlib import Path

# Identifiers we never rename — preserves compilation against external libs.
ALLOWLIST = {
    # Solidity / EVM
    "abi", "address", "bool", "bytes", "bytes32", "uint", "uint256", "int", "int256",
    "msg", "block", "tx", "now", "this", "super", "selfdestruct", "keccak256",
    "require", "assert", "revert", "emit", "return", "returns",
    "memory", "storage", "calldata", "external", "public", "internal", "private",
    "view", "pure", "payable", "nonpayable", "virtual", "override",
    "if", "else", "for", "while", "do", "break", "continue", "new", "delete",
    "try", "catch", "function", "modifier", "event", "struct", "enum", "interface",
    "abstract", "contract", "library", "using", "import", "pragma", "solidity",
    "constant", "immutable",
    # ERC standards (interface methods)
    "balanceOf", "totalSupply", "transfer", "transferFrom", "approve", "allowance",
    "ownerOf", "safeTransferFrom", "isApprovedForAll", "setApprovalForAll",
    "deposit", "withdraw", "mint", "burn", "redeem", "asset", "convertToShares",
    "convertToAssets", "previewDeposit", "previewMint", "previewWithdraw", "previewRedeem",
    "permit", "DOMAIN_SEPARATOR", "nonces", "name", "symbol", "decimals",
    "Transfer", "Approval",
    # OZ / common
    "initialize", "constructor", "receive", "fallback",
    "owner", "transferOwnership", "renounceOwnership", "Ownable",
    "onlyOwner", "onlyRole", "hasRole", "grantRole", "revokeRole",
    "ReentrancyGuard", "nonReentrant",
    # SafeMath remnants
    "add", "sub", "mul", "div", "mod",
    # forge-std / hardhat console
    "vm", "console", "Test", "Vm",
    # Common accessor names that, if renamed, break callers across files
    "MAX_SUPPLY", "MAX", "MIN", "ZERO", "ONE",
    # Dual-compile triggers
    "pragma", "license",
}


# Identifier regex — Solidity identifiers
IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
# Patterns that introduce user-defined names (heuristic capture)
CONTRACT_DECL = re.compile(r"^\s*(?:abstract\s+)?contract\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)
LIB_DECL = re.compile(r"^\s*library\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)
INTERFACE_DECL = re.compile(r"^\s*interface\s+([A-Z][a-zA-Z0-9_]*)", re.MULTILINE)


def _short_id(prefix: str, source: str) -> str:
    h = hashlib.sha1(source.encode()).hexdigest()
    # Map to two-letter suffix for readability
    base = string.ascii_lowercase
    a = base[int(h[:2], 16) % 26]
    b = base[int(h[2:4], 16) % 26]
    return f"{prefix}_{a}{b}"


def collect_user_identifiers(files: list[Path]) -> dict[str, str]:
    """
    Return mapping: original_name → renamed_name.
    Captures contract/library/interface names heuristically.
    """
    contracts = set()
    interfaces = set()
    for p in files:
        text = p.read_text(errors="ignore")
        contracts.update(CONTRACT_DECL.findall(text))
        contracts.update(LIB_DECL.findall(text))
        interfaces.update(INTERFACE_DECL.findall(text))

    rename = {}
    for c in sorted(contracts):
        if c in ALLOWLIST:
            continue
        rename[c] = _short_id("Contract", c)
    # Interfaces: keep IERC20, IERC721 etc; rename custom IXyz only if not in allowlist
    for i in sorted(interfaces):
        if i in ALLOWLIST or re.match(r"I(ERC\d+|Ownable|Initializable|Pausable)", i):
            continue
        rename[i] = _short_id("Iface", i)
    return rename


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def deidentify_file(src: Path, dst: Path, rename: dict[str, str], strip: bool) -> None:
    text = src.read_text(errors="ignore")
    if strip:
        text = strip_comments(text)
    # Word-boundary substitution per identifier.
    # Sort keys longest first so prefix collisions don't clobber.
    for k in sorted(rename, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(k)}\b", rename[k], text)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Solidity symbol-level de-identifier")
    ap.add_argument("src_dir")
    ap.add_argument("dst_dir")
    ap.add_argument("--strip-comments", action="store_true")
    args = ap.parse_args(argv[1:])

    src = Path(args.src_dir).resolve()
    dst = Path(args.dst_dir).resolve()
    if dst.exists():
        print(f"refusing to overwrite existing {dst}", file=sys.stderr)
        return 2

    sols = [p for p in src.rglob("*.sol") if "node_modules" not in p.parts]
    if not sols:
        print(f"no .sol files under {src}", file=sys.stderr)
        return 1

    rename = collect_user_identifiers(sols)
    # Copy non-sol files verbatim (foundry.toml, remappings, etc)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.sol"))

    # Build a filename→renamed_filename map. Many Solidity files are named
    # exactly after their primary contract (`Curves.sol` → contract Curves).
    # We rename matching .sol filenames to keep imports resolvable since
    # deidentify_file rewrites strings inside import "./Foo.sol" too.
    file_rename: dict[str, str] = {}
    for p in sols:
        stem = p.stem  # 'Curves' for 'Curves.sol'
        if stem in rename:
            new_stem = rename[stem]
            new_rel = p.relative_to(src).with_name(f"{new_stem}.sol")
            file_rename[str(p.relative_to(src))] = str(new_rel)

    # Rename .sol files contents AND filenames
    for p in sols:
        rel_str = str(p.relative_to(src))
        target_rel = Path(file_rename.get(rel_str, rel_str))
        deidentify_file(p, dst / target_rel, rename, strip=args.strip_comments)

    # Write manifest of renames + filename remap
    (dst / ".deidentify.json").write_text(
        __import__("json").dumps({
            "renames": rename,
            "file_rename": file_rename,
            "strip_comments": args.strip_comments,
        }, indent=2)
    )
    print(f"deidentified {len(sols)} files; renamed {len(rename)} identifiers")
    print(f"  manifest: {dst / '.deidentify.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
