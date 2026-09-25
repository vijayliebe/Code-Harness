# Fusion pack — steal matrix + gap audit

This directory is the **map** from researched sources to a hybrid code-RAG that can compete on Recall@k, prompt tokens, latency, and local-first ops. It does **not** claim best-of-kind is achieved.

| File | Purpose |
|------|---------|
| [FUSION_THESIS.md](FUSION_THESIS.md) | What “best of its kind” means for *this* product + scorecard |
| [STEAL_MATRIX.md](STEAL_MATRIX.md) | Every INDEX resource → stealable idea, module, status, priority |
| [GAP_AUDIT.md](GAP_AUDIT.md) | Open gaps only, ranked; recommended next 5 PRs |
| [notes/](notes/) | Mini-deepens for thin Medium / weak-primary cards |

Spine already shipped on `cursor/kg-enrichment-3590` (and ancestors):

`eval metrics → CCR-lite packer → corrective query loop → KG enrichment (exposes/tested_by/gloss + beam + Mermaid)`

Fusion PR #1 (wiki generate MVP) adds `wiki generate|list|show` — OKF `WikiPage` markdown under `knowledge/wiki/`, `path:symbol` cites, Mermaid from the KG. Typed memory / RRF wiki boost / watch regen are still open.

Read [../INDEX.md](../INDEX.md) → this pack → [../deep/INTEGRATION_PLAN.md](../deep/INTEGRATION_PLAN.md) for the original sequence.
