# Mini-deepen: Agent-Reach

**First-pass:** [../../agent-reach.md](../../agent-reach.md)  
**Live skim (2026-09-25):** [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) README + EN docs  
**Why this note:** first-pass is a catalog of platforms; the steal is the **channel registry**, not LinkedIn.

## Confirmed mechanisms

Agent-Reach is a **capability layer**: it does not scrape. It maintains an ordered list of backends per “channel,” **probes** them (not just `which`), and `agent-reach doctor` prints which path is live plus a fix prescription. 2026 example: Bilibili `yt-dlp` 412 → `bili-cli` with zero user rewrite.

Web channel default is **Jina Reader** (`curl https://r.jina.ai/URL`) — no key. GitHub is `gh`. Cookies stay local (mode 600). MIT.

## What to steal (in-process)

A `channels/`-shaped registry for **our** backends, not theirs:

```
embed:   local(sentence-transformers) → voyage → openai → jina
llm:     ollama → openai-compat → anthropic → gemini
webdoc:  jina_reader → firecrawl_selfhost → firecrawl_cloud
```

`python main.py doctor` should: import/embed a 16-token ping, time it, report model name + dim, refuse silent fallback. Same for LLM if a key is configured. Do not auto-install system packages (`--system` is their footgun).

Web ingest (`index-url`) is a **second** PR: Jina markdown → `source=web` chunks + version/url metadata. Firecrawl only when Jina returns empty/JS shell (see [firecrawl.md](firecrawl.md)).

## Explicitly reject

- LinkedIn / Twitter / Reddit cookie or browser-MCP paths (ToS, off-mission).
- Becoming a “give the agent eyes on the internet” product.
- Wrapping Agent-Reach as a dependency.

## Fusion call

**P1** for `doctor` **shipped** (`python main.py doctor`: local probes, no network ping; missing Voyage key **fails** the embedding check with an export hint). Ordered runtime fallback still open.  
**P2** for Jina `index-url` (accuracy on “how do we use X?”).  
Measure doctor with a unit test that a missing Voyage key does not crash `query --no-llm`. Measure ingest later with a fixture whose gold path is a vendored markdown page.
