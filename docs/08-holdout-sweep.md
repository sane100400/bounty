# 08 — Holdout Sweep: First Real KG Lift Measurement

After hard-wiring KG + MCGA into the agent and building the SCONE
Foundry scaffolder, we ran the first multi-case comparison on
post-cutoff DeFiHackLabs incidents.

## Setup

- 3 confirmed-address post-cutoff cases: LAXO_Token (BSC, 2026-02),
  AlkemiEarn (ETH, 2026-03), EST (BSC, 2026-03)
- Per-case pipeline: Sourcify fetch → assemble src/ → scaffold_forge
  → run agent in two modes
- Budget: 3 verify iterations
- Modes: `baseline` (no KG, no MCGA), `full` (HARNESS_KG=1, HARNESS_MCGA=1)

## Results (`bench/ablation/results/holdout_sweep.md`)

| Case | Mode | Cost | Wall | Findings |
|---|---|---|---|---|
| laxo | baseline | $0.97 | 220s | 1 |
| laxo | full | $1.53 | 337s | 1 |
| alkemi | baseline | $0.25 | 26s | 0 |
| alkemi | full | $0.31 | 46s | 0 |
| est | baseline | $0.80 | 198s | 2 |
| est | full | $0.80 | 180s | 2 |

**Aggregate**:
- Baseline: 3 findings, $2.02, 444s
- Full v2: 3 findings, $2.64 (+31%), 563s (+27%)

## Verdict — at this scale, no measurable lift

The full v2 stack (KG retrieval + MCGA sink injection) **delivers zero
recall lift** over baseline at N=3 cases / budget=3. Cost goes up by
31%, wall time up by 27%.

### Why this disagrees with our LAXO smoke (which showed +100% findings)
The earlier smoke (budget=2, N=1) showed full v2 with 2 findings vs
baseline's 1. **That was N=1 noise**. The same setup at budget=3
returns 1 finding from each mode — *finding count for a single case
varies between runs*. Cross-run variance dominates the signal.

### Why this disagrees with Knowdit's published +62.6pp
Three honest reasons:

1. **N=3 is too small.** Knowdit measured on 75 vulnerabilities across
   12 projects. Our 3 cases cannot detect a 30pp lift at α=0.05.
2. **Budget mismatch.** Knowdit runs unbounded; we cap at 3 verify
   iterations. KG amplifies recall over time as the agent ranks
   hypotheses against retrieved patterns. Tight budget cuts that off.
3. **AlkemiEarn killed both modes.** Both runs gave up in <1 minute
   with 0 findings. The KG retrieval there had a 0.67 top-1 score
   (Bazaar 2024) — not specific enough to seed productive hypotheses.

## What this changes about our priorities

1. **Cannot claim KG lift on this evidence.** Update the docs and
   v2_design.md so no one mistakes the LAXO smoke for a real number.
2. **Larger N before re-evaluating.** Either:
   - Run all 6 holdout cases (need to manually map vuln addrs for
     Moonwell/Curve/Venus from PoC bodies)
   - Add 2026 audit-competition findings to holdout (more cases per
     month going forward)
3. **Per-case breakdown matters.** The right metric isn't aggregate
   finding count — it's per-case conditional precision (when full
   finds something baseline misses, was that a real bug?). N=3
   gives 0 such cases, so the question is unanswered.
4. **Do not over-engineer the v2 stack** until there's evidence it
   pays off. The harness-vs-raw lift in the literature (Knowdit
   +62pp, ReX +31pp) was measured on bigger datasets with longer
   budgets — neither condition is met here yet.

## Reproduce

```bash
cd /home/sane100400/projects/bounty
python3 bench/ablation/run_holdout.py --budget 3
# Outputs: bench/ablation/results/holdout_sweep.{json,md}
```

## Open question — does longer budget unlock KG benefit?

The cleanest follow-up: rerun LAXO + EST at budget=10 in both modes.
If full v2 finds 1+ additional bug per case at higher budget,
the KG lift hypothesis remains alive. If not, we need to seriously
revisit assumptions about retrieval quality.
