# Mini-deepen: OpenHuman

**First-pass:** [../../open-human.md](../../open-human.md)  
**Live skim (2026-09-25):** [tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman) README  
**Why this note:** first-pass only said “memory MCP”; live product is a desktop orchestrator. Pin the 1–2 mechanisms we can own in-process.

## Confirmed mechanisms (steal vs ignore)

| Mechanism | Steal? |
|-----------|--------|
| **Memory Tree + Obsidian wiki** — scored markdown in SQLite, mirrored as files | **Idea only** — our analog is OKF `knowledge/` + gloss, not SQLite life-memory |
| **TokenJuice** — compress tool output ≤80% before the model | **Done analog** — CCR-lite. Do not vendor TokenJuice |
| **`memory.search` / `read_chunk` MCP** | **P1** — expose `retrieve`, `retrieve_chunk`, `graph_neighbors` |
| **Privacy Mode** — one switch, inference never leaves the machine | **P1 docs + doctor** — local embed + Ollama; no new runtime |
| **agentmemory backend** — shared store across Claude/Cursor | **P2** — OKF export is the portable form; don’t take their GPL core |
| Desktop Tauri, 100+ OAuth, 5k MCP, workflows, A2A Signal | **reject** |

GPL-3: ideas only.

## Fusion call

**partial** — MCP retrieve tools + documented loopback-only `mcp serve` + **stdio** (`mcp stdio`) shipped.  
**partial** conceptually (CCR ≈ TokenJuice; gloss ≈ wiki notes).  
Do not become a desktop agent. Position: OpenHuman/Claude/Forge call *us* for code RAG.
