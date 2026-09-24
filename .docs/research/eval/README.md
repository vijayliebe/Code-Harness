# Retrieval eval

Local, no-network-required scoring of the current retrieve → pack pipeline. Later PRs (CCR packer, query loop, KG) should paste before/after numbers from this harness.

## Run

```bash
# 1. Index this repo once (local embeddings; no LLM key required)
python main.py index .

# 2. Score the committed golden suite
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml

# Validate suite shape without an index
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --dry-run
```

The last run is written to `.code-harness/eval/{suite}-{timestamp}.json` (gitignored). Override with `--output path.json`. `--k` overrides the suite cutoff (default 10).

Eval is retrieval-only: it never calls the LLM.

## Suite format

YAML or JSON. A list of fixtures, or a mapping with `suite`, optional `k`, and `fixtures`:

```yaml
suite: code-harness
k: 10
fixtures:
  - id: q-context-builder
    query: How does context assembly work?
    relevant_chunk_ids:           # optional; stable entity ids preferred
      - class:harness/context_builder.py:ContextBuilder
    must_cite_paths:
      - harness/context_builder.py
    difficulty: easy              # easy | medium | hard
```

`relevant_chunk_ids` may be full chunk ids or parser entity ids (`class:path:Name`, `func:path:name`, or treesitter `method:path:Class.name`). `func:path:name` also matches `method:path:Class.name`. Chunk UUIDs change on re-index; entity ids do not. If chunk ids are omitted, Recall@k / nDCG@k fall back to `must_cite_paths`.

## Metrics

| Metric | Meaning |
|--------|---------|
| Recall@k | Fraction of gold ids (or paths) in the top-k retrieved chunks |
| nDCG@k | Binary-relevance ranking quality over the same gold set |
| citation-path hit rate | Fraction of `must_cite_paths` present in packed `ContextBuilder` context |
| prompt tokens | `len(context) // 4` after `build_context` (active pack mode) |
| prompt_tokens_full | Same estimate for the legacy full assembly |
| prompt_tokens_packed | Same estimate for the CCR-lite assembly (signatures + key spans) |
| prompt_token_drop | `1 - packed_mean / full_mean` (packer is post-retrieval; Recall@k must not move) |
| stage latency | dense / BM25 / graph / CE / MMR when that stage ran |

Failure labels (same vocabulary as `--debug` source misses):

`dense_miss | bm25_miss | graph_miss | rerank_drop | packer_drop`

`packer_drop` is recorded when a gold item was retrieved but omitted from the assembled context (token-budget truncation in `full`; CCR-lite keeps MMR ids in headers and uses the same label only if a survivor is dropped).

Default pack mode is `full`. Compare token columns without changing ranking:

```bash
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --pack-mode ccr_lite
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop
```

`--loop` (or `--max-loops N`) enables the bounded corrective retrieve loop. Default `max_loops=0` is one-shot. Reports include `metrics.by_difficulty` and per-case `loop` traces (`attempts`, `grade`, `stop_reason`).

Path-question fixtures (`q-who-calls-*`, `q-what-exposes-*`) are the KG-enrichment anchors: they should improve when `exposes` / `tested_by` / beam expansion fire. If extractors cannot yet find enough edges, beam + Mermaid still ship.

**Citation-path vs one expand:** citation-path hit rate is computed from packed file-path headers, not omitted bodies, so it does not require an LLM `retrieve_chunk` to stay within 5% of `full`. To manually check answer quality after one expand, run `query --pack-mode ccr_lite --expand-chunk <id>` (or `retrieve-chunk <id>`) on a fixture whose packed header still names the must-cite path.
