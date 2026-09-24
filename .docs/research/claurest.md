# Claurst (Claurest / ClauRest)

## What it is
**Best match:** [Kuberwastaken/claurst](https://github.com/Kuberwastaken/claurst) — open-source **Rust reimplementation of Claude Code–style terminal agent**: streaming, 40+ tools, multi-provider, ratatui TUI, plugins, ACP (Agent Client Protocol), memory consolidation, `/compact`, `/caveman` (telegraphic speech for token savings).

**Near-miss candidates (documented):**
- `triepod-ai/claude-cli-rest-api` — FastAPI wrapper around Claude Code CLI
- `tigz/claude-code-plugin-rest-api` — NestJS REST for Claude plugins/agents

User spelling “Claurest” most closely matches **Claurst**.

## Links
- Primary: https://github.com/Kuberwastaken/claurst
- Docs (index): https://github.com/Kuberwastaken/claurst/blob/main/docs/index.md
- REST wrappers (secondary): https://github.com/triepod-ai/claude-cli-rest-api

## How it works
- Clean-room Rust agent with multi-provider Messages API + SSE streaming (`cc-api` crate).
- Tools, sandbox, managed agents, chat forking, memory consolidation.
- **ACP** over JSON-RPC/stdio for editor integration (Zed, Neovim, JetBrains).
- Session commands: `/compact` (history compression), `/cost`, `/insights`, `/caveman` (terse speech mode), `/ultrareview`, advisor model, rewind/export.
- Prompt-cache friendly: `CacheControl` ephemeral on tool defs.

## Relevance to Code-Harness
**Medium.** Not a RAG library — a coding-agent product. Transferable ideas: session compact, cost telemetry, terse modes, ACP as a way to *expose* Code-Harness as an editor-native code-memory backend.

## Concrete ideas to adopt
1. **`/compact` for interactive mode** (tokens): Summarize prior turns + keep last N retrieval packs. Why: interactive REPL grows unbounded.
2. **`/cost` + token telemetry** (cost): Per-query token/cost breakdown (dense/BM25/graph/LLM). Why: users optimize what they see.
3. **Terse / caveman mode** (tokens): System prompt option to forbid restating retrieved code. Why: output tokens dominate cost on Opus-class models.
4. **ACP or MCP server mode** (perf/product): Expose Code-Harness retrieve as ACP/MCP so editors use it like Claurst uses tools.
5. **Prompt caching on tools/system** (cost): Mark stable tool schemas + ARCHITECTURE.md as cacheable prefixes.

## Risks/caveats
- GPL-3.0 license on Claurst — don’t copy code; take ideas only.
- Competing as “another Claude Code” is out of scope for a RAG harness.

## Open questions
- Should Code-Harness stay retrieval-only or grow a thin agent loop like Claurst?
