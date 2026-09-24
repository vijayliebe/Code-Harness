# Deep dive: Prompt → Loop → Graph (query loop + eval anchors)

**First-pass:** [../prompt-loop-graph-engineering.md](../prompt-loop-graph-engineering.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) PR 1 (anchors) and PR 3 (loop)  
**Seam:** `harness/retriever.py`, `harness/context_builder.py`, `main.py` `query`/`interactive`

## 1. What we confirmed

The stack (Learn Harness Engineering L14, DesignGurus/FutureAGI, Anthropic “Building Effective Agents”):

| Layer | Code-Harness today | Gap |
|-------|--------------------|-----|
| Prompt | system + “answer from context” | fine; version it later |
| Context | hybrid retrieve + ARCHITECTURE inject + MMR | packer (PR 2) |
| Loop | **none** — linear pipeline | PR 3 |
| Graph (orchestration) | **none** — and we should not graphify yet | after loop is boring and correct |

Lecture caveats we adopt: ignore viral “+18%/−85%” numbers; **orchestration tax** is real; prefer replayability and observability over topology fashion. Indexing stays a script. Querying becomes a loop.

CRAG / Self-RAG / Adaptive RAG (from the 500-AI-Agents catalog) are the same loop with different grade/rewrite tactics.

## 2. The one-shot pipeline we are wrapping

```
dense ∪ BM25 ∪ graph-expand
    → RRF (dense_weight 0.3, sparse 0.25, graph 0.2)
    → cross-encoder (optional)
    → MMR / ContextBuilder
    → LLM
```

HyDE exists and is **off**. `--no-llm` already skips generation. `--debug` already names sources. We are adding **control flow**, not new indexes.

## 3. PR 1 — anchors before the loop

Loop engineering without eval is cosplay. Minimal suite:

```yaml
# .docs/research/eval/code-harness.fixture.yaml  (example shape; not shipped as code)
- id: q-context-builder
  query: How does context assembly work?
  relevant_chunk_ids: []   # fill after first index of this repo
  must_cite_paths: [harness/context_builder.py]
  difficulty: easy
- id: q-who-calls-retrieve
  query: Who calls Retriever index_chunks?
  must_cite_paths: [harness/retriever.py]
  difficulty: hard
```

Metrics (no LLM-as-judge in v1):

- Recall@k / citation-path hit (path ∈ assembled context)
- Stage latency + estimated tokens
- Taxonomy: `dense_miss | bm25_miss | graph_miss | rerank_drop | packer_drop`

Chip Huyen / LLM-in-production first-pass ([../llm-in-production.md](../llm-in-production.md)) is the methodology source; this note owns the **Code-Harness-shaped** suite.

Replayability: store `config` snapshot + suite id next to results so a loop PR cannot silently change weights.

## 4. PR 3 — loop contract

```
state = {query, rewritten, packs[], grades[], answer, attempts}
loop:
  hits = retrieve(state.query_effective, mode)
  packed = pack(hits)                  # PR 2
  grade = heuristic_grade(query, hits) # name/path overlap + CE score if on
  if grade < τ and attempts < max_loops:
      state.query_effective = rewrite_or_hyde_or_deepen(query, grade)
      continue
  answer = LLM(prefix + packed) or skip if --no-llm
  if citation_coverage(answer, hits) < τ and attempts < max_loops:
      deepen graph; continue
  stop
```

**Modes**

| Query shape | Mode |
|-------------|------|
| Looks like an identifier (`CamelCase`, `snake_case`, `` `tick` ``) | `bm25` first; stop if grade high |
| `who calls` / `exposes` / `used by` | `graph` deepen allowed |
| `explain` / `why` / `how` | full hybrid + packer expand-on-explain |
| `--no-llm` | retrieve + pack only (today’s path) |

**Rewrite:** start with HyDE-on-retry (we already have the flag), not a new model. Query rewrite can be rule-based (`add file extension synonyms`) before an LLM rewrite.

**Verify (optional flag):** second LLM call `{query, answer, packed}` → `{supported: bool, missing_paths: []}`. Independent of generator CoT. Off by default (cost).

**max_loops default:** 1 extra retrieve. Orchestration tax.

## 5. Shared state (not a graph yet)

Persist per interactive session:

```json
{"query": "...", "chunk_ids": [], "attempts": 1, "grade": 0.7}
```

That is Strands session-lite ([strands-harness.md](strands-harness.md)). Promote to a node graph only when we have two *specialized* roles (retrieve vs verify) that must run in parallel or rollback. We do not.

## 6. File-level sketch (future)

| File | PR | Change |
|------|----|--------|
| `harness/eval.py` | 1 | suite loader, metrics, JSON report |
| `main.py` | 1, 3 | `eval` subcommand; loop around `query` |
| `harness/retriever.py` | 3 | `retrieve_once`, traces, mode switch |
| `harness/loop.py` (new, optional) | 3 | grade / stop / rewrite |
| `harness/config.py` | 3 | `retrieval.max_loops`, `retrieval.grade_threshold` |

## 7. Acceptance

**PR 1:** `eval` runs offline on a committed suite; CI can run it without API keys if it only scores retrieval (not LLM).

**PR 3:** hard subset citation-path +≥10 points or documented miss; easy p50 latency +<15%; `max_loops=0` reproduces one-shot.

## 8. Risks

- Grader that uses the same CE as rerank will rubber-stamp the rerank. Prefer cheap lexical grade for the retry decision; CE already had its turn.
- HyDE-on-retry doubles embed cost — measure in eval traces.
- “Graph engineering” vanity: do not add LangGraph.

## 9. Recommended PR slice

Exactly INTEGRATION_PLAN PRs 1 and 3. This note is the loop spec; [headroom.md](headroom.md) is the packer the loop calls; [code-graph.md](code-graph.md) is the deepen action.

## 10. Heuristic grade (v1, no extra model)

```
name_hits = query_tokens ∩ {entity_name, file_stem}
path_hits = any(token in file_path for token in query_tokens)
ce = max CE score if cross-encoder ran else 0
grade = 0.45 * min(1, name_hits/2) + 0.25 * path_hits + 0.30 * ce
```

Retry if `grade < 0.35` (config). Do not reuse CE as the *only* signal.

## 11. Citation coverage

After an LLM answer (if any):

```
cited = paths mentioned in answer (regex on `foo.py` and `foo.py:Symbol`)
coverage = |cited ∩ retrieved_paths| / max(1, |must_cite ∪ cited|)
```

For `--no-llm`, coverage = fraction of `must_cite_paths` present in packed context (eval-only).

## 12. Trace record (eval + `--debug`)

```json
{
  "query": "...",
  "mode": "hybrid",
  "attempt": 1,
  "dense_ids": [],
  "bm25_ids": [],
  "graph_ids": [],
  "fused_ids": [],
  "packed_ids": [],
  "grade": 0.42,
  "action": "deepen_graph",
  "ms": {"dense": 12, "bm25": 4, "graph": 3, "ce": 40, "pack": 1}
}
```

PR 1 can emit this without implementing retry (`action` always `stop`).

## 13. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Infinite rewrite | `max_loops`, no rewrite if HyDE already used |
| Easy queries get slower | Detector in [strands-harness.md](strands-harness.md) §11 |
| Eval Goodhart on `top_k` | Suite frozen; config snapshot in report |
| Verify call doubles cost | Flag off; use citation regex first |

## 14. Implementation checklist

### PR 1
- [ ] Suite YAML schema documented
- [ ] `eval` runs without API keys (retrieval-only)
- [ ] Stage traces in JSON
- [ ] Config snapshot in report
- [ ] One committed fixture (this repo or tiny tree)

### PR 3
- [ ] `max_loops=0` matches one-shot
- [ ] Easy-path skips extra retrieve
- [ ] Grade threshold configurable
- [ ] HyDE only on retry (still default-off globally)
- [ ] No LangGraph / multi-agent

## 15. Cross-links

- Packer: [headroom.md](headroom.md)
- Policy: [strands-harness.md](strands-harness.md)
- Deepen: [code-graph.md](code-graph.md)
- Methodology: [../llm-in-production.md](../llm-in-production.md)
