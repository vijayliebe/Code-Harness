# Mini-deepen: Firecrawl

**First-pass:** [../../firecrawl.md](../../firecrawl.md)  
**Live skim (2026-09-25):** [docs.firecrawl.dev](https://docs.firecrawl.dev/)  
**Why this note:** first-pass is accurate but thin on *default path*. Live docs now have a **keyless** MCP/API tier for scrape/search/parse.

## Confirmed mechanisms

- `/scrape`, `/crawl`, `/map`, `/search`, `/interact`, `/parse` (local PDF/DOCX → markdown).
- Keyless `https://mcp.firecrawl.dev/v2/mcp` and unauthenticated scrape with low limits; API key for throughput.
- Self-host OSS exists; hosted is the default gravity well.

## Steal for Code-Harness

`index-url` / `index-site` is real, but **Jina Reader is the local-first default** (Agent-Reach). Firecrawl is the **fallback when Jina returns a JS shell or the page needs Playwright**.

Chunk contract:

```
source: web
url: https://…
retrieved_at: ISO-8601
provider: jina | firecrawl
```

Joint retrieve with code (RRF as today). Version-pin: if `package.json` says `foo@3`, prefer docs that mention v3 in title/url.

Do **not** default-on hosted Firecrawl (egress + cost). Do **not** crawl arbitrary sites from `watch`. Parse-local PDFs can wait.

## Fusion call

**gap**, **P2**, depends on doctor + a tiny ingest CLI.  
Measure with one vendored markdown fixture (`source=web`) in the eval suite — Recall@k on “how does library X work?” should include the web chunk **and** the local wrapper path.
