# Harness Result Summary

Counts use `verification_results.exit_code == 0` only. Current cell summaries are accepted; other rows without `verification_results` are legacy and count as 0 verified.

| Source | Case | Mode | Backend | Candidates | Compile-pass | Verified | Cost | Wall | Notes |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| bench/ablation/results/current_eval/H1_codex_harness/_summary.json | ? | H1_codex_harness | codex | 3 | 3 | 3 | ? | ? | cell summary |
| bench/ablation/results/current_eval/R0_codex_raw/_summary.json | ? | R0_codex_raw | codex | 3 | 3 | 3 | ? | ? | cell summary |
| bench/ablation/results/holdout_sweep.json#0 | laxo | baseline | codex | 3 | 3 | 3 | ? | 438s |  |
| bench/ablation/results/holdout_sweep.json#1 | laxo | full | codex | 3 | 3 | 3 | ? | 540s |  |

Total candidates: 12
Total compile-pass PoCs: 12
Total verified by current gate: 12
Total cost (where reported): $0.000
