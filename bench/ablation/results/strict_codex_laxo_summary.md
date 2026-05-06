# Harness Result Summary

Counts use `verification_results.exit_code == 0` only. Current cell summaries are accepted; other rows without `verification_results` are legacy and count as 0 verified.

| Source | Case | Mode | Backend | Candidates | Compile-pass | Verified | Cost | Wall | Notes |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| bench/ablation/results/holdout_sweep.json#0 | laxo | baseline | codex | 3 | 3 | 0 | ? | 410s |  |
| bench/ablation/results/holdout_sweep.json#1 | laxo | full | codex | 3 | 3 | 3 | ? | 555s |  |

Total candidates: 6
Total compile-pass PoCs: 6
Total verified by current gate: 3
Total cost (where reported): $0.000
