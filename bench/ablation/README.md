# Ablation Study — Harness Engineering for EVMBench

**Goal**: Quantify how much each harness component contributes to valid-finding
recall on EVMBench, while controlling for training-data memorization.

## Background — contamination is real

OpenZeppelin's audit of EVMBench found that ~36 of 40 repos come from
Code4rena contests ending before August 2025 — within all frontier model
training windows. Audit reports + exploit write-ups are likely in pretraining
corpora. arXiv 2603.10795 ("Re-Evaluating EVMBench") proposes a 22-case
contamination-free dataset of post-cutoff incidents.

This means: **a single absolute number on raw EVMBench is misleading**. Either
(a) the model memorized the case → high score with no harness gain, or
(b) the model didn't → low score and harness gain may look bigger than real.

## Methodology — 3-way control

For every (model, harness configuration) cell we report:

1. **Counterfactual delta on raw EVMBench**: same model, harness ON vs OFF.
   Memorization affects both equally, so the delta isolates harness contribution.
   PRIMARY metric.
2. **Absolute score on de-identified EVMBench**: symbols renamed, comments
   optionally stripped. Drop magnitude (raw → deid) is itself a memorization
   signal we report.
3. **Absolute score on held-out post-cutoff set**: 2026 Q1+ incidents.
   Honest absolute number for headline reporting.

## Ablation axes

| Axis | Component                                             | Hypothesis |
|------|-------------------------------------------------------|------------|
| A0   | Vanilla LLM, raw source files                         | Baseline   |
| A1   | + Recon Pack (inscope / storage / diff)               | Indexed structure removes grep overhead |
| A2   | + Externalized Hypothesis Bank                        | Per-candidate context isolation |
| A3   | + ReX class-invariant template (Invariants.sol)       | Universal oracle removes per-case spec writing |
| A4   | + Verification Gate (compile→exec→delta→econ)         | Hallucination filter |
| A5   | + Trace2Inv 23-template oracle library                 | Vuln-class coverage |
| A6   | + Slither-as-tool (on-demand dup_check / func_summary) | Static-analysis duplicate filter |
| A7   | + Halmos symbolic gate                                 | Strong invariant proofs |
| A8   | + Echidna closed loop                                  | Counterexample-driven hypothesis variants |

## Per-cell metrics

For each cell measure:
- **Valid recall** (judge-confirmed valid findings / total cases)
- **PoC compile rate**
- **PoC pass rate** (compiled and assertion fires)
- **Median tool calls per finding**
- **$ cost per attempt and per finding**
- **Time-to-first-finding (median)**

## Layout

```
bench/ablation/
├── README.md              ← this file
├── deidentify.py          ← Solidity symbol obfuscation
├── (planned) run.py       ← per-cell runner, writes JSON results
├── (planned) judge.py     ← LLM judge for valid/invalid decision
├── (planned) holdout.py   ← post-cutoff dataset puller (Immunefi / Cantina 2026+)
└── results/               ← per-cell JSON outputs, plus aggregated tables
```

## What we publish

- Architecture diagram + harness component descriptions
- Per-axis ablation table (delta + absolute deid + absolute held-out)
- Cost / quality Pareto plot
- Methodology notes (contamination handling, judge protocol, sample sizes)

We do NOT publish: raw model outputs that contain case-identifying text from
contaminated EVMBench, since that re-uploads training data into our writeup.
