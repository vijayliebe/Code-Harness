# DeepSeek Harness (official `dsh`)

## What it is
Official **DeepSeek AI** open-source agent harness (`dsh`, developer preview). Tagline is “Everything is a Plugin”: a **thin agent loop** plus swappable **capability seams**, not a retrieval engine. Built on [Cordis](https://github.com/cordiverse/cordis) (spatiotemporal composability). Steal the **seams** — event-sourced `Session`, `deriveMessages()`, compaction-as-plugin, `llm-retry`, concurrency-safe tools, prefix-cache discipline, `sessionQuery` FTS, resume/fork. **Do not** vendor the Cordis megasystem or `@deepseek-ai/*`.

## Links
- Repo: https://github.com/deepseek-ai/deepseek-harness
- Docs: https://deepseek-harness.github.io/deepseek-harness/
- Architecture: https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/docs/architecture.md
- Agent loop: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md
- Session: https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/session
- Session query (FTS): https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/session-query
- Capability seams: https://deepseek-harness.github.io/deepseek-harness/en/reference/capability-seams
- Compaction: https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/compaction-basic/README.md
- Tools pipeline: https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/tools.md
- Thin-loop explainer: https://deepseekdocs.com/en/docs/learn/core/agent-loop
- Cordis paper: https://arxiv.org/abs/2608.25512
- npm: `npx @deepseek-ai/dsh web` · MIT

## How it works
**Thin core.** `dsh-agent-loop` does one job: claim inbox → open turn → assemble prompt → `deriveMessages()` → stream LLM → dispatch tools → append facts → repeat. Search, compaction, retry, sandbox, and UI hang off events as plugins. The docs’ rule: before adding something *inside* the loop, ask whether it can be a listener. 99% yes.

**Plugin seams (not a monolith).** A seam is a Service Definition + Provider + Consumer. `ctx.llm`, `ctx.compaction`, `ctx.sessionPersistence`, `ctx.sessionQuery`, `ctx.tools`, `ctx.fs`, `ctx.subagents` are swappable. The loop consumes them; it does not own them. Profiles (`web`, `headless`, `sdk`, `sdk-minimal`, `acp`) compose bundles at boot.

**Event-sourced Session.** A `Session` is an append-only log of typed `SessionEvent`s (`turn/start|end`, `step/start|end`, `user/message`, `system/message`, `assistant/message`, `assistant/attempt`, `tool/call`, `tool/result`, `request/header`, `request/context`, …). Plugins declaration-merge extra types (`compaction/*`, `llm/retry`, `hook/*`) that are **log-only** — they do not become model history. The **surface** is an ordered projection of message-producing events; compaction replaces a balanced surface range and increments `replaceGeneration`. Shadowed events stay in the raw log so replay is deterministic.

**`deriveMessages()`.** Model history is *derived* from the current surface, not stored twice. Projection is cached (each surface node once; a rewrite rebuilds) and frozen (mutating a projection is unrepresentable). A runtime invariant: every model-visible request must reconstruct from the log. System prompt travels as `system/message` surface nodes; the request is `header.config` + `deriveMessages()` + `header.tools`.

**Compaction as plugin.** `ctx.compaction` (`dsh-compaction-basic`) listens at `agent/pre-step` (pressure) and `agent/request-error` (canonical overflow). Optional sibling `dsh-compaction-tool-result-pruner` rewrites oversized current `tool/result` nodes *before* summary. Boundaries snap with `toolPairingBalancedBefore/After` so a cut never orphans a tool-call/result pair. Summarization is a one-shot `ctx.llm.stream()` (not a loop step) that **replays the last routed prefix byte-for-byte** so the provider KV/prefix cache stays warm; only the trailing compaction instruction is uncached. There is no model-facing compact tool.

**`llm-retry`.** Optional `dsh-llm-retry` handles `agent/request-error` with `{ kind: 'retry' }`, backoff, and non-surface `llm/retry` status. No listener = terminal failure. Retries reuse the same assembled request (no second `agent/pre-step`).

**Concurrency-safe tools.** Default is exclusive. `isConcurrencySafe(args)` must return **exactly `true`** to join a bounded parallel pool (`maxParallelToolCalls` default 10). Omission, exceptions, and non-`true` are serial. Only dispatch/body overlaps; policy, durable `tool/result` commits, and result context stay in model order. Pipeline: `tools/pre-execute` (allow/deny/ask) → monotonic guards → `tools/execute` (timeout/retry) → `tools/post-execute` → `finalizeContent` → observe-only `tools/result`.

**KV / prefix-cache discipline.** Frozen message identities, unchanged request series inherit the last `request/header`, and compaction/summarization replay the exact system prompt + tools + shadowed-region messages. Routing the summarizer to a different provider/model, or compacting a non-head range, forgoes cache reuse. Overlaps Headroom CacheAligner / Strands prompt-caching — **count once**.

**`sessionQuery` FTS.** Seam `ctx.sessionQuery` (`session-query-sqlite`): exact reads + filters + traces; backend adds FTS reconciliation, ranking, snippets, cursors. `searchSessions()` groups the corpus by strongest matching event; `searchEvents()` searches one session. Query text is **literal data, never executable FTS syntax**. Semantic text includes messages, tool call/result, todos, failure/status; reasoning blocks, stream chunks, and log-only `llm/retry` / `compaction/*` are excluded from message search.

**Resume / fork.** `ctx.agents.resume({ resumeSessionId })` opens the persisted JSONL write handle, reconstructs history, repairs interrupted turns, continues turn numbering. Fork is `ctx.agents.create({ sessionId, seed, meta: { isSeeded, inheritedEventCount } })`. `session/end-seed` marks the inherited-prefix cut so seed history and live work stay distinguishable.

## Relevance to Code-Harness
**High (architectural seams), Low (runtime).** Code-Harness is the *retrieval/context* half of a harness; DeepSeek shows how a production loop stays thin and how session/compaction/retry/search attach without becoming the product. Natural pairing: `dsh` (or any agent) calls Code-Harness via MCP `retrieve` / `retrieve_chunk`. **Reject** `pip`-installing or wrapping Cordis.

## Code-Harness mapping

| Steal | Module | Status |
|-------|--------|--------|
| Event-sourced session + `derive_messages` | `harness/events.py`, `harness/session.py` | **shipped opt-in** (`--event-session`, default off) |
| Prefix-stable packing / KV | `harness/prefix.py`, `ContextBuilder` | **shipped opt-in** (implied by event-session; `--prefix-stable`) |
| Retrieve as `agent/pre-step` | `harness/prestep.py` | **shipped, default on** (`--no-retrieve-prestep` skips) |
| Session-event FTS | `harness/session_fts.py` beside `memory search` | **shipped** (`session search` / `/search` / `search_session`) |
| Tool-result prune before compact | `harness/tool_clear.py` (Claude Code sibling #24) | **shipped opt-in** |
| `llm-retry` listener | query / LLM prepare | **gap** |
| Concurrency-safe retrieve | MCP / future tool loop | **n/a** until we run parallel tools |

Default interactive JSONL (`event: turn`) is unchanged when the flag is off. `migrate_legacy_events()` reads old files.

### Knobs

| Knob | Default |
|------|---------|
| `session.event_session` / `--event-session` / `CODEHARNESS_EVENT_SESSION` | **off** |
| `context.prefix_stable` / `--prefix-stable` / `CODEHARNESS_PREFIX_STABLE` | **off** (on when event-session is on, unless `--no-prefix-stable`) |
| `prestep.retrieve` / `--retrieve-prestep` / `CODEHARNESS_RETRIEVE_PRESTEP` | **on** (`--no-retrieve-prestep` / env `0` skips) |

`RetrievePreStep` is the first `before_model` hook. A second hook (memory brief, verify prep) registers with `default_registry().register(...)` or `HookRegistry([MemoryBriefPreStep(), RetrievePreStep()])`.

Session-event FTS is **shipped** (DeepSeek steal wave **5/5**). It indexes typed events (`user` / `assistant` / `tool_use` / `tool_result` / `system` / `compact`; skips `meta` / `clear` / `verify`) into a co-located SQLite FTS5 sidecar (`<session>.fts.sqlite`). Incremental on append; rebuild on `migrate_legacy_session_file` or a stale sidecar.

| Knob | Default |
|------|---------|
| FTS itself | **on whenever a typed event log exists** (no extra flag) |
| `/search` in the REPL | requires `--event-session` (legacy turn JSONL is not indexed) |
| `python main.py session search "…"` | requires a typed event JSONL (`--session` or last file under `.code-harness/sessions/`) |

Not BM25-over-memory. Do not mix the sidecar into Chroma.

Event types (local vocabulary, not DeepSeek’s 13-type TS envelope): `user`, `assistant`, `tool_use`, `tool_result`, `system`, `compact`, `clear`, `verify`, `meta`.

**Overlap with Strands (#3):** session budget (never-drop-latest-pack) and “session ≠ long-term memory ≠ code index” are already owned by #3 / #5. DeepSeek adds *how* (event log + derive + FTS + compaction plugin), not a second budget policy.

## Reject
- **Cordis megasystem** — plugins, profiles, HMR, Electron desktop, experimental agent teams, webhook runtime, dynamic Cordis runner. Orthogonal to hybrid RAG.
- Vendor lock on `@deepseek-ai/*` or Bedrock-like “assembled harness” (same reject as Strands `create_harness`).
- Copying the 13-event taxonomy wholesale. Steal *append-only + derive*, not their TypeScript envelope.
- Mixing session FTS into the code embedding collection (Strands/Memanto failure mode).
- Compaction that mutates the only transcript copy (breaks resume/fork and eval replay).
- Developer-preview compatibility breaks — treat docs as pattern source, not an API to pin.
