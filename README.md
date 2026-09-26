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

# Interactive session (aliases: chat, session, repl)
python main.py chat /path/to/your/repo
python main.py chat /path/to/your/repo --profile sage

# Local health check + localhost retrieve / MCP
python main.py doctor /path/to/your/repo
python main.py serve /path/to/your/repo
```

## Testing

```bash
make test      # python3 -m unittest discover -s tests -v
make eval-ab   # python3 main.py eval-ab .  — skips cleanly (exit 0) if the turbovec extra is missing
```

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and on push to `main` (Ubuntu, Python 3.12): install `requirements.txt`, `make test`, then the canonical eval below. It does not run `make eval-ab`.

## Canonical eval recipe

Same commands CI uses (local embeddings; no LLM key):

```bash
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
```

See [`.docs/research/eval/README.md`](.docs/research/eval/README.md) for the suite schema, metrics, and the optional TurboVec A/B (`make eval-ab`).

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

### `chat` / `session` / `interactive` — Multi-turn REPL

```bash
python main.py chat ./my-project
python main.py session ./my-project --profile sage
python main.py interactive --cross-repo ./my-project   # same command
```

Turns are stored as JSONL under `.code-harness/sessions/` (ephemeral dialogue — not typed memory and not the code index). Default files stay the mixed `event: turn` log. Opt-in **event-sourced session** (`--event-session`, `CODEHARNESS_EVENT_SESSION=1`, or `session.event_session`) appends typed events (`user` / `assistant` / `tool_use` / `tool_result` / `system` / `compact` / `clear` / `verify` / `meta`); model history is **derived** (`derive_messages`) so `/compact` and tool-result clearing are events, not a rewrite of the only copy. Resume reloads the log and re-derives. The flag implies **prefix-stable packing** (`context.prefix_stable` / `--prefix-stable`): system sections + sorted tool schemas + path-stable knowledge prefix stay byte-identical across packs; volatile hits last. **Default off.** With an event log, **session-event FTS** (SQLite FTS5 sidecar `<session>.fts.sqlite`, not BM25-over-memory) lets you find prior turns without dumping the transcript: `python main.py session search "…"`, REPL `/search`, MCP `search_session`. Legacy `event: turn` files are **not** indexed — `/search` and `session search` require `--event-session` (or a typed event JSONL). `query` / `chat` / session run retrieve as a shared **pre-step hook** (`harness/prestep.py`, default **on**; `--no-retrieve-prestep` / `CODEHARNESS_RETRIEVE_PRESTEP=0` skips, or when a caller already supplies packs). `/compact` is extractive conversation compression (keep user lines + the latest pack ids); it is not the CCR packer. Opt-in **tool-result clearing** (`--clear-tool-results`, `CODEHARNESS_CLEAR_TOOL_RESULTS=1`, or `session.clear_tool_results`) replaces aged retrieve/`tool_result` dumps with short placeholders that keep `chunk_id` / path / tool+args so `/expand` and `retrieve_chunk` still work. Opt-in **default-fail verify gate** (`--verify`, `CODEHARNESS_SESSION_VERIFY=1`, or `session.verify`) starts completion criteria all-false; the builder cannot mark them true. `/verify` runs an independent read-only checker (file / command / contains / coverage first; LLM only as fallback). `/done` is refused until the gate is green unless you pass `--force-done` or `/done --force`. **Both default off.** `/compact` runs clearing as a micro-step only when the flag is on; `/clear-tool-results` is the explicit slash. `/cost` prints packed vs full prompt tokens, completion tokens, loop attempts, query-cache hit/miss when `--query-cache` is on, **tool-result tokens freed** when clearing fires, and approx $ when `llm.input_usd_per_1m` / `llm.output_usd_per_1m` are set. Without an LLM key the session still retrieves, packs, and prints context.

`--profile sage` (or `CODEHARNESS_PROFILE=sage`, or `/profile sage`) is a flag pack: `pack_mode=ccr_lite`, `max_loops=1`, larger graph expand, higher pack budget. It does **not** retune RRF weights. One-shot `query` stays unchanged unless you pass `--profile`.

`--wiki` (or `CODEHARNESS_WIKI_MODE=1`, config `chat.wiki_mode`, or `/wiki` / `/wiki on` in the session) is **chat-over-wiki**: retrieve and pack `kind=wiki` / `knowledge/wiki/**` first, keep code as fallback when the vault is sparse, and CCR-expand linked `` `path:symbol` `` cites when the pack needs depth. **Default is off** (eval-safe; ordinary chat/query ranking unchanged). System framing then says: answer from the wiki first and cite `` `knowledge/wiki/<page>` `` plus `` `path:symbol` ``.

System framing always includes a `path:symbol` citation instruction. Explain/why queries (and packs that omit >50% of bodies with no loop) auto-expand a few cached chunks.

```
/help                     Show available commands
/compact                  Summarize older turns; never drop the latest pack
/search <query>           FTS over the event log (requires --event-session)
/clear-tool-results       Replace aged retrieve dumps with re-fetch stubs
/cost                     Session token counters (+ $ if a rate is configured)
/profile default|sage     Switch the flag pack
/expand <id|path:symbol>  Print a CCR-cached original (`/retrieve` is an alias)
/memory brief             Dump the typed memory brief
/wiki                     Toggle chat-over-wiki (ask the living wiki)
/wiki on|off              Enable or disable wiki mode
/wiki <page>              Show a generated wiki page
/verify                   Independent completion-criteria check (default-fail)
/done                     Accept completion only if the verify gate is green
/done --force             Override the verify gate (explicit)
/llm on|off               Enable/disable AI responses
/context                  Show the last retrieved context
/clear                    Clear the screen
/exit                     Leave (`/quit` is an alias)
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

### `wiki` — Living project wiki from the KG

Template-only (no LLM). Reads `graph_{repo}.json` and writes OKF `WikiPage` markdown under `knowledge/wiki/` (override with `--out`, including `.code-harness/wiki/`). Pages are package-level plus an architecture index; they cite `path:symbol` (never chunk UUIDs) and embed Mermaid from existing graph edges. Human `.docs/` files are never overwritten.

```bash
python main.py index .
python main.py wiki generate .
python main.py wiki generate . --dirty          # only pages whose sources / subgraph changed
python main.py wiki list .
python main.py wiki show architecture
python main.py wiki generate . --module harness --out knowledge/wiki
```

`--dirty` compares source-file content hashes and per-package KG subgraphs to `knowledge/wiki/.wiki-manifest.json`. It rewrites only affected package (module) pages plus the architecture index. Full `wiki generate` still rebuilds the vault. Missing graph: full generate exits non-zero; `--dirty` fails soft (prints a skip, exit 0). `watch` runs the same dirty path after re-index when a vault already exists.

OKF = [Open Knowledge Format (Google SPEC v0.2)](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md). We emit a code-repo subset (`type: WikiPage`, `okf_version: "0.2"`, `x_codeharness` citations). See `harness/okf.py` for the field mapping. Missing graph → clear error asking you to `index` first.

Indexed wiki pages (`kind=wiki` or `knowledge/wiki/<page>`) can join hybrid RRF as a fourth list when `retrieval.wiki_weight > 0`. **Default is `0.0`** (off — eval-safe; existing dense/BM25/graph ranking unchanged). A careful starting weight is `0.08`–`0.12`. Source citations stay `` `path:symbol` ``; wiki hits may cite `knowledge/wiki/<page>`.

Chat-over-wiki is a separate UX switch, not a new generator:

```bash
python main.py chat . --wiki
python main.py query . --wiki --no-llm -q "how does context assembly fit?"
```

`--wiki` / `chat.wiki_mode` turns on the wiki RRF channel (weight `0.15` when `wiki_weight` is still `0.0`), prefers wiki pages in the pack, prefix-loads the vault if the index is sparse, and CCR-expands linked `` `path:symbol` `` / child pages. Code cites stay allowed. Turn it off and ranking/prompt are the previous defaults.

### `memory` — Typed project memory + OKF import/export

Local store of `decision` / `error` / `preference` / `fact` beside the code index (not written into Chroma). Files are OKF markdown under `knowledge/memory/` (override with `--dir`; `.code-harness/memory/` is also read if present). New entries with `--supersedes <id>` tombstone the old file (`status: superseded`). `memory brief` packs **active** entries only, hard-capped at 800 tokens (same `len//4` estimator as the packer). `memory search` is on-the-fly BM25 over those same active files (debug; superseded excluded).

```bash
python main.py memory add . --type decision --title "Default vector backend stays Chroma" \
  --body "Keep Chroma until TurboVec recall gates pass." \
  --link harness/vector_store.py:VectorStore
python main.py memory list .
python main.py memory list . --all --as-of 2026-06-01
python main.py memory brief . -q "why chroma?"
python main.py memory search . -q "chroma default"
python main.py memory export . ./okf-bundle
python main.py memory import . ./okf-bundle
```

Query/interactive can inject the brief **between** project docs and packed hits, default off:

```bash
python main.py query . --no-llm --include-memory-brief -q "why is Chroma the default?"
python main.py query . --no-llm --include-memory-search -q "why is Chroma the default?"
python main.py memory brief . --redact
python main.py memory search . -q "chroma default" --redact
python main.py memory export . ./okf-bundle --redact
```

Active memory can also join hybrid RRF as a fifth list when `retrieval.memory_weight > 0`. **Default is `0.0`** (off — eval-safe; wiki/code ranking unchanged). `--include-memory-search` / `CODEHARNESS_MEMORY_SEARCH=1` applies weight `0.15` when the configured weight is still `0.0`. Hits cite `knowledge/memory/<kind>/<file>` and keep the OKF `mem/…` id. The packer drops memory RRF hits already injected by `--include-memory-brief` and path-dedupes them against `--include-knowledge-prefix`.

### `knowledge` — Full-vault OKF export/import

One command copies the whole knowledge vault (generated wiki pages, typed memory, and gloss notes under `knowledge/gloss` or `.code-harness/gloss`) into an OKF-compatible bundle with `okf-manifest.yaml` (`okf_version`, `generated`, per-kind counts). Import writes the same relative paths back. Unknown frontmatter keys (`x_memanto`, `x_other`, gloss `entity:`) survive because files are copied, not rewritten. Empty vaults fail soft (manifest only, exit 0). Alias: `okf`.

```bash
python main.py knowledge export . --out ./okf-bundle
python main.py knowledge import ./okf-bundle --repo ./other-repo
python main.py okf export . --out ./okf-bundle --redact
```

`harness.okf.load_knowledge_docs` prefix-loads `knowledge/**/*.md` into the packer when **`context.knowledge_prefix`** is on (CLI `--include-knowledge-prefix`, env `CODEHARNESS_KNOWLEDGE_PREFIX=1`). **Default is off** (eval-safe, same posture as `wiki_weight` and `--include-memory-brief`). When enabled, a keyword-ranked slice is capped at **`context.knowledge_token_budget`** (default **800**, same `len//4` estimator). Wiki cites stay `` `knowledge/wiki/<page>` ``; memory entries keep their OKF type; source cites stay `` `path:symbol` ``. Paths already present as wiki RRF hits are skipped, and memory pages are skipped when `--include-memory-brief` is also on (no double-stuff).

```bash
python main.py query . --no-llm --include-knowledge-prefix -q "why is Chroma the default?"
```

### `doctor` — Local health check

No network. Prints pass / warn / fail plus a fix hint. Exit `0` when required checks pass (missing cloud LLM key is a warn). Exit `1` when index, graph, embedding config, core deps, or the audit path fail.

```bash
python main.py doctor .
python main.py doctor ./my-project
```

Checks: Python 3.9+, importable deps, Chroma persist + `graph_{repo}.json`, embedding provider/model (API key presence only — no ping), redaction enabled, audit JSONL writable, optional LLM key.

### `serve` / `mcp serve` / `api serve` — Localhost retrieve + MCP

Binds **127.0.0.1 only** by default (no auth). Refuses `0.0.0.0` / `::` / non-loopback hosts unless you pass `--allow-public` (dangerous: no authentication). Response bodies are redacted via `harness.redact.redact_and_audit`. Optional SQLite query-hash cache under `.code-harness/query_cache.sqlite` (serve default-on, `--no-cache` to disable). The same cache is **opt-in** on `query` / `chat` / `eval` via `--query-cache` or `CODEHARNESS_QUERY_CACHE=1` (`--no-query-cache` / `CODEHARNESS_QUERY_CACHE=0` disables).

```bash
python main.py serve .
python main.py mcp serve . --port 7432
python main.py api serve . --pack-mode ccr_lite
# DANGEROUS — all interfaces, no auth:
python main.py serve . --host 0.0.0.0 --allow-public
```

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness + bind + redact flag |
| `POST /v1/retrieve` | `{"query": "...", "top_k": 20, "pack_mode": "full"}` → ranked ids/paths + packed context |
| `POST /mcp` | JSON-RPC 2.0 `initialize` / `tools/list` / `tools/call` |

MCP tools: `retrieve`, `retrieve_chunk`, `doctor`, `wiki_show`, `memory_brief`, `graph_neighbors`, `search_session`. Same pipeline flags as `query` (`--pack-mode`, `--loop`, `--profile sage`, `--include-memory-brief`, `--include-memory-search`, `--include-knowledge-prefix`, `--no-redact`).

```bash
curl -s http://127.0.0.1:7432/health
curl -s http://127.0.0.1:7432/v1/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"how does the chunker work?","top_k":10}'
```

Does **not** start on import. Index stays on disk; this is not Forge hosted `:sync` and not a cloud deploy.

### `mcp stdio` — IDE MCP over stdin/stdout

Same tool surface as `POST /mcp`, but JSON-RPC on **stdin/stdout** (no port, no bind). Status logs go to **stderr** so stdout stays protocol-only. Index the repo first (`python main.py index .`).

```bash
python main.py mcp stdio .
python main.py mcp serve --stdio .
python main.py mcp --stdio
```

Wire Cursor / other MCP hosts to the subprocess (not `http://127.0.0.1:7432/mcp`):

```json
{
  "mcpServers": {
    "code-harness": {
      "command": "python3",
      "args": [
        "/absolute/path/to/code-harness/main.py",
        "mcp",
        "stdio",
        "/absolute/path/to/your-indexed-repo"
      ]
    }
  }
}
```

Cursor: Settings → MCP → add the server above (or merge into `~/.cursor/mcp.json`). Claude Desktop uses the same `mcpServers` shape in its config file. The process must be able to import this repo (`cwd` can be the harness checkout or the target repo; pass an absolute `main.py`). Outbound tool payloads still go through `redact_and_audit`.

### `audit` — Secret redaction log

Outbound packed/LLM text is redacted by default (API keys, tokens, PEM blocks, `.env` assignments, Bearer headers, connection-string passwords). Session JSONL and chat prints use the same helper. Wiki generate strips env-like echoes. Memory brief/export redact only when `--redact` is passed.

Append-only events land in `.code-harness/audit/audit.jsonl`: timestamp, action, redaction counts, fingerprint hashes (never the raw secret), optional query/session id.

```bash
python main.py audit show --last 20
python main.py audit tail --path .code-harness/audit/audit.jsonl
# tests / explicit opt-out only:
CODEHARNESS_REDACT=0 python main.py query . --no-llm -q "..."
python main.py query . --no-redact --no-llm -q "..."
```

Sample `path:symbol` entries in this repo: `knowledge/memory/decision/2026-09-24-chroma-default.md` (`harness/vector_store.py:VectorStore`) and `knowledge/memory/error/2026-09-24-faiss-oom.md` (`harness/vector_store.py:VectorStore.add_chunks`). Mapping: [`harness/okf.py`](harness/okf.py) and [`.docs/research/fusion/notes/memory-okf.md`](.docs/research/fusion/notes/memory-okf.md).

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

Local, retrieval-only (no LLM, no API keys). Requires a prior `index` of the repo. The first two commands are the [canonical recipe](#canonical-eval-recipe) (same as CI).

```bash
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --dry-run
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --compare-backends chromadb,turbovec
python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --query-cache
python3 main.py eval-ab .
```

Reports Recall@k, nDCG@k, citation-path hit rate, stage latency (dense / BM25 / graph / CE / MMR), estimated prompt tokens after context assembly (`prompt_tokens_full` vs `prompt_tokens_packed`), and easy/hard splits. Writes `.code-harness/eval/{suite}-{timestamp}.json`. Use `--pack-mode ccr_lite` to score citation paths against packed headers (Recall@k is unchanged). `--loop` / `--max-loops N` is opt-in; default remains one-shot. `--query-cache` reuses retrieve/pack on identical queries (hit/miss in the summary); Recall@k stays identical.

`--compare-backends chromadb,turbovec` prints a side-by-side Recall@k / nDCG@k table and writes `.code-harness/eval/{suite}-ab-{timestamp}.json` (override with `--compare-output` / `--compare-markdown`). `eval-ab` indexes both persist dirs and writes [`.docs/research/eval/RESULTS.md`](.docs/research/eval/RESULTS.md). If `turbovec` is the selected backend and it misses the gate (more than 5% relative drop, or the deep-dive point limits: Recall@10 −2 pts / Recall@30 −1), eval exits non-zero unless `--force-experimental`. Missing `turbovec` extra is an honest skip, not a silent FAISS swap.

See [`.docs/research/eval/README.md`](.docs/research/eval/README.md) for the fixture schema, failure taxonomy (`dense_miss | bm25_miss | graph_miss | rerank_drop | packer_drop`), and the experimental TurboVec recall gate.

### Experimental TurboVec backend (recall-gated)

Default dense store remains **Chroma**. [TurboVec](https://github.com/RyanCodrai/turbovec) (`pip install turbovec`, extra `requirements-turbovec.txt`) is an optional 4-bit TurboQuant index (`IdMapIndex`). It is **not** the default and must not be silently replaced by FAISS.

```bash
# 1. Optional extra (Rust wheel). Unittest suite stays green without it.
pip install -r requirements-turbovec.txt

# 2. Rebuild into the TurboVec persist dir (do not reuse .code-harness/chromadb)
python main.py index . --vector-backend turbovec

# 3. A/B vs the Chroma index (needs both indexes present)
python main.py eval-ab .
# or explicitly:
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \
  --compare-backends chromadb,turbovec \
  --compare-markdown .docs/research/eval/RESULTS.md \
  --compare-output .docs/research/eval/RESULTS.json
```

Config (never flip `type` in a copied blog snippet without the gate):

```json
{
  "vector_store": {
    "type": "turbovec",
    "turbovec": { "bits": 4, "persist_directory": ".code-harness/turbovec" }
  }
}
```

CLI `--vector-backend` / `CODEHARNESS_VECTOR_BACKEND` override `vector_store.type` (`chroma`/`chromadb`, `turbovec`). Switching backends requires a **full re-index**. `doctor` warns while type is `turbovec`. Gate: fail if selected TurboVec Recall/nDCG is >5% relative below Chroma, or Recall@10 drops >2 points / Recall@30 >1 point. `--force-experimental` records the miss and continues. If the wheel is missing, eval skips the TurboVec column instead of inventing another ANN.

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

The corrective loop is **off by default** (`retrieval.max_loops: 0`) so one-shot latency is unchanged. Enable with `--loop` (one extra retrieve) or `--max-loops N` (0–2 extra). Identifier-like queries short-circuit to BM25-only. Query `--verify` adds an independent LLM citation check (off by default). Chat/session `--verify` also enables the default-fail completion gate (`session.verify`).

### Retrieval Pipeline

1. **Dense retrieval**: query embedded with sentence-transformers/Voyage/Jina/OpenAI, top-K from the configured vector backend (default **ChromaDB** HNSW, `ef_search=256`; opt-in experimental **TurboVec** / TurboQuant)
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
    "wiki_weight": 0.0,
    "memory_weight": 0.0,
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
    "pack_mode": "full",
    "max_tokens_multiplier": 2,
    "include_memory_brief": false,
    "include_memory_search": false,
    "knowledge_prefix": false,
    "knowledge_token_budget": 800,
    "prefix_stable": false
  },
  "ccr": {
    "first_lines": 12,
    "last_lines": 8,
    "spill_dir": ".code-harness/ccr",
    "expand_on": []
  },
  "session": {
    "dir": ".code-harness/sessions",
    "keep_recent": 1,
    "clear_tool_results": false,
    "clear_tool_keep": 1,
    "clear_tool_token_trigger": 0,
    "verify": false,
    "force_done": false,
    "criteria": [],
    "event_session": false
  },
  "chat": {
    "wiki_mode": false
  },
  "prestep": {
    "retrieve": true
  },
  "redaction": {
    "enabled": true,
    "audit": true,
    "audit_path": ".code-harness/audit/audit.jsonl"
  },
  "serve": {
    "host": "127.0.0.1",
    "port": 7432,
    "cache": true
  },
  "vector_store": {
    "type": "chromadb",
    "hnsw_ef_search": 256,
    "hnsw_ef_construction": 200,
    "hnsw_m": 32,
    "turbovec": { "bits": 4, "persist_directory": ".code-harness/turbovec" }
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
| `--profile` | `default` or `sage` (query/chat). Env: `CODEHARNESS_PROFILE` |
| `--wiki` | Chat-over-wiki (query/chat). Config: `chat.wiki_mode`. Env: `CODEHARNESS_WIKI_MODE` |
| `--query-cache` | Opt-in retrieve/pack cache for `query` / `chat` / `eval` (default off). Env: `CODEHARNESS_QUERY_CACHE=1`. `--no-query-cache` disables. Serve stays default-on (`--no-cache`). |
| `--clear-tool-results` | Opt-in session retrieve/tool dump clearing (default off). Env: `CODEHARNESS_CLEAR_TOOL_RESULTS=1`. Knobs: `--clear-tool-keep`, `--clear-tool-token-trigger`. |
| `--verify` | Query: independent citation check. Chat/session/eval: also the default-fail completion gate (`session.verify`). Env: `CODEHARNESS_SESSION_VERIFY=1`. Override: `--force-done` / `/done --force`. |
| `--event-session` | Opt-in append-only session events + `derive_messages` (default off). Env: `CODEHARNESS_EVENT_SESSION=1`. Implies prefix-stable packing. `--no-event-session` disables. Enables `/search` and a co-located FTS5 sidecar. |
| `session search` | FTS over a typed event log (`session search "…"` / `session-search -q … --session <jsonl>`). Requires `--event-session` file; legacy transcripts refused. No extra config knob. |
| `--prefix-stable` | Freeze system/tool/knowledge prefix bytes (default off; implied by `--event-session`). Env: `CODEHARNESS_PREFIX_STABLE=1`. |
| `--retrieve-prestep` | Shared retrieve-as-pre-step hook (default **on**). Env: `CODEHARNESS_RETRIEVE_PRESTEP=1`. `--no-retrieve-prestep` / `CODEHARNESS_RETRIEVE_PRESTEP=0` / `prestep.retrieve: false` skips. |

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
├── chromadb/              # Default ChromaDB persist (HNSW index + metadata)
│   ├── chroma.sqlite3
│   └── ...
├── turbovec/              # Optional TurboVec persist (index.tvim + sidecar.json)
├── graph_{repo}.json      # Per-repo knowledge graph (NetworkX node-link format)
├── graph.json             # Fallback single-repo graph
├── bm25_{repo}.pkl        # Per-repo BM25 serialized index
├── repo_graph.json        # Inter-repo relationship graph
├── eval/                  # Retrieval eval reports ({suite}-{timestamp}.json)
├── ccr/                   # Optional CCR-lite originals ({sanitized_chunk_id}.txt)
├── audit/                 # Append-only redaction/LLM audit JSONL
├── query_cache.sqlite     # Optional query-hash cache (serve default-on; query/chat/eval opt-in)
├── wiki/                  # Optional generated wiki (`--out .code-harness/wiki`)
├── okf-bundle/            # Default `knowledge export` destination

knowledge/
├── wiki/                  # Default OKF WikiPage markdown from `wiki generate`
├── memory/                # Typed OKF Decision/Error/Preference/Fact
└── gloss/                 # Human gloss notes (entity frontmatter)
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
│   ├── vector_store.py            VectorStore protocol + Chroma default + stub
│   ├── turbovec_store.py          Optional TurboVec (IdMapIndex) adapter
│   ├── vector_eval.py             Recall@k A/B gate (chroma vs turbovec)
│   ├── knowledge_graph.py         NetworkX code relationship graph (intra-repo)
│   ├── kg_enrich.py               exposes / tested_by / gloss + Mermaid export
│   ├── okf.py                     OKF (Google SPEC v0.2) WikiPage + vault export
│   ├── wiki.py                    Deterministic wiki generate from the KG
│   ├── wiki_chat.py               Opt-in chat-over-wiki retrieve/pack/CCR expand
│   ├── repo_graph.py              Inter-repo relationship graph
│   ├── retriever.py               Hybrid retrieval (dense + sparse + graph + cross-encoder)
│   ├── context_builder.py         Context assembly (MMR, prefix docs, full | ccr_lite pack)
│   ├── ccr.py                     CCR-lite pack + retrieve-back cache
│   ├── eval.py                    Golden-suite loader, eval runner, JSON reports
│   ├── metrics.py                 Recall@k, nDCG@k, citation hit, failure taxonomy
│   ├── llm.py                     LLM integration layer (OpenAI/Anthropic/Gemini/Ollama)
│   ├── doctor.py                  Local health checks (no network)
│   ├── serve.py                   Localhost POST /v1/retrieve + MCP JSON-RPC
│   └── utils.py                   Shared utilities (retry, import/export extraction)
├── tests/                         Offline unit tests (eval, CCR, loop, KG, wiki, wiki-chat, memory, vault OKF)
├── knowledge/
│   ├── gloss/                     Human gloss notes (entity frontmatter)
│   ├── memory/                    Typed OKF Decision/Error/Preference/Fact
│   └── wiki/                      Generated OKF WikiPages (`wiki generate`)
├── visualizer/
│   └── visualize.py               Embedding space visualization (PCA/t-SNE)
└── .docs/
    ├── architecture.md            System architecture
    ├── walkthrough.md             End-to-end pipeline walkthrough
    ├── knowledge-graph.md         Graph schema and usage
    └── research/                  External research (INDEX + SYNTHESIS + source notes + deep/)
```
