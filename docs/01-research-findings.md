# 01 — Research Findings: Harness vs Raw LLM

Cross-domain empirical evidence collected to answer: *does harness
engineering actually move the benchmark needle vs raw frontier LLMs?*

## SWE-bench (general SE)

| Approach | Score | Cost |
|---|---|---|
| Agentless (3-phase) on Lite | 32.0% (96/300) | $0.70 |
| Raw GPT-4o on Verified | 33.2% | — |
| Anthropic custom harness on SWE-bench | claimed **+10pp** absolute | — |
| SWE-bench Pro (agents) | ≤23.3% public, ≤17.8% commercial | — |

**Insight**: SWE-bench Verified score >70% drops to <25% on Pro → contamination
on Verified is real. Agentless 3-phase beats most agentic harnesses on
contamination-heavy benchmarks. Adopted by OpenAI for o1 demos and DeepSeek for
V3/R1.

## AIxCC Final (DARPA, August 2025) — strongest harness-wins evidence

| | |
|---|---|
| Winner | **Team Atlanta "Atlantis"** ($4M, ≈ 2nd + 3rd combined) |
| 2nd | Trail of Bits "Buttercup" |
| 3rd | Theori (XINT Code) — $1.5M |
| Discovery | 86% of 63 synthetic vulnerabilities |
| Patch | 68% |
| Runtime | 143 hours fully autonomous |
| Eval | 53 challenge projects, 7 finalist teams |

**Key finding**: smaller models (8B-class, GPT-4o-mini) outperformed reasoning
models inside Atlantis. *The harness, not the model, was load-bearing.* This is
the strongest published evidence that scaffolding can outperform raw frontier
models on real CVE discovery.

## A1 exploit agent (Solidity, arXiv 2507.05558)

| | |
|---|---|
| Tools | 6 (source_fetcher, constructor_param, state_reader, sanitizer, concrete_executor, revenue_normalizer) |
| Cost per attempt | $0.01 – $3.59 |
| Iteration marginal gain | +9.7%, +3.7%, +5.1%, +2.8% (iters 2–5) |
| Total experiments | 432 (× 6 LLMs) |

**Insight**: budget cap at 5 iterations — beyond that, marginal gain ≤ 2.8%.

## Smart contract RAG harnesses (point comparisons)

| System | Result |
|---|---|
| **Knowdit** (full) | **81.3% recall, 96.1% precision** on AuditEval |
| Knowdit-NKG (no knowledge graph) | 18.7% recall, 6 FPs |
| LLM-BSCVM | F1 = 91% |
| PropertyGPT | recall 0.80, precision 0.64, F1 = 0.71 |
| LLMAudit | 29.3% on AuditEval |
| GPTScan | 5.3% |
| PromFuzz | 0% |

**Knowledge-graph alone delivers +62.6pp recall.** This is the single most
important ablation finding in the literature for our domain.

## XBOW (web pentest, architectural lesson)

| | |
|---|---|
| Standard challenges | 75% solved autonomously |
| Custom (novel) | 85% |
| Architecture rule | **discovery agent ≠ validation agent** → zero FP claim |

**Insight**: separating who-finds from who-confirms is a reproducible
architectural win, regardless of domain.

## Implications for our work

1. Harness CAN beat raw — AIxCC + Knowdit are decisive evidence in our domain
2. Effective lift comes from **infrastructure** (verifier, oracle, KG) more than
   from the LLM
3. Iteration budget cap = 5
4. Discovery / validation separation is the reproducible architectural win
5. Watch contamination — held-out post-cutoff evaluation is essential

## Citations

See [sources.md](sources.md) for all URLs. Primary sources:
- AIxCC results, Atlantis paper (KAIST/GT/POSTECH/Samsung)
- Knowdit (arXiv 2603.26270)
- A1 (arXiv 2507.05558)
- ReX (arXiv 2508.01371)
- Agentless (arXiv 2407.01489)
- VulnLLM-R, VulX, LLM-BSCVM, SCALM
