# 07 — DeFiHackLabs Knowledge Graph

The single biggest expected lift in our v2 design (Knowdit ablation:
**+62.6pp recall**). Built on the public DeFiHackLabs PoC corpus.

## Corpus

- Source: https://github.com/SunWeb3Sec/DeFiHackLabs
- 79 month-directories: 2017-07 → 2026-03
- Each `*_exp.sol` is a Foundry PoC for a real DeFi exploit
- Headers carry `// @KeyInfo`, `// @Info`, `// @Analysis` metadata
- Loss totals, attacker addresses, vulnerable contract addresses, attack tx, source URLs

## Cutoff-split design

Our model is Claude Opus 4.7 with knowledge cutoff January 2026. To produce
contamination-resistant evaluation:

| Bucket | Date filter | Use |
|---|---|---|
| **KG_TRAIN** | date ≤ 2026-01-31 | indexed into pattern DB; surfaced via retrieval |
| **KG_HELDOUT** | date ≥ 2026-02-01 | reserved for ablation evaluation, never indexed |

Assumption: train-bucket incidents are *already in Opus weights* — KG retrieval is therefore a **recall amplifier**, not a leak. We surface the structural shape to focus the agent's attention, not to teach it new bugs.

## Build run output

```json
{
  "cutoff": "2026-02-01",
  "train_count": 676,
  "holdout_count": 6,
  "holdout_ids": [
    "2026-02__LAXO_Token_exp",
    "2026-02__Moonwell_exp",
    "2026-03__AlkemiEarn_exp",
    "2026-03__Curve_LlamaLend_exp",
    "2026-03__EST_exp",
    "2026-03__Venus_THE_exp"
  ]
}
```

## Files

| File | Purpose |
|---|---|
| `harness/kg/build_index.py` | Parse all `*_exp.sol`, extract metadata + interfaces, emit train/holdout JSON |
| `harness/kg/retrieve.py` | Top-K nearest past incidents by Jaccard over interface symbol set |
| `harness/kg/index.train.json` | 676 train incidents (gitignored, regenerable) |
| `harness/kg/index.holdout.json` | 6 holdout incidents (gitignored, regenerable) |
| `harness/kg/defi-hack-labs/` | Cloned upstream (gitignored) |

## Retrieval API

CLI:
```bash
echo '{"interfaces":["IERC20","IUniswapV2Pair","IFlashLoan"]}' \
  | python3 harness/kg/retrieve.py --top-k 5
```

Library:
```python
from harness.kg.retrieve import retrieve
hits = retrieve({"interfaces": ["IERC20", "IUniswapV2Pair"]}, top_k=5)
# returns list of {score, id, name, date, total_lost_usd, file, shared_interfaces, analysis_urls}
```

## Hard-wired into agent

`bench/ablation/agent.py::_kg_preamble()` — when `HARNESS_KG=1`, runs at agent startup:

1. `grep -rho '\bI[A-Z]\w+\b' --include='*.sol' <project>` — extract interface symbols
2. `retrieve.py top_k=5`
3. JSON result prepended to the user message as a "KG retrieval (hard-injected)" block, instructing the agent to treat each hit as a candidate hypothesis seed

This bypasses the optional-suggestion problem we hit in our [first KG run](06-experiments.md#3-laxo-holdout-post-cutoff-defihacklabs-case).

## Holdout retrieval quality (leave-one-out check)

For each post-cutoff incident, query KG with its own interface set and inspect the top-3 train-set neighbors:

| Holdout | Top-1 score | Top-1 match | Comment |
|---|---|---|---|
| LAXO_Token | 0.75 | GSS (2023-08), KRCToken_pair (2025-05), Qixi (2022-08) | strong — BSC fee-on-transfer cohort |
| Moonwell | 0.33 | Pickle, Cover, dodo_flashloan | weak — 1 shared interface only |
| AlkemiEarn | 0.67 | Bazaar (2024-06) | medium |
| Curve_LlamaLend | 0.40 | GradientMakerPool (2025-06) | medium |
| EST | 0.38 | BEVO, FiberRouter | weak-medium |
| Venus_THE | 0.13 | makina (2026-01) | very weak |

Quality is variable. Future improvements:
- Add storage-layout features (not just interfaces)
- Add entry-point class tags (external/payable/onlyOwner)
- Embedding-based similarity (would require a local model — defer)

## Regenerate

```bash
git clone --depth 1 https://github.com/SunWeb3Sec/DeFiHackLabs.git harness/kg/defi-hack-labs
python3 harness/kg/build_index.py
```
