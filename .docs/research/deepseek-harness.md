# DeepSeek Harness (official `dsh`)

## What it is
Official **DeepSeek AI** open-source agent harness (`dsh`, developer preview). Tagline is “Everything is a Plugin”: a **thin agent loop** plus swappable **capability seams**, not a retrieval engine. Built on [Cordis](https://github.com/cordiverse/cordis). Steal the **seams** — event-sourced `Session`, `deriveMessages()`, prefix-cache discipline, compaction-as-plugin, `sessionQuery` FTS, resume/fork. **Do not** vendor the Cordis megasystem or `@deepseek-ai/*`.

## Links
- Repo: https://github.com/deepseek-ai/deepseek-harness
- Docs: https://deepseek-harness.github.io/deepseek-harness/
- Session: https://deepseek-harness.github.io/deepseek-harness/en/reference/subsystems/session
- Cordis paper: https://arxiv.org/abs/2608.25512

## How it works (steal, don’t copy)
**Thin core.** Claim inbox → open turn → assemble prompt → `deriveMessages()` → stream LLM → dispatch tools → append facts → repeat. Search, compaction, retry, and UI hang off events as plugins.

**Event-sourced Session.** Append-only typed events. The **surface** is a projection of message-producing events. Compaction shadows a range (`replaceGeneration`); raw events stay so replay is deterministic. Model history is *derived*, not stored twice.

**Prefix / KV discipline.** Freeze system prompt + tools + knowledge prefix; put volatile packs last. Same request series → byte-identical prefix. Count once with Headroom CacheAligner / CCR-lite.

## Code-Harness mapping

| Steal | Module | Status |
|-------|--------|--------|
| Event-sourced session + `derive_messages` | `harness/events.py`, `harness/session.py` | **shipped opt-in** (`--event-session`, default off) |
| Prefix-stable packing / KV | `harness/prefix.py`, `ContextBuilder` | **shipped opt-in** (implied by event-session; `--prefix-stable`) |
| Retrieve as `agent/pre-step` | `harness/prestep.py` | **shipped, default on** (`--no-retrieve-prestep` skips) |
| Session-event FTS | NEW beside `memory search` | **later** |
| `llm-retry` listener | query / LLM prepare | **gap** |

Default interactive JSONL (`event: turn`) is unchanged when the flag is off. `migrate_legacy_events()` reads old files.

### Knobs

| Knob | Default |
|------|---------|
| `session.event_session` / `--event-session` / `CODEHARNESS_EVENT_SESSION` | **off** |
| `context.prefix_stable` / `--prefix-stable` / `CODEHARNESS_PREFIX_STABLE` | **off** (on when event-session is on, unless `--no-prefix-stable`) |
| `prestep.retrieve` / `--retrieve-prestep` / `CODEHARNESS_RETRIEVE_PRESTEP` | **on** (`--no-retrieve-prestep` / env `0` skips) |

`RetrievePreStep` is the first `before_model` hook. A second hook (memory brief, verify prep) registers with `default_registry().register(...)` or `HookRegistry([MemoryBriefPreStep(), RetrievePreStep()])`. Session-event FTS stays a later PR.

Event types (local vocabulary, not DeepSeek’s 13-type TS envelope): `user`, `assistant`, `tool_use`, `tool_result`, `system`, `compact`, `clear`, `verify`, `meta`.

## Reject
Cordis megasystem, profiles/HMR/Electron, vendoring `@deepseek-ai/*`, copying the full event taxonomy, mixing session FTS into Chroma.
