# Deep dives — HIGH sources

First-pass catalog: [../INDEX.md](../INDEX.md) · synthesis: [../SYNTHESIS.md](../SYNTHESIS.md).

These notes go one level deeper than the per-source cards. Each one maps a **HIGH** research item onto Code-Harness files (`harness/*.py`, `main.py` CLI) and proposes a **minimal, reversible** design — not a rewrite. Nothing here is implemented; this directory is the design backlog.

**Sequenced PRs** live in [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md). Recommended first slice:

`eval metrics → CCR-lite packer → query loop → KG enrichment`

## Notes

| # | Source | Deep dive | Primary Code-Harness seam |
|---|--------|-----------|---------------------------|
| 1 | Headroom | [headroom.md](headroom.md) | `context_builder.py` post-MMR packer + local chunk cache |
| 3 | Strands Harness | [strands-harness.md](strands-harness.md) | interactive session policy; later MCP/tool packaging |
| 5 | Memanto | [memanto.md](memanto.md) | typed project memory beside the code index |
| 13 | ForgeCode | [forgecode.md](forgecode.md) | read-only “sage” query profile, compact, knobs |
| 14 | Code graph | [code-graph.md](code-graph.md) | `knowledge_graph.py` edges, gloss, beam expand |
| 15 | TurboVec | [turbovec.md](turbovec.md) | optional `vector_store` backend after recall eval |
| 16 | OKF (Google SPEC) | [okf.md](okf.md) | portable wiki/memory interchange (not a Memanto format) |
| 18 | Prompt→Loop→Graph | [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md) | graded retrieve loop in `retriever.py` / `main.py` |
| 23 | Google Code Wiki | [google-code-wiki.md](google-code-wiki.md) | incremental `wiki generate` + cite `path:symbol` |

## How to read

1. Start at [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) for PR order and “what not to build yet.”
2. Open the deep dive that owns the next PR.
3. Keep the matching first-pass card for links and caveats.

## Corrections vs first pass

- **OKF ownership:** Open Knowledge Format is a **Google Cloud specification** (`GoogleCloudPlatform/open-knowledge-format`, SPEC v0.2). Memanto is an *implementer* (export/import), not the owner. See [okf.md](okf.md) and the updated [../okf.md](../okf.md).
