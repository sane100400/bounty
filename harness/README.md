# Harness Engineering

Foundry-grounded harness for LLM-driven smart contract vulnerability finding.
Replaces the prior token-optimization orchestration approach.

## Why this exists

LLM-only vuln finding hallucinates. The fix is **execution grounding**: every finding
must be backed by a Foundry test that compiles, executes, and violates a class-level
safety invariant. This directory holds the deterministic infrastructure that surrounds
the LLM and turns its guesses into verified findings.

Design synthesis:
- **Knowdit** (arXiv 2603.26270) — three-state spec → setUp + require oracles.
- **ReX** (arXiv 2508.01371) — class-level invariants as universal oracle; compiler
  feedback loop took 27% → 58%.
- **Anthropic Red** — fork + balance-delta gate; Opus 4.5 65% on 2025 exploits.

## Layout

```
harness/
├── README.md
├── schemas/
│   └── hypothesis.schema.json   # candidate finding spec (one file per hypothesis)
├── templates/
│   ├── AttackHarness.t.sol.tmpl # universal Foundry test scaffold (LLM fills slots)
│   ├── AttackInvariants.sol     # Trace2Inv-style attack-side invariant helpers
│   ├── HalmosProperty.t.sol.tmpl # symbolic property scaffold
│   └── Invariants.sol           # ClassInvariants — the ReX universal oracles
├── kg/                          # DeFiHackLabs cutoff-split index + retrieval
├── tools/                       # Sourcify fetch, Foundry scaffold, Slither/Forge tools
├── attack_surface.py            # CPUA ranked coverage/tracing plan
├── mcga_sinks.py                # sink-tagged attack surface ranker
├── recon_pack.py                # pre-LLM static analysis dump
└── verify.py                    # Verification Gate (compile → exec → delta → econ)
```

## Architecture: why this helps

The harness is not a bigger prompt. It is a set of deterministic rails around
the model that convert plausible candidates into reproducible PoCs.

| Layer | Files | Job | Failure mode it reduces |
|---|---|---|---|
| Scope/recon | `recon_pack.py` | Precompute in-scope files, entry points, storage hints, MCGA sinks, and metadata. | Spending the budget reading irrelevant files. |
| Function ranking | `attack_surface.py`, `mcga_sinks.py` | Rank public functions and internal primitives by token movement, balance/share writes, oracle reads, LP syncs, external calls, and arithmetic sinks. | Finding the right bug class too late. |
| Hypothesis schema | `schemas/hypothesis.schema.json` | Force every candidate to state target, setup, attack steps, expected invariant, and PoC path. | Vague reports that cannot be scored or repaired. |
| PoC scaffolding | `templates/AttackHarness*.tmpl`, `templates/Invariants.sol`, `templates/AttackInvariants.sol` | Give the model reusable Foundry structure and class-level invariant helpers. | Tests that compile but prove the wrong thing. |
| Verification gate | `verify.py` | Re-run schema, targeted compile, exact test execution, state delta, economic threshold, duplicate, and optional Halmos checks. | Self-reported success and passing-but-empty tests. |
| Repair loop | `bench/ablation/agent.py` with `HARNESS_VERIFY=1` | Feed exact failing gate output back to the agent during generation. | Stopping at the first compile/revert/oracle failure. |

The important benchmark distinction is:

- **strict baseline**: source + ordinary Foundry commands only. It never sees
  `verify.py`, recon packs, CPUA/MCGA ranking, invariant templates, KG, or old
  hypotheses during generation. The benchmark runner still post-scores its
  candidates with `verify.py` after it stops.
- **full harness**: gets the recon/ranking/invariant/verifier feedback during
  generation, then is scored by the same post-hoc verifier.

This keeps the judge identical while isolating the harness as the treatment.

On the strict Codex LAXO holdout, baseline generated three compile-passing PoCs
but all failed `state_delta` because they did not emit verifier-consumable
`InvariantEvidence`. The full harness generated three compile-passing PoCs and
all three passed. That is the current evidence that the harness improves
candidate-to-verified conversion.

## The Verification Gate

`verify.py <hypothesis.json>` runs in order:

| # | Gate         | Failure exit | Feedback to next LLM iter             |
|---|--------------|--------------|----------------------------------------|
| 0 | schema       | 97           | missing/invalid hypothesis fields     |
| 1 | compile      | 1            | missing PoC file, wrong PoC name, or targeted `forge build <poc>` stderr |
| 2 | execute      | 2            | revert reason + last 2KB of trace     |
| 3 | state_delta  | 3            | missing matching `InvariantEvidence`  |
| 4 | econ         | 4            | actual emitted delta vs profit threshold |
| 5 | dup          | 5            | on-demand Slither duplicate signal    |
| 6 | halmos       | 6            | symbolic counterexample (when used)   |

Output is written to `harness/last_verify.json` and printed to stdout — both consumable
by the LLM loop.

The strict path requires PoCs to use `ClassInvariants` helpers, which emit
`InvariantEvidence(classInvariant, subject, asset, before, after, delta, threshold)`.
For custom checks, call `assertCustomInvariant(...)`; a plain passing
`assertTrue(true)` no longer satisfies the state-delta gate.

## Hypothesis lifecycle

1. LLM generates one or more candidates → `harness/hypotheses/_runs/<case>/<id>.json`
2. LLM drafts `poc-forge/test/AttackHarness_<id>.t.sol` from the template, filling slots
3. Run `verify.py harness/hypotheses/<id>.json`
4. If exit ≠ 0: LLM reads `last_verify.json`, repairs, GOTO 3 (max N retries)
5. If exit = 0: hypothesis becomes a finding; `triage.status = "verified"`

## Class-level invariants (`templates/Invariants.sol`)

ReX's key insight: most exploits map onto a small set of universal oracles:
`attacker_eth_increased`, `attacker_token_increased`, `victim_token_drained`,
`share_price_collapsed/inflated`, `supply_inflated`, `permission_acquired`,
`function_unexpectedly_callable`. Custom oracles are an escape hatch, not the default.

This means the LLM rarely has to write oracle logic — only the attack flow.

## Current status

- [x] **P1.1** Spec schema + AttackHarness template + ClassInvariants + verify.py skeleton
- [x] **P1.2** Recon Pack: inscope, storage, entry points, MCGA sinks, CPUA attack surface, diff
- [ ] **P2** Wire `verify.py` into `web3-hunt` skill — no finding without exit=0
- [ ] **P3** hypothesis-bank: persistent dir of all candidates with triage status
- [x] **P4** Halmos gate active when `hypothesis.invariant.halmos_check=true`
- [ ] **P5** Echidna closed loop — LLM writes property, Echidna shrinks counterexample, LLM interprets

Recent hardening:
- `verify.py` now validates the hypothesis contract before running Foundry
- `verify.py` requires the expected PoC file and matches the exact test path
- compile gate now builds only the PoC file instead of the entire Foundry workspace
- state/econ gates now consume emitted `InvariantEvidence` and compare actual delta
- ERC20/token profit must include `profit_token_price_wei` outside `mode="research"` so gas-vs-profit is meaningful
- hypothesis ids are normalized to Solidity-safe test names in `verify.py`
- Forge's `No tests found` output is now an execute-gate failure
- `bench/ablation/agent.py` separates candidate hypotheses from verified findings
- `bench/ablation/agent.py` post-verifies overwritten candidates on reruns
- `bench/ablation/agent.py` defaults to Codex CLI; Claude remains available via `HARNESS_AGENT_BACKEND=claude`
- strict raw benchmark cells use score-only prompts that hide `verify.py`, recon packs, invariant templates, KG, and prior hypotheses from the agent
- `HARNESS_DRY_RUN_PROMPT=1` writes the exact Codex prompt without calling the model for benchmark contamination checks
- benchmark runners default to `--backend codex` and reset inherited `HARNESS_*` flags per cell/mode
- subprocess agents run from the target project cwd with case-local hypothesis output
- recon/MCGA default scope excludes tests, scripts, dependencies, interfaces, abstract contracts, and stale artifacts
- CPUA-ranked `attack_surface.json` upgrades the coverage tracker from file-level inventory to function-level tracing order
