# Bug Hunting Workspace

Smart contract security research with two tracks:

1. **Live hunting** — `/web3-hunt` and `/web3-loop` skills for Immunefi
   bug bounties and audit competitions
2. **Harness research** — `harness/` + `bench/ablation/` framework for
   measuring whether harness engineering beats raw LLM (see `docs/`)

## Track 1 — Live hunting commands

### `/web3-hunt [target] [platform?] [rpc-url?]`

Single-run structured hunt. Auto-detects mode (audit vs bounty) from URL/platform.

```bash
# Audit competitions
/web3-hunt "https://cantina.xyz/competitions/..." cantina
/web3-hunt "https://code4rena.com/audits/..." codearena
/web3-hunt "./local-repo" sherlock

# Immunefi bug bounty
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"
```

Phases: Recon → Systematic File Sweep (100% coverage target) → Cross-Compare → Attack Surface → Triage → PoC → Report.

### `/web3-loop [target] [platform?] [max-iterations?] [rpc-url?]`

Autonomous loop. Each iteration covers files missed in previous iterations.

```bash
/web3-loop "https://cantina.xyz/competitions/..." cantina 10
/web3-loop "https://immunefi.com/bug-bounty/balancer" immunefi 10 "https://1rpc.io/eth"
```

Key features:
- Coverage gap tracking — each iteration reads unanalyzed files
- Drop reasons feed back to avoid repeating mistakes
- Audit mode: accumulates multiple findings
- Bounty mode: stops on first confirmed finding

---

## Track 2 — Harness research framework

Measures harness engineering lift vs raw Claude Opus on smart-contract
auditing. See `docs/README.md` for the full research archive.

### Quick start

```bash
# Build the DeFiHackLabs knowledge graph (one-time)
git clone --depth 1 https://github.com/SunWeb3Sec/DeFiHackLabs.git harness/kg/defi-hack-labs
python3 harness/kg/build_index.py   # 676 train + 6 holdout incidents

# Run the agent on a Foundry project (KG + MCGA hard-wired)
HARNESS_KG=1 HARNESS_MCGA=1 python3 bench/ablation/agent.py poc-forge \
  --case-id my_case --budget 3

# Run the post-cutoff holdout sweep (baseline vs full v2)
python3 bench/ablation/run_holdout.py --budget 3
```

### Key components

| Path | Purpose |
|---|---|
| `harness/recon_pack.py` | Pre-LLM static dump (storage, entry-points, MCGA sinks, diff) |
| `harness/mcga_sinks.py` | MLLA-style sink tagger (16 categories) |
| `harness/kg/` | DeFiHackLabs KG: indexer + Jaccard retrieval |
| `harness/verify.py` | 6-gate Foundry-grounded verifier (compile/exec/state/econ/dup/halmos) |
| `harness/tools/source_fetcher.py` | Sourcify → forge clone fallback |
| `harness/tools/scaffold_forge.py` | Wrap Sourcify src/ with minimal Foundry plumbing |
| `harness/templates/` | AttackHarness, Invariants, AttackInvariants (Trace2Inv 14), Halmos property |
| `bench/ablation/agent.py` | `claude -p` subprocess agent with `_kg_preamble`, `_mcga_preamble`, `_scone_scaffold` |
| `bench/ablation/run_holdout.py` | 6-case holdout sweep runner |
| `docs/` | Research archive — empirical evidence, architecture, ceiling, blueprint |

---

## Universal rules (apply to both tracks)

- **100% file coverage.** Read EVERY in-scope .sol file. Peripheral files hide the best bugs.
- **Cross-compare pattern groups.** 20 connectors implementing the same interface? Compare all 20.
- **Attack scenario first.** Never start from "this code looks weird."
- **Asymmetry ≠ bug.** Find the technical reason before calling it a bug.
- **Trace every value function.** TVL, balance, price, shares — trace the math.
- **Economic feasibility.** Include gas vs profit analysis with optimal parameters.

### Bounty-specific (Immunefi)
- Focus on post-audit code
- Dup check before PoC
- PoC = mainnet fork only + web3.py verification

### Audit-competition-specific
- All code is target
- Duplicates still pay
- High AND Medium count
- Foundry mock PoC is fine

## Directory layout

```
bounty/
├── CLAUDE.md            ← this file
├── .claude-commands/    ← /web3-hunt, /web3-loop skill sources
├── docs/                ← harness research archive (9 docs)
├── harness/             ← harness components (recon, MCGA, KG, verify, tools, templates)
├── bench/ablation/      ← agent + holdout runner
└── poc-forge/           ← Foundry workspace (src/, test/, lib/forge-std)
```

## RPC URLs

- Ethereum: `https://1rpc.io/eth`
- No mainnet testing — fork only (Immunefi rule)
