# DeepSeek Harness (official `dsh`)

## What it is
Official **DeepSeek AI** open-source agent harness (`dsh`, developer preview). Tagline is “Everything is a Plugin”: a **thin agent loop** plus swappable **capability seams**, not a retrieval engine. Built on [Cordis](https://github.com/cordiverse/cordis) (spatiotemporal composability). Steal the **seams** — event-sourced `Session`, `deriveMessages()`, compaction-as-plugin, `llm-retry`, concurrency-safe tools, prefix-cache discipline, `sessionQuery` FTS, resume/fork. **Do not** vendor the Cordis megasystem.

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

## Concrete ideas to adopt
1. **Retrieve as `agent/pre-step` plugin** (accuracy/cost): Hang hybrid retrieve + CCR pack on a pre-step hook (`reject | enter(messages)`), not inside a fatter driver. Why: MCP serve, `query`, and `chat` already share `Retriever`; a named seam stops each path growing its own retrieve fork. DeepSeek’s loop never “is” search.
2. **Event-sourced session + `deriveMessages`** (reliability/cost): Append typed events; *project* the model-visible transcript. Compaction becomes a surface replacement, not a rewrite of the only copy. Why: today’s `.code-harness/sessions/*.jsonl` is a mixed turn log; prefix-stable packing and resume/fork need a reconstructable log.
3. **Prefix-stable packing** (cost): Freeze system prompt + ARCHITECTURE/wiki/memory brief; put volatile packs last; keep request-series headers stable so provider prefix cache hits. Compaction that needs a summary should replay that prefix verbatim. Why: DeepSeek’s compaction README is explicit — byte-identical replay is the KV win. **Overlap:** Headroom CacheAligner (#1) + Strands prompt cache (#3) — do not triple-count.
4. **Tool-result prune *before* summary compact** (cost): Sibling pruner rewrites bulky `tool/result` (and, for us, packed retrieval dumps / `retrieve_chunk` expansions) to a placeholder + id, then compact only if still over budget. Why: cheapest token recovery; pairs with Claude Code tool-result clearing (#25).
5. **Session-event FTS** (accuracy): SQLite FTS over session events (user query, packed chunk ids, tool/retrieve results) — **not** another Chroma collection. Why: “what did we retrieve last Tuesday?” is a session question; BM25-over-memory (#5) already covers typed `knowledge/memory/` files. Different store.
6. **`llm-retry` as listener** (reliability): Bounded backoff on provider/overflow errors; log a non-surface retry event; do not re-run retrieve/`pre-step` on a pure transport fail. Why: doctor already fails closed; the query path still lacks a named retry seam.
7. **Concurrency-safe retrieve tools** (perf): `retrieve` / `retrieve_chunk` / `graph_neighbors` are read-only → `isConcurrencySafe=true`; writes (index, memory add) exclusive. Why: DeepSeek and Claude Code both default-fail to serial.

## Risks / reject / avoid
- **Cordis megasystem** — plugins, profiles, HMR, Electron desktop, experimental agent teams, webhook runtime, dynamic Cordis runner. Orthogonal to hybrid RAG.
- Vendor lock on `@deepseek-ai/*` or Bedrock-like “assembled harness” (same reject as Strands `create_harness`).
- Copying the 13-event taxonomy wholesale. Steal *append-only + derive*, not their TypeScript envelope.
- Mixing session FTS into the code embedding collection (Strands/Memanto failure mode).
- Compaction that mutates the only transcript copy (breaks resume/fork and eval replay).
- Developer-preview compatibility breaks — treat docs as pattern source, not an API to pin.

## Open questions
- Is a thin `harness/events.py` enough, or do we keep JSONL turns and only add a `derive_messages()` projection?
- Should `sessionQuery` FTS wait until session events exist, or can we FTS today’s JSONL as a stepping stone?

## Code-Harness mapping
| Steal | Module | Status on this tip |
|-------|--------|--------------------|
| Thin loop + retrieve-as-pre-step | `harness/loop.py`, `serve.py` MCP tools | **gap** — loop *is* retrieve; no plugin seam |
| Event-sourced session + derive | `harness/session.py` | **gap** — mixed JSONL turns; heuristic `/compact` mutates the working list |
| Prefix-stable packing / KV | `context_builder.py`, CCR prefix hash | **partial** — prefix hash + knowledge/memory briefs; no request-series freeze |
| Tool-result prune before compact | session / CCR spill | **gap** (CCR-lite is packer, not transcript prune) |
| Session-event FTS | NEW beside `memory search` | **gap** — BM25-over-memory is typed files, not session events |
| `llm-retry` listener | query / LLM prepare | **gap** |
| Concurrency-safe retrieve | MCP / future tool loop | **n/a** until we run parallel tools |

**Overlap with Strands (#3):** session budget (never-drop-latest-pack) and “session ≠ long-term memory ≠ code index” are already owned by #3 / #5. DeepSeek adds *how* (event log + derive + FTS + compaction plugin), not a second budget policy.
