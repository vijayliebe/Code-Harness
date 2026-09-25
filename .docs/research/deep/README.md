# Deep dives — HIGH sources

First-pass catalog: [../INDEX.md](../INDEX.md) · synthesis: [../SYNTHESIS.md](../SYNTHESIS.md) · **fusion pack:** [../fusion/README.md](../fusion/README.md).

These notes go one level deeper than the per-source cards. Each one maps a **HIGH** research item onto Code-Harness files (`harness/*.py`, `main.py` CLI) and proposes a **minimal, reversible** design — not a rewrite.

**Spine status:** the [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) first slice is **implemented** on the KG-enrichment line (`eval → CCR-lite → query loop → KG enrichment`). This directory remains the *design* record for those PRs plus the still-open HIGH items. What to steal next is the [fusion pack](../fusion/README.md) (matrix + gap audit), not a second copy of PRs 1–4.

**Sequenced PRs** live in [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md). Shipped first slice:

`eval metrics → CCR-lite packer → query loop → KG enrichment`

## Notes

| # | Source | Deep dive | Seam | Fusion |
|---|--------|-----------|------|--------|
| 1 | Headroom | [headroom.md](headroom.md) | `context_builder.py` post-MMR packer + local chunk cache | **Partially fused** (CCR-lite) |
| 3 | Strands Harness | [strands-harness.md](strands-harness.md) | interactive session policy; later MCP/tool packaging | **Partially fused** (loop policy only) |
| 5 | Memanto | [memanto.md](memanto.md) | typed project memory beside the code index | **Design-only** |
| 13 | ForgeCode | [forgecode.md](forgecode.md) | read-only “sage” query profile, compact, knobs | **Partially fused** (pack knob) |
| 14 | Code graph | [code-graph.md](code-graph.md) | `knowledge_graph.py` edges, gloss, beam expand | **Partially fused** (PR 4 slice) |
| 15 | TurboVec | [turbovec.md](turbovec.md) | optional `vector_store` backend after recall eval | **Design-only** |
| 16 | OKF (Google SPEC) | [okf.md](okf.md) | portable wiki/memory interchange (not a Memanto format) | **Partially fused** (WikiPage emit) |
| 18 | Prompt→Loop→Graph | [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md) | graded retrieve loop in `retriever.py` / `main.py` | **Partially fused** (eval + loop) |
| 23 | Google Code Wiki | [google-code-wiki.md](google-code-wiki.md) | incremental `wiki generate` + cite `path:symbol` | **Partially fused** (`wiki generate` + `--dirty` + opt-in wiki RRF) |

No deep dive yet for **#24 Claude Code harness** or **#25 DeepSeek Harness** — first-pass cards plus matrix rows are the record (steal wave shipped; remainder `llm-retry`). Do not vendor Cordis / Claude Code.

## How to read

1. For **what to build next**, start at [../fusion/GAP_AUDIT.md](../fusion/GAP_AUDIT.md) (spine + fusion 1–5 + post-polish steal wave done).
2. [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) is the design record for the shipped spine and the original “later #5–8” list.
3. Open the deep dive that owns the next fusion PR; keep the matching first-pass card for links and caveats.

## Corrections vs first pass

- **OKF ownership:** Open Knowledge Format is a **Google Cloud specification** (`GoogleCloudPlatform/open-knowledge-format`, SPEC v0.2). Memanto is an *implementer* (export/import), not the owner. See [okf.md](okf.md) and the updated [../okf.md](../okf.md).
