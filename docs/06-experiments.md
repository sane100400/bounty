# 06 — In-House Experiments Log

Our own measurements with the v1 harness. Three side-by-side comparisons:

1. Single-bug smoke test (v1 vs split prompt)
2. Multi-bug synthetic case (v1 vs split prompt) — **drove the H2 drop decision**
3. LAXO holdout (v1 vs +KG prompt section) — **uncovered KG wiring gap**

---

## 1) Smoke test — synthetic vault, 1 bug (donation)

| | v1 single prompt | v2 split prompt |
|---|---|---|
| Cost | $0.428 | $0.896 (**+109%**) |
| Wall | 54s | 64s |
| Verified findings | 1 | 1 |
| Hypothesis quality | basic, `min_profit_wei = "0"` | structured, `min_profit_wei = "1e19"` (10 ETH realistic) |

**Verdict**: degenerate — 1-bug case can't measure split's anti-rationalization claim. Cost up, recall flat.

---

## 2) Multi-bug — `MultiBugVault.sol` (1 real bug + 1 plausible-but-not + 1 decoy)

Construct: real donation bug, plausible-looking yield-rounding (actually impossible to exploit), decoy reentrancy (CEI preserved by modifier order).

| | v1 single | v2 split |
|---|---|---|
| Cost | $0.96 | $1.15 (**+19%**) |
| Wall | 213s | 158s (-26%) |
| Real bugs found | 1/1 donation | 1/1 donation |
| False-lead handling | proposed yield, **self-rejected with algebraic proof** (score=0.1, status=dropped) | never proposed |
| Decoy reentrancy | correctly skipped | correctly skipped |

**v1's self-rejection chain** (verbatim):
> "sum_i floor((dt_i·S·P)/1e9) ≤ floor((T·S·P)/1e9) always — splitting can never extract more. Real artifact is user-side dust LOSS not protocol drain — does not satisfy attacker_token_increased class invariant."

**Verdict**: split prompt is a **regression**. The verifier loop's `class_invariant` requirement already provides the anti-rationalization discipline H2 was meant to add. Split adds cost without recall lift.

**Decision**: drop H2 from v2 priority. Reroute attention to KG (H1) and MCGA (H3). Keep `agent_prompt_split.md` in repo as documented dead-end.

---

## 3) LAXO holdout (post-cutoff DeFiHackLabs case)

**Ground truth**: LAXO_Token `_transfer` calls `pair.sync()` after burn — flash-loan attacker manipulates LP price for $137K BUSD. *Disclosed 2026-02, post Opus 4.7 cutoff.*

Source materialized via Sourcify (full match on BSC).

| | V1 NO-KG | V2 +KG-prompt-section |
|---|---|---|
| Cost | $1.43 | $1.17 (-18%) |
| Wall | 331s | 268s (-19%) |
| Turns | 15 | 13 |
| Findings (candidates) | 3 | 2 |
| **KG retrieval calls** | n/a | **0** |
| Closest to ground truth | finding #3 oracle_manipulation @ `_transfer` | finding #1 invariant_break_other @ `_transfer` |
| Verified PoCs | 0 | 0 (budget=3 too tight for 14-file case) |

**The gotcha**: the `{{IF HARNESS_KG}}` block in `agent_prompt.md` *suggested* running `python3 harness/kg/retrieve.py` but didn't enforce it. Single-call CLI agent prioritized direct source reading and never executed the suggested shell pipeline.

**Fix applied**: KG retrieval now hard-wired in `agent.py` — at startup, the harness extracts interfaces from project source and prepends top-5 retrieval results to the user message. See [07-defihacklabs-kg.md](07-defihacklabs-kg.md).

**Surprising secondary finding**: even without KG calls, V2 was cheaper + faster + arguably more focused. Possible explanation: the prompt section nudged "pattern-matching" reasoning style. N=1, can't claim.

**Next**: re-run with hard-wired KG on multiple holdout cases to get a real lift number.

---

## 4) LAXO holdout — FULL v2 stack (KG + MCGA hard-wired)

After hard-wiring both KG and MCGA into `agent.py` preamble:

| | V1 NO-KG | V2 KG-prompt-only | **V3 KG+MCGA hard-wired** |
|---|---|---|---|
| Cost | $1.43 | $1.17 | $1.33 |
| Wall | 331s | 268s | **296s** |
| Turns | 15 | 13 | 14 |
| Findings (candidates) | 3 | 2 | **3** |
| KG retrieval injected | n/a | 0 (ignored) | ✅ 5 incidents (Sushi_Badger_Digg top-1) |
| MCGA injection | n/a | n/a | ✅ top-10 ext + top-10 internal sinks |
| Agent cited KG IDs in rationale | n/a | n/a | ✅ "KG seed: Sushi_Badger_Digg confirms direct-donation manipulation of pair invariants is a recurring pattern" |

**Key change in finding quality** (V3 finding #3 verbatim):
> "KG retrieval did not return a directly analogous fee-rounding incident, so this is a code-only finding with no historical anchor."

The agent now *self-calibrates confidence based on KG support*. Without
KG, every hypothesis looks equally novel; with KG, the agent
distinguishes "we've seen this exact pattern hit production for $X" from
"speculative new code-only finding." That distinction is the entire
point of the harness.

**Closest to ground truth**: V3 finding #2 (`invariant_break_other` @
`_transfer`) — matches the actual `_transfer → pair.sync()` lp_sync
exploit. MCGA had explicitly flagged `_transfer` in `top_internal_callees`
with `lp_sync` sink class, which the agent picked up.

**Caveat**: verify.py 6-gate did not run (the Sourcify-extracted source
is not a Foundry project — no `foundry.toml`). To complete the loop,
SCONE-mode cases need a project scaffolder that writes a minimal
`foundry.toml` + remappings around fetched source. Tracked as next-step
build item.

---

## Reproduce these experiments

```bash
# Smoke (1-bug case)
python3 bench/ablation/agent.py poc-forge --case-id smoke_v1 --budget 2
HARNESS_SPLIT=1 python3 bench/ablation/agent.py poc-forge --case-id smoke_v2 --budget 2

# Multi-bug
python3 bench/ablation/agent.py poc-forge --case-id multi_v1 --budget 3
HARNESS_SPLIT=1 python3 bench/ablation/agent.py poc-forge --case-id multi_v2 --budget 3

# LAXO holdout (after fetching source)
python3 harness/tools/source_fetcher.py bsc 0x62951CaD7659393BF07fbe790cF898A3B6d317CB /tmp/laxo
mkdir -p /tmp/laxo_case/src && cp -r /tmp/laxo/contracts/full_match/56/0x*/sources/* /tmp/laxo_case/src/
python3 bench/ablation/agent.py /tmp/laxo_case --case-id laxo_v1 --budget 3
HARNESS_KG=1 python3 bench/ablation/agent.py /tmp/laxo_case --case-id laxo_v2 --budget 3
```
