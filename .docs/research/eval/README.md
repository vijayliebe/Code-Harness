# Retrieval eval

Local, no-network-required scoring of the current retrieve → pack pipeline. Later PRs (CCR packer, query loop, KG) should paste before/after numbers from this harness.

## Run

Canonical recipe (same path [CI](../../../.github/workflows/ci.yml) uses on PRs and push to `main`):

```bash
# 1. Index this repo once (local embeddings; no LLM key required)
python3 main.py index .

# 2. Score the committed golden suite
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml

# Validate suite shape without an index
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --dry-run
```

CI does not run `make eval-ab`, `make eval-ab-embed`, or `make eval-ablate`. The first two skip cleanly (exit 0) if the `turbovec` extra or Jina-code weights are missing. Ablation is eval-only and needs a local MiniLM + Chroma index of this repo.

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

### Harness-on vs harness-off ablation

Leave-one-out of the default retrieve+pack stack on this same fixture (inspired by the with-vs-without harness contrast in [arXiv 2610.02858](https://arxiv.org/abs/2610.02858)):

```bash
python3 main.py eval-ab . --ablate
# or
make eval-ablate
# equivalent: index once, then score without rebuilding
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --ablate --skip-index
```

Variants:

| variant | What changes |
|---------|----------------|
| `full` | Default harness (dense + BM25 + graph expand + CE + MMR; HyDE off; pack `full`) |
| `no_dense` / `no_bm25` / `no_graph` / `no_rerank` / `no_mmr` | That stage off |
| `hyde` | Local HyDE template **on** (default is off, so leave-one-out would be a no-op) |
| `ccr_lite` | CCR-lite packing **on** (default pack is `full`) |
| `no_retrieval` | Empty hits. Recall@k / nDCG@k / citation-path are zero |

Each row reports Recall@k, nDCG@k, citation-path hit rate, stage latency (dense / BM25 / graph / CE / MMR), `prompt_tokens_full` vs packed, and failure taxonomy counts. Writes [RESULTS-ablation.md](RESULTS-ablation.md) / `RESULTS-ablation.json`. Eval stays retrieval-only: if no chat-LLM key is set, the table states that answer-level citation (full vs no-retrieval) was not run. The default query path is not changed.

### Optional verify stage (`--verify`)

Eval stays retrieval-only unless you pass `--verify`. Fixtures that declare `completion_criteria` then run the independent default-fail gate (file / command / contains / coverage). Retrieval fixtures without a rubric are **skipped** by that stage — they do not fail the suite. See [agent-completion.example.yaml](agent-completion.example.yaml).

```yaml
completion_criteria:
  - id: module
    kind: file
    check: harness/verify.py
  - id: tests
    kind: command
    check: python3 -m unittest tests.test_verify_gate
```

```bash
python main.py eval . --suite .docs/research/eval/agent-completion.example.yaml --verify --dry-run
python main.py eval . --suite .docs/research/eval/agent-completion.example.yaml --verify
```

Reports then include per-case `verify` and `metrics.verify_pass_rate` (only over non-skipped agent-completion fixtures). Default **off**.

### Optional validity stage (`--validity`)

A separate opt-in (`verify.validity`, default **off**) checks an answer — fixture `answer` when present, otherwise a generated answer — against index/entity records and the knowledge graph. Missing `path:symbol` citations and contradicted claimed edges (`calls` / `inherits` / `tested_by`) are flagged. Eval then reports `metrics.invalid_citation_rate`. Retrieval-only fixtures with no answer contribute `citation_count=0`. The default query path is unchanged.

## Experimental backend A/B

Routine recipe (indexes both persist dirs, compares, writes the committed table):

```bash
python main.py eval-ab .
# or
make eval-ab
# or
python scripts/eval_chroma_vs_turbovec.py
```

Equivalent explicit commands:

```bash
python main.py index .
python main.py index . --vector-backend turbovec
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \
  --compare-backends chromadb,turbovec \
  --compare-markdown .docs/research/eval/RESULTS.md \
  --compare-output .docs/research/eval/RESULTS.json
```

Prints and persists side-by-side Recall@k / nDCG@k / dense p50 in
[RESULTS.md](RESULTS.md) / [RESULTS.json](RESULTS.json). Selecting
`vector_store.type: turbovec` (or `--vector-backend turbovec`) without
`--force-experimental` exits `2` when TurboVec is below Chroma by more than
5% relative, or by more than 2 Recall@10 points / 1 Recall@30 point. Missing
`turbovec` extra → skip that column (exit 0 unless it was the selected
backend). Switching `type` always requires a rebuild; Chroma and TurboVec
do not share persist directories.

`eval-ab` is the optional path: if the TurboVec wheel or the embedder/Chroma
stack cannot run, it writes a placeholder RESULTS table and exits 0. Selecting
TurboVec as the active backend while the extra is missing still fails clearly.

## Optional embedder A/B (MiniLM vs Jina-code)

Same fixture suite, same Chroma default, two local sentence-transformers models.
Production default stays `all-MiniLM-L6-v2`. `decision.enabled` stays false.

| Model | Dims | Persist | Tradeoff |
|-------|------|---------|----------|
| `all-MiniLM-L6-v2` | 384 | `.code-harness/chromadb` | Default. Fast, small, CI-friendly. |
| `jina-embeddings-v2-base-code` (`jina-code`) | 768 | `.code-harness/chromadb-jina-embeddings-v2-base-code` | Code-specialized (HF `jinaai/jina-embeddings-v2-base-code`). Heavier first download. Isolated dir because 384-d and 768-d cannot share a Chroma collection. |

```bash
# One embedder at a time (same retrieve/eval recipe)
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python3 main.py index . --embed-model jina-embeddings-v2-base-code
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \
  --embed-model jina-embeddings-v2-base-code

# Fixture A/B (indexes both persist dirs, writes RESULTS-embed.*)
python3 main.py eval-ab . --compare-embedders all-MiniLM-L6-v2,jina-embeddings-v2-base-code
make eval-ab-embed
python3 scripts/eval_minilm_vs_jina.py
```

CLI aliases: `--embed-model jina-code` and `CODEHARNESS_EMBED_MODEL=jina-code`.
This is a **local** Hugging Face model (`provider: local`), not the Jina API.

`make eval-ab-embed` is the optional path: if the Jina weights are not cached
or the download fails, it writes a placeholder [RESULTS-embed.md](RESULTS-embed.md)
and exits 0. Selecting `--embed-model jina-embeddings-v2-base-code` as the
active model while it cannot be loaded still fails clearly. `make test` does
not download Jina and stays green on the MiniLM default.

Interactive `/compact` is session-only and does not change these eval numbers (easy p50 stays the one-shot path). A synthetic 10-turn session is kept under `llm.max_tokens * 2` by dropping older packs while never dropping the latest pack ids; see `tests/test_session.py`.

Path-question fixtures (`q-who-calls-*`, `q-what-exposes-*`) are the KG-enrichment anchors: they should improve when `exposes` / `tested_by` / beam expansion fire. If extractors cannot yet find enough edges, beam + Mermaid still ship.

**Citation-path vs one expand:** citation-path hit rate is computed from packed file-path headers, not omitted bodies, so it does not require an LLM `retrieve_chunk` to stay within 5% of `full`. To manually check answer quality after one expand, run `query --pack-mode ccr_lite --expand-chunk <id>` (or `retrieve-chunk <id>`) on a fixture whose packed header still names the must-cite path.
