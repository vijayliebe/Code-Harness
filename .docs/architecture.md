# Architecture

## Overview

Code Harness is a RAG (Retrieval-Augmented Generation) system purpose-built for code repositories. It transforms a raw codebase into a queryable knowledge store combining vector embeddings, keyword indexing, structural code analysis, and a relational code graph.

```
┌─────────────────────────────────────────────────────────┐
│                     CLI (main.py)                        │
│   index | query | interactive | info | clear             │
│   watch | visualize | eval | doctor | serve / mcp        │
└──────┬──────────────────┬──────────────────────────────┘
       │                  │
       ▼                  ▼
┌──────────────┐   ┌──────────────┐
│   Index      │   │   Query      │
│   Pipeline   │   │   Pipeline   │
└──────┬───────┘   └──────┬───────┘
       │                  │
       ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│                   Core Pipeline                          │
│                                                          │
│  ┌────────┐   ┌────────┐   ┌────────┐   ┌───────────┐  │
│  │ Parser │──▶│Chunker │──▶│Embedder│──▶│VectorStore│  │
│  │ TS/Reg │   │        │   │ Lcl/V/J│   │  HNSW     │  │
│  └────┬───┘   └────────┘   └────────┘   └───────────┘  │
│       │              │         │                         │
│       ▼              ▼         ▼                         │
│  ┌────────┐   ┌──────────┐  ┌────────┐                  │
│  │  KG    │   │  BM25    │  │  HyDE  │                  │
│  │(NwtX)  │   │  (pkl)   │  │ (opt)  │                  │
│  └────┬───┘   └────┬─────┘  └────────┘                  │
│       │            │                                     │
│  ┌────┴────────────┴──────────────────────────┐          │
│  │              Retriever                      │          │
│  │  RRF Fusion → Cross-Encoder → MMR Diverse   │          │
│  └──────────────────┬─────────────────────────┘          │
│                     ▼                                     │
│  ┌──────────────────────────────────────────────────┐    │
│  │         Context Builder                           │    │
│  │  ARCHITECTURE.md injection → dedup → assemble     │    │
│  └──────────────────┬───────────────────────────────┘    │
│                     ▼                                     │
│  ┌──────────────────────────────────────────────────┐    │
│  │         LLM Interface (retry with backoff)         │    │
│  │  OpenAI | Anthropic | Gemini | Ollama | Custom     │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Component Design

### 1. Parser (`harness/parser.py` + `harness/parser_treesitter.py`)

**Purpose**: Convert files into structured code entities.

**Strategy**: Tree-sitter AST parsing (21 languages) with regex fallback.

```
File → detect extension → Tree-sitter parse → extract entities
                          fallback to regex
├── FILE (every file)
├── CLASS/STRUCT/TRAIT (with bases, methods)
├── FUNCTION (with params)
├── METHOD (with parent class)
└── DOCUMENTATION (markdown sections)
```

- **Tree-sitter**: Full AST for 21 languages — Python, JS/TS, Go, Rust, Java, C/C++, Ruby, PHP, Swift, Kotlin, Scala, Elixir. Graceful fallback to regex on import failure.
- **Python fallback**: `ast` module for structural understanding.
- **Regex fallback**: Brace-matching algorithm (`_find_block_end`) tracks `{}` depth.
- **Parallel parsing**: `ThreadPoolExecutor` with configurable `max_workers`.

Key design: A file entity is created for every file; then sub-entities (classes, functions) are created with source code and positional metadata. Dual representation enables both broad file-level and precise entity-level retrieval.

### 2. Chunker (`harness/chunker.py`)

**Purpose**: Split code entities into embeddable chunks of appropriate size.

**Strategy**: Entity-type-aware chunking with overlap for context continuity.

```
ENTITY TYPES → CHUNKING STRATEGY
  FILE        → sliding window (max_chunk_size, overlap_lines)
  CLASS       → header chunk + body chunks (sliding window)
  FUNCTION    → single chunk (functions are atomic)
  METHOD      → single chunk
  DOCUMENTATION → section-based (markdown header splitting)
```

Key design decisions:
- **max_chunk_size = 1500 chars**: Balances embedding quality with context completeness
- **overlap_lines = 20**: Prevents context loss at chunk boundaries
- **prev_start guard**: Prevents infinite loops from overlap regression
- **reached_end check**: Stops once all lines consumed

### 3. Embedder (`harness/embedder.py`)

**Purpose**: Convert chunk text into dense vector embeddings.

**Strategy**: Pluggable backends with module-level model caching and HyDE expansion.

```
Embedder
├── provider: "local"   → sentence-transformers (cached at module level)
├── provider: "openai"  → OpenAI Embeddings API (retry-wrapped)
├── provider: "voyage"  → Voyage AI API (retry-wrapped, timeout=60)
└── provider: "jina"    → Jina AI API (retry-wrapped, timeout=60)
```

Key design:
- Model cached at module level (`_model_cache`) — repeated instantiations reuse loaded model
- Embeddings L2-normalized for cosine similarity
- `embed_query()` supports HyDE query expansion (disabled by default): generates hypothetical code document, concatenates with query before embedding
- `_expand_query()` prepends "code that" to non-code queries for better alignment
- All HTTP API calls wrapped with `retry_with_backoff(3 retries, exponential backoff)`

### 4. Vector Store (`harness/vector_store.py`)

**Purpose**: Persistent storage and similarity search over embeddings.

**Backend**: ChromaDB PersistentClient with tuned HNSW index.

```
add_chunks(chunks, embeddings)
  → UUID-based IDs for uniqueness
  → Metadata: entity_id, entity_name, entity_type, file_path, line range, repo_name

search(query_embedding, top_k, repo_name)
  → Optional repo_name filter for multi-repo scoping
  → Returns RetrievalResult list with cosine similarity scores
```

**HNSW parameters**:
- `ef_search: 256` — search breadth (higher = more accurate but slower)
- `ef_construction: 200` — graph construction quality
- `M: 32` — max edges per node

### 5. Knowledge Graph (`harness/knowledge_graph.py`)

**Purpose**: Model intra-repo code relationships for graph-based context expansion.

**Backend**: NetworkX DiGraph, persisted as per-repo JSON (`graph_{repo}.json`).

See [knowledge-graph.md](./knowledge-graph.md) for detailed schema.

```
build(entities)
  ├── FILE → contains → all entities in that file
  ├── CLASS → inherits → parent class (from bases metadata)
  ├── CLASS → contains → methods
  ├── FUNC/METHOD → calls → referenced entities (regex-detected imports)
  ├── FUNC/METHOD → references → identifier references
  ├── FILE/FUNC → exposes → endpoint (CLI/HTTP/MCP; fail-soft)
  ├── test → tested_by → code (unique imported names)
  └── gloss note → gloss → entity (`.code-harness/gloss/`, `knowledge/gloss/`)
```

### 6. RepoGraph (`harness/repo_graph.py`)

**Purpose**: Model inter-repo relationships across indexed repositories.

**Backend**: NetworkX DiGraph, persisted as `repo_graph.json`.

```
update_repo(repo_name, path, entities)
  → Extract imports/exports from entities
  → Add node with import/export lists
build_cross_repo_edges()
  → shared_import: two repos import same module
  → shared_entity: two repos export same entity name
  → depends_on: repo A imports what repo B exports
```

### 7. Retriever (`harness/retriever.py`)

**Purpose**: Hybrid search fusing three independent retrieval signals.

```
retrieve(query, debug=False)
  ├── Dense (30%): vector_store.search(embed_query(query))
  ├── Sparse (25%): BM25 keyword scoring
  ├── Graph (20%): beam neighbor expansion (width=6, depth=2; `expand_mode=bfs` for old BFS)
  ├── Wiki (opt-in): `kind=wiki` / `knowledge/wiki/` channel via RRF when `wiki_weight` > 0 (default 0.0)
  ├── Fusion: RRF with k=60
  ├── Cross-encoder rerank: cross-encoder/ms-marco-MiniLM-L-6-v2
  └── Return top-k (default: 30)
       debug=True → returns (results, trace) tuple with per-source breakdown,
                    fused pre-CE list, and latencies_ms (dense/bm25/graph/ce)
```

Key design:
- **BM25 persistence**: serialized to `bm25_{repo}.pkl` for fast cold starts on re-query, falls back to ChromaDB load
- **Cross-encoder**: cached as global singleton; reranks top `rerank_top_k*2` candidates; falls through silently on import failure
- **RRF formula**: `score = Σ weight * 1/(k + rank)` for each result list
- **Graph boost**: graph-matched results get `score + graph_weight * max_fused_score`
- **Debug mode**: when `debug=True`, returns `(results, {dense, sparse, graph, fused, reranked, latencies_ms})` — used by `--debug` and `eval`

### 8. Context Builder (`harness/context_builder.py`)

**Purpose**: Assemble retrieved chunks into an optimal LLM context window.

```
build_context(query, results)
  ├── load_project_context(): inject ARCHITECTURE.md / AGENTS.md / CLAUDE.md
  ├── load_memory_brief(): opt-in typed memory (default off)
  ├── load_knowledge_prefix(): opt-in knowledge/** slice (default off, 800-token budget)
  ├── deduplicate(): remove overlapping line ranges, keep higher-scored
  ├── rerank(): boost for term overlap, entity type, docstrings
  ├── diversity_rerank(): MMR with lambda=0.3
  └── assemble_context(): full text (default) or CCR-lite signatures + key spans
build_context_report(...) → context + prompt_tokens + packed ids/paths + MMR ms
                           + prompt_tokens_full / prompt_tokens_packed + prefix_hash
```

Pack modes (`context.pack_mode`, default **`full`**):

- `full` — today's file-grouped assembly (golden-string compatible).
- `ccr_lite` — KV-cache-friendly order: `ARCHITECTURE.md` / `AGENTS.md` / `CLAUDE.md` first, then the query and packed hits. Bodies collapse to first/last lines with `retrieve_chunk <id>`; originals live in `CCRCache` (optional `.code-harness/ccr/` spill).

Ranking (dense / BM25 / graph / CE / MMR) is unchanged. Expand via `python main.py retrieve-chunk <id>` or `query --expand-chunk <id>`.

After assembly, `ContextBuilder` runs `harness/redact.py` (default on) so packed/LLM text never carries common secrets. Hits append fingerprints to `.code-harness/audit/audit.jsonl` (`python main.py audit show --last 20`). Disable only with `--no-redact` / `CODEHARNESS_REDACT=0`. `LLMInterface.prepare_outbound` is the same helper PR5 should call on retrieve/API bodies.

MMR diversity: `MMR_score = relevance - lambda * max(similarity_to_selected)` prevents the same file from dominating the context window.

Context output format:
```
# Query: <user question>

## Relevant Code Context

### Project-level Context Files
**ARCHITECTURE.md** (project root)
``` ... ```

### File: <path>
**Function: `name`** [Lines X-Y] (relevance: 0.XX)
```code```
```

### 9. LLM Interface (`harness/llm.py`)

**Purpose**: Send assembled context + question to an AI model and return the answer.

**Providers**:
| Provider | Client | Notes |
|----------|--------|-------|
| openai | `openai.OpenAI` | gpt-4o, gpt-4-turbo, etc. |
| anthropic | `anthropic.Anthropic` | claude-3-opus, claude-3-sonnet |
| gemini | `openai.OpenAI` (compat) | gemini-2.5-flash (via OpenAI-compat endpoint) |
| ollama | HTTP API | codellama, llama3, mistral |
| custom | HTTP API | Any OpenAI-compatible endpoint |

- All provider chat completions wrapped with `retry_with_backoff` (except streaming)
- `stream_query()` for token-by-token streaming (OpenAI/Gemini only)
- Auto-resolves API keys from environment variables per provider

## Data Flow

### Index Flow

```
Files on disk
    │
    ▼
[Parser.discover_files()]
    │ Filters by extension, excludes patterns, max size
    ▼
[Parser.parse_repository()] → List[CodeEntity]
    │ ThreadPoolExecutor parallel file parsing
    │ Tree-sitter or regex per language
    ▼
[CodeChunker.chunk_entities()] → List[Chunk]
    │ Entity-type-aware splitting
    ▼
┌───────────────────────────────────────────┐
│ [Embedder.embed()] → List[float[]]        │
│ [VectorStore.add_chunks()] → ChromaDB     │
└───────────────────────────────────────────┘
    │
    ▼
[KnowledgeGraph.build()] → NetworkX DiGraph
    │
    ▼
[KnowledgeGraph.save()] → graph_{repo}.json
    │
    ▼
[Retriever.index_chunks(persist=True)] → bm25_{repo}.pkl
    │
    ▼
[RepoGraph.update_repo()] → inter-repo edges
[RepoGraph.save()] → repo_graph.json
```

### Query Flow

```
User Query (--debug flag optional)
    │
    ▼
[Embedder.embed_query()] → query vector (HyDE optional)
    │
    ├──→ [VectorStore.search()] → dense results (0.3 weight)
    │
    ├──→ [BM25.get_scores()] → sparse results (0.25 weight)
    │
    └──→ [KG.get_related_chunks()] → graph results (0.2 weight)
    │
    ▼
[RRF Fusion] → merged + deduplicated results
    │
    ├─── [--debug] → print per-source tables to stdout
    │
    ▼
[Cross-Encoder Rerank] → re-score top candidates
    │
    ▼
[ContextBuilder.build_context()]
    │  Inject ARCHITECTURE.md / AGENTS.md / CLAUDE.md
    │  Deduplicate by line range
    │  Rerank (term overlap, entity type, docstring)
    │  MMR diversity rerank (lambda=0.3)
    │  Assemble context within token budget
    ▼
[LLMInterface.query()] → AI response (retry-wrapped)
```

## Storage Layout

```
.code-harness/
├── chromadb/
│   ├── chroma.sqlite3
│   └── ...                      # HNSW index + document metadata
├── graph_{repo}.json            # Per-repo knowledge graph
├── bm25_{repo}.pkl              # Per-repo BM25 serialized index
├── repo_graph.json              # Inter-repo relationship graph
```

**No `chunks.json`**: Chunks are loaded from ChromaDB at query time.

## Key Design Decisions

1. **Local-first**: All components work offline. No API calls required for indexing. Embedding uses local sentence-transformers by default.

2. **Hybrid retrieval**: No single method is sufficient for code. Semantic search finds conceptually related code, BM25 finds exact name matches, graph traversal finds related entities.

3. **Entity-aware chunking**: Code is not prose. Chunking by function/class boundaries preserves logical units. File-level chunks are fallback for amorphous files.

4. **Pluggable LLMs**: Retriever + context builder decoupled from LLM. Use any model without re-indexing.

5. **Persistent storage**: Index once, query many times. ChromaDB, BM25, and knowledge graphs persist to disk.

6. **Multi-repo**: Tagged chunks, per-repo persistence, incremental clear, inter-repo relationship tracking.

7. **Resilience**: All HTTP calls wrapped with retry+backoff. Cross-encoder/BM25 fall through silently on failure.

## Performance Considerations

- **Embedding**: ~38s for 275 chunks on Apple Silicon M-series (all-MiniLM-L6-v2)
- **Vector search**: <10ms per query (ChromaDB HNSW, ef_search=256)
- **BM25**: <5ms per query (pre-built inverted index from pkl)
- **Graph traversal**: <2ms per query (NetworkX in-memory)
- **Cross-encoder**: ~50ms for 30 pairs
- **Context assembly**: <1ms per query
- **Total query time (no LLM)**: ~1-2s (including embedding)
- **Storage**: ~5-10MB per 10K chunks (ChromaDB + graph + BM25 pkl)
