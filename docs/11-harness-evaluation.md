# 11 — Harness Evaluation Protocol

The harness is only persuasive if we show current-verifier performance, not
candidate volume. A finding counts only when `verification_results.exit_code == 0`
under the checked-in `harness/verify.py`.

## What We Need To Prove

1. **More valid verified findings** than raw/baseline agents on the same cases.
2. **Higher candidate-to-verified conversion** from better oracle scaffolding.
3. **Lower false-positive rate** after manual or ground-truth judging.
4. **Reasonable cost/time** per verified valid finding.

Candidate counts alone are weak evidence. Old result files that have a
`verified` array but no `verification_results` are legacy outputs and must not
be cited as verified.

## Cells To Run

Keep the matrix small enough to actually repeat:

| Cell | Purpose | Env |
|---|---|---|
| R0 raw | Strict score-only baseline: source + Foundry only, no verifier/recon/invariant prompt | `R0_codex_raw` |
| C1 coverage | File inventory + budget discipline | `A1_recon` or explicit coverage-only preset |
| C2 CPUA | Coverage plus ranked function plan | default attack-surface preamble, no KG |
| H1 full | Current harness stack | `H1_codex_harness` |
| H2 deep | Higher PoC-repair pressure | `H2_codex_deep` |

For budget-constrained runs, use R0, C2, and H1 first. KG/MCGA only matter if
they beat C2 after controlling for coverage.

## Datasets

Use three tiers:

1. **Positive controls**: `poc-forge` synthetic vaults. Expected result is not
   recall, but verifier health: compile, execute, state_delta, econ all pass.
2. **Known-ground-truth holdout**: DeFiHackLabs post-cutoff cases with clear
   vulnerable contract and exploit class. These support valid recall.
3. **Audit-contest repos**: larger codebases for robustness. Treat findings as
   candidates unless final reports or manual review establish validity.

Every case should have a dataset entry:

```json
{
  "case_id": "name",
  "project_dir": "/abs/path/to/foundry/project",
  "budget": 5,
  "ground_truth": [
    {"file": "src/Vulnerable.sol", "vuln_class": "donation_share_inflation"}
  ]
}
```

## Metrics

Report these per cell:

| Metric | Definition |
|---|---|
| candidates | `len(hypotheses)` |
| compile_rate | compile gate passes / candidates |
| verified_rate | `exit_code == 0` / candidates |
| valid_verified | verified findings matching ground truth/manual judge |
| false_verified | verified findings rejected by judge |
| valid_recall | cases with >=1 valid verified / total ground-truth cases |
| cost_per_valid | total cost / valid_verified |
| median_wall | median elapsed seconds |

## Commands

Run the generic ablation runner where possible:

```bash
python3 bench/ablation/run.py \
  --cell R0_codex_raw \
  --backend codex \
  --dataset bench/ablation/datasets/synthetic_poc_forge.json \
  --budget 3 \
  --max-budget-usd 2 \
  --timeout-sec 1800 \
  --out bench/ablation/results/current_eval

python3 bench/ablation/run.py \
  --cell H1_codex_harness \
  --backend codex \
  --dataset bench/ablation/datasets/synthetic_poc_forge.json \
  --budget 3 \
  --max-budget-usd 2 \
  --timeout-sec 1800 \
  --out bench/ablation/results/current_eval
```

For real cases, replace the dataset:

```bash
python3 bench/ablation/run.py \
  --cell R0_codex_raw \
  --backend codex \
  --dataset bench/ablation/my_dataset.json \
  --budget 5 \
  --out bench/ablation/results/current_eval

python3 bench/ablation/run.py \
  --cell H1_codex_harness \
  --backend codex \
  --dataset bench/ablation/my_dataset.json \
  --budget 5 \
  --out bench/ablation/results/current_eval
```

Summarize only current verifier evidence:

```bash
python3 bench/ablation/summarize_results.py \
  bench/ablation/results/current_eval \
  --out bench/ablation/results/current_eval/summary.md
```

For contest and holdout scripts, their summaries also count only
`verification_results.exit_code == 0`. They default to `--backend codex` and
write the selected backend into result JSON/Markdown. Benchmark runners reset
inherited `HARNESS_*` flags before applying each cell/mode, so stray shell
values do not silently change the benchmark. If a row says `legacy:no
verification_results`, rerun it before citing it.

`run_holdout.py` / `run_contest_sweep.py` mode mapping:

| Mode | Prompt/tools exposed to agent | Scoring |
|---|---|---|
| `baseline` | strict raw Codex, no recon, no CPUA/MCGA, no `verify.py` repair loop | post-hoc `harness/verify.py` only |
| `full` | H1-style recon, hypothesis bank, invariant guidance, verifier repair loop, Slither, MCGA | same post-hoc `harness/verify.py` |

This keeps the scorer identical while preventing the baseline from using the
harness during generation.

## Acceptance Bar

A strong claim needs:

- At least 10 non-synthetic cases.
- At least 3 repeated runs per main cell or a fixed deterministic backend setup.
- No cherry-picking failed runs.
- Current verifier artifacts retained for every verified finding.
- Manual judging notes for every verified finding that lacks public ground truth.

The honest headline should be framed as:

> Current harness improves verified-finding yield / conversion / cost on this
> benchmark under this budget.

Do not claim KG/MCGA lift unless the coverage-controlled cell is beaten.
