# Bounty Hunter — Smart Contract Security Workspace

Two parallel tracks in one workspace:

1. **Live hunting** — `/web3-hunt` and `/web3-loop` skills produce
   PoC-confirmed findings on Immunefi bug bounties and audit
   competitions (Cantina, Sherlock, Code4rena, CodeHawks).
2. **Harness research** — `harness/` + `bench/ablation/` measure
   whether harness engineering actually beats strict raw-model baselines on
   contamination-resistant smart-contract benchmarks.

> **Default benchmark backend**: Codex CLI (`codex exec`) with optional
> Claude/Anthropic paths retained for comparison.
>
> **Current research summary**: the useful lift is not "the model writes more
> candidates"; it is that the harness converts candidates into verifier-passing
> PoCs. In the strict Codex LAXO holdout, raw Codex produced 3 compile-passing
> candidates but 0 passed the current verifier, while the full harness produced
> 3 candidates and all 3 passed. See
> [strict_codex_laxo_report.md](bench/ablation/results/strict_codex_laxo_report.md).
> The broad claim still needs more holdout cases and manual validity judging.

---

## Repository layout

```
bounty/
├── CLAUDE.md           ← project instructions (loaded by Claude Code)
├── README.md           ← this file
├── scope.md            ← scratchpad for active hunt scopes
├── .claude-commands/   ← /web3-hunt, /web3-loop skill sources
├── docs/               ← research archive + evaluation protocol
│   ├── README.md            ← research index + TL;DR
│   ├── 01-research-findings.md
│   ├── 02-architecture.md   ← Atlantis CRS + MLLA pattern
│   ├── 03-component-attribution.md
│   ├── 04-smart-contract-ceiling.md
│   ├── 05-v2-blueprint.md
│   ├── 06-experiments.md    ← in-house measurements
│   ├── 07-defihacklabs-kg.md
│   ├── 08-holdout-sweep.md  ← N=3 null result
│   ├── 09-budget-and-codebase-effects.md
│   ├── 10-fix-rerun-coverage-tracker-wins.md
│   ├── 11-harness-evaluation.md
│   └── sources.md
├── harness/            ← harness components
│   ├── recon_pack.py        ← pre-LLM static dump
│   ├── attack_surface.py    ← CPUA ranked file/function coverage plan
│   ├── mcga_sinks.py        ← MLLA-style sink tagger (16 categories)
│   ├── verify.py            ← 6-gate Foundry-grounded verifier
│   ├── kg/                  ← DeFiHackLabs KG
│   │   ├── build_index.py
│   │   └── retrieve.py
│   ├── tools/               ← source_fetcher, scaffold_forge, slither_tools
│   ├── templates/           ← AttackHarness, Invariants, AttackInvariants, Halmos
│   └── schemas/             ← hypothesis JSON schema
├── bench/ablation/     ← agent + holdout sweep
│   ├── agent.py             ← Codex/Claude subprocess agent
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

### Why the harness improves performance

The benchmark is intentionally split into two roles:

| Role | What the agent sees | What the scorer does |
|---|---|---|
| `baseline` / `R0_codex_raw` | Target source + ordinary Foundry commands only. No recon, no CPUA/MCGA ranking, no invariant helpers, no `verify.py` repair loop, no prior hypotheses. | Runs the same `harness/verify.py` after the agent stops. |
| `full` / H1 | Target source plus recon, CPUA attack-surface ranking, MCGA sink tags, class-invariant guidance, Slither helpers, and verifier feedback for repair. | Runs the same post-hoc `harness/verify.py` after the agent stops. |

That means the scorer is identical, but generation is different. The measured
lift is therefore attributed to the harness context and repair loop, not to a
different judge.

The full harness is built as a conversion pipeline:

1. **Scope compression**: `recon_pack.py` extracts in-scope files, entry points,
   storage hints, sink-ranked functions, and metadata before the model starts.
2. **Attack-surface ordering**: `attack_surface.py` ranks files/functions by
   public entry points, token movement, balance/share writes, oracle reads,
   LP syncs, external calls, and arithmetic sinks. This keeps the model from
   spending the budget on low-signal files first.
3. **Sink semantics**: `mcga_sinks.py` labels high-risk primitives such as
   `transfer_token`, `balance_write`, `share_write`, `oracle_read`, `lp_sync`,
   `unchecked_arith`, `delegatecall`, and `external_call`, then injects the
   highest-density functions into the prompt.
4. **Structured hypothesis contract**: each candidate must be written as a
   schema-checked JSON file with target, state setup, attack steps, invariant
   class, and PoC path. This makes failures machine-actionable instead of vague.
5. **Invariant-backed PoC template**: `ClassInvariants` / `AttackInvariants`
   emit `InvariantEvidence`, so the verifier can prove that the intended
   state/economic delta actually happened. A test that merely passes is not
   enough.
6. **Verifier repair loop**: `verify.py` returns the exact failing gate
   (`schema`, `compile`, `execute`, `state_delta`, `econ`, `dup`, `halmos`).
   The full harness can repair against that feedback during generation; the
   strict baseline cannot.
7. **Post-hoc scoring**: every benchmark row is scored again after the run using
   only `verification_results.exit_code == 0`, so agent prose and self-reported
   success do not count.

The LAXO strict Codex result shows the mechanism clearly:

| Mode | Candidates | Compile-pass | Verified | Main failure mode |
|---|---:|---:|---:|---|
| strict baseline | 3 | 3 | 0 | PoCs compiled, but did not emit verifier-consumable `InvariantEvidence` |
| full harness | 3 | 3 | 3 | All candidates satisfied the current verifier |

So the current evidence supports the narrow claim: on this holdout, the harness
improved candidate-to-verified conversion from `0/3` to `3/3`. It does not yet
prove broad bounty-valid recall; that requires manual judging and more cases.

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
# Default single-agent run. Defaults to Codex CLI.
python3 bench/ablation/agent.py poc-forge --case-id my_case --budget 3

# Harness-assisted run with verifier repair and sink guidance.
HARNESS_VERIFY=1 HARNESS_INV=1 HARNESS_SLITHER=1 HARNESS_MCGA=1 \
  python3 bench/ablation/agent.py poc-forge --case-id my_case --budget 3

# Force Claude backend if desired
HARNESS_AGENT_BACKEND=claude \
  python3 bench/ablation/agent.py poc-forge --case-id my_case --budget 3
```

The default agent is a `codex exec` subprocess (uses your Codex CLI auth or
OpenAI API setup, not Claude tokens). `HARNESS_AGENT_BACKEND=claude` keeps
the old Claude Code path. It writes hypotheses to
`harness/hypotheses/_runs/<case_id>/<case_id>-N.json`,
PoCs to `poc-forge/test/AttackHarness_<id>.t.sol`, and runs
`harness/verify.py` for the 6-gate verification loop.

For strict baseline/full benchmark comparisons, use `run.py`, `run_holdout.py`,
or `run_contest_sweep.py`; those scripts reset inherited `HARNESS_*` flags and
apply the score-only baseline/full-harness mode split consistently.

### Run the post-cutoff holdout sweep
```bash
python3 bench/ablation/run_holdout.py --backend codex --budget 3
# Output: bench/ablation/results/holdout_sweep.{json,md}
```

Benchmark runners default to `--backend codex` and reset inherited `HARNESS_*`
flags before applying each cell/mode. The `baseline`/`R0_codex_raw` path is a
strict score-only prompt: the agent gets source + Foundry only, while
`harness/verify.py` is used only after the run for scoring. The `full`/H1 path
gets recon, invariant guidance, verifier repair feedback, Slither, and MCGA.

### Verifier gates (`harness/verify.py`)
1. **compile** — PoC-name checks + targeted `forge build <poc>`
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
| Verifier loop (schema + 6-gate, Foundry) | ✅ working — synthetic vault PoC passes strict PoC-file, evidence, delta gates |
| Codex CLI subprocess agent | ✅ working — strict LAXO benchmark completed |
| `claude -p` subprocess agent | ✅ working ($0.43/case smoke), now optional |
| Source-fetcher (Sourcify → forge clone) | ✅ working (USDC, LAXO confirmed) |
| SCONE Foundry scaffolder | ✅ working (LAXO src/ → buildable Foundry project) |
| DeFiHackLabs KG (682 incidents indexed) | ✅ working, 6 post-cutoff holdout |
| KG hard-wired into agent system prompt | ✅ working (`HARNESS_KG=1`) |
| MCGA sink tagger (16 categories) | ✅ working — correctly flags LAXO `_transfer` lp_sync |
| Halmos parallel gate | ✅ template + smoke test passing |
| Coverage-tracker lift (N=2) | ✅ Brings baseline 0 → 3 findings on 77-file Fluid DEX. Dominant lift component; now upgraded with CPUA function ranking. |
| Strict Codex LAXO lift | ✅ baseline 3 compile / 0 verified vs full harness 3 compile / 3 verified |
| KG/MCGA *additional* lift | 🟡 At N=2 (Fluid + Chainlink), no measurable contribution above coverage tracker. May matter for verified-finding quality, not candidate count. |
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
