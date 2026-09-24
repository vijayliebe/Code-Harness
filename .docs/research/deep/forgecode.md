# Deep dive: ForgeCode (sage profile + knobs)

**First-pass:** [../forgecode.md](../forgecode.md)  
**Plan slot:** compact/knobs with PR 2–3; MCP “sage” in later #8  
**Seam:** `main.py` query/interactive UX; stay local indexer, not a TUI agent

## 1. What we confirmed

Forge (Tailcall, Rust) is a terminal coding agent: TUI, `forge -p`, ZSH `:` plugin. Agents: `forge` (writes), `sage`/`ask` (read-only), `muse`/`plan` (plans under `plans/`). Workspace semantic search via `:sync` (default **hosted** `api.forgecode.dev`, overridable). Context: `:compact`, conversation clone/resume, `@file`, `AGENTS.md`, skills. Knobs: `FORGE_SEM_SEARCH_LIMIT`, `FORGE_SEM_SEARCH_TOP_K`, walker depth, tool-failure limits.

Closest peer UX to “ask the repo a question.” Their hosted `:sync` is the thing we must **not** copy; local hybrid retrieval is our advantage.

## 2. Steal vs ignore

| Steal | Ignore / later |
|-------|----------------|
| Read-only default profile (sage) | Full TUI / ZSH plugin |
| Explicit compact of *conversation* | Hosted workspace index |
| Env/config knobs for top_k / limits | Write agent + sandbox worktrees |
| `AGENTS.md` as a first-class prefix (already partial) | Custom skills marketplace |
| Position as backend behind Forge (`:sync` replacement) | Reimplement muse/plan |

## 3. Sage profile for Code-Harness

Today `query` always retrieves; LLM is optional (`--no-llm`). There is no named profile.

Propose config:

```yaml
profiles:
  sage:        # default
    pack_mode: ccr_lite
    allow_writes: false
    max_loops: 1
  debug:
    pack_mode: full
    retrieval.debug: true
  apply:       # future; do not implement
    allow_writes: true
```

CLI: `python main.py query . -q "..." --profile sage`. Interactive starts in sage.

This is a **flag pack**, not a new agent runtime.

## 4. Compact vs CCR-lite

Forge `:compact` summarizes the *dialogue*. Headroom CCR packs *retrieved code*. Implement them separately:

- Packer = PR 2 ([headroom.md](headroom.md)).
- `/compact` in interactive = PR 3 companion (or a 20-line “drop packs older than K, keep user lines”).

Do not use an LLM compact until eval shows history tokens dominating. Heuristic compact is enough.

## 5. Knobs to expose (already in `config.retrieval`)

| Forge | Ours (exists / add) |
|-------|---------------------|
| `FORGE_SEM_SEARCH_TOP_K` | `retrieval.top_k`, `rerank_top_k` — document env overrides |
| `FORGE_SEM_SEARCH_LIMIT` | max files walked at index time (`indexing` exclude/include) |
| tool failure limits | LLM retry already in `utils.retry_with_backoff` |
| max requests/turn | `retrieval.max_loops` (PR 3) |

Work is **docs + env binding**, not new math.

## 6. Distribution (later PR 8)

Forge can keep its agent loop if Code-Harness offers:

- `mcp serve` tools: `retrieve`, `retrieve_chunk`, `info`
- or `POST /v1/retrieve` (Proxima/OpenAI-compatible idea from first pass)

That is the “sage = Code-Harness” story. Do not build a Rust TUI.

## 7. Privacy constraint

Document in README when MCP/API lands: **index never leaves the machine** unless the user points embeddings/LLM at a cloud provider. Contrast with Forge’s default hosted `:sync`.

## 8. Acceptance

- `--profile sage` is read-only (no file writes even if apply lands later).
- Knobs documented next to existing flags.
- No new hosted service.

## 9. Recommended PR slice

Ship knobs + profile names with the loop PR (tiny). Compact heuristic with packer or loop. MCP behind Forge is explicitly later.

## 10. Profile matrix

| Flag / field | sage | debug | apply (future) |
|--------------|------|-------|----------------|
| `pack_mode` | `ccr_lite` | `full` | `ccr_lite` |
| `--debug` traces | off | on | off |
| `max_loops` | 1 | 2 | 1 |
| writes / shell | forbidden | forbidden | allowed |
| LLM | on if key present | on | on |

Unknown profile name → error, do not fall through to apply.

## 11. Env binding

```
CODEHARNESS_TOP_K=30
CODEHARNESS_RERANK_TOP_K=15
CODEHARNESS_PACK_MODE=ccr_lite
CODEHARNESS_MAX_LOOPS=1
CODEHARNESS_PROFILE=sage
```

Override order: CLI flag > env > `.code-harness/config.json` > `DEFAULT_CONFIG`. Mirror Forge’s “knobs without a rebuild.”

## 12. Interactive compact

`/compact` keeps:

- All user lines
- Last assistant answer
- Last pack’s chunk IDs (not bodies)

Drops older assistant prose. This is **not** CCR-lite (already applied per turn).

## 13. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Profile changes ranking silently | Profiles must not change RRF weights in v1 |
| Users sync to Forge cloud | README privacy note; we never add a default remote index |
| Skill packs sprawl | Stay with existing three prefix files until OKF |

## 14. Implementation checklist

- [ ] `--profile` validates against known names
- [ ] Env bindings documented in README flags table
- [ ] `/compact` heuristic (no LLM)
- [ ] Profiles do not change RRF weights
- [ ] Privacy sentence: index stays local
- [ ] MCP interface sketched in comments only

## 15. Cross-links

- Packer vs compact: [headroom.md](headroom.md)
- Loop knobs: [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md)
- Later backend-for-Forge: INTEGRATION_PLAN #8
