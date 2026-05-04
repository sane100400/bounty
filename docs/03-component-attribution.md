# 03 — Component Lift Attribution

Per-component contribution to harness lift over raw LLM, drawn from
ablation studies across 6+ recent papers.

## What contributes how much

| Component | Lift | Source |
|---|---|---|
| **Knowledge graph / RAG over past incidents** | **+62.6pp recall**, FPs halved | Knowdit ablation (Knowdit-NKG) |
| **Iterative repair loop with PoV oracle** | **+31pp** absolute (×2.15) | ReX (1-turn 27% → max-4-turn 58%) |
| Multi-agent step-by-step verification | +15.3% to +36.5% F1 | VulX |
| Reasoning chain (vs single-shot) | +0.08–0.09 F1, +37% on OOD CWE | VulnLLM-R |
| Static analysis integration (Slither-class) | "Significant" F1/acc decline when removed | LLM-BSCVM |
| Iteration budget (cumulative iters 2–5) | +21.3% (9.7 + 3.7 + 5.1 + 2.8) | A1 |
| Dual-teacher distillation (vs single) | +3–5 F1 | VulnLLM-R |
| Chain-length / constitution filter | +4–6 F1 | VulnLLM-R |
| Ensemble voting (LLMBugScanner) | "Consistent improvements" over single-model | LLMBugScanner |
| PoV-as-oracle (verifier loop) | enables zero-FP claim | XBOW, Atlantis |
| Adapter-predicted budget allocation | reduces token cost, maintains acc | Sonata |

## Super-additivity evidence

Atlantis stacked verifier + multi-agent + parallel fuzzers + small-model-with-strong-harness → **86% vuln discovery, 68% patch in AIxCC**. None of the individual lifts above is that big — the gains compound.

## What does NOT lift

- Reasoning model (o1/o3) over GPT-4o-mini *when harness is strong* — Atlantis used 8B-class
- Hand-engineered heuristics that string-match on filenames (near-disaster from "fuzz" prefix)
- Complex agentic loops on contamination-heavy benchmarks (Agentless beats them on SWE-bench)
- **Prompt-level BCDA/BGA splitting** — measured by us as a regression on multi-bug case (+19% cost, 0 recall lift). The verifier loop's class_invariant requirement already provides the anti-rationalization discipline. See [06-experiments.md](06-experiments.md).

## Allocation rule for v2

1. Verifier loop (PoV oracle) — non-negotiable
2. RAG / KG over DeFiHackLabs — non-negotiable for recall (Knowdit ablation)
3. Static-analysis dup-check — non-negotiable for FP control
4. Iter budget cap = 5 (A1 diminishing returns)
5. **Skip**: prompt-split (regression measured), reasoning-model premium, ensemble ranking until #1–4 are tight
