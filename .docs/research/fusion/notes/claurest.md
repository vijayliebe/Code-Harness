# Mini-deepen: Claurst (Claurest)

**First-pass:** [../../claurest.md](../../claurest.md)  
**Live skim (2026-09-25):** [Kuberwastaken/claurst](https://github.com/Kuberwastaken/claurst) README + docs index  
**Why this note:** first-pass is thin on *mechanisms*; ACP/`/goal`/`/share` look like product surface unless we pin what is stealable.

## Confirmed mechanisms

Claurst is a **Rust Claude-Code-style terminal agent** (GPL-3.0; ideas only). Live README adds, beyond first-pass `/compact` `/cost` `/caveman` / ACP:

- **ACP over JSON-RPC/stdio:** `claurst acp` implements `initialize`, `session/new`, `session/prompt`, `session/cancel`, streams `session/update`, routes tools through `session/request_permission`. Editors (Zed, Neovim, JetBrains) drive it as a subprocess.
- **`/goal`:** multi-turn objective that does not stop after one turn (experimental). Overlaps Jules/Jitro “persistent goal” *as agent UX*, not as retrieval.
- **`/share`:** gist export of a session (experimental). Privacy anti-pattern for a local indexer.
- **Prompt-cache friendly tool defs** (first-pass): still the cheapest token idea if we ever expose tools.
- **No telemetry by default** — contrast with hosted Forge `:sync`.

`/compact` is **conversation** compression (keep last N turns + a summary), not retrieval packing. `/cost` is per-session token/provider spend, not retrieval-stage traces.

## Map to Code-Harness

| Steal | Status on this branch | Module |
|-------|----------------------|--------|
| Heuristic `/compact` in `interactive` (keep user lines + last pack IDs) | **done** | CLI / `session.py` |
| `/cost` printing packed vs full tokens + loop attempts | **done** — REPL `/cost`; eval JSON already had the numbers | eval + CLI |
| Terse / caveman system prompt (“do not restate retrieved code”) | **gap** | context builder / LLM |
| ACP server | **reject** as v1 — MCP `retrieve` is the local-first equivalent | — |
| `/goal`, `/share`, ultracode swarms | **reject** — agent clone | — |

## Fusion call

**P1** for `/compact` + interactive `/cost` (pairs with Strands session budget and Forge `:compact`).  
**P3** for caveman output mode (output tokens, not Recall@k).  
Do not wrap Claurst; do not speak ACP until MCP retrieve exists.
