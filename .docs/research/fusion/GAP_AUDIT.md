# Gap audit — open fusion work only

Status `gap`, or `partial` with a **material** delta. Rejects and fully shipped steals are in [STEAL_MATRIX.md](STEAL_MATRIX.md), not here.

Spine already shipped: **eval → CCR-lite → corrective loop → KG enrichment**. This file is what to fuse *next*. Nothing here claims best-of-kind is done.

## Recommended next 5 fusion PRs

Order is RAG-quality first (Recall@k, citation-path, tokens), then ops/distribution. Each PR stays local-first and eval-gated.

| # | PR | Size | Owns | Depends on |
|---|-----|------|------|------------|
| **1** | **Wiki generate MVP** — template + KG + Mermaid, write `knowledge/wiki/` as OKF `WikiPage`, cite `path:symbol`. No LLM polish. *(generate + schema shipped; RRF `kind=wiki` boost and watch dirty-module regen still open)* | M | Code Wiki #23, OKF #16, Obsidian vault shape #6 | Mermaid (done). Shared `okf.py` with PR 2 |
| **2** | **Typed memory + OKF import/export** — `decision`/`error`/`preference`/`fact`, supersede, `memory brief` ≤800 tokens in the semi-stable prefix. *(store + CLI + brief + OKF round-trip shipped; packer inject is opt-in / default-off; no LLM auto-extract)* | M | Memanto #5, OKF #16 | PR 1’s `okf.py` *or* land parser in this PR and have wiki call it |
| **3** | **Interactive session + `/compact` + `/cost`** — JSONL session, budget never drops latest pack, heuristic compact, print packed/full tokens + loop attempts. Tiny: `--profile sage` flag pack + `path:symbol` system line + `expand_on=explain`. *(session + slash cmds + sage + cite line shipped; LLM compact / eval session fixture still open)* | M | Strands #3, Forge #13, Claurst #2, Loop #18, Headroom remainder #1 | None (CCR/loop exist) |
| **4** | **Secret redaction + audit JSONL** — strip key/token patterns before assemble; append query/chunk_ids/tokens/model. Optional max-token hard stop. | S | Governance #19, Proxima `analyze_file` strip | None. **Do this before binding a network port** |
| **5** | **`doctor` + `mcp serve` / `POST /v1/retrieve`** — probe embed/LLM with ordered fallbacks; expose `retrieve`, `retrieve_chunk`, `graph_neighbors`. Optional SQLite query-hash cache. | L (or S doctor + M serve) | Agent-Reach #11, OpenHuman #8, Proxima #9, Forge #13 | PR 4 preferred. Query cache can split as S |

**Immediately after these (not in the 5):** grow the eval hard-set (LLM-in-production #20); TurboVec dual-write (#15, blocked on recall); Jina/`index-url` (#11/#22, P2); path templates + `calls` quality (#14).

---

## Ranked gaps

### G1. Living wiki from the KG (P0)

- **Steal:** Code Wiki incremental pages + edge-exported diagrams + grounded `path:symbol`; OKF `WikiPage`; Obsidian-shaped `knowledge/` tree.
- **Sources:** #23, #16, #6
- **Shipped:** `python main.py wiki generate|list|show` writes hash-stable OKF `WikiPage` files under `knowledge/wiki/` with `path:symbol` cites and Mermaid from `to_mermaid`. `harness/okf.py` is the shared SPEC v0.2 subset (PR 2 can reuse it). Ranking / packer defaults unchanged.
- **Change:** Problem — `ARCHITECTURE.md` drifts and answers re-stuff raw bodies. Outcome — remaining: index pages as high-priority documentation chunks (`kind=wiki` RRF boost), watch regen per dirty module, architecture/explain eval fixtures.
- **Metric move:** citation-path and Recall@k on a new “explain architecture / how does context assembly fit” fixture **up**; `prompt_tokens_*` **down** when packer prefers wiki then `retrieve_chunk`. Measure: add 2–3 fixtures to `.docs/research/eval/code-harness.fixture.yaml`; run `eval` with and without wiki chunks in the index.
- **Risk / complexity / local-first:** Medium. Template-only avoids LLM cost/hallucinated diagrams. Must not dump per-chunk pages.
- **PR size / deps:** M. Needs shipped Mermaid. OKF frontmatter can be stubbed then shared with G2.

### G2. Typed project memory (P0)

- **Steal:** Memanto observe/reconcile/brief **without** the SaaS agent; four types; supersession; OKF round-trip.
- **Sources:** #5, #16
- **Shipped:** `python main.py memory add|list|brief|export|import` writes OKF `Decision`/`Error`/`Preference`/`Fact` files under `knowledge/memory/` (optional `.code-harness/memory/` overlay). Supersede tombstones the old file; `memory brief` packs active entries ≤800 tokens (`len//4`). Query/CCR inject is `--include-memory-brief` (default off). Shared `harness/okf.py` with wiki. See [notes/memory-okf.md](notes/memory-okf.md).
- **Change:** Problem — decisions live in chat or fight code chunks in Chroma. Outcome — remaining: LLM observe/auto-extract, eval fixtures “why Chroma / why pack_mode full”, prefix-load of all `knowledge/**/*.md`.
- **Metric move:** new fixtures “why Chroma default / why pack_mode full” hit decision files **without** Recall@k on `vector_store.py` dropping. Token mean on those queries **down** vs retrieving long architecture prose. Measure: suite + `prompt_tokens_mean` by_difficulty.
- **Risk / complexity / local-first:** Medium. Junk-drawer risk — require `type`+`title`. No auto-extract from sessions in v1. No Memanto/Mem0 dependency.
- **PR size / deps:** M. Shared OKF parser with G1. After loop (done) so brief has a place to sit.

### G3. Session budget, compact, cost, sage, cite style (P1) — done (remainder: LLM compact / session eval fixture)

- **Steal:** Strands never-drop-latest-pack; Forge/Claurst conversation compact + knobs; Loop `path:symbol` instruction; Headroom `expand_on`.
- **Sources:** #3, #13, #2, #18, #1
- **Shipped:** `python main.py chat|session|repl|interactive` is a stateful REPL. JSONL under `.code-harness/sessions/`. Heuristic `/compact` keeps user lines + latest pack ids and never drops the latest pack. `/cost` prints packed/full/completion tokens + loop attempts (+ $ if `llm.*_usd_per_1m` set). `--profile sage` / `CODEHARNESS_PROFILE` / `/profile sage` is a flag pack (`ccr_lite`, `max_loops=1`, more graph expand, higher pack budget) and does not retune RRF. System prompt cites `` `path:symbol` ``. Session auto-expands omitted chunks on explain/why **or** omitted >50% with no tool loop. `/expand`, `/memory brief`, `/wiki` are cheap extras. One-shot `query` is unchanged unless `--profile` is passed.
- **Change:** Problem — `interactive` was a stateless one-shot loop. Outcome — remaining: LLM compact (rejected for v1), a 10-turn eval fixture file, caveman prompt.
- **Metric move:** Tokens on a 10-turn synthetic session stay under `llm.max_tokens * 2` (unit-tested; documented in eval README). Easy p50 **unchanged** (compact is interactive-only). CCR citation-path stays within 5 points of `full` with one expand. Measure: existing eval `--pack-mode ccr_lite`.
- **Risk / complexity / local-first:** Low–medium. Heuristic compact only (no extra LLM). Profiles must **not** retune RRF weights.
- **PR size / deps:** M. Independent of G1/G2. Shipped.

### G4. Redaction + audit (P1)

- **Steal:** Governance default-deny *lite*; Proxima credential strip.
- **Sources:** #19, #9
- **Change:** Problem — packed first/last lines can leak secrets; no durable “what we sent.” Outcome — regex/entropy redaction in `ContextBuilder`; JSONL audit; optional token cap error.
- **Metric move:** Tokens slightly **down** (redacted spans). Recall@k unchanged (ids unchanged). Measure: unit tests on synthetic key-bearing chunks + eval suite must stay green.
- **Risk / complexity / local-first:** Low. False-positive redaction of example keys in *this* repo’s tests — allowlist fixtures. No OPA.
- **PR size / deps:** S. Before G5.

### G5. Doctor + retrieve API + MCP (P1)

- **Steal:** Agent-Reach channel probe; OpenHuman/Forge MCP tools; Proxima OpenAI-shaped local HTTP (**retrieve**, not their chat gateway).
- **Sources:** #11, #8, #13, #9
- **Change:** Problem — other agents cannot use our index; embed/LLM failures are silent. Outcome — `doctor` prints live backend + fix; `mcp serve` / `POST /v1/retrieve` returns ranked chunk ids, paths, packed or full text; index stays on disk.
- **Metric move:** Product/latency for *clients*; eval Recall@k of the retrieve endpoint **equals** CLI `eval` (same `Retriever`). Doctor: no metric, contract tests. Optional query cache: p50 **down** on second suite pass, Recall@k identical.
- **Risk / complexity / local-first:** Medium–high (serve). Bind localhost default. No hosted `:sync`. Redaction (G4) must run on the serve path.
- **PR size / deps:** L, or split doctor S + serve M. Depends on G4. Query cache optional S follow-on.

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

### G9. TurboVec experimental backend (P1, blocked)

- **Steal:** TurboQuant + allowlist hybrid + incremental sync.
- **Sources:** #15
- **Change:** Problem — Chroma RAM/disk on multi-repo. Outcome — dual-write spike, then `vector_store.type: turbovec` opt-in. Default stays Chroma until Recall@10 ≥ −2 pts, Recall@30 ≥ −1, dense p50 ≤ 1.0×.
- **Metric move:** dense-stage p50 and RSS **down** *if* gates pass; otherwise **reject default**. Measure: same suite, two backends, table in the PR.
- **Risk / complexity / local-first:** High (recall cliff, sidecar text, extra Rust wheel). Optional extra dep only.
- **PR size / deps:** L. **Depends on G6.** Do not start before wiki/memory unless a spike is explicitly wanted.

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

### G12. Query-hash cache (P2, or S with G5)

- **Steal:** Proxima SQLite sidecar (only the retrieve cache, not four memory DBs).
- **Sources:** #9
- **Change:** Problem — repeated interactive/eval questions re-embed. Outcome — `(query_hash, repo, embed_model, retrieval_config) → chunk_ids`; invalidate on index gen.
- **Metric move:** p50 **down** on warm second `eval` pass; Recall@k identical (assert).
- **Risk / complexity / local-first:** Low if keyed on full config snapshot (already in eval JSON).
- **PR size / deps:** S. Nice with G5.

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

---

## Deferred (tracked, not next)

| Item | Why wait |
|------|----------|
| NL→Cypher, Memgraph/Neo4j, eBPF traces, GraphCodeBERT | Deep #14; NetworkX + templates first |
| Headroom proxy / Kompress / SmartCrusher | Deep #1; CLI is code-not-JSON |
| Memanto/Mem0 cloud, OpenHuman agentmemory | OKF files are enough |
| LangGraph / multi-agent topology | Loop lecture: orchestration tax |
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
