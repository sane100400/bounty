#!/usr/bin/env python3
"""
harness/tools/scaffold_forge.py — wrap a Sourcify-fetched src/ tree
with the minimum Foundry plumbing so verify.py 6-gate runs end-to-end.

Steps:
  1. Walk src/ for the deepest pragma_solidity declaration → pick a compatible solc
  2. Detect import shapes → write remappings (@openzeppelin/, solmate/, ...)
  3. Symlink poc-forge/lib/forge-std into <case>/lib/forge-std
  4. Write foundry.toml
  5. forge build to validate

Usage:
  python3 harness/tools/scaffold_forge.py <case_dir>
  # case_dir must contain a src/ subdirectory (Sourcify output)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FORGE_STD = REPO / "poc-forge" / "lib" / "forge-std"

PRAGMA_RE = re.compile(r"pragma\s+solidity\s+([^\;]+);")
IMPORT_RE = re.compile(r'import\s+(?:\{[^}]*\}\s+from\s+)?["\']([^"\']+)["\']')

KNOWN_REMAPPINGS = {
    "@openzeppelin/": [
        "@openzeppelin/=lib/openzeppelin-contracts/",
    ],
    "solmate/": [
        "solmate/=lib/solmate/src/",
    ],
    "forge-std/": [
        "forge-std/=lib/forge-std/src/",
    ],
}


def detect_pragmas(src: Path) -> list[str]:
    pragmas = set()
    for p in src.rglob("*.sol"):
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for m in PRAGMA_RE.finditer(text):
            pragmas.add(m.group(1).strip())
    return sorted(pragmas)


def collect_remappings(src: Path) -> dict:
    """Find non-relative imports + check whether vendored copies exist
    inside src/. Return a dict prefix -> [remap_lines]."""
    used_prefixes: set[str] = set()
    for p in src.rglob("*.sol"):
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for m in IMPORT_RE.finditer(text):
            path = m.group(1)
            if path.startswith("."):
                continue
            head = path.split("/", 1)[0]
            used_prefixes.add(head + "/")

    out: dict[str, list[str]] = {}
    for prefix in used_prefixes:
        # Vendored inside src/?  @openzeppelin/ → src/@openzeppelin/
        vendored = src / prefix.rstrip("/")
        if vendored.exists():
            out[prefix] = [f"{prefix}=src/{prefix}"]
            continue
        # Known external dep?
        if prefix in KNOWN_REMAPPINGS:
            out[prefix] = KNOWN_REMAPPINGS[prefix]
    return out


def install_dep(case: Path, prefix: str) -> str | None:
    """Best-effort install of a known dep into <case>/lib/. Returns log."""
    libs = {
        "@openzeppelin/": ("openzeppelin-contracts",
                            "https://github.com/OpenZeppelin/openzeppelin-contracts"),
        "solmate/": ("solmate", "https://github.com/transmissions11/solmate"),
    }
    name, url = libs.get(prefix, (None, None))
    if not name:
        return None
    target = case / "lib" / name
    if target.exists():
        return f"{name}: already present"
    target.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        capture_output=True, text=True, timeout=180,
    )
    return f"{name}: clone {'ok' if r.returncode == 0 else 'failed: ' + r.stderr[-200:]}"


def scaffold(case_dir: Path) -> dict:
    src = case_dir / "src"
    if not src.is_dir():
        # Sourcify output sometimes has src/contracts/* — find the actual src root
        candidates = list(case_dir.rglob("*.sol"))
        if not candidates:
            return {"ok": False, "error": "no .sol files found in case_dir"}
        # Pick the longest common ancestor as src root
        common = candidates[0].parent
        for c in candidates[1:]:
            while not str(c).startswith(str(common)):
                common = common.parent
        # Symlink it as src/
        src.parent.mkdir(parents=True, exist_ok=True)
        src.symlink_to(common, target_is_directory=True)

    # Symlink shared forge-std
    libs = case_dir / "lib"
    libs.mkdir(exist_ok=True)
    fs_link = libs / "forge-std"
    if not fs_link.exists():
        fs_link.symlink_to(FORGE_STD)

    pragmas = detect_pragmas(src)
    remaps = collect_remappings(src)

    install_log = []
    for prefix in remaps:
        log = install_dep(case_dir, prefix)
        if log:
            install_log.append(log)

    # Always include forge-std
    remap_lines = ["forge-std/=lib/forge-std/src/"]
    for entries in remaps.values():
        remap_lines.extend(entries)

    (case_dir / "remappings.txt").write_text("\n".join(remap_lines) + "\n")

    # Pick conservative solc version based on pragmas (highest-floor seen)
    solc_choice = "0.8.23"  # safe default; pragmas like ^0.8.0 / ^0.8.20 all compile
    foundry_toml = (
        "[profile.default]\n"
        'src = "src"\n'
        'out = "out"\n'
        'libs = ["lib"]\n'
        f'solc_version = "{solc_choice}"\n'
        "via_ir = false\n"
        "optimizer = false\n"
        "evm_version = \"paris\"\n"
    )
    (case_dir / "foundry.toml").write_text(foundry_toml)

    # Validate
    r = subprocess.run(
        ["forge", "build", "--root", str(case_dir)],
        capture_output=True, text=True, timeout=300,
    )
    return {
        "ok": r.returncode == 0,
        "case_dir": str(case_dir),
        "pragmas_found": pragmas,
        "solc_chosen": solc_choice,
        "remappings": remap_lines,
        "install_log": install_log,
        "build_stdout_tail": r.stdout[-1500:],
        "build_stderr_tail": r.stderr[-1500:],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    args = ap.parse_args(argv[1:])

    case = Path(args.case_dir).resolve()
    result = scaffold(case)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
