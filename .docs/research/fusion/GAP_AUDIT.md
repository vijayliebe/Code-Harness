# Gap audit — open fusion work only

Status `gap`, or `partial` with a **material** delta. Rejects and fully shipped steals are in [STEAL_MATRIX.md](STEAL_MATRIX.md), not here.

Spine already shipped: **eval → CCR-lite → corrective loop → KG enrichment**. Fusion PRs 1–5 below landed on this tip family. Nothing here claims best-of-kind is done.

## Recommended next 5 — after polish wave

**Current polish (do these first, not in the 5):** TurboVec A/B / default-flip gates (#15) and query-hash cache on eval/interactive (G12 remainder). They are already in-tree as experimental / serve-path-only.

Then, ranked by expected **accuracy / cost / reliability** from DeepSeek (#24) and Claude Code (#25). Do **not** rebuild Strands session budget or the memory/index split (#3 / #5).

| # | PR | Size | Owns | Depends on |
|---|-----|------|------|------------|
| **1** | **Tool-result clearing in session/interactive** — keep pack/`chunk_id` records; replace aged packed bodies and `retrieve_chunk` expansions with a placeholder; re-expand via CCR. Do this *before* another `/compact` summary. | S–M | Claude Code #25 (cookbook clearing); DeepSeek pruner sibling #24 | Session + CCR spill (done) |
| **2** | **Default-fail independent eval gate** — criteria start `false`; fresh-context `--verify` (no generator CoT, no writes) flips them for citation-coverage / agent-done. | S | Claude Code #25 (cwc-long-running-agents); Loop #18 `--verify` | Verify hook (wired, off) |
| **3** | **Event-sourced session + prefix-stable packing** — append-only events; `derive_messages()` projection; freeze system/wiki/memory prefix; compaction is a surface replacement. **Not** a second Strands budget. | M | DeepSeek #24 Session/`deriveMessages`; Headroom CacheAligner #1 (count once) | Session JSONL (done) |
| **4** | **Retrieve-as-pre-step plugin seam** — one `pre_step` hook (`reject \| enter`) shared by `query` / `chat` / MCP; driver stays thin. | M | DeepSeek #24 agent-loop | Loop + serve (done) |
| **5** | **Session-event FTS** — SQLite FTS over session events (queries, pack ids, retrieve/tool text). **Beyond** BM25-over-memory (typed `knowledge/memory/` files). Not a Chroma collection. | M | DeepSeek #24 `sessionQuery` | PR 3 events (or FTS today’s JSONL as a stepping stone) |

**Still not in the 5:** eval hard-set growth (#20); Jina/`index-url` (#11/#22, P2); path templates + `calls` quality (#14); LLM wiki polish (#23); caveman prompt (#2).

## Shipped fusion PRs (historical next-5)

Order was RAG-quality first, then ops/distribution. Kept as the record of what landed.

| # | PR | Size | Owns | Depends on |
|---|-----|------|------|------------|
| **1** | **Wiki generate MVP** — template + KG + Mermaid, write `knowledge/wiki/` as OKF `WikiPage`, cite `path:symbol`. No LLM polish. *(generate + schema + `--dirty` / watch hook + opt-in RRF `wiki_weight` + full-vault `knowledge export|import` + opt-in packer `knowledge/**` prefix + chat-over-wiki via CCR shipped; LLM polish still open)* | M | Code Wiki #23, OKF #16, Obsidian vault shape #6 | Mermaid (done). Shared `okf.py` with PR 2 |
| **2** | **Typed memory + OKF import/export** — `decision`/`error`/`preference`/`fact`, supersede, `memory brief` ≤800 tokens in the semi-stable prefix. *(store + CLI + brief + OKF round-trip + heuristic `memory extract` + whole-vault `knowledge export|import` + opt-in BM25-over-memory RRF shipped; `memory.auto_extract` default off; packer inject is opt-in / default-off)* | M | Memanto #5, OKF #16 | PR 1’s `okf.py` *or* land parser in this PR and have wiki call it |
| **3** | **Interactive session + `/compact` + `/cost`** — JSONL session, budget never drops latest pack, heuristic compact, print packed/full tokens + loop attempts. Tiny: `--profile sage` flag pack + `path:symbol` system line + `expand_on=explain`. *(session + slash cmds + sage + cite line shipped; LLM compact / eval session fixture still open)* | M | Strands #3, Forge #13, Claurst #2, Loop #18, Headroom remainder #1 | None (CCR/loop exist) |
| **4** | **Secret redaction + audit JSONL** — strip key/token patterns before assemble; append query/chunk_ids/tokens/model. *(redact + audit JSONL + `audit show` shipped; optional max-token hard stop still open)* | S | Governance #19, Proxima `analyze_file` strip | None. **Do this before binding a network port** |
| **5** | **`doctor` + `mcp serve` / `mcp stdio` / `POST /v1/retrieve`** — probe embed/LLM with ordered fallbacks; expose `retrieve`, `retrieve_chunk`, `graph_neighbors`. Optional SQLite query-hash cache. *(`doctor` + localhost HTTP/MCP + **stdio MCP** + bind guard + redacted bodies + optional query-hash cache shipped; no public bind without `--allow-public`)* | L (or S doctor + M serve) | Agent-Reach #11, OpenHuman #8, Proxima #9, Forge #13 | PR 4 preferred. Query cache can split as S |

---

## Ranked gaps

### G1. Living wiki from the KG (P0)

- **Steal:** Code Wiki incremental pages + edge-exported diagrams + grounded `path:symbol`; OKF `WikiPage`; Obsidian-shaped `knowledge/` tree.
- **Sources:** #23, #16, #6
- **Shipped:** `python main.py wiki generate|list|show` writes hash-stable OKF `WikiPage` files under `knowledge/wiki/` with `path:symbol` cites and Mermaid from `to_mermaid`. `wiki generate --dirty` hashes source files + package subgraphs against `.wiki-manifest.json` and rewrites only affected package pages plus the architecture index (full regen still default). Missing graph + `--dirty` fails soft. `watch` calls the same dirty path after re-index when a vault already exists. Hybrid retrieve merges a `kind=wiki` / `knowledge/wiki/` channel via RRF when `retrieval.wiki_weight > 0` (default **0.0**, eval-safe). Wiki cites may be `knowledge/wiki/<page>`; source cites stay `path:symbol`. `harness/okf.py` is the shared SPEC v0.2 subset. `python main.py knowledge export|import` (alias `okf`) dumps the whole vault — wiki + memory + gloss — as an OKF bundle with `okf-manifest.yaml`. Packer prefix-load of that vault is opt-in (`context.knowledge_prefix` / `--include-knowledge-prefix`, default **off**, 800-token budget, path-deduped against wiki RRF hits). Chat-over-wiki (`chat --wiki` / `query --wiki` / `/wiki` / `chat.wiki_mode`) prefers the wiki channel, packs wiki pages first, CCR-expands linked `path:symbol`, and falls back to code when the vault is sparse. Default chat/query is unchanged when the flag is off.
- **Change:** Problem — `ARCHITECTURE.md` drifts and answers re-stuff raw bodies. Outcome — remaining: architecture/explain eval fixtures, LLM polish (out of scope). Chat-over-wiki via CCR is shipped (`harness/wiki_chat.py`). Packer prefix-load of `knowledge/**/*.md` is opt-in (`context.knowledge_prefix`, default off, 800-token budget) and is turned on automatically in wiki mode.
- **Metric move:** citation-path and Recall@k on a new “explain architecture / how does context assembly fit” fixture **up**; `prompt_tokens_*` **down** when packer prefers wiki then `retrieve_chunk`. Measure: add 2–3 fixtures to `.docs/research/eval/code-harness.fixture.yaml`; run `eval` with and without wiki chunks in the index.
- **Risk / complexity / local-first:** Medium. Template-only avoids LLM cost/hallucinated diagrams. Must not dump per-chunk pages.
- **PR size / deps:** M. Needs shipped Mermaid. OKF frontmatter can be stubbed then shared with G2.

### G2. Typed project memory (P0)

- **Steal:** Memanto observe/reconcile/brief **without** the SaaS agent; four types; supersession; OKF round-trip.
- **Sources:** #5, #16
- **Shipped:** `python main.py memory add|list|brief|search|export|import|extract` writes OKF `Decision`/`Error`/`Preference`/`Fact` files under `knowledge/memory/` (optional `.code-harness/memory/` overlay). Supersede tombstones the old file; `memory brief` packs active entries ≤800 tokens (`len//4`). Heuristic `memory extract` reads the last session JSONL (or `--session`); `--dry-run` writes nothing; optional `--llm` refine is a clean no-op without client+key. `memory.auto_extract` default **false**; when true, extract runs after `/compact` or session exit. Query/CCR inject is `--include-memory-brief` (default off). On-the-fly BM25 over active memory joins hybrid RRF when `retrieval.memory_weight > 0` (default **0.0**) or `--include-memory-search` / `CODEHARNESS_MEMORY_SEARCH=1`. `memory search` is the debug CLI. Packer drops memory RRF hits already in the brief and path-dedupes vs the knowledge prefix. Shared `harness/okf.py` with wiki. `knowledge export|import` round-trips the whole vault (wiki + memory + gloss) without dropping unknown keys. See [notes/memory-okf.md](notes/memory-okf.md).
- **Change:** Problem — decisions live in chat or fight code chunks in Chroma. Outcome — remaining: eval fixtures “why Chroma / why pack_mode full”. Packer can prefix vault docs via `context.knowledge_prefix` (dedupes memory brief by path/type).
- **Metric move:** new fixtures “why Chroma default / why pack_mode full” hit decision files **without** Recall@k on `vector_store.py` dropping. Token mean on those queries **down** vs retrieving long architecture prose. Measure: suite + `prompt_tokens_mean` by_difficulty.
- **Risk / complexity / local-first:** Medium. Junk-drawer risk — require `type`+`title`; heuristic extract is opt-in. No Memanto/Mem0 dependency.
- **PR size / deps:** M. Shared OKF parser with G1. After loop (done) so brief has a place to sit.

### G3. Session budget, compact, cost, sage, cite style (P1) — done (remainder: LLM compact / session eval fixture)

- **Steal:** Strands never-drop-latest-pack; Forge/Claurst conversation compact + knobs; Loop `path:symbol` instruction; Headroom `expand_on`.
- **Sources:** #3, #13, #2, #18, #1
- **Shipped:** `python main.py chat|session|repl|interactive` is a stateful REPL. JSONL under `.code-harness/sessions/`. Heuristic `/compact` keeps user lines + latest pack ids and never drops the latest pack. `/cost` prints packed/full/completion tokens + loop attempts (+ $ if `llm.*_usd_per_1m` set). `--profile sage` / `CODEHARNESS_PROFILE` / `/profile sage` is a flag pack (`ccr_lite`, `max_loops=1`, more graph expand, higher pack budget) and does not retune RRF. System prompt cites `` `path:symbol` ``. Session auto-expands omitted chunks on explain/why **or** omitted >50% with no tool loop. `/expand`, `/memory brief`, `/wiki` are cheap extras. One-shot `query` is unchanged unless `--profile` is passed.
- **Change:** Problem — `interactive` was a stateless one-shot loop. Outcome — remaining: LLM compact (rejected for v1), a 10-turn eval fixture file, caveman prompt.
- **Metric move:** Tokens on a 10-turn synthetic session stay under `llm.max_tokens * 2` (unit-tested; documented in eval README). Easy p50 **unchanged** (compact is interactive-only). CCR citation-path stays within 5 points of `full` with one expand. Measure: existing eval `--pack-mode ccr_lite`.
- **Risk / complexity / local-first:** Low–medium. Heuristic compact only (no extra LLM). Profiles must **not** retune RRF weights.
- **PR size / deps:** M. Independent of G1/G2. Shipped.

### G4. Redaction + audit (P1) — done (remainder: optional max-token hard stop)

- **Steal:** Governance default-deny *lite*; Proxima credential strip.
- **Sources:** #19, #9
- **Shipped:** `harness/redact.py` + `harness/audit.py`. Outbound packed/LLM text is redacted by default (regex + assignment entropy; disable only via `CODEHARNESS_REDACT=0` / `redaction.enabled: false` / `--no-redact`). Session JSONL and chat prints go through the same helper. `memory brief|export --redact` is opt-in. Wiki generate strips env-like echoes. Append-only `.code-harness/audit/audit.jsonl` records counts + fingerprint hashes (never raw secrets). CLI: `audit show --last N` / `audit tail`. PR5 should call `redact_text` / `redact_and_audit` on retrieve/API bodies.
- **Change:** Problem — packed first/last lines can leak secrets; no durable “what we sent.” Outcome — remaining: optional `redaction.max_prompt_tokens` hard stop (config key reserved, not enforced).
- **Metric move:** Tokens slightly **down** (redacted spans). Recall@k unchanged (ids unchanged). Measure: unit tests on synthetic key-bearing chunks + eval suite must stay green.
- **Risk / complexity / local-first:** Low. False-positive redaction of example keys in *this* repo’s tests — allowlist fixtures. No OPA. Repo scan of harness/docs only hit `tests/test_redact.py` fixtures.
- **PR size / deps:** S. Before G5. Shipped.

### G5. Doctor + retrieve API + MCP (P1) — done (remainder: eval-path cache)

- **Steal:** Agent-Reach channel probe; OpenHuman/Forge MCP tools; Proxima OpenAI-shaped local HTTP (**retrieve**, not their chat gateway).
- **Sources:** #11, #8, #13, #9
- **Shipped:** `python main.py doctor` (Python/deps/index/graph/embed/redact/audit/LLM-key; no network; actionable hints; exit 1 on required fails). `python main.py serve` / `mcp serve` / `api serve` bind **127.0.0.1** (`GET /health`, `POST /v1/retrieve`, `POST /mcp`). `python main.py mcp stdio` (also `mcp serve --stdio`) speaks the same JSON-RPC tool surface on stdin/stdout with **no network bind**; logs on stderr. MCP tools: `retrieve`, `retrieve_chunk`, `doctor`, `wiki_show`, `memory_brief`, `graph_neighbors`. Bodies go through `redact_and_audit`. `--allow-public` is the documented dangerous all-interfaces opt-in (no auth). Optional SQLite query-hash cache. Same `Retriever` + pack/loop flags as CLI. Does not start on import.
- **Change:** Problem — other agents cannot use our index; embed/LLM failures are silent. Outcome — remaining: query cache on eval/interactive (serve-path only today); live embed ping (intentionally skipped — no network). Stdio MCP **done**.
- **Metric move:** Product/latency for *clients*; eval Recall@k of the retrieve endpoint **equals** CLI `eval` (same `Retriever`). Doctor: no metric, contract tests. Optional query cache: p50 **down** on second suite pass, Recall@k identical.
- **Risk / complexity / local-first:** Medium–high (serve). Bind localhost default. No hosted `:sync`. Redaction (G4) runs on the serve path.
- **PR size / deps:** L, or split doctor S + serve M. Depends on G4. Shipped.

### G6. Eval suite growth (P1)

- **Steal:** Chip Huyen eval-first; Medium question-pack *shape* only.
- **Sources:** #20, (#12 rejected as product)
- **Change:** Problem — 8 fixtures, one repo, hard-set is thin for wiki/TurboVec gates. Outcome — more `who calls` / `what exposes` / `tested_by` / “why decision” / architecture questions; optional second tiny fixture repo later.
- **Metric move:** Does not raise production Recall by itself; it **enables** honest deltas for G1–G2 and TurboVec. Measure: `n` and `by_difficulty.hard` in eval JSON.
- **Risk / complexity / local-first:** Low. Do not Goodhart by adding only easy path-hits.
- **PR size / deps:** S. Can land anytime; best **with** G1/G2 fixtures.

### G7. Graph path templates + `calls` quality (P1)

- **Steal:** Code-Graph-RAG allowlisted queries (`callers_of`, `callees_of`, `exposes`, `tests_for`); honest call edges vs name regex.
- **Sources:** #14
- **Change:** Problem — beam helps, but `calls` are still import-regex; “who calls X” is fragile. Outcome — structured expand actions the loop already requests (`deepen_graph`); qualified-name / file-local linking; drop `get`/`main` collisions.
- **Metric move:** Hard-set Recall@k and citation-path **up** on `q-who-calls-*`. Measure: existing fixtures + 1–2 new caller/callee golds. Index time +<15%.
- **Risk / complexity / local-first:** Medium (false edges). No Cypher, no Memgraph.
- **PR size / deps:** M. Independent; nicer after G6.

### G8. Headroom remainder (P1/P2)

- **Steal:** expand-on-explain (P1, **folded into G3 and shipped** for session/sage); stable cache key `(path, range, hash)` (P1); ContentRouter/SmartCrusher (P2).
- **Sources:** #1
- **Change:** Problem — `retrieve_chunk` 404 after `watch`; explain answers invent omitted lines; JSON/tool dumps (rare on this CLI) still huge.
- **Metric move:** citation-path on explain fixtures **up** with one expand; Recall@k unchanged. Measure: eval + a unit test that re-index hash still resolves spill.
- **Risk / complexity / local-first:** Low for key+expand; do not import `headroom-ai`.
- **PR size / deps:** S if not folded into G3. P2 compressors: L, only if eval shows non-code tokens dominating.

### G9. TurboVec experimental backend (P1, partial)

- **Steal:** TurboQuant + allowlist hybrid + incremental sync.
- **Sources:** #15
- **Shipped:** `vector_store.type: chromadb|turbovec` (aliases `chroma` / `turbo-vec`). `TurboVecStore` wraps real `turbovec.IdMapIndex` (`add_with_ids` / `search(..., allowlist=)` / `sync`) plus a JSON sidecar for chunk text. `eval --compare-backends chromadb,turbovec` prints Recall@k / nDCG@k and fails if turbovec is selected and below chromadb by >5% relative or the deep-dive point gates (R@10 −2 pts, R@30 −1). `--force-experimental` bypasses. Optional extra: `requirements-turbovec.txt`. Retriever uses BM25 allowlist (≥20 ids) only when the backend `supports_allowlist`. Default remains Chroma.
- **Change:** Problem — Chroma RAM/disk on multi-repo. Outcome remaining — dual-write spike; TQ+ `calibrate`; flip default only after Recall@10 ≥ −2 pts, Recall@30 ≥ −1, dense p50 ≤ 1.0× on the fixture suite.
- **Metric move:** dense-stage p50 and RSS **down** *if* gates pass; otherwise **reject default**. Measure: `eval --compare-backends`.
- **Risk / complexity / local-first:** High (recall cliff, sidecar text, extra Rust wheel). Optional extra dep only.
- **PR size / deps:** L. Wiki/memory already landed on this parent tip.

### G10. Self-RAG auto `--no-llm` (P2)

- **Steal:** 500-AI-Agents Self-RAG “retrieve only if needed.”
- **Sources:** #10
- **Change:** Problem — easy identifier queries still pay an LLM if a key is set. Outcome — if heuristic grade ≥ τ and mode is `bm25`, skip generation unless `--llm`.
- **Metric move:** Easy p50 and tokens **down**; Recall@k unchanged. Measure: `eval` (retrieval-only already) + a query-path unit test.
- **Risk / complexity / local-first:** Low. Users may want a sentence anyway — keep flag.
- **PR size / deps:** S. After G3 profiles so sage can set the default.

### G11. Jina ingest + Firecrawl fallback (P2)

- **Steal:** Agent-Reach no-key web; Firecrawl for JS-heavy; version-pin metadata.
- **Sources:** #11, #22
- **Change:** Problem — “how do we use X?” misses upstream docs. Outcome — `index-url` / `index-site` → `source=web` chunks; Jina default; Firecrawl only on empty/JS shell; no LinkedIn.
- **Metric move:** Recall@k on a vendored web+wrapper fixture **up**. Tokens up unless CCR. Measure: new fixture with `must_cite_paths` for both wrapper and stub doc.
- **Risk / complexity / local-first:** Medium (ToS, stale docs, egress if cloud Firecrawl). Prefer Jina; self-host Firecrawl if needed.
- **PR size / deps:** M. After G5 doctor (channel list).

### G12. Query-hash cache (P2) — partial (serve path shipped with G5)

- **Steal:** Proxima SQLite sidecar (only the retrieve cache, not four memory DBs).
- **Sources:** #9
- **Shipped:** `.code-harness/query_cache.sqlite` on `serve` / `mcp serve` / `mcp stdio` / `api serve` (`--no-cache` to disable). Key includes query, repo, embed model, retrieval snapshot, pack mode, index fingerprint (chroma/graph mtime).
- **Change:** Problem — repeated interactive/eval questions re-embed. Outcome — remaining: wire the same cache into `query` / `eval` / interactive.
- **Metric move:** p50 **down** on warm second `eval` pass; Recall@k identical (assert).
- **Risk / complexity / local-first:** Low if keyed on full config snapshot (already in eval JSON).
- **PR size / deps:** S remainder. Serve slice shipped with G5.

### G13. Visualizer gloss / vault UX (P2)

- **Steal:** Obsidian graph feel.
- **Sources:** #6
- **Change:** Problem — gloss edges exist; viz does not treat them as first-class.
- **Metric move:** None in eval (UX). Do not block RAG PRs.
- **Risk / complexity / local-first:** Low.
- **PR size / deps:** S. After G1 vault files exist.

### G14. Graph-explorer digest (P2)

- **Steal:** Strands subagent returns `{paths, digest, chunk_ids}` instead of stuffing beam walks.
- **Sources:** #3, #14
- **Change:** Problem — deep graph still costs tokens even with CCR.
- **Metric move:** `prompt_tokens_packed` **down** on graph-mode fixtures; hard Recall@k held via digest ids still packed.
- **Risk / complexity / local-first:** Medium (digest quality). No extra model if digest is heuristic (names + signatures).
- **PR size / deps:** M. After G1/G7.

### G15. Tool-result clearing in session/interactive (P0) — post-polish #1

- **Steal:** Anthropic cookbook tool-result clearing; DeepSeek `dsh-compaction-tool-result-pruner` before summary.
- **Sources:** #25, #24
- **Change:** Problem — session JSONL and interactive history keep full packed bodies / expansions; `/compact` is the expensive rung. Outcome — aged retrieve dumps become placeholders + ids; CCR `retrieve_chunk` re-expands.
- **Metric move:** `prompt_tokens_packed` **down** on a 10-turn session fixture; citation-path held after one expand. Recall@k unchanged (post-retrieve).
- **Risk / complexity / local-first:** Low. Do not delete `tool_use` / pack-id records (breaks pairing). No Anthropic server API.
- **PR size / deps:** S–M. Independent of G16–G19. After polish query-cache if we want the same cache key to see cleared vs full.

### G16. Default-fail independent eval gate (P0) — post-polish #2

- **Steal:** `cwc-long-running-agents` default-FAIL + fresh-context evaluator; Loop lecture independent verify.
- **Sources:** #25, #18
- **Change:** Problem — `--verify` is wired off and the builder can still declare “done.” Outcome — criteria start `false`; a no-write verifier with fresh `{query, answer, packed chunks}` flips them. Reuse typed memory for handoff notes (#5); do not add `PROGRESS.md`.
- **Metric move:** citation-path / verify-pass on explain fixtures **honest** (no self-grade). Does not raise Recall@k by itself.
- **Risk / complexity / local-first:** Low. Opt-in flag; eval stays retrieval-first. No second LLM-as-judge product.
- **PR size / deps:** S. Can land anytime; nicest after G6 fixtures.

### G17. Event-sourced session + prefix-stable packing (P0) — post-polish #3

- **Steal:** DeepSeek append-only `Session` + `deriveMessages()`; compaction as surface replacement; byte-identical prefix replay for KV cache.
- **Sources:** #24, #1 (CacheAligner — **count once**), not a second Strands budget (#3)
- **Change:** Problem — mixed JSONL turns are the only copy; `/compact` mutates the working list; prefix cache is folklore. Outcome — events + projection; freeze system/wiki/memory brief; volatile packs last.
- **Metric move:** Tokens/latency on repeated interactive turns **down** when provider prefix cache hits; resume/fork correctness (unit). Recall@k unchanged.
- **Risk / complexity / local-first:** Medium (migration of existing JSONL). Keep a reader for old turn files.
- **PR size / deps:** M. Unlocks G19 FTS. Do not vendor Cordis.

### G18. Retrieve-as-pre-step plugin seam (P0) — post-polish #4

- **Steal:** DeepSeek thin loop: retrieve is an `agent/pre-step` listener, not the driver.
- **Sources:** #24
- **Change:** Problem — `query` / `chat` / MCP each grow retrieve forks. Outcome — one `pre_step(query) -> reject|enter(pack)` shared by CLI and serve.
- **Metric move:** Product/ops (one code path); eval Recall@k of MCP **equals** CLI (already a G5 contract — keep it).
- **Risk / complexity / local-first:** Medium (refactor, not a new ranker). No plugin runtime (no Cordis).
- **PR size / deps:** M. After G17 if the hook writes session events; can land as a function seam first.

### G19. Session-event FTS (P1) — post-polish #5

- **Steal:** DeepSeek `ctx.sessionQuery` SQLite FTS (`searchSessions` / `searchEvents`).
- **Sources:** #24
- **Change:** Problem — “what did we retrieve last Tuesday?” has no index. BM25-over-memory (#5) covers typed files only. Outcome — FTS over session events; literal query; exclude log-only retry/compact internals.
- **Metric move:** Debug/UX; optional “session cite” fixture later. Must **not** change code-index Recall@k (separate store).
- **Risk / complexity / local-first:** Low–medium. Do not write session text into Chroma.
- **PR size / deps:** M. After G17 (or FTS today’s JSONL as a stepping stone).

---

## Deferred (tracked, not next)

| Item | Why wait |
|------|----------|
| NL→Cypher, Memgraph/Neo4j, eBPF traces, GraphCodeBERT | Deep #14; NetworkX + templates first |
| Headroom proxy / Kompress / SmartCrusher | Deep #1; CLI is code-not-JSON |
| Memanto/Mem0 cloud, OpenHuman agentmemory | OKF files are enough |
| LangGraph / multi-agent topology | Loop lecture: orchestration tax |
| Cordis / DeepSeek desktop / experimental agent teams | #24 reject — steal seams only |
| Community Plan→Work→Review `claude-code-harness` | #25 reject — skill pack, not retrieval |
| Streaming tool executor / Ralph-loop / `/goal` clone | #25 reject for v1 |
| WorkOS, AirLLM, Jitro, OpenMontage, interview curricula | Rejected in matrix |
| LLM-as-judge | Eval README: after citation metric saturates |

## How to measure any gap

```bash
python main.py index .
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --pack-mode ccr_lite
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop
```

Paste `metrics.recall_at_k`, `citation_path_hit_rate`, `prompt_token_drop`, `latencies_ms.*.p50`, `by_difficulty.hard` before/after. If a PR cannot name which of those it moves, it is not a fusion PR.
