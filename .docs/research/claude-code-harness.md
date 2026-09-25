# Claude Code harness patterns (product + public primitives)

## What it is
**Two different things share this name.** This card steals from **product Claude Code** (Anthropic’s production coding agent) and from **Anthropic’s published harness primitives**. It does **not** steal from community `claude-code-harness` Plan→Work→Review skill packs (see Reject).

Product Claude Code is a hosted+local coding agent: streaming tool executor, concurrency tiers, and a **cheap-first context ladder** (snip → microcompact → collapse → autocompact). Anthropic’s cookbooks and the Code-with-Claude long-running-agent take-home publish the transferable contracts: **tool-result clearing vs compaction vs memory**, and **default-fail / independent evaluator / handoff files**.

## Links
### Product Claude Code + API
- Session + compaction UX: https://claude.com/blog/using-claude-code-session-management-and-1m-context
- Server-side compaction: https://platform.claude.com/docs/en/build-with-claude/compaction
- Cookbook (memory / compaction / tool clearing): https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- Cookbook notebook: https://github.com/anthropics/claude-cookbooks/blob/main/tool_use/context_engineering/context_engineering_tools.ipynb

### Long-running agent primitives (Anthropic)
- Take-home repo: https://github.com/anthropics/cwc-long-running-agents
- Engineering: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents (Nov 2025)
- Engineering: https://www.anthropic.com/engineering/harness-design-long-running-apps (Mar 2026)
- `/goal` docs: https://code.claude.com/docs/en/goal

### Public internals writeups (unofficial; pattern confirmation)
- Four-level context: https://notes.tsukino.dev/99-%E5%B7%A5%E5%85%B7%E4%B8%8E%E5%8F%82%E8%80%83/repos/claude-code-book/en/Part-2-Core-Systems/07-Context-Management-Agent-Working-Memory
- Compaction layers: https://github.com/mo0rti/coding-agent-documentation/blob/39c5262ddbdf23561fae6d24b19d9301dc3898a2/code-findings/14-compaction-summarization-and-context-budget-management.md
- Streaming tools + concurrency: https://claude-code-from-source.com/ch07-concurrency/
- Tool orchestration: https://zhanghandong.github.io/harness-engineering-from-cc-to-ai-coding/en/part1/ch04.html

### Community “claude-code-harness” (distinguish, do not steal as product)
- https://github.com/Chachamaru127/claude-code-harness (and forks: xenosnikos, houssemdz, bowyer-app)
- https://github.com/tim-hub/powerball-harness

## How it works
### Streaming tool executor + concurrency tiers
Product Claude Code does **not** wait for the full assistant message before starting tools. `StreamingToolExecutor` starts each `tool_use` as soon as the block parses. Two tiers:

| Tier | Who | Rule |
|------|-----|------|
| Concurrent | Read-only (Read, Glob, Grep, WebFetch, WebSearch); Bash *only if* parsed read-only | May overlap; default cap **10** (`CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`) |
| Serial / exclusive | Write, Edit, unknown, failed `isConcurrencySafe`, mixed batches | Exclusive access; later tools see prior side effects |

`partitionToolCalls` groups consecutive safe tools into parallel batches and isolates unsafe ones. Results emit in **request order** even if execution finishes out of order. Fail-closed: exception or invalid schema ⇒ serial. Same default as DeepSeek `isConcurrencySafe` (#24).

### Four-level context (cheap → expensive)
Public internals describe a ladder applied **before** the next model request. Light ops first; LLM summary last. A fifth **reactive compact** is overflow recovery (circuit-breaker: skip after ~3 consecutive autocompact failures).

1. **Snip** — no LLM. Drop or placeholder messages listed on a `snip_boundary` (`removedUuids`). Freed-token estimate is threaded into later threshold checks (API usage cannot see local deletions).
2. **Microcompact** — no LLM. Surgical clear of old **re-fetchable tool results** (keep last N). Two implementations: local placeholder rewrite, or API `clear_tool_uses_20250919` / cache edits that preserve the prompt-cache prefix. Triggered by token pressure *or* idle-time (cache expiry ⇒ rewrite is free, so drop bulk now).
3. **Collapse** — projected view: summarize/archive spans **without rewriting the REPL transcript**. Query-time projection; can suppress proactive autocompact so the two do not race.
4. **Autocompact** — full conversation summary (lossy). Reserves output headroom (on the order of min(max_output, 20k)) so the summary itself cannot overflow. Manual `/compact` is the same idea with user steering.

Effective window ≈ model window − reserved output. Thresholds sit below the hard cap (warning / autocompact / block).

### Tool-result clearing vs compaction vs memory (Anthropic cookbook)
These three are **not** substitutes. Cookbook mental model:

| Lever | Operation | Survives across sessions? | Typical win |
|-------|-----------|---------------------------|-------------|
| **Tool-result clearing** | Sub-transcript: replace old `tool_result` with a placeholder; keep `tool_use` so the model knows the call happened | No | Re-fetchable file reads / API / retrieve dumps |
| **Compaction** | Whole-transcript summary when the window is full | No (lossy inside one session) | Mixed growth (dialogue + tools + reasoning) |
| **Memory** | Move durable facts *out* of the window | Yes | Decisions, errors, preferences |

Clearing is the safest cheap reclaim: if the agent needs the bytes again, it re-reads. Compaction is lossy by design. Memory is the only cross-session store. **Overlap with Strands (#3) / Memanto (#5):** “session ≠ long-term memory ≠ code index” is already counted — Claude Code adds the *third* in-session lever (clearing) that we do not have.

API knobs (first-party): `clear_tool_uses_20250919` (`trigger`, `keep` default 3, `clear_at_least`, `exclude_tools`, `clear_tool_inputs`); server-side `compact_20260112` / `compact-2026-09-04` on-demand; memory tool for structured notes.

### Default-fail / independent evaluator / handoff files
From `anthropics/cwc-long-running-agents` (patterns also in the two engineering posts):

1. **Default-FAIL contract.** Every criterion starts `false`. A hook denies writes to the results file unless the agent first **Read** evidence (screenshot, log, test output). “Done” is structural, not a vibe.
2. **Fresh-context evaluator.** Separate agent, **no Write/Edit**, grades from a window that never saw the build. Returns `PASS` | `NEEDS_WORK`. Builder must not mark its own homework.
3. **Agent-maintained handoff.** Session-scoped `PROGRESS.md` + git commits. Compaction loses detail; the next session re-reads the file. `commit-on-stop` is the backstop.

`/goal` is the in-product version (separate fast model checks a condition after every turn). The take-home is the readable primitive set, not a turnkey harness.

This is the same *shape* as Code-Harness `--verify` (independent, no generator CoT) and loop-engineering “stop conditions” (#18) — applied to **agent-done**, not only citation coverage.

### Community Plan→Work→Review repos
`Chachamaru127/claude-code-harness` and forks wrap Claude Code/Codex/Cursor with `/harness-plan` → `spec.md`/`Plans.md` → `/harness-work` → `/harness-review` → release evidence. Useful as *process* folklore; they are **skill packs around an agent**, not a retrieval harness. Independent review ≈ Anthropic’s evaluator primitive (already stolen above). Do not grow Code-Harness into another `/harness-work all` product.

## Relevance to Code-Harness
**High (context + eval gates), Med (tool runtime).** Interactive `chat`/`session` already has heuristic `/compact` and CCR-lite packing. The missing cheap lever is **clearing re-fetchable retrieve/tool payloads** in the session path. The missing accuracy lever is a **default-fail independent “done” gate** on top of `--verify`. Streaming concurrency only matters if/when we run a tool loop; MCP retrieve stays request/response.

## Concrete ideas to adopt
1. **Tool-result clearing in session/interactive** (cost, first): After a turn, keep `pack_ids` / `chunk_id` / tool-name; replace the bulky packed text (and `retrieve_chunk` expansions) with a short placeholder once they age past “last N”. Re-expand via existing CCR cache. Why: cookbook’s #1 reclaim; session JSONL today keeps full packs. Cheaper than `/compact` summary.
2. **Layered compact policy** (cost): snip/clear → collapse-to-pack-ids → heuristic/LLM summary. Never jump to summary while clearing would suffice. Why: product ladder; our `/compact` is already the expensive rung.
3. **Default-fail independent eval gate** (accuracy/reliability): “Agent done” / citation-coverage / wiki-explain fixtures start `false`; a verify step with **fresh context** (no builder CoT, no write tools) must flip them. Why: Anthropic’s structural done-ness; `--verify` is wired and off — this is the contract around it, not a second grader.
4. **Handoff file ≠ session transcript** (reliability): Session-scoped progress notes belong in typed memory / `PROGRESS`-shaped OKF (`decision`/`error`), not in compacted chat. Why: compaction is lossy; Memanto brief already exists — **do not invent a fourth store**.
5. **Concurrency tiers later** (perf): If a tool loop lands, mark retrieve tools concurrent-safe; writes exclusive; cap ~10. Why: same fail-closed rule as DeepSeek (#24). Not a RAG PR.

## Risks / reject / avoid
- **Community Plan→Work→Review as product** — agent-clone; we stay the local indexer.
- Forking leaked `claude-code` source trees (`claude-code-best`, `codeaashu/claude-code`) — cite public writeups for patterns only; do not vendor.
- Server-side Anthropic-only compaction as a *dependency* — steal the policy, implement locally (we are model-agnostic).
- Treating memory, clearing, and compact as one `/compact` flag (cookbook anti-pattern).
- Builder-graded “done” (self-eval). `--verify` must stay independent.
- Double-counting Strands session budget / separate stores (#3) or Memanto typed memory (#5).
- Growing a TUI, `/goal` clone, or Ralph-loop unattended runner.

## Open questions
- Clear *packed retrieval text* in session JSONL, or only clear expansions that were pulled via `retrieve_chunk`?
- Should the independent gate live in `eval` (retrieval fixtures) or only on an opt-in agent-done path?

## Code-Harness mapping
| Steal | Module | Status on this tip |
|-------|--------|--------------------|
| Tool-result clearing in session | `harness/session.py`, CCR spill, `retrieve_chunk` | **gap** |
| Snip / microcompact / collapse / autocompact ladder | session `/compact` | **partial** — heuristic summary + never-drop-latest-pack only |
| Clearing vs compact vs memory | session / `memory.py` / CCR | **partial** — compact + typed memory exist; **clearing missing** |
| Default-fail + independent evaluator | `harness/loop.py` `--verify`, `harness/eval.py` | **partial** — verify wired off; no default-fail contract file |
| Handoff files | `knowledge/memory/` OKF | **partial** — use memory, do not add `PROGRESS.md` as a fourth store |
| Streaming tool executor | — | **reject for v1** (no in-process tool loop) |
| Community `/harness-plan|work|review` | — | **reject** |

**Overlap with Strands (#3):** budget + separate stores already counted. Claude Code’s *new* rows are clearing and the default-fail gate. Prefix-cache-preserving microcompact overlaps DeepSeek (#24) / Headroom (#1) — implement once as “prefix-stable pack + clear.”
