# OpenHuman

## What it is
Open-source **desktop AI agent** (TinyHumans AI): persistent local memory, integrations, workflows, research, multi-agent orchestration. GPL-3. React/Vite + Tauri v2 + Rust core.

## Links
- GitHub: https://github.com/tinyhumansai/openhuman
- Product: https://tinyhumans.ai/openhuman
- Alt/related casing: openhuman-ai/OpenHuman

## How it works
- Local desktop app with JSON-RPC/CLI.
- Memory backends (incl. agentmemory); MCP servers (memory.search / recall / read_chunk).
- Multi-agent orchestration + skills/integrations.

## Relevance to Code-Harness
**Medium.** Pattern source for local memory MCP and desktop packaging — not a code RAG core.

## Concrete ideas to adopt
1. **Memory MCP tools** (accuracy/tokens): Expose `memory.search` / `code.search` as MCP so other agents (OpenHuman, Claude Code) call Code-Harness.
2. **Local-first default** (cost): Keep index + memory on disk; optional cloud embeddings.
3. **Tree/chunk read tools** (tokens): `read_chunk(id)` instead of dumping whole files into context.

## Risks/caveats
- GPL-3; ideas only.
- Desktop scope ≠ CLI RAG scope.

## Open questions
- Priority of MCP server vs CLI-first roadmap?
