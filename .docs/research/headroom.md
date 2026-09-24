# Headroom

## What it is
Local-first **context optimization layer** for LLM apps/agents. Compresses tool outputs, logs, files, RAG chunks, and conversation history *before* they reach the model — with reversible compression so the model can fetch originals on demand.

## Links
- GitHub: https://github.com/headroomlabs-ai/headroom
- Docs: https://docs.headroomlabs.ai/
- PyPI: `headroom-ai`
- Related: Serena (semantic code nav), Kompress-v2-base (HF text compressor)

## How it works
Pipeline (local): **CacheAligner → ContentRouter → compressor → CCR**

- **ContentRouter** classifies content (JSON/logs/code/prose/search results/images) and picks a compressor.
- **SmartCrusher** — statistical compression for JSON arrays: keeps errors, anomalies, boundaries; typical 70–90% savings on structured tool output.
- **CodeCompressor** — tree-sitter AST-aware (signatures kept, bodies optionally collapsed); gated behind safety (recent-code protection, analysis-intent protection); often passthrough by default for correctness.
- **Kompress-v2-base** — HF model for prose redundancy removal (~30–50%).
- **CCR (Compress-Cache-Retrieve)** — originals stored locally with hash; LLM gets `headroom_retrieve` tool to expand when needed.
- **CacheAligner** — flags volatile content that would bust provider KV-cache prefixes (does not rewrite prompts).
- Modes: Python/TS library `compress()`, transparent proxy, `headroom wrap <agent>`, MCP server.
- Also: output-token shaping (terse steering + effort routing), `headroom learn` (mines failed sessions → AGENTS.md/CLAUDE.md corrections), cross-agent memory.

Published directional savings (own benches): code search ~21%, SRE logs ~57%, codebase exploration ~42%; JSON/logs can hit 60–95%. Compression latency claimed sub-ms for typical payloads.

## Relevance to Code-Harness
**High.** Code-Harness already builds LLM context from hybrid retrieval. Headroom is the natural *post-retrieval / pre-LLM* token-saving layer — especially for tool-heavy agent sessions that consume Code-Harness output.

## Concrete ideas to adopt
1. **Reversible context compression after MMR assembly** (tokens): Compress large chunk bodies to signatures + first/last N lines; keep full text in a local CCR keyed by chunk_id; expose `retrieve_chunk(id)` tool to the LLM. Why: same answers, far fewer prompt tokens on large repos.
2. **Content-type-aware assembly** (tokens/accuracy): Route tool/log/JSON vs code vs markdown through different compressors (like ContentRouter). Why: code needs AST care; JSON search results do not.
3. **KV-cache-friendly prompt layout** (perf/cost): Stabilize system prompt + ARCHITECTURE.md prefix; put volatile retrieved chunks at the end. Why: provider prompt caching becomes a real saving.
4. **Optional Headroom proxy integration** (tokens): Document wrapping Code-Harness LLM calls through `headroom proxy` with zero core changes.
5. **Failure learning → AGENTS.md** (accuracy): Mine wrong answers / user corrections into repo `AGENTS.md` the way `headroom learn` does.

## Risks/caveats
- Aggressive code compression can hurt correctness; Headroom itself defaults code/RAG docs to passthrough for this reason.
- Headline savings are workload-dependent; must benchmark on Code-Harness contexts.
- Extra local process/proxy complexity.

## Open questions
- Best default: signature+docstring compression vs full-chunk CCR?
- Does CCR round-trip add more tokens (tool calls) than it saves on average for code Q&A?

## Deep dive
Design note: [deep/headroom.md](deep/headroom.md)
