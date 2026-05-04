# 02 — Architecture References

Two architectural patterns from the literature drive our v2 redesign:
**Atlantis CRS** (AIxCC winner) and its core agent system **MLLA**.

---

## Atlantis CRS (Team Atlanta, AIxCC Final winner — Aug 2025, $4M)

### Five-system decomposition (NOT monolithic)

| System | Role |
|---|---|
| Atlantis-Multilang | Conservative, no build-time instrumentation |
| Atlantis-C | Aggressive libafl directed fuzzing |
| Atlantis-Java | Language-specific bug-pattern detection |
| Atlantis-Patch | Vulnerability remediation agents |
| Atlantis-SARIF | Report standardization |

### Three orthogonal fuzzers run in parallel
**LibAFL + libFuzzer + AFL++** — independent execution prevents cascading
failures, maximizes coverage variance.

### Three oracle types (verification matters more than discovery)
1. **Hardware oracles** — segfaults via page-table violations
2. **Software oracles** — ASAN / UBSAN / MSAN
3. **PoV-as-oracle** — re-run exploit against patched code

> Our `harness/verify.py` 6-gate is exactly the PoV-as-oracle pattern.

### Three LLM engagement levels (cost gradient)
| Level | Description |
|---|---|
| **LLM-Augmented** | Fills tool gaps (seed/dictionary generation) — cheap |
| **LLM-Opinionated** | Hints with performance penalties for errors — bounded |
| **LLM-Driven** | Full autonomy (only for MLLA-class agents) — expensive |

### Lessons translated to smart contract domain
- Run 3 orthogonal verifiers: **forge test (concrete) + Halmos (symbolic) + Echidna (property fuzz)**
- Separate LLM-Driven (hypothesis gen) from LLM-Augmented (template fill)
- Smaller model often beats reasoning model when harness is strong (Atlantis: 8B-class > o1)
- Defensive against fragile string-matching heuristics — they had a near-disaster from "fuzz" prefix

---

## MLLA — Multi-Language LLM Agent (Atlantis core)

The five-agent system that does discovery inside Atlantis.

| Agent | Role | Input | Output | Smart-contract analog |
|---|---|---|---|---|
| **CGPA** | Call Graph Parser — navigation | codebase | function locations, deps, nav maps | our `recon_pack` ECG ✅ |
| **CPUA** | CP Understanding — entry scout | program harness/entry | ~50 prioritized fns handling untrusted input | entry_points extraction (basic; weak ranking) |
| **MCGA** | Make Call Graph — cartographer | structure + conns | call graphs + flagged high-value sinks | **MISSING — biggest single gap** |
| **BCDA** | Bug Candidate Detection — detective | MCGA hotspots | BITs (bug case files): exploitable conditions + attack sequence | conflated with BGA in v1 |
| **BGA** | Blob Generation — exploit dev | BCDA BITs | Python fuzzer seed generators | our AttackHarness template ✅ |

### Engineering obstacles MLLA solved (and we'll hit)
1. **Cost** — 5 agents × 1000s of LLM calls bankrupts. Solved via prompt cache + aggressive truncation.
2. **Speed/intelligence tradeoff** — async parallelization (LLM latency vs fuzzer speed)
3. **Hallucination** — validation layers + cross-check between agents
4. **Context** — compression to fit "elephant-sized problems into mouse-sized windows"

### What we measured

We hypothesized that splitting BCDA from BGA at the *prompt* level would
deliver MLLA's anti-rationalization benefit. **It did not** — see
[06-experiments.md](06-experiments.md). The verifier loop already
provides that discipline. The MLLA value lies in *separate agent calls
with separate context budgets*, not prompt phasing within one call.

---

## Counter-pattern: when minimal scaffolding wins

- **Agentless** (Xia et al., arXiv 2407.01489) — 3-phase localize→repair→validate with no agentic loop, no tool invention. Beats agentic on SWE-bench Lite at 1/10 cost.
- Adopted by OpenAI for o1 demos and DeepSeek for V3/R1.
- Apr 2026 consensus piece: "general-purpose agent doing unrestricted search matches hand-engineered harnesses" (medium.com/@windead).

**Our resolution**: Agentless wins on contamination-heavy benchmarks; AIxCC pattern wins on real-world tasks. Smart contract auditing is closer to AIxCC.
