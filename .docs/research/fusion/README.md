# Fusion pack — steal matrix + gap audit

This directory is the **map** from researched sources to a hybrid code-RAG that can compete on Recall@k, prompt tokens, latency, and local-first ops. It does **not** claim best-of-kind is achieved.

| File | Purpose |
|------|---------|
| [FUSION_THESIS.md](FUSION_THESIS.md) | What “best of its kind” means for *this* product + scorecard |
| [STEAL_MATRIX.md](STEAL_MATRIX.md) | Every INDEX resource (25) → stealable idea, module, status, priority |
| [GAP_AUDIT.md](GAP_AUDIT.md) | Open gaps only, ranked; **next 5 after polish** (TurboVec A/B, query-cache) then historical fusion 1–5 |
| [notes/](notes/) | Mini-deepens for thin Medium / weak-primary cards |

Spine already shipped on `cursor/kg-enrichment-3590` (and ancestors):

`eval metrics → CCR-lite packer → corrective query loop → KG enrichment (exposes/tested_by/gloss + beam + Mermaid)`

Fusion PR #1 (wiki generate MVP) adds `wiki generate|list|show` — OKF `WikiPage` markdown under `knowledge/wiki/`, `path:symbol` cites, Mermaid from the KG.

Fusion PR #2 (typed memory) adds `memory add|list|brief|search|export|import|extract` — OKF `Decision`/`Error`/`Preference`/`Fact` under `knowledge/memory/`, supersede/tombstone, `memory brief` ≤800 tokens, OKF unknown-key round-trip, heuristic session extract (opt-in `memory.auto_extract`). Query-path inject is opt-in. Opt-in BM25-over-memory RRF (`retrieval.memory_weight`, default 0.0 / `--include-memory-search`). Mapping: [notes/memory-okf.md](notes/memory-okf.md).

Full-vault OKF: `knowledge export|import` (alias `okf`) copies wiki + memory + gloss (plus `.code-harness/gloss`) into a bundle with `okf-manifest.yaml`. Packer prefix-load of `knowledge/**/*.md` is opt-in (`context.knowledge_prefix`, default off, `context.knowledge_token_budget=800`).

Wiki remainders now closer: `wiki generate --dirty` + watch hook + opt-in RRF `retrieval.wiki_weight` (default 0.0) + chat-over-wiki (`chat --wiki` / `query --wiki` / `/wiki` / `chat.wiki_mode`) via CCR pack/expand. LLM polish stays open.

Fusion PR #3 (interactive session) adds `chat` / `session` / `repl` aliases, JSONL under `.code-harness/sessions/`, heuristic `/compact` + `/cost`, `--profile sage`, and a `path:symbol` system line. Expand-on-explain is session/sage-scoped. Claude Code steal #1 (tool-result clearing) is opt-in on that path (`--clear-tool-results`, default off). DeepSeek steal (event-sourced session + prefix-stable packing) is opt-in (`--event-session`, default off). Session-event FTS (steal wave 5/5) is `session search` / `/search` / `search_session` over the typed event log (SQLite FTS5 sidecar; legacy turn JSONL refused).

Fusion PR #4 (secret redaction + audit) adds `harness/redact.py` + append-only `.code-harness/audit/audit.jsonl`. Packed/LLM text is redacted by default; `audit show --last N` prints fingerprint hashes, never raw secrets. Disable only via `--no-redact` / `CODEHARNESS_REDACT=0`.

Fusion PR #5 (`doctor` + localhost MCP / `POST /v1/retrieve`) adds `harness/doctor.py` + `harness/serve.py`. `doctor` probes Python/deps/index/graph/embed/redact/audit/LLM-key (no network). `serve` / `mcp serve` / `api serve` bind **127.0.0.1** only unless `--allow-public` (dangerous, no auth). `mcp stdio` speaks the same tools over stdin/stdout (no bind). Response bodies go through `redact_and_audit`. Query-hash cache lives in `harness/query_cache.py` (serve default-on; `query` / `chat` / `eval` opt-in via `--query-cache`).

First-pass research (2026-09-25, merged from the parallel BM25 notes branch): [../claude-code-harness.md](../claude-code-harness.md) (#24) and [../deepseek-harness.md](../deepseek-harness.md) (#25). Steal **seams** (clearing, default-fail eval, event-sourced session, retrieve-as-pre-step, session FTS) — **shipped** on the session-event FTS tip. Do **not** vendor Cordis or clone Claude Code / community Plan→Work→Review repos. Session budget and “session ≠ memory ≠ index” stay with Strands (#3) / Memanto (#5) — do not double-count.

Read [../INDEX.md](../INDEX.md) → this pack → [../deep/INTEGRATION_PLAN.md](../deep/INTEGRATION_PLAN.md) for the original sequence.
