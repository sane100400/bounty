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
│   └── Invariants.sol           # ClassInvariants — the ReX universal oracles
├── verify.py                    # Verification Gate (compile → exec → delta → econ)
└── (planned) recon_pack.py      # pre-LLM static analysis dump
```

## The Verification Gate

`verify.py <hypothesis.json>` runs in order:

| # | Gate         | Failure exit | Feedback to next LLM iter             |
|---|--------------|--------------|----------------------------------------|
| 1 | compile      | 1            | forge build stderr                    |
| 2 | execute      | 2            | revert reason + last 2KB of trace     |
| 3 | state_delta  | 3            | invariant did not fire correctly      |
| 4 | econ         | 4            | gas_cost vs min_profit_wei            |
| 5 | dup          | 5            | (stub) similar past findings          |
| 6 | halmos       | 6            | symbolic counterexample (when used)   |

Output is written to `harness/last_verify.json` and printed to stdout — both consumable
by the LLM loop.

## Hypothesis lifecycle

1. LLM generates one or more candidates → `harness/hypotheses/<id>.json`
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

## Phased rollout

- [x] **P1.1** Spec schema + AttackHarness template + ClassInvariants + verify.py skeleton
- [ ] **P1.2** Recon Pack: callgraph.json, storage.json, perms.json, slither.sarif, diff.patch
- [ ] **P2** Wire `verify.py` into `web3-hunt` skill — no finding without exit=0
- [ ] **P3** hypothesis-bank: persistent dir of all candidates with triage status
- [ ] **P4** Halmos gate active for invariant-class candidates
- [ ] **P5** Echidna closed loop — LLM writes property, Echidna shrinks counterexample, LLM interprets

Active loop: `/loop 10m solidity를 위한 harness engineering...` (job 5a2036e2).
