# Strict Codex LAXO Benchmark

Run date: 2026-05-06

Dataset: LAXO holdout (`0x62951CaD7659393BF07fbe790cF898A3B6d317CB`, BSC) fetched through `run_holdout.py`.

Scoring rule: only `verification_results.exit_code == 0` counts as verified.

| Case | Mode | Backend | Candidates | Compile-pass | Verified | Wall |
|---|---|---|---:|---:|---:|---:|
| laxo | strict baseline | codex | 3 | 3 | 0 | 410s |
| laxo | full harness | codex | 3 | 3 | 3 | 555s |

## Interpretation

The strict baseline was allowed source reading and normal Foundry build/test
commands, but it did not receive recon, CPUA/MCGA ranking, invariant helpers,
or `verify.py` repair feedback. It produced three compile-passing PoC
candidates, but all failed the scorer's `state_delta` gate because they did not
emit the required `InvariantEvidence` / `assertCustomInvariant` evidence.

The full harness received recon, invariant guidance, verifier repair feedback,
Slither, and MCGA. Under the same Codex backend and the same candidate budget,
it produced three compile-passing candidates and all three passed the current
verification gate.

This is evidence for a harness lift in candidate-to-verified conversion on this
case:

- strict baseline verified rate: `0 / 3 = 0%`
- full harness verified rate: `3 / 3 = 100%`
- absolute lift: `+100 percentage points`

## Caveats

This is a single-case benchmark. It supports the narrower claim that the
harness improves current-verifier conversion on LAXO under this budget. It does
not yet prove broad bounty-valid recall. The next step is manual validity
judgment for the three full-harness findings, followed by the same strict
baseline/full comparison across more holdout cases.
