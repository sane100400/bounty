# Sources

All URLs cited across the research, organized by topic.

## Smart contract harnesses (most directly relevant)

- [Knowdit — arXiv 2603.26270](https://arxiv.org/html/2603.26270) — current SOTA on AuditEval, 81.3% recall
- [A1 — arXiv 2507.05558](https://arxiv.org/abs/2507.05558) — 6-tool Solidity exploit agent, iter ablation
- [ReX — arXiv 2508.01371](https://arxiv.org/abs/2508.01371) — Foundry-grounded exploit gen, 5 LLMs × 8 vuln classes
- [PropertyGPT — arXiv 2405.02580](https://arxiv.org/pdf/2405.02580) — formal verification baseline (recall 0.80)
- [LLM-BSCVM — arXiv 2505.17416](https://arxiv.org/html/2505.17416v1) — RAG + static + LLM hybrid, F1=91%
- [LLMBugScanner — arXiv 2512.02069](https://arxiv.org/html/2512.02069v1) — ensemble voting
- [SCALM (AAAI 2026)](https://ojs.aaai.org/index.php/AAAI/article/view/32026/34181) — RAG + Step-Back prompting
- [Halmos](https://github.com/a16z/halmos) + [v0.3.0 release notes](https://a16zcrypto.com/posts/article/halmos-v0-3-0-release-highlights/) — symbolic testing, stateful invariants
- [DeFiHackLabs](https://github.com/SunWeb3Sec/DeFiHackLabs) — our KG corpus

## AIxCC and Atlantis

- [DARPA AIxCC results](https://www.darpa.mil/news/2025/aixcc-results) — competition outcome
- [Team Atlanta blog post](https://team-atlanta.github.io/blog/post-afc/) — Atlantis CRS architecture
- [MLLA overview](https://team-atlanta.github.io/blog/post-mlla-overview/) — 5-agent system
- [Atlantis paper (KAIST/GT/POSTECH/Samsung)](https://taesoo.kim/pubs/2025/kim:atlantis.pdf)
- [Trail of Bits — Buttercup 2nd place](https://blog.trailofbits.com/2025/08/09/trail-of-bits-buttercup-wins-2nd-place-in-aixcc-challenge/)
- [SoK: AIxCC architectures (arXiv 2602.07666)](https://arxiv.org/abs/2602.07666)

## General SE harnesses (context)

- [Agentless — arXiv 2407.01489](https://arxiv.org/abs/2407.01489) — 3-phase, beats agentic on SWE-bench Lite
- [SWE-bench Pro](https://static.scale.com/uploads/654197dc94d34f66c0f5184e/SWEAP_Eval_Scale%20(9).pdf) — contamination-resistant SWE-bench
- [SWE-bench Verified (OpenAI)](https://openai.com/index/introducing-swe-bench-verified/)

## Cybersecurity agents (broader context)

- [XBOW — discovery vs validation architecture](https://xbow.com/blog/we-ran-1060-autonomous-attacks)
- [XBOW HackerOne #1](https://xbow.com/blog/top-1-how-xbow-did-it)
- [VulnLLM-R](https://www.emergentmind.com/topics/vulnllm-r) — reasoning chain ablation
- [VulX](https://www.sciencedirect.com/science/article/abs/pii/S0957417425037212) — agent-based verification

## Tools / infra used in our v2

- [Sourcify](https://sourcify.dev) — source fetcher (no API key)
- [Foundry forge clone](https://book.getfoundry.sh/reference/forge/forge-clone) — Etherscan-verified clone
- [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code) — `claude -p` subprocess agent path
