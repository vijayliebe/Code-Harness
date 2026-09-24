# Code Harness

Vectorize, index, and query any code repository with AI. Provides optimal context to AI models via hybrid retrieval (semantic + lexical + knowledge graph + cross-encoder reranking).

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Index a repository
python main.py index /path/to/your/repo

# Query it
python main.py query /path/to/your/repo -q "how does authentication work?"

# Interactive mode
python main.py interactive /path/to/your/repo
```

## Commands

### `index` — Index a repository

```bash
python main.py index ./my-project
python main.py index ./my-project --embed-model voyage-code-2
python main.py index ./my-project --save-config
```

Parses all code files (via tree-sitter AST when available, regex fallback), chunks into semantic units (functions, classes, files), builds a knowledge graph of code relationships, generates embeddings (local/Voyage/Jina/OpenAI), builds BM25 lexical index, stores everything for fast retrieval.

### `query` — Query the indexed repository

```bash
python main.py query ./my-project -q "find the authentication logic"
python main.py query ./my-project -q "how does the chunker work?" --no-llm
python main.py query ./my-project -q "explain the data flow" --stream
python main.py query --cross-repo -q "how do repos relate?"  # cross-repo search
python main.py query ./my-project -q "auth logic" --debug   # per-source breakdown
python main.py query ./my-project --no-llm --pack-mode ccr_lite -q "how does the chunker work?"
python main.py retrieve-chunk 'func:harness/chunker.py:CodeChunker.chunk_entities:abcd1234'
```

Hybrid retrieval: dense vector search (semantic), BM25 keyword index (exact name matches), knowledge graph neighbor expansion. Results are fused via RRF, cross-encoder reranked, diversified via MMR, and assembled into optimal LLM context.

| Flag | Description |
|------|-------------|
| `-q, --query` | Your question about the codebase (required) |
| `--no-llm` | Show context only, skip AI response |
| `--stream` | Stream the AI response token by token |
| `--cross-repo` | Search across all indexed repositories |
| `--debug` | Show per-source retrieval breakdown (dense/BM25/graph/cross-encoder) |
| `--pack-mode` | `full` (default) or `ccr_lite` — pack signatures + key spans after MMR |
| `--expand-chunk ID` | Inject a cached original chunk into the prompt (repeatable) |

### `interactive` — Interactive REPL mode

```bash
python main.py interactive ./my-project
python main.py interactive --cross-repo ./my-project
```

Type questions continuously. Commands within the session:

```
/help           Show available commands
/llm on|off     Enable/disable AI responses
/context        Show the last retrieved context
/retrieve <id>  Print a CCR-cached original chunk (`/expand` is an alias)
/clear          Clear the screen
/quit           Exit
```

### CCR-lite packer (`--pack-mode ccr_lite`)

Default pack mode is **`full`**: every MMR survivor is sent as complete chunk text (no behavior change).

`ccr_lite` is a reversible post-retrieval packer. After MMR it sends **signatures + docstrings + first/last lines** to the LLM and keeps originals in a process-local cache, spilled to `.code-harness/ccr/` so `retrieve-chunk` works in a later process. Set `"ccr": { "spill": false }` to keep memory-only. Retrieval ranking is unchanged.

Enable it:

```bash
# CLI flag (highest precedence)
python main.py query . --pack-mode ccr_lite --no-llm -q "how does the chunker work?"

# or environment
CODEHARNESS_PACK_MODE=ccr_lite python main.py query . --no-llm -q "how does the chunker work?"

# or config
# { "context": { "pack_mode": "ccr_lite" },
#   "ccr": { "first_lines": 12, "last_lines": 8, "spill_dir": ".code-harness/ccr" } }
```

Expand a packed body (same process cache, or the on-disk spill after a `ccr_lite` query):

```bash
python main.py query . --pack-mode ccr_lite --expand-chunk 'CHUNK_ID' -q "explain this algorithm"
python main.py retrieve-chunk 'CHUNK_ID'
```

Eval always reports `prompt_tokens_full` vs `prompt_tokens_packed` so you can compare modes without flipping the default. Recall@k is computed on retriever hits, so the packer cannot change it.

### `info` — Show repository statistics

```bash
python main.py info ./my-project
python main.py info ./my-project --mermaid
python main.py info ./my-project --mermaid --focus class:harness/context_builder.py:ContextBuilder
```

Shows file count, chunk count, knowledge graph size, cross-repo relationships, and file type distribution. `--mermaid` prints a `graph TD` subgraph from `graph_{repo}.json` (wiki precursor; no extra services). `--focus` centers the diagram on an entity id.

Graph expansion defaults to **beam** (`retrieval.expand_mode: beam`, `beam_width: 6`, `beam_depth: 2`). `expand_neighbors: 3` is the added-chunk cap (`max_added = expand_neighbors * 2`). Set `expand_mode: bfs` to restore the old hop walk. Gloss notes live in `knowledge/gloss/*.md` or `.code-harness/gloss/*.md` with frontmatter `entity: class:path:Name`.

### `watch` — Watch and auto re-index

```bash
python main.py watch ./my-project
```

Watches the repository for file changes (creates, modifications, deletions) with a 2-second debounce and triggers a full re-index automatically.

### `visualize` — Launch vector space visualizer

```bash
python main.py visualize
python main.py visualize --output viz.html
```

Generates an interactive 3D HTML visualization of the embedding space using PCA/t-SNE projection.

### `eval` — Score retrieval against a golden suite

Local, retrieval-only (no LLM, no API keys). Requires a prior `index` of the repo.

```bash
python main.py index .
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --dry-run
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop
```

Reports Recall@k, nDCG@k, citation-path hit rate, stage latency (dense / BM25 / graph / CE / MMR), estimated prompt tokens after context assembly (`prompt_tokens_full` vs `prompt_tokens_packed`), and easy/hard splits. Writes `.code-harness/eval/{suite}-{timestamp}.json`. Use `--pack-mode ccr_lite` to score citation paths against packed headers (Recall@k is unchanged). `--loop` / `--max-loops N` is opt-in; default remains one-shot.

See [`.docs/research/eval/README.md`](.docs/research/eval/README.md) for the fixture schema and failure taxonomy (`dense_miss | bm25_miss | graph_miss | rerank_drop | packer_drop`).

### `clear` — Clear all indexed data

```bash
python main.py clear
python main.py clear ./my-project  # clear single repo only
```

Removes the vector store, knowledge graph, BM25 index, and inter-repo graph.

## How It Works

```
Repository → Tree-sitter/Regex Parse → Entities → Chunk → Embed → Vector DB
                                               → Build → Knowledge Graph
                                               → Build → BM25 Lexical Index

Query → [optional loop] retrieve → grade → (rewrite | HyDE | deepen | proceed)
      → Embed Query (HyDE optional) → Dense Search (40%)
                                     → BM25 Search (30%)
                                     → Graph Expansion (30%)
                                     → RRF Fusion
                                     → Cross-Encoder Rerank
                                     → MMR Diversity Ranking
                                     → Context Assembly (ARCHITECTURE.md prefix; optional CCR-lite pack)
                                     → LLM → Answer → citation check (maybe re-retrieve)
```

The corrective loop is **off by default** (`retrieval.max_loops: 0`) so one-shot latency is unchanged. Enable with `--loop` (one extra retrieve) or `--max-loops N` (0–2 extra). Identifier-like queries short-circuit to BM25-only. `--verify` adds an independent LLM citation check (off by default).

### Retrieval Pipeline

1. **Dense retrieval**: query embedded with sentence-transformers/Voyage/Jina/OpenAI, top-K from ChromaDB (HNSW index, ef_search=256)
2. **Sparse retrieval**: BM25 keyword search over all chunks — catches exact function/variable name matches
3. **Graph expansion**: beam walk over the knowledge graph (exposes / tested_by / gloss / calls / inheritance). Default `beam_width=6`, `beam_depth=2`; `expand_mode: bfs` keeps the old hop walk.
4. **HyDE** (optional): hypothetical code document generation for query expansion
5. **RRF fusion**: three signals combined via Reciprocal Rank Fusion
6. **Cross-encoder reranking**: `cross-encoder/ms-marco-MiniLM-L-6-v2` re-scores top candidates
7. **MMR diversity**: Maximum Marginal Relevance prevents file dominance
8. **Context injection**: `ARCHITECTURE.md`, `AGENTS.md`, `CLAUDE.md` as a stable prefix when present; optional CCR-lite pack (`--pack-mode ccr_lite`) after MMR
9. **Corrective loop** (opt-in): heuristic grade → rewrite / HyDE-on-retry / graph deepen, then citation-coverage stop. Default is one-shot.

## Configuration

Default config in `harness/config.py`. Override via JSON/YAML:

```bash
python main.py --config my-config.json index ./project
```

Key settings:

```json
{
  "embedding": {
    "provider": "local",
    "model": "all-MiniLM-L6-v2"
  },
  "chunking": {
    "max_chunk_size": 1500,
    "overlap_lines": 20
  },
  "retrieval": {
    "dense_weight": 0.3,
    "sparse_weight": 0.25,
    "graph_weight": 0.2,
    "top_k": 30,
    "rerank_top_k": 15,
    "expand_mode": "beam",
    "beam_width": 6,
    "beam_depth": 2,
    "expand_neighbors": 3,
    "cross_encoder": { "enabled": true, "model": "cross-encoder/ms-marco-MiniLM-L-6-v2" },
    "hyde": { "enabled": false, "on_retry": true },
    "max_loops": 0,
    "grade_threshold": 0.35,
    "citation_threshold": 0.5
  },
  "context": {
    "pack_mode": "full"
  },
  "ccr": {
    "first_lines": 12,
    "last_lines": 8,
    "spill_dir": ".code-harness/ccr"
  },
  "vector_store": {
    "hnsw_ef_search": 256,
    "hnsw_ef_construction": 200,
    "hnsw_m": 32
  },
  "llm": {
    "provider": "openai",
    "model": "gpt-4o"
  }
}
```

Global flags:

| Flag | Description |
|------|-------------|
| `-c, --config` | Path to config file |
| `-v, --verbose` | Verbose output |
| `--llm-provider` | LLM provider: openai, anthropic, gemini, ollama, custom |
| `--llm-model` | LLM model name (e.g. gpt-4o, claude-3-opus) |
| `--embed-model` | Embedding model name |
| `--repo-name` | Override auto-derived repository name |

## Embedding Providers

| Provider | Model | Config | API Key |
|----------|-------|--------|---------|
| **local** | sentence-transformers (default: all-MiniLM-L6-v2) | `"provider": "local"` | None |
| **openai** | text-embedding-3-small / text-embedding-3-large | `"provider": "openai"` | `OPENAI_API_KEY` |
| **voyage** | voyage-code-2, voyage-3-large | `"provider": "voyage"` | `VOYAGE_API_KEY` |
| **jina** | jina-embeddings-v3 | `"provider": "jina"` | `JINA_API_KEY` |

All API-based embedding calls wrapped with `retry_with_backoff` (3 retries, exponential backoff).

## LLM Providers

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
python main.py query . -q "explain this code"
```

### Gemini

```bash
export GEMINI_API_KEY="AIza..."
python main.py --llm-provider gemini query . -q "explain this code"
```

Uses OpenAI-compatible endpoint. Default model: `gemini-2.5-flash`.

### Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python main.py --llm-provider anthropic --llm-model claude-3-opus-20240229 query .
```

### Ollama (local)

```bash
export OLLAMA_HOST="http://localhost:11434"
python main.py --llm-provider ollama --llm-model codellama query .
```

### Custom (OpenAI-compatible)

```bash
python main.py --llm-provider custom --llm-model my-model \
  --llm-provider-api-base http://localhost:8080/v1 query .
```

## Supported Languages

| Language | Extensions | Parser |
|----------|-----------|--------|
| Python | .py | Tree-sitter / AST |
| JavaScript | .js, .jsx, .mjs, .cjs | Tree-sitter |
| TypeScript | .ts, .tsx | Tree-sitter |
| Go | .go | Tree-sitter |
| Rust | .rs | Tree-sitter |
| Java | .java | Tree-sitter |
| C/C++ | .c, .cpp, .h, .hpp | Tree-sitter |
| Ruby | .rb | Tree-sitter |
| PHP | .php | Tree-sitter |
| Swift | .swift | Tree-sitter |
| Kotlin | .kt | Tree-sitter |
| Scala | .scala | Tree-sitter |
| Elixir | .ex, .exs | Tree-sitter |
| Others | .md, .rst, .yaml, .json, .toml | Regex / section-based |

## Multi-Repo Support

Index multiple repositories into the same store:

```bash
python main.py index ./repo-a
python main.py index ./repo-b
python main.py query --cross-repo -q "how do these projects interact?"
```

- Each chunk tagged with `repo_name` for scoped/per-repo queries
- Per-repo knowledge graph: `graph_{repo}.json`, per-repo BM25: `bm25_{repo}.pkl`
- Incremental `clear ./repo-name` removes single repo's data
- `RepoGraph` tracks inter-repo relationships (shared imports, shared entities, dependency edges)

## Storage Layout

```
.code-harness/
├── chromadb/              # ChromaDB persistent data (HNSW index + metadata)
│   ├── chroma.sqlite3
│   └── ...
├── graph_{repo}.json      # Per-repo knowledge graph (NetworkX node-link format)
├── graph.json             # Fallback single-repo graph
├── bm25_{repo}.pkl        # Per-repo BM25 serialized index
├── repo_graph.json        # Inter-repo relationship graph
├── eval/                  # Retrieval eval reports ({suite}-{timestamp}.json)
├── ccr/                   # Optional CCR-lite originals ({sanitized_chunk_id}.txt)
```

## Research

External research informing performance, accuracy, and token-cost improvements is in [`.docs/research/INDEX.md`](.docs/research/INDEX.md) (source catalog) and [`.docs/research/SYNTHESIS.md`](.docs/research/SYNTHESIS.md) (prioritized recommendations). Deep design notes for the HIGH sources, plus a sequenced PR plan (`eval metrics → CCR-lite packer → query loop → KG enrichment`), are in [`.docs/research/deep/README.md`](.docs/research/deep/README.md) and [`.docs/research/deep/INTEGRATION_PLAN.md`](.docs/research/deep/INTEGRATION_PLAN.md).

## Project Structure

```
code-harness/
├── main.py                        CLI entry point
├── requirements.txt
├── README.md
├── clean_db.py                    Standalone DB cleaner
├── harness/
│   ├── __init__.py
│   ├── models.py                  Data models (Chunk, CodeEntity, Relationship)
│   ├── config.py                  Configuration with sensible defaults
│   ├── parser.py                  Multi-language code parser (tree-sitter + regex)
│   ├── parser_treesitter.py       Tree-sitter AST parser (21 languages)
│   ├── chunker.py                 Smart code chunking (entity-type aware)
│   ├── embedder.py                Embedding (local/Voyage/Jina/OpenAI + HyDE)
│   ├── vector_store.py            ChromaDB vector storage (tuned HNSW)
│   ├── knowledge_graph.py         NetworkX code relationship graph (intra-repo)
│   ├── repo_graph.py              Inter-repo relationship graph
│   ├── retriever.py               Hybrid retrieval (dense + sparse + graph + cross-encoder)
│   ├── context_builder.py         Context assembly (MMR, prefix docs, full | ccr_lite pack)
│   ├── ccr.py                     CCR-lite pack + retrieve-back cache
│   ├── eval.py                    Golden-suite loader, eval runner, JSON reports
│   ├── metrics.py                 Recall@k, nDCG@k, citation hit, failure taxonomy
│   ├── llm.py                     LLM integration layer (OpenAI/Anthropic/Gemini/Ollama)
│   └── utils.py                   Shared utilities (retry, import/export extraction)
├── tests/                         Offline unit tests for eval metrics
├── visualizer/
│   └── visualize.py               Embedding space visualization (PCA/t-SNE)
└── .docs/
    ├── architecture.md            System architecture
    ├── walkthrough.md             End-to-end pipeline walkthrough
    ├── knowledge-graph.md         Graph schema and usage
    └── research/                  External research (INDEX + SYNTHESIS + source notes + deep/)
```
