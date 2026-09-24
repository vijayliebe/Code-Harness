# ForgeCode (Forge)

## What it is
Open-source **Rust AI coding agent for the terminal** by Tailcall: interactive TUI, one-shot CLI, ZSH `:` plugin; multi-provider; agents (forge/sage/muse); semantic workspace search; MCP; skills; conversation compact/clone.

## Links
- GitHub: https://github.com/tailcallhq/forgecode
- Site/docs: https://forgecode.dev
- Install: `curl -fsSL https://forgecode.dev/cli | sh`

## How it works
- Three modes: interactive TUI, `forge -p`, ZSH `:` prefix.
- Agents: `forge` (writes), `sage`/`ask` (read-only research), `muse`/`plan` (plans to `plans/`).
- **Semantic search**: `:sync` indexes workspace (default server `api.forgecode.dev`; overridable).
- Context tools: `:compact`, conversation clone/resume, `@file` attach, AGENTS.md, custom agents/skills.
- Sandbox via git worktree (`--sandbox`).
- Config knobs: `FORGE_SEM_SEARCH_LIMIT`, `FORGE_SEM_SEARCH_TOP_K`, max walker depth, tool failure limits, max requests/turn.

## Relevance to Code-Harness
**High.** Closest “coding agent with semantic search” peer. Code-Harness can be the *better local indexer* behind a Forge-like UX (sage = retrieve-only).

## Concrete ideas to adopt
1. **Read-only research agent profile** (tokens/accuracy): Default query path = sage-like (no file writes); separate “apply” mode.
2. **Local `:sync` / workspace index** (perf): Replace hosted Forge workspace with Code-Harness index — privacy + hybrid retrieval quality.
3. **`:compact` + conversation clone** (tokens): Interactive mode features.
4. **AGENTS.md + custom skills** (accuracy): Already partially there (ARCHITECTURE/AGENTS injection) — formalize skill packs.
5. **Sem-search top-k + limit env knobs** (perf): Expose like Forge’s FORGE_SEM_SEARCH_*.

## Risks/caveats
- Hosted workspace indexing may send code off-machine — Code-Harness should stay local-default.
- Don’t duplicate full agent; stay best-in-class at retrieval.

## Open questions
- Position Code-Harness as MCP behind Forge/Claude vs own TUI?
