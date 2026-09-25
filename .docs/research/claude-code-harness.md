# Claude Code / Anthropic cookbook — tool-result clearing

**Primary:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) session compaction + Anthropic cookbook context-management notes (tool_result eviction, keep `tool_use` ids).  
**Why this card:** conversation `/compact` already shipped (Claurst/Forge/Strands). The remaining cheap token lever on `interactive` is **not** summarizing user/assistant prose — it is dropping *re-fetchable* retrieve/tool payloads after they age, while keeping pack/`chunk_id` handles so CCR can `retrieve_chunk`.

## Steal (this PR)

1. **Tool-result clearing ≠ full compact.** Keep dialogue. Replace aged retrieve dumps / `tool_result` bodies with a short placeholder that names `tool`, args, `chunk_ids`, and `paths`.
2. **Never drop** the latest user query or the current top pack. Never drop non-re-fetchable user/assistant text (that still goes through `/compact`).
3. **Opt-in.** `--clear-tool-results` / `CODEHARNESS_CLEAR_TOOL_RESULTS=1` / `session.clear_tool_results` default **false**.
4. **Metrics.** `/cost` prints `tool-result tokens freed` when clearing fires.
5. **Re-expand.** Placeholders keep ids; packer/CCR `retrieve_chunk` / `/expand` still load by id.

## Reject / later

- Default-fail verify gate (independent steal).
- Event-sourced session log.
- LLM compact of user/assistant prose.
- Changing retrieval defaults or flipping TurboVec.

## Map

| Knob | Default |
|------|---------|
| `session.clear_tool_results` / `--clear-tool-results` / `CODEHARNESS_CLEAR_TOOL_RESULTS` | **off** |
| `session.clear_tool_keep` / `--clear-tool-keep` | `1` (same idea as `keep_recent`) |
| `session.clear_tool_token_trigger` / `--clear-tool-token-trigger` | `0` (keep-N only; `>0` also fires on dump-token budget) |

Modules: `harness/tool_clear.py`, `harness/session.py` (`/clear-tool-results`, `/compact` micro-step, `/cost`), CCR `retrieve_chunk`.
