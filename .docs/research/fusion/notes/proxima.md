# Mini-deepen: Proxima

**First-pass:** [../../proxima.md](../../proxima.md)  
**Live skim (2026-09-25):** [Zen4-bit/Proxima](https://github.com/Zen4-bit/Proxima) README v5  
**Why this note:** first-pass guessed an OpenAI `/v1/retrieve`; live product is an **LLM gateway**, not a retriever.

## Confirmed mechanisms

Proxima is a local **MCP + OpenAI-compatible HTTP host** (`127.0.0.1:3210`) that routes to (1) logged-in **browser sessions** or (2) BYOK / Ollama. Separate `proxima-agent/` adds a self-heal write loop.

Stealable, in-process:

1. **OpenAI-shaped local HTTP** — one endpoint other agents already know how to call. Their `/v1` is *chat completions*, not retrieve. Code-Harness should still ship `POST /v1/retrieve` (ranked chunks + ids) because that is the drop-in *we* need; do not copy session-mode browser routing (ToS / Non-Commercial license).
2. **Self-heal loop** — run → error → patch. We already have the *retrieval* analog (`grade → rewrite | HyDE | deepen`). Do not steal write/test/CDP.
3. **Four SQLite sidecars** (`vault.db`, `insights.db`, `memory.db`, `skills.db`) — the useful slice is a **query-hash → chunk_id cache** plus an optional `error` experience log. That is not typed project memory (Memanto/OKF); keep them separate.
4. **`analyze_file` secret strip** before context leaves the machine — same as governance redaction.
5. **Gated execution (Smart/Suggest)** — only if write tools ever exist.

Do **not** steal: browser session emulation, 40 MCP chat tools, crew/consensus multi-model debate.

## Map to Code-Harness

| Steal | Status | Module |
|-------|--------|--------|
| Corrective retrieve loop | **done** (`harness/loop.py`, opt-in `--loop`) | query loop |
| `POST /v1/retrieve` + MCP retrieve | **gap** | NEW serve / CLI |
| SQLite `(query_hash, repo, config) → chunk_ids` | **gap** | NEW cache beside Chroma |
| Secret strip on assemble | **gap** | context builder |
| Browser session LLM routing | **reject** — ToS + not retrieval | — |

## Fusion call

**P1** for retrieve API (with Forge/OpenHuman MCP) and a **local query cache** (latency on repeated eval/interactive questions).  
Measure cache with eval p50 on a warm second pass of the same suite; Recall@k must be identical to cold (same ids).
