# Bounty Hunter — Smart Contract Security Workspace

Two parallel tracks in one workspace:

1. **Live hunting** — `/web3-hunt` and `/web3-loop` skills produce
   PoC-confirmed findings on Immunefi bug bounties and audit
   competitions (Cantina, Sherlock, Code4rena, CodeHawks).
2. **Harness research** — `harness/` + `bench/ablation/` measure
   whether harness engineering actually beats raw Claude Opus on
   contamination-resistant smart-contract benchmarks.

> **Model**: Claude Opus 4.7 (1M context) — knowledge cutoff January 2026
>
> **Research summary**: harness *can* beat raw Opus (Knowdit 81% recall vs ~25% baseline in literature; AIxCC won by an 8B-class model with strong harness). Our N=3 holdout sweep at budget=3 has not yet reproduced a lift — see [docs/08-holdout-sweep.md](docs/08-holdout-sweep.md) for the honest null result.

---

## Repository layout

```
bounty/
├── CLAUDE.md           ← project instructions (loaded by Claude Code)
├── README.md           ← this file
├── scope.md            ← scratchpad for active hunt scopes
├── .claude-commands/   ← /web3-hunt, /web3-loop skill sources
├── docs/               ← 9-doc research archive
│   ├── README.md            ← research index + TL;DR
│   ├── 01-research-findings.md
│   ├── 02-architecture.md   ← Atlantis CRS + MLLA pattern
│   ├── 03-component-attribution.md
│   ├── 04-smart-contract-ceiling.md
│   ├── 05-v2-blueprint.md
│   ├── 06-experiments.md    ← in-house measurements
│   ├── 07-defihacklabs-kg.md
│   ├── 08-holdout-sweep.md  ← N=3 null result
│   └── sources.md
├── harness/            ← harness components
│   ├── recon_pack.py        ← pre-LLM static dump
│   ├── mcga_sinks.py        ← MLLA-style sink tagger (16 categories)
│   ├── verify.py            ← 6-gate Foundry-grounded verifier
│   ├── kg/                  ← DeFiHackLabs KG
│   │   ├── build_index.py
│   │   └── retrieve.py
│   ├── tools/               ← source_fetcher, scaffold_forge, slither_tools
│   ├── templates/           ← AttackHarness, Invariants, AttackInvariants, Halmos
│   └── schemas/             ← hypothesis JSON schema
├── bench/ablation/     ← agent + holdout sweep
│   ├── agent.py             ← `claude -p` subprocess agent
│   ├── agent_prompt.md
│   ├── run.py               ← 8-cell ablation runner
│   ├── run_holdout.py       ← post-cutoff DeFiHackLabs sweep
│   ├── scone_loader.py
│   └── results/
└── poc-forge/          ← Foundry workspace
    ├── src/                 ← active eval cases (SyntheticVault, MultiBugVault)
    ├── test/                ← AttackHarness_*, HalmosProperty_*, Invariants
    └── lib/forge-std/
```

---

## Track 1 — live hunting

### Quick start
```bash
# Audit competition
/web3-hunt "https://cantina.xyz/competitions/..." cantina

# Immunefi bounty
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"

# Autonomous loop (audit mode accumulates; bounty mode stops on first valid)
/web3-loop "https://immunefi.com/bug-bounty/balancer" immunefi 10 "https://1rpc.io/eth"
```

Phases inside each hunt: Recon → Systematic File Sweep (100% coverage)
→ Cross-Compare → Attack Surface → Triage → PoC → Report.

Skill source: [.claude-commands/web3-hunt.md](.claude-commands/web3-hunt.md), [.claude-commands/web3-loop.md](.claude-commands/web3-loop.md).

---

## Track 2 — harness research

### One-time setup
```bash
# Foundry + halmos
curl -L https://foundry.paradigm.xyz | bash && foundryup
pip install halmos

# DeFiHackLabs Knowledge Graph (682 incidents, ≤ 2026-01 train + ≥ 2026-02 holdout)
git clone --depth 1 https://github.com/SunWeb3Sec/DeFiHackLabs.git harness/kg/defi-hack-labs
python3 harness/kg/build_index.py
```

### Run the agent on your own Foundry project
```bash
# Baseline (no KG / no MCGA)
python3 bench/ablation/agent.py poc-forge --case-id my_case --budget 3

# Full v2 stack (KG retrieval + MCGA sink injection hard-wired)
HARNESS_KG=1 HARNESS_MCGA=1 \
  python3 bench/ablation/agent.py poc-forge --case-id my_case --budget 3
```

The agent is a `claude -p` subprocess (uses your Claude Code CLI auth —
no API key needed) restricted to Read / Grep / Bash / Edit / Glob /
Write. It writes hypotheses to `harness/hypotheses/<case_id>-N.json`,
PoCs to `poc-forge/test/AttackHarness_<id>.t.sol`, and runs
`harness/verify.py` for the 6-gate verification loop.

### Run the post-cutoff holdout sweep
```bash
python3 bench/ablation/run_holdout.py --budget 3
# Output: bench/ablation/results/holdout_sweep.{json,md}
```

### Verifier gates (`harness/verify.py`)
1. **compile** — `forge build`
2. **execute** — `forge test --match-test testPoC_<id> -vvvv`
3. **state_delta** — invariant assertion fired in correct direction
4. **econ** — gas cost vs claimed profit at realistic gas price
5. **dup** — Slither already flagged this location? (likely-duplicate)
6. **halmos** — symbolic verification when `hypothesis.invariant.halmos_check=true`

Hypothesis JSON may set `"forge_root": "<path>"` to redirect verification
to a per-case Foundry root (used by SCONE-mode runs after `scaffold_forge.py`).

---

## What the research has produced

| Component | Status |
|---|---|
| Verifier loop (6-gate, Foundry) | ✅ working — synthetic vault PoC passes all 6 |
| `claude -p` subprocess agent | ✅ working ($0.43/case smoke) |
| Source-fetcher (Sourcify → forge clone) | ✅ working (USDC, LAXO confirmed) |
| SCONE Foundry scaffolder | ✅ working (LAXO src/ → buildable Foundry project) |
| DeFiHackLabs KG (682 incidents indexed) | ✅ working, 6 post-cutoff holdout |
| KG hard-wired into agent system prompt | ✅ working (`HARNESS_KG=1`) |
| MCGA sink tagger (16 categories) | ✅ working — correctly flags LAXO `_transfer` lp_sync |
| Halmos parallel gate | ✅ template + smoke test passing |
| 6-holdout KG-lift measurement | 🟡 N=3 done (lift = 0 at budget=3); N=6 + budget=10 needed |
| BCDA/BGA prompt split | ❌ dropped — measured regression |

For the long-form research story (questions, evidence, ceiling, blueprint, in-house experiments), read [docs/README.md](docs/README.md).

---

## Universal hunt rules (apply to both tracks)

- **100% file coverage.** Read every in-scope `.sol` file.
- **Cross-compare pattern groups.** 20 connectors with the same interface? Compare all 20.
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

---

## RPC URLs

- Ethereum: `https://1rpc.io/eth`
- BSC: `https://bsc-dataseed.binance.org/`
- Base: `https://mainnet.base.org/`
- No mainnet testing — fork only (Immunefi rule)

---

## License

Internal research workspace. No external license.
