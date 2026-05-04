# 04 — Smart Contract Harness Ceiling

What is the realistic upper bound for our v2 design? Numbers from
Knowdit, ReX, Halmos, plus the cross-domain realism gap.

## Knowdit (Mar 2026, arXiv 2603.26270) — current SOTA on AuditEval

**AuditEval**: 12 projects, 75 vulns (14 High + 61 Medium)

| System | High | Medium | Total recall | FPs |
|---|---|---|---|---|
| **Knowdit** (full) | 14/14 (100%) | 47/61 (77%) | **61/75 (81.3%)** | **2** |
| Knowdit-NKG (no knowledge graph) | 3/14 | 11/61 | 14/75 (18.7%) | 6 |
| LLMAudit | 4/14 | 18/61 | 22/75 (29.3%) | 3 |
| PropertyGPT | 0/14 | 6/61 | 6/75 (8.0%) | 4 |
| GPTScan | 0/14 | 4/61 | 4/75 (5.3%) | 5 |
| PromFuzz | 0/14 | 0/61 | 0/75 (0%) | 0 |

- **Knowledge graph alone** delivers **+62.6pp recall** while halving FPs.
- Real-world deployment: 6 projects → 12 H + 10 M, **0 FPs**, all confirmed by devs.
- Implementation: 10k LOC Rust, Foundry as fuzzing engine, GPT-5.1 primary + GPT-5-mini for synthesis.

## ReX (Aug 2025, arXiv 2508.01371) — exploit generation

**Bench**: 38+ real PoCs, 8 vuln classes, 5 LLMs

| LLM | Overall success |
|---|---|
| Gemini 2.5 Pro | **67.3%** |
| Claude Opus 4 | 63.3% |
| GPT-4.1 | 58.1% |
| DeepSeek-R1 | 48.3% |
| Qwen3-Plus | 28.8% |

### Per-vulnerability class (Claude Opus 4)
| Class | Success |
|---|---|
| DoS | 100% |
| Arithmetic | 85.7% |
| Time Manipulation | 80% |
| Reentrancy | 63.3% |
| Bad Randomness | 57.1% |
| Access Control | 55.6% |
| Low-Level Calls | 40% |
| Front Running | 25% |

### Iterative repair lift
| Class | 1 turn | Max 4 turns |
|---|---|---|
| Reentrancy | 33% | 71% |
| Access Control | 28% | 65% |
| Arithmetic | 42% | 59% |
| DoS | 18% | 44% |
| Time Manipulation | 12% | 39% |
| **Overall** | **27%** | **58%** |

### Real-world brutal gap
**Web3-AEG (38 real contracts)**: best model = Gemini **4/38 (10.5%)**.
GPT-4.1 and Claude Opus 4 each succeeded **once (1/38)**.
Synthetic 67.3% → real 10.5% = **realism gap of −57pp**.
Cross-contract attacks: weak across all models.

## Halmos v0.3.0 (a16z, 2025–2026)

- Stateful invariant testing: auto-find target/fn, explore states, assert invariants
- 32× faster EVM interpreter (lift on time-bounded budgets)
- lcov coverage output
- New cheatcodes for env manipulation
- solx compiler support

*No published recall numbers; complementary to fuzz-based oracles.*

## Implications for our v2 ceiling

Stacking knowledge-graph (Knowdit-style) + Foundry verifier + iterative repair (ReX-style) + Halmos invariants + Slither dup:

| Setting | Realistic ceiling |
|---|---|
| **AuditEval-like held-out** | 80–85% recall, 95% precision |
| **Web3-AEG-like real-world** | 10–20% (per ReX gap) |
| **Per bug class (synthetic)** | Reentrancy ~63%, Arithmetic ~93%, Cross-contract ~5% |

## Gap to ceiling — where v1 underperforms

1. **Knowledge graph** — biggest single gap; +62pp recall potential — implemented in [07](07-defihacklabs-kg.md)
2. **Iterative repair loop with compiler feedback** — +31pp from ReX ablation — verify.py exists but loop is not aggressive
3. ~~MLLA-style BCDA/BGA agent split~~ — VulX +15–36% F1 — but our prompt-level test regressed. May still be worth trying as separate agent calls (more expensive).
4. **Halmos invariant adapter** — coverage of bugs fuzzing misses
5. **Cross-contract reasoning** — universal weakness (no SOTA above 30%)
