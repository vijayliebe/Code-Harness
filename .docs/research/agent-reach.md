# Agent-Reach

## What it is
[Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) — capability layer that gives AI agents **internet/platform access without paid APIs** where possible: selects, installs, health-checks, and routes primary+fallback backends per platform.

## Links
- https://github.com/Panniantong/Agent-Reach
- EN docs: https://github.com/Panniantong/Agent-Reach/blob/main/docs/README_en.md
- Install skill URL in README

## How it works
- Philosophy: **not a scraper wrapper** — agents call upstream tools directly; Agent-Reach maintains ordered backend lists + `agent-reach doctor`.
- Platforms: Web (Jina Reader — no key), GitHub (`gh`), YouTube (yt-dlp), Bilibili, RSS, Twitter (cookie), Reddit (OpenCLI/cookie), LinkedIn (**Jina for public pages**; full profiles/jobs via browser MCP `mcp-server-linkedin` after local login — session in `~/.linkedin-mcp/profile/`), Exa search, etc.
- LinkedIn without API keys: public pages via Jina; richer access via browser session MCP, not LinkedIn Marketing API.
- Cookies stay local; MIT.

## Relevance to Code-Harness
**Medium (adjacent).** Useful if Code-Harness expands to index *external* docs/issues/LinkedIn job specs; not core to local code RAG. Pattern of **primary+fallback channel registry + doctor** is excellent for embedding providers.

## Concrete ideas to adopt
1. **Provider channel registry** (perf/reliability): Ordered backends for embeddings/LLM (local → Voyage → OpenAI) with `code-harness doctor`.
2. **Jina Reader ingest** (accuracy): Optional `index-url` to pull README/docs as Markdown into BM25/vector.
3. **No-key web path for docs** (cost): Prefer free readers before paid crawl APIs.
4. **Don’t scrape LinkedIn by default** (governance/ToS): If needed, document browser-session MCP path explicitly; respect ToS.

## Risks/caveats
- Platform ToS / account bans for cookie/browser automation.
- Off-mission for pure local code RAG.

## Open questions
- Should Code-Harness grow `index-url` / issue ingest, or stay repo-local?
