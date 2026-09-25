# Claude Code / Anthropic cookbook — session hygiene + verify gate

**Primary:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) session compaction + Anthropic cookbook context-management and long-running-agent notes.  
**Why this card:** conversation `/compact` already shipped (Claurst/Forge/Strands). Two remaining cheap steals on `interactive`: (1) drop *re-fetchable* retrieve/tool payloads after they age; (2) a **default-fail independent verify gate** so the builder cannot self-grade “done”.

## Steal — tool-result clearing (shipped)

1. **Tool-result clearing ≠ full compact.** Keep dialogue. Replace aged retrieve dumps / `tool_result` bodies with a short placeholder that names `tool`, args, `chunk_ids`, and `paths`.
2. **Never drop** the latest user query or the current top pack. Never drop non-re-fetchable user/assistant text (that still goes through `/compact`).
3. **Opt-in.** `--clear-tool-results` / `CODEHARNESS_CLEAR_TOOL_RESULTS=1` / `session.clear_tool_results` default **false**.
4. **Metrics.** `/cost` prints `tool-result tokens freed` when clearing fires.
5. **Re-expand.** Placeholders keep ids; packer/CCR `retrieve_chunk` / `/expand` still load by id.

## Steal — default-fail verify gate (this PR)

1. **Default-fail checklist.** `session.verify` / `--verify` (chat/session) / `CODEHARNESS_SESSION_VERIFY=1` builds a completion-criteria list that starts all-false. No verify run → not done.
2. **Independent verifier.** Separate role (`verifier`), no write tools. Prefer runnable checks (`file` / `command` / `contains` / `coverage` / `assertion`) over an LLM judge. LLM fallback is read-only and still default-fail.
3. **Builder cannot mark criteria true.** Adding a rubric is allowed; `set_met(..., role=builder)` raises. `/done` is refused until the gate is green.
4. **Explicit override only.** `--force-done` / `/done --force` / `CODEHARNESS_FORCE_DONE=1`. End-of-turn and bare `done` claims are refused while the gate is on and red.
5. **Eval stage (optional).** `eval --verify` runs the same gate on fixtures that declare `completion_criteria`. Retrieval-only fixtures are skipped. Default **off**.

## Reject / later

- Event-sourced session log + prefix-stable packing (DeepSeek #25 — **shipped opt-in** on the sibling card).
- Retrieve-pre-step / session FTS.
- LLM compact of user/assistant prose.
- Changing retrieval defaults or flipping TurboVec.

## Map

| Knob | Default |
|------|---------|
| `session.clear_tool_results` / `--clear-tool-results` / `CODEHARNESS_CLEAR_TOOL_RESULTS` | **off** |
| `session.clear_tool_keep` / `--clear-tool-keep` | `1` (same idea as `keep_recent`) |
| `session.clear_tool_token_trigger` / `--clear-tool-token-trigger` | `0` (keep-N only; `>0` also fires on dump-token budget) |
| `session.verify` / `--verify` (chat/session/eval) / `CODEHARNESS_SESSION_VERIFY` | **off** |
| `session.force_done` / `--force-done` / `/done --force` / `CODEHARNESS_FORCE_DONE` | **off** |
| `session.criteria` / fixture `completion_criteria` | `[]` (empty rubric cannot pass without force) |
| `retrieval.verify` | unchanged citation hook (query `--verify`) |

Modules: `harness/tool_clear.py`, `harness/verify.py`, `harness/session.py` (`/clear-tool-results`, `/verify`, `/done`, `/compact` micro-step, `/cost`), `harness/eval.py` (optional verify stage), CCR `retrieve_chunk`.

## Recipe (eval verify stage)

```yaml
# optional agent-completion fixture (not in the retrieval golden suite)
- id: agent-verify-module
  query: Implement the independent verify gate
  must_cite_paths: [harness/verify.py]
  difficulty: medium
  completion_criteria:
    - id: module
      kind: file
      check: harness/verify.py
    - id: tests
      kind: command
      check: python3 -m unittest tests.test_verify_gate
```

```bash
python3 main.py eval . --suite .docs/research/eval/agent-completion.example.yaml --verify
```

Without `--verify`, eval is unchanged (retrieval-only).
