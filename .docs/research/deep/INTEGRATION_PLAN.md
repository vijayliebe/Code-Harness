# Integration plan — sequenced PRs

**Status:** PRs 1–4 (eval → CCR-lite → query loop → KG enrichment) are **implemented** on the KG-enrichment line. This file stays the design record for that sequence. Fusion PRs 1–5 on that line also landed (wiki / memory / session / redact / doctor+MCP, plus BM25-over-memory). Polish (TurboVec A/B, query-cache) and the post-polish steal wave (Claude Code #24 + DeepSeek #25) **shipped**. Remaining deltas: [../fusion/GAP_AUDIT.md](../fusion/GAP_AUDIT.md) · [../fusion/STEAL_MATRIX.md](../fusion/STEAL_MATRIX.md).

This plan turns the HIGH-source deep dives into a short, ordered backlog. It **reorders** the first-pass 90-day table in [../SYNTHESIS.md](../SYNTHESIS.md): evaluation lands *before* packer/loop/KG so later PRs have anchors instead of Goodharting `top_k` and token folklore.

## Recommended first PRs

| Order | PR | Owner deep dive | Why this order |
|-------|----|-----------------|----------------|
| 1 | **Eval metrics** | [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md) (§anchors) + [../llm-in-production.md](../llm-in-production.md) | Without golden Q→`chunk_id` / citation / token traces, CCR and loop tweaks cannot be judged. |
| 2 | **CCR-lite packer** | [headroom.md](headroom.md), [google-code-wiki.md](google-code-wiki.md), [forgecode.md](forgecode.md) | Largest token win; reversible; does not change retrieval ranking. |
| 3 | **Query loop** | [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md), [strands-harness.md](strands-harness.md) | Uses packer + metrics; adds grade/retry/verify around the existing one-shot pipeline. |
| 4 | **KG enrichment** | [code-graph.md](code-graph.md), [google-code-wiki.md](google-code-wiki.md) | Multi-hop accuracy; benefits from loop (conditional deepen) and eval (path questions). |

Later (do **not** start until 1–4 have numbers):

| Later | PR | Owner |
|-------|----|--------|
| 5 | Typed memory + OKF import/export | [memanto.md](memanto.md), [okf.md](okf.md) |
| 6 | Wiki generate MVP | [google-code-wiki.md](google-code-wiki.md), [okf.md](okf.md) |
| 7 | TurboVec experimental backend | [turbovec.md](turbovec.md) — **blocked on eval recall@k** |
| 8 | MCP / `POST /v1/retrieve` + doctor | [forgecode.md](forgecode.md), [strands-harness.md](strands-harness.md), [../proxima.md](../proxima.md) |

## PR 1 — Eval metrics

**Goal:** A tiny, local, no-network-required harness that scores the *current* pipeline so later PRs report deltas.

**In scope**

- Golden set format (YAML/JSON) per fixture repo: `{id, query, relevant_chunk_ids[], must_cite_paths[], difficulty}`.
- CLI: `python main.py eval <repo> --suite path` (or a `scripts/eval_retrieval.py` if CLI surface is deferred — prefer CLI for discoverability).
- Metrics: Recall@k, nDCG@k (optional), citation-path hit rate, stage latency (dense / BM25 / graph / CE / MMR), estimated prompt tokens after `ContextBuilder.build_context`.
- Failure taxonomy labels already hinted by `--debug`: `dense_miss | bm25_miss | graph_miss | rerank_drop | packer_drop`.
- Persist last run under `.code-harness/eval/{suite}-{timestamp}.json`.

**Out of scope**

- LLM-as-judge (add after loop PR if citation metric is insufficient).
- TurboVec / HyDE-on-by-default.
- Publishing a public leaderboard.

**Touches (when implemented):** `main.py` (new subcommand), new `harness/eval.py` (or `harness/metrics.py`), `retriever.py` (emit stage traces), `context_builder.py` (token estimate). **This docs PR does not touch those files.**

**Exit:** one committed fixture suite against *this* repo (or a tiny vendored fixture) plus README snippet. Subsequent PRs paste before/after tables.

## PR 2 — CCR-lite packer

**Goal:** After MMR assembly, send **signatures + docstrings + key spans** to the LLM; keep full chunk text in a process-local cache keyed by `chunk_id`.

**In scope**

- `ContextBuilder` pack mode: `full | ccr_lite` (default `full` until eval says otherwise).
- Cache: in-memory dict this process; optional `.code-harness/ccr/{chunk_id}.txt` for interactive multi-turn.
- LLM tool or CLI: `retrieve_chunk <id>` / `--expand-chunk` to materialize originals.
- KV-cache-friendly layout: stable system prompt + `ARCHITECTURE.md`/`AGENTS.md`/`CLAUDE.md` prefix; volatile packed chunks last.
- Token metrics wired into PR 1 (`prompt_tokens_full` vs `prompt_tokens_packed`).

**Out of scope**

- Headroom proxy / Kompress model / SmartCrusher JSON compressor (optional later; see [headroom.md](headroom.md)).
- Aggressive AST body deletion without retrieve-back.

**Exit:** eval shows ≥30% prompt-token drop on the fixture suite with **no Recall@k change** (packer is post-retrieval) and citation-path hit rate within 5% of baseline when the model is allowed one expand.

## PR 3 — Query loop

**Goal:** Replace linear `dense|BM25|graph → RRF → CE → MMR → LLM` with a bounded loop.

```
retrieve → grade → (rewrite | HyDE | deepen graph | proceed)
        → answer → citation-coverage check → maybe re-retrieve
```

**In scope**

- Max 2 extra retrieve rounds (configurable `retrieval.max_loops`, default 1 extra).
- Cheap grader: heuristic first (overlap of query tokens vs chunk names/paths); optional tiny LLM grade later.
- Easy path: identifier-like queries → BM25-only (or `--no-llm` unchanged).
- Independent verify: optional second LLM call that sees `{query, answer, packed chunks}` only — no generator CoT.
- Stop conditions: grade ≥ threshold, citation coverage ≥ threshold, or loop budget.

**Out of scope**

- Multi-agent graphs, subagents, LangGraph.
- Full Strands/Forge rewrite.

**Exit:** eval hard-query subset improves citation-path hit rate; easy-query p50 latency does not regress >15%; token budget on easy queries drops (BM25-only).

## PR 4 — KG enrichment

**Goal:** Deepen the existing NetworkX graph without requiring Neo4j/Memgraph.

**In scope**

- New `RelationshipType` values: `exposes` (HTTP/CLI/MCP endpoints), `tested_by` (test file/func → code), `gloss` (note node → entity). Optional `data_flow` only if extractors stay cheap.
- Gloss notes as `EntityType` documentation nodes written under `.code-harness/gloss/` (markdown) and linked in `graph_{repo}.json`.
- Neighbor expansion: beam (score-ordered) instead of fixed BFS `max_depth=3` / `expand_neighbors=3`.
- Mermaid export from a subgraph (`info --mermaid` or `wiki` precursor).

**Out of scope**

- NL→Cypher, Memgraph, eBPF traces (later; see [code-graph.md](code-graph.md)).
- Full Code Wiki product.

**Exit:** eval path-questions (“who calls X”, “what exposes Y”) improve; `graph_*.json` remains loadable by current visualizer; no mandatory extra services.

## Guardrails (all PRs)

- **Docs-first default:** flags off or `full` packer until eval is green.
- **Local-first:** no new hosted index (Forge `:sync` default is a cautionary tale).
- **Do not change ranking to “look better”** without suite deltas.
- **OKF / wiki / TurboVec / MCP** stay behind PRs 5–8.
- **No `harness/*.py` or `main.py` changes in this research PR.**

## Mapping back to first-pass phases

| First-pass phase ([SYNTHESIS](../SYNTHESIS.md)) | This plan |
|--------------------------------------------------|-----------|
| A CCR packer + `/cost` | PR 2 (metrics already in PR 1) |
| B CRAG loop | PR 3 |
| C KG + wiki | PR 4 (KG only); wiki is later #6 |
| D typed memory + OKF | later #5 |
| E TurboVec + recall suite | later #7 (recall suite *starts* in PR 1) |
| F MCP + ingest | later #8 |

## Decision log

| Decision | Choice | Rejected |
|----------|--------|----------|
| First PR | Eval metrics | Starting with packer (no baseline) |
| Packer | CCR-lite (signature + spans + retrieve-back) | Headroom-as-proxy first |
| Graph store | Keep NetworkX JSON | Memgraph/Neo4j in v1 |
| OKF | Implement Google SPEC subset | Treat Memanto as the format owner |
| Agent UX | Stay retrieval CLI; MCP later | Fork Forge/Strands |
