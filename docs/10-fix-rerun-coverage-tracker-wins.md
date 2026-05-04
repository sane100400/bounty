# 10 — Re-measurement after the 3 fixes: coverage tracker is the lift

After [docs/09](09-budget-and-codebase-effects.md) showed dramatic
KG+MCGA lift on Fluid DEX (0 → 3 findings vs baseline), we shipped
three fixes:

1. **Leak channel closed** — DefiHackLabs clone moved to `~/.cache/`
2. **Force full budget** — explicit budget remaining + stop-early-is-failure
3. **Coverage tracker** — in-scope file inventory injected into user message

Re-ran the same Fluid case + added Chainlink (88 files / 11K LOC,
post-cutoff Code4rena 2026-03 contest). Results below contradict
the prior simple "KG+MCGA = lift" story.

## Results

| Case | Mode | Cost | Wall | Turns | Findings (candidates) |
|---|---|---|---|---|---|
| Fluid DEX | baseline | $1.67 | 295s | 21 | **3** |
| Fluid DEX | full | $3.18 | 400s | 36 | 3 |
| Chainlink | baseline | $2.85 | 470s | 23 | **3** |
| Chainlink | full | $3.98 | 757s | 25 | 3 |

**Verified findings**: 0 across all 4 runs (no full PoC passed the
6-gate verifier; budget wasn't enough for these complex protocols).

## What changed vs docs/09

| | docs/09 (no fixes) | docs/10 (fixes applied) |
|---|---|---|
| Fluid baseline | 0 findings | **3 findings** |
| Fluid full | 3 candidates | 3 candidates |
| Cost (full vs baseline) | -34% | **+90%** |

**The dramatic Fluid 0 → 3 lift in docs/09 was orientation effect,
not KG/MCGA-specific.** Once baseline gets the same in-scope file
inventory the full mode had implicitly (via MCGA preamble + KG hits
listing files), baseline finds the same number of candidates.

## Honest re-attribution

| Component | What it actually contributes (per N=2 measurement) |
|---|---|
| **Coverage tracker** (file inventory) | **Brought baseline from 0 → 3 findings on Fluid.** This is the dominant lift. |
| MCGA (sink-tagged attack surface) | Marginal *quality* signal — full mode found different vuln classes on same functions, but not more findings. |
| KG retrieval (DefiHackLabs Jaccard) | At N=2 codebases, no measurable contribution above coverage tracker. Cost +90%. |

The coverage tracker was a docs/09 afterthought (priority 14 task,
added during issue cleanup). It turned out to be the load-bearing
component. KG+MCGA, which we built first as the v2 headline, may
or may not contribute on top of coverage. We can't tell at N=2.

## What's still unmeasured

1. **Coverage-only ablation**: turn off MCGA, keep coverage tracker.
   See if the 3 baseline findings change. (We didn't run this cell.)
2. **MCGA-only ablation**: keep MCGA, turn off coverage. Test the
   inverse hypothesis.
3. **Verified-findings differentiator**: at budget=15 across all 4
   runs, 0 verified PoCs. KG/MCGA may help only at higher budget
   when the agent gets to the hard part (writing & repairing PoCs).
4. **Real-world ground truth**: of the 12 candidate findings here,
   how many are actually exploitable? We don't know — Fluid and
   Chainlink contests' final reports aren't public yet.

## What this means for the v2 priority list

Original priorities (in [05](05-v2-blueprint.md)):
1. KG (DefiHackLabs)
2. ~~Split prompt~~ (dropped)
3. MCGA sink tagger
4. Halmos parallel
5. Echidna parallel
6. CPUA ranker

**Updated, evidence-based priorities**:
1. **Coverage tracker** — newly identified as the dominant lift
2. **CPUA ranker** — if file-level orientation works, function-level
   ranking should compound (was prior priority 6)
3. **Verified-finding pipeline** — current bottleneck is candidates
   not converting to verified PoCs
4. KG/MCGA — keep for now, but their incremental value is unproven;
   if N=10 shows no lift, demote
5. Halmos parallel gate — still useful for coverage of fuzz-resistant
   bugs

## Reproduce

```bash
python3 bench/ablation/run_contest_sweep.py --budget 15
# Outputs to bench/ablation/results/contest_sweep/
```

## Honest decision: what does the harness actually do

After this re-measurement:

> The harness *does* lift performance — but the lift comes from
> **giving the agent an explicit attack-surface inventory**, not
> from historical pattern retrieval (KG) or sink tagging (MCGA)
> in isolation. On a 77-file codebase, raw Opus + a short list
> of "here are your in-scope files, ranked by size" finds 3
> candidates where bare-prompt Opus found 0. KG and MCGA add cost
> without measurable additional recall at N=2 codebases.

This is a smaller win than docs/09 implied, but it's real and
reproducible.
