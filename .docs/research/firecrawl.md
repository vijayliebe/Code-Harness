# Firecrawl

## What it is
Web data API for AI agents: **search, scrape, crawl, map, interact, parse** → clean Markdown/HTML/JSON. Open-source + hosted. MCP server available (including keyless tier for basics).

## Links
- GitHub: https://github.com/firecrawl/firecrawl
- Docs: https://docs.firecrawl.dev/
- MCP: `https://mcp.firecrawl.dev/v2/mcp`

## How it works
- Engines race (fetch, Playwright, PDF, …) → transformers (clean HTML → Markdown → optional LLM extract).
- `/scrape`, `/crawl`, `/map`, `/search`, `/agent`, Interact (click/fill on live page).
- Self-host or cloud; SDKs Python/Node/CLI.

## Relevance to Code-Harness
**Medium.** Best for **ingesting external documentation / blog / API refs** into the same hybrid index as code — not for parsing source trees (tree-sitter already wins there).

## Concrete ideas to adopt
1. **`index-url` / `index-site`** (accuracy): Firecrawl Markdown → chunk → BM25+vector with `source=web` metadata.
2. **Doc+code joint retrieval** (accuracy): “How do we use library X?” mixes local wrappers + upstream docs.
3. **Prefer Firecrawl for JS-heavy docs** (accuracy); Jina Reader for simple pages (cost) — Agent-Reach style fallback.
4. **MCP optional** for agent workflows that research then code.

## Risks/caveats
- Hosted costs; self-host ops.
- Stale web docs vs local code version mismatch — version pin metadata.
- ToS on target sites.

## Open questions
- Default to Jina (free) with Firecrawl as upgrade path?
