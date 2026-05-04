# Harness v2 — design blueprint

Synthesis of literature ablations into a concrete redesign with measurable
hypotheses. Each component is justified by a published lift number; build
order follows expected lift × engineering cost.

---

## TL;DR

**Will harness engineering beat raw Opus on our benchmark?**
Yes — on real Solidity audit tasks, the literature shows harness lifts of
+30 to +60 percentage points absolute. Raw Opus 4 alone scores 63.3% on
ReX synthetic (pre-cutoff bias likely) but only ~3% on real-world Web3-AEG.
Knowdit (full harness) hits 81.3% recall at 96.1% precision on AuditEval —
that is the operational ceiling this design targets.

**Where the lift comes from** (ranked by reproducible ablation):
1. Knowledge graph / RAG over audit reports — Knowdit ablation: **+62.6pp recall**
2. Iterative repair loop (compiler/PoV feedback) — ReX: **+31pp** (×2.15)
3. Multi-agent split (BCDA + BGA min) — VulX: +15–36% F1
4. Static-analysis dup gate — LLM-BSCVM: significant F1
5. Reasoning chain enforcement — VulnLLM-R: +0.08 F1, +37% OOD

**Where lift does NOT come from**:
- Reasoning model > smaller model (Atlantis used 8B-class and won AIxCC)
- Hand-engineered string heuristics (near-disasters historically)
- Iterating beyond turn 5 (A1 diminishing returns)

---

## Component diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE GRAPH (NEW)                        │
│  audit-report corpus → DeFi pattern DB → invariant library      │
│  preserves cutoff discipline (Knowdit: data ≤ 2024-09)          │
└────────────────┬────────────────────────────────────────────────┘
                 │ retrieves
┌────────────────▼────────────────────────────────────────────────┐
│           CGPA — Call Graph Parser (existing recon_pack)        │
│  output: ECG + storage layout + entry points                    │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│           CPUA — Entry-Point Scout                              │
│  ranks ~50 fns handling untrusted input by sink-distance        │
│  STATUS: weak — currently dumps all 124 entry_points unranked   │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│           MCGA — Sink Tagger                                    │
│  marks: external_call, balance_write, oracle_read, delegatecall │
│  STATUS: missing — biggest single gap                           │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│           BCDA — Bug Candidate Detector  (NEW, split from BGA)  │
│  output: hypothesis JSON with class_invariant + preconditions   │
│  cross-checks against KG patterns (FP filter)                   │
│  STATUS: missing — currently merged with BGA                    │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│           BGA — Exploit Body Generator                          │
│  emits ATTACK_BODY into AttackHarness.t.sol.tmpl                │
│  STATUS: have basic version                                     │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│       VERIFY GATES (parallel, orthogonal — Atlantis pattern)    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐      │
│  │ forge    │  │ Halmos   │  │ Echidna  │  │ Slither dup │      │
│  │ concrete │  │ symbolic │  │ property │  │   gate      │      │
│  └──────────┘  └──────────┘  └──────────┘  └─────────────┘      │
│       │            │             │              │               │
│       └────────────┴─────────────┴──────────────┘               │
│                       feedback ↑                                │
└────────────────┬────────────────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────────────────┐
│  REPAIR LOOP — max 5 iters (A1 diminishing returns)             │
│  on fail: send full gate-failure JSON + last patch back to BGA  │
│  ReX shows +31pp lift from this loop alone                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Build priority

| # | Component | Expected lift | Eng effort | Build order |
|---|---|---|---|---|
| 1 | KG built from **DeFiHackLabs** (cutoff-split) — see §KG below | +60pp recall | medium | **first** |
| 2 | BCDA/BGA split with hypothesis schema enforcement | +15-30% F1 | low | **second** (cheapest big win) |
| 3 | MCGA sink tagger | +10-20% recall | medium | third |
| 4 | Halmos parallel gate | +5-15% (catches what fuzz misses) | medium | fourth |
| 5 | Echidna parallel gate | +5-10% | medium | fifth |
| 6 | CPUA ranker | +5% (cost reduction more than recall) | low | sixth |

**Skip / not yet**: ensemble voting (LLMBugScanner-style) — wait until #1-3 are tight; cross-contract reasoning specialization — universal SOTA gap, not solvable with current models.

---

## Measurable hypotheses (for ablation cells)

For each cell vs A0-vanilla baseline on our held-out:

- H1 (KG): adding knowledge graph lifts recall by ≥30pp at FP-cost ≤1.5×
- H2 (BCDA split): splitting hypothesis-from-PoC lifts F1 by ≥10pp
- H3 (MCGA): sink tagging lifts top-5 hypothesis precision by ≥20pp
- H4 (Halmos parallel): Halmos catches ≥1 bug per 10 cases that forge misses
- H5 (Repair loop): max-5-iter repair loop lifts verified-finding rate by ≥20pp vs single-shot
- H6 (Cost discipline): full v2 stack stays under $10 per case at Opus 4.7 prices

H6 is the killer constraint — the AIxCC lesson is that *cheap models with strong harness > expensive models with weak harness*. If our harness needs Opus to work, we've built it wrong.

---

## Anti-patterns (what we will not do)

- **String-matching filename heuristics** — Atlantis nearly lost from "fuzz" prefix
- **Iterating past turn 5** — A1: marginal gain ≤ 2.8% by iter 5
- **Single-LLM ranking without dup-check** — ReX shows model variance ±38pp (Gemini vs Qwen)
- **Reporting findings without PoV oracle** — XBOW: discovery ≠ validation
- **Optimizing for AuditEval/SCONE leaderboard alone** — Verified-vs-Pro gap (SWE-bench) shows leaderboard contamination is real

---

## §KG — DeFiHackLabs Knowledge Graph (decided)

**Corpus source**: github.com/SunWeb3Sec/DeFiHackLabs — 400+ real DeFi
incidents with Foundry PoCs, dated incident-by-incident.

**Cutoff-split**:
- `KG_TRAIN`: incidents with date ≤ 2026-01-31 → indexed into pattern DB
  (assumption: already in Opus training corpus, so KG retrieval ≠
  contamination signal — it's a *recall amplifier*, not a leak)
- `KG_HELDOUT`: incidents with date ≥ 2026-02-01 → reserved for ablation
  evaluation, never seen by KG indexer

**What gets indexed** (NOT raw report text — that's already in Opus weights):
- Vuln class label (15-20 categories: donation, oracle-stale, reentrancy-cross,
  collateral-rounding, ...)
- Affected primitive shape (e.g. "ERC4626 first-depositor", "AMM K invariant
  on rebase token", "Chainlink stale price + L2 sequencer")
- Class-invariant template (mapped to our existing AttackInvariants.sol 14)
- Foundry PoC structural skeleton (deduplicated — vault-donation appears in
  ~30 incidents, indexed once)

**Transfer evaluation**: EVMBench (117 vulns / 40 audits) is held *outside* the
KG. Running v2 (KG-DefiHackLabs) on EVMBench measures whether DeFi-pattern
knowledge transfers to general audit findings. This is the strongest test of
"harness lift is real and not just memorization."

**Implementation outline**:
1. Clone DeFiHackLabs, parse `incidents/<YYYY-MM-DD>/` dirs by date
2. Extract per-incident: PoC.sol, README.md (vuln class), affected
   contracts list
3. Embed (PoC AST + README first paragraph) → vector store (e.g. chromadb
   local; no external API)
4. Retrieval API: given target contract's storage layout + entry points,
   return top-K matching pattern templates
5. Inject top-K into BCDA system prompt as "candidate hypotheses to verify
   or rule out"

## Open questions (next research iter)

1. ~~How to build the KG without contaminating Opus knowledge?~~ → DECIDED: cutoff-split DeFiHackLabs as above
2. Halmos integration: which invariants from our 14-template AttackInvariants.sol are symbolic-tractable?
3. What's the Web3-AEG-equivalent held-out we can build (ReX dropped from 67% synthetic to 10% real)?
4. Sonata-style budget allocation: worth it at our scale?

---

## Citations

- Knowdit — arXiv 2603.26270
- ReX — arXiv 2508.01371
- A1 — arXiv 2507.05558
- Atlantis CRS / MLLA — team-atlanta.github.io
- Agentless — arXiv 2407.01489
- VulnLLM-R, VulX, LLM-BSCVM, SCALM — see memory references
