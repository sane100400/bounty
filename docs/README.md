# Harness Engineering Research

**Question**: Can a smart-contract security harness beat raw Claude Opus on
benchmarks? If so, how do we push performance further?

**Answer (short)**: Yes — literature ceiling is **81% recall at 96% precision**
(Knowdit on AuditEval) vs raw LLM baselines of 18–29%. Lift comes from
**verifier loop + knowledge graph + iterative repair**, not from prompt-level
multi-agent splitting (which we measured as a regression).

---

## Index

| Doc | What's in it |
|---|---|
| [01-research-findings.md](01-research-findings.md) | Empirical evidence: AIxCC, Knowdit, ReX, A1, SWE-bench. Cross-domain harness-vs-raw lift numbers. |
| [02-architecture.md](02-architecture.md) | Atlantis CRS (AIxCC winner) and MLLA 5-agent pattern. Translation to smart contract domain. |
| [03-component-attribution.md](03-component-attribution.md) | Per-component lift % from 6 ablation studies. What we should and should not build. |
| [04-smart-contract-ceiling.md](04-smart-contract-ceiling.md) | Knowdit/ReX/Halmos numbers. Theoretical ceiling for our v2 design. |
| [05-v2-blueprint.md](05-v2-blueprint.md) | Component diagram + 6 measurable hypotheses (H1–H6) + DeFiHackLabs KG plan. |
| [06-experiments.md](06-experiments.md) | Our own measurements: split prompt (regression, dropped), KG first-run on LAXO holdout. |
| [07-defihacklabs-kg.md](07-defihacklabs-kg.md) | KG implementation: 682 incidents indexed, cutoff-split, retrieval API. |
| [08-holdout-sweep.md](08-holdout-sweep.md) | First multi-case KG-lift measurement: **N=3, lift = 0**. Honest negative data; budget too tight + N too small. |
| [09-budget-and-codebase-effects.md](09-budget-and-codebase-effects.md) | **Lift seen** at budget=15 on Fluid DEX (baseline 0 → full v2 3). LAXO full v2 verified the real-world exploit. *Subsequently retracted* — see 10. |
| [10-fix-rerun-coverage-tracker-wins.md](10-fix-rerun-coverage-tracker-wins.md) | After 3 fixes (leak channel, force-budget, coverage tracker): re-ran Fluid + added Chainlink. **Baseline now also finds 3 findings.** The docs/09 lift was orientation effect, not KG/MCGA. **Coverage tracker is the real load-bearing component**; KG/MCGA value at N=2 is unproven. |
| [sources.md](sources.md) | All URLs cited across the research. |

---

## TL;DR table

| Benchmark | Raw LLM | Best harnessed system | Lift |
|---|---|---|---|
| AuditEval (Knowdit) | 18–29% recall | **81% recall, 96% prec** | +52pp |
| AIxCC Final | n/a | 86% vuln discovery, 68% patch (Team Atlanta with 8B-class model) | dominant |
| ReX synthetic | 28.8% (Qwen) – 67.3% (Gemini) | 27% → 58% with iter-repair | +31pp |
| ReX real-world (Web3-AEG) | 0–10.5% | same — **realism gap is brutal** | minimal |
| SWE-bench Lite | n/a | Agentless 32% @ $0.70 | beats all agentic |

---

## What lifts performance (ranked, from ablations)

1. **Knowledge graph / RAG over past incidents** — Knowdit ablation: **+62.6pp recall**
2. **Iterative repair loop with PoV oracle** — ReX: **+31pp**, ×2.15
3. **Multi-agent step-by-step verification** — VulX: +15–36% F1
4. **Static-analysis dup gate** — LLM-BSCVM: significant F1
5. **Reasoning chain enforcement** — VulnLLM-R: +0.08 F1, +37% OOD

## What does NOT lift (or regresses)

- Reasoning model > smaller model when harness is strong (Atlantis used 8B-class)
- **Prompt-level BCDA/BGA split** — we measured this on multi-bug case, +19% cost, 0 recall lift. Verifier loop already enforces anti-rationalization.
- Hand-engineered string heuristics — Atlantis nearly lost from "fuzz" prefix
- Iterating past turn 5 — A1 diminishing returns
- Ensemble voting (LLMBugScanner-style) until #1–3 are tight

---

## Status of our v2 build

| Component | Status | Doc |
|---|---|---|
| Verifier loop (6-gate, Foundry-grounded) | ✅ working | [05](05-v2-blueprint.md) |
| `claude -p` subprocess agent path | ✅ working ($0.43/case smoke) | [06](06-experiments.md) |
| Source-fetcher (Sourcify + forge clone) | ✅ working (USDC, LAXO confirmed) | [05](05-v2-blueprint.md) |
| DeFiHackLabs KG indexer + retrieval | ✅ 682 incidents indexed, 6 holdout | [07](07-defihacklabs-kg.md) |
| KG hard-wired into agent system prompt | ✅ implemented (`HARNESS_KG=1`) | [07](07-defihacklabs-kg.md) |
| Harness lift on big codebase | ✅ Coverage tracker brings baseline 0 → 3 findings on Fluid DEX. KG/MCGA incremental value unproven at N=2 (3 = 3 with coverage tracker). See [10](10-fix-rerun-coverage-tracker-wins.md) | |
| MCGA sink-tagger | ✅ built (`harness/mcga_sinks.py`), wired into recon_pack + agent.py preamble | [05](05-v2-blueprint.md) |
| Halmos parallel gate | ✅ verify.py gate exists; halmos installed; template at `harness/templates/HalmosProperty.t.sol.tmpl`; smoke test passing | [05](05-v2-blueprint.md) |
| BCDA/BGA prompt split | ❌ **dropped** (measured regression) | [06](06-experiments.md) |
