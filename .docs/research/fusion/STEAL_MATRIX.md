# Steal matrix — every INDEX resource

Inventory of [../INDEX.md](../INDEX.md) (23 items, including fuzzy-resolved names). Status is judged against **this branch tip**: eval harness, CCR-lite packer, corrective query loop, KG enrichment, wiki, typed memory+OKF + heuristic `memory extract` (opt-in auto), session `/compact`/`/cost`, secret redaction + audit JSONL, doctor + localhost MCP / `POST /v1/retrieve`, **experimental TurboVec backend (recall-gated)**.

**This is not a claim that fusion is complete.** Rows marked `done` mean the *stealable mechanism* is in-tree; siblings on the same card may still be `gap`.

## Legend

| Field | Values |
|-------|--------|
| Status | `done` · `partial` · `gap` · `reject` |
| Fusion priority | P0 (next accuracy/token spine) · P1 · P2 · P3 · — (reject) |
| Evidence | `deep-dive` · `first-pass` · `fusion-note` · `weak-primary` |
| Modules | retriever · context builder/CCR · query loop · KG · eval · CLI · NEW · — |

Mini-deepens for thin cards: [notes/](notes/).

## Scoreboard

| # | Resource | Status | P | Evidence |
|---|----------|--------|---|----------|
| 1 | Headroom | partial | P1 | deep-dive |
| 2 | Claurest (Claurst) | partial | P1 | fusion-note |
| 3 | Strands Harness | partial | P1 | deep-dive |
| 4 | OpenMontage | reject | — | fusion-note |
| 5 | Memanto | partial | P0 | deep-dive |
| 6 | Obsidian | partial | P1 | fusion-note |
| 7 | Jitro | reject | — | weak-primary |
| 8 | OpenHuman | partial | P1 | fusion-note |
| 9 | Proxima | partial | P1 | fusion-note |
| 10 | 500 AI Agents | partial | P2 | first-pass |
| 11 | Agent-Reach | partial | P1 | fusion-note |
| 12 | Medium interview repos | reject | — | first-pass |
| 13 | ForgeCode | partial | P1 | deep-dive |
| 14 | Code graph | partial | P1 | deep-dive |
| 15 | TurboVec | partial | P1 | deep-dive |
| 16 | OKF (Google SPEC) | partial | P0 | deep-dive |
| 17 | AirLLM | reject | — | fusion-note |
| 18 | Prompt→Loop→Graph | partial | P1 | deep-dive |
| 19 | AI governance | gap | P1 | first-pass |
| 20 | LLM in production | partial | P1 | first-pass |
| 21 | WorkOS | reject | — | fusion-note |
| 22 | Firecrawl | gap | P2 | fusion-note |
| 23 | Google Code Wiki | partial | P0 | deep-dive |

---

## 1. Headroom

- **Links:** https://github.com/headroomlabs-ai/headroom · https://docs.headroomlabs.ai/ · deep [../deep/headroom.md](../deep/headroom.md)
- **Best stealable ideas:** (1) Reversible CCR — signatures + spans in the prompt, originals in a local cache, `retrieve_chunk`. (2) CacheAligner layout — stable prefix, volatile hits last. (3) ContentRouter later — type-aware compressors for JSON/logs vs code.
- **Why it matters:** Largest token/cost lever that does **not** change ranking. Latency stays post-MMR (cheap). UX: expand-on-demand instead of stuffing 1500-char bodies.
- **Map to module:** context builder/CCR (`harness/ccr.py`, `ContextBuilder`), CLI `retrieve-chunk` / `--expand-chunk`, eval token columns.
- **Status:** `partial` — CCR-lite, spill cache, prefix hash, dual token metrics, retrieve-back line, and session/sage `expand_on=explain` **shipped**. Default remains `full` with empty `ccr.expand_on`. **Delta:** no ContentRouter / SmartCrusher / Kompress; no `headroom learn` → AGENTS.md; no proxy wrap. Chunk IDs still churn on re-index (hash key still open).
- **Fusion priority:** P1 (expand-on-explain + stable cache key). P2 for type-aware compressors. P3 for proxy/`headroom-ai` extra.
- **Evidence:** deep-dive

## 2. Claurest / Claurst

- **Links:** https://github.com/Kuberwastaken/claurst · [notes/claurest.md](notes/claurest.md)
- **Best stealable ideas:** (1) Interactive `/compact` of *dialogue* (keep user lines + last pack IDs). (2) `/cost` — show packed vs full tokens and loop attempts in the REPL. (3) Optional terse system prompt (do not restate retrieved code).
- **Why it matters:** Tokens/UX on multi-turn `interactive` (today unbounded). Ops: users optimize what they see. ACP is editor distribution — MCP retrieve is the local equivalent.
- **Map to module:** CLI (`chat`/`session`/`interactive`); `harness/session.py`; eval already has the numbers `/cost` would print.
- **Status:** `partial` — `/compact` + `/cost` + session JSONL **shipped**. **Delta:** caveman prompt. **Reject** `/goal` `/share` ultracode (agent clone). GPL — ideas only.
- **Fusion priority:** P1 for compact+cost. P3 for caveman.
- **Evidence:** fusion-note (live README skim)

## 3. Strands Harness

- **Links:** https://github.com/strands-agents/harness-sdk · https://strandsagents.com/docs/user-guide/harness/ · deep [../deep/strands-harness.md](../deep/strands-harness.md)
- **Best stealable ideas:** (1) Context-budget policy — never drop latest query + top pack; summarize/drop older packs. (2) Session ≠ long-term memory ≠ code index. (3) Explicit loop stops (grade / coverage / max_loops / easy path).
- **Why it matters:** Tokens on interactive; accuracy by not mixing chat into Chroma; latency via BM25-only easy path (already in loop).
- **Map to module:** query loop (`harness/loop.py` — **done** for stops/easy/HyDE-on-retry); NEW session store; later MCP.
- **Status:** `partial` — loop policy shipped (`max_loops` default 0); session JSONL + never-drop-latest-pack budget **shipped**. **Delta:** no subagent graph-explorer, no `create_harness` factory (correctly skipped).
- **Fusion priority:** P1 session+budget. P2 graph-explorer subagent after wiki. Do not `pip install strands-harness`.
- **Evidence:** deep-dive

## 4. OpenMontage

- **Links:** https://github.com/calesthio/OpenMontage · [notes/openmontage.md](notes/openmontage.md)
- **Best stealable ideas:** Skill packs; scored multi-stage selection; approval gates — all already owned better by OKF/wiki, loop grade, and future write-path policy.
- **Why it matters:** Does not. Video domain.
- **Map to module:** —
- **Status:** `reject` — AGPL video studio; no code-RAG core.
- **Fusion priority:** —
- **Evidence:** fusion-note

## 5. Memanto

- **Links:** https://github.com/moorcheh-ai/memanto · https://arxiv.org/abs/2604.22085 · Mem0 (related layer) https://github.com/mem0ai/mem0 · deep [../deep/memanto.md](../deep/memanto.md)
- **Best stealable ideas:** (1) Typed memories (`decision` / `error` / `preference` / `fact`) **beside** the code index. (2) Conflict **supersession** + `as-of`, not silent overwrite. (3) `memory brief` on interactive start (≤800 tokens).
- **Why it matters:** Accuracy on “why did we…?” without polluting chunk embeddings. Tokens: brief ≪ re-retrieving narrative docs. Ops: git-reviewable facts.
- **Map to module:** NEW `harness/memory.py` + CLI `memory`; context builder prefix (semi-stable, after ARCHITECTURE, before packed hits).
- **Status:** `partial` — `harness/memory.py` + CLI `memory add|list|brief|export|import|extract` shipped: four types, supersede/tombstone, `memory brief` ≤800 tokens, OKF markdown under `knowledge/memory/`. Heuristic `memory extract` (session JSONL / last session, `--dry-run`, optional `--llm` no-op without client+key). `memory.auto_extract` default **false**; when true, runs after `/compact` or session exit. Query-path inject is default-off (`--include-memory-brief`). **Delta:** no BM25-over-memory index; no eval “why did we…?” fixtures yet. Do not vendor Memanto. OKF is the interchange (Google SPEC; Memanto is implementer).
- **Fusion priority:** P0
- **Evidence:** deep-dive

## 6. Obsidian

- **Links:** https://obsidian.md · Smart Connections · [notes/obsidian.md](notes/obsidian.md)
- **Best stealable ideas:** (1) Human notes on graph nodes (gloss). (2) Export KG+notes as a markdown vault with wikilinks. (3) Local embeddings over notes (same embedder).
- **Why it matters:** Accuracy (tribal knowledge); UX (navigable overlay); portability without forcing the Obsidian app.
- **Map to module:** KG (gloss **done**); wiki vault (`knowledge/wiki/`) **partial**; CLI `knowledge export|import` **done**; visualizer (gloss UX **gap**).
- **Status:** `partial` — gloss extract + boost + example `knowledge/gloss/context-builder.md` shipped; `wiki generate` writes a markdown vault with relative links; `knowledge export` ships the whole `knowledge/` tree (wiki + memory + gloss, including `.code-harness/gloss`). **Delta:** bidirectional viz, no Obsidian sync protocol.
- **Fusion priority:** P1 if folded into wiki/OKF `knowledge/` tree; P2 viz-only.
- **Evidence:** fusion-note

## 7. Jitro

- **Links:** Jules API https://developers.google.com/jules/api · SDK https://github.com/google-labs-code/jules-sdk · [notes/jitro.md](notes/jitro.md)
- **Best stealable ideas:** Persistent goal + verify; repo-wide context — already expressed by the query loop + hybrid index. KPI-driven write agents are out of scope.
- **Why it matters:** None until a public spec exists. Press (Jules V2 / “Project Jitro”) is not an implementation.
- **Map to module:** —
- **Status:** `reject` — weak primary; no code to own. Watch Jules only as a future MCP *client*.
- **Fusion priority:** —
- **Evidence:** weak-primary

## 8. OpenHuman

- **Links:** https://github.com/tinyhumansai/openhuman · [notes/open-human.md](notes/open-human.md)
- **Best stealable ideas:** (1) MCP tools `retrieve` / `retrieve_chunk` / `memory.search`. (2) Privacy-mode story (local embed + local LLM). TokenJuice ≈ CCR (already). Memory Tree ≈ OKF+gloss (build ours, don’t take GPL core).
- **Why it matters:** Distribution (other agents bring the loop). Local-first UX. Tokens already covered by packer.
- **Map to module:** NEW MCP/CLI serve; doctor; memory/wiki (shared with #5/#16/#23).
- **Status:** `partial` — MCP tools `retrieve` / `retrieve_chunk` / `doctor` / `wiki_show` / `memory_brief` / `graph_neighbors` shipped on localhost HTTP (`python main.py mcp serve`, `POST /mcp`) **and** stdio (`python main.py mcp stdio`). Documented loopback-only HTTP profile. Desktop/OAuth/A2A **reject**.
- **Fusion priority:** P1 (MCP with Forge/Proxima). **Delta:** Desktop/OAuth/A2A remain reject; HTTP stays loopback-only. Stdio transport **done**.
- **Evidence:** fusion-note

## 9. Proxima

- **Links:** https://github.com/Zen4-bit/Proxima · [notes/proxima.md](notes/proxima.md)
- **Best stealable ideas:** (1) Local OpenAI-shaped HTTP — **our** `POST /v1/retrieve`, not their chat-gateway `/v1`. (2) Query-hash → chunk_id SQLite cache. (3) Self-heal retrieve (shipped). (4) Strip secrets on assemble.
- **Why it matters:** Latency on repeated questions; product drop-in for agents; ops/safety. Browser session routing is ToS/Non-Commercial — **reject**.
- **Map to module:** query loop **done**; `harness/serve.py` `POST /v1/retrieve` + optional SQLite query-hash cache; context builder redaction **done**.
- **Status:** `partial` — local OpenAI-shaped **retrieve** + query cache **shipped** (localhost only). **Delta:** cache is serve-path only (not eval/interactive). Browser session routing **reject**.
- **Fusion priority:** P1
- **Evidence:** fusion-note (README v5 skim — they are an LLM gateway, not a retriever)

## 10. 500 AI Agents Project

- **Links:** https://github.com/ashishpatel26/500-AI-Agents-Projects · CRAG / Self-RAG / Adaptive RAG tutorials
- **Best stealable ideas:** (1) Corrective RAG grade+rewrite. (2) Adaptive easy vs hard routing. (3) Self-RAG “retrieve only if needed.”
- **Why it matters:** Accuracy on hard queries; tokens/latency on easy ones.
- **Map to module:** query loop **done** (heuristic grade, BM25 easy path, deepen/HyDE/rewrite). Self-RAG skip-LLM when `--no-llm` or high grade is **partial** (user still opts in).
- **Status:** `partial` — catalog itself is a pointer, not a library. **Delta:** auto `--no-llm` when grade high; no code-review agent recipe (non-goal).
- **Fusion priority:** P2 (auto-skip LLM).
- **Evidence:** first-pass

## 11. Agent-Reach

- **Links:** https://github.com/Panniantong/Agent-Reach · [notes/agent-reach.md](notes/agent-reach.md)
- **Best stealable ideas:** (1) Ordered backend list + **real probe** + `doctor` prescription. (2) Jina Reader as no-key web ingest. (3) Fallback, not wrapper.
- **Why it matters:** Ops/reliability when Voyage/OpenAI keys die; local-first default embed. Accuracy later via `index-url`.
- **Map to module:** `harness/doctor.py` + CLI `doctor`; embedder/LLM fallback; ingest (P2 with Firecrawl).
- **Status:** `partial` — `python main.py doctor` probes Python/deps/index/graph/embed config/redact/audit/LLM-key (no network) and prints a fix hint. Silent cloud-key fallback still refused (missing Voyage key **fails** doctor). LinkedIn/cookie platforms **reject**. **Delta:** no live embed ping; no ordered runtime fallback channel registry; Jina `index-url` still P2.
- **Fusion priority:** P1 doctor **shipped**; P2 Jina ingest.
- **Evidence:** fusion-note

## 12. Medium: 4 interview-prep repos

- **Links:** https://medium.com/lets-code-future/top-4-github-repos-with-50k-stars-to-supercharge-your-interview-prep-67ad572d065b · system-design-primer, tech-interview-handbook, coding-interview-university, awesome-system-design-resources
- **Best stealable ideas:** Structured “interview the codebase” question banks as **eval fixtures** (auth, data flow, failure modes).
- **Why it matters:** Eval coverage only. Zero retrieval-algorithm transfer.
- **Map to module:** eval suite (optional extra YAML).
- **Status:** `reject` as product/tech. Question-pack idea is absorbed into the eval **gap** in [GAP_AUDIT.md](GAP_AUDIT.md) (do not track a Medium-owned PR).
- **Fusion priority:** —
- **Evidence:** first-pass (paywalled body; titles fuzzy-resolved in INDEX)

## 13. ForgeCode

- **Links:** https://github.com/tailcallhq/forgecode · https://forgecode.dev · deep [../deep/forgecode.md](../deep/forgecode.md)
- **Best stealable ideas:** (1) Read-only **sage** profile (flag pack, not a new runtime). (2) Conversation `:compact` ≠ retrieval packer. (3) Env knobs for top_k / pack / loops. (4) Local index as `:sync` replacement via MCP.
- **Why it matters:** UX clarity; tokens; **privacy** vs hosted `api.forgecode.dev`. We win as the better local indexer.
- **Map to module:** CLI `--profile sage` **done**; env `CODEHARNESS_PACK_MODE` **done**; `/compact` **done**; MCP **done** (`mcp serve` / `mcp stdio` / `POST /mcp` retrieve tools).
- **Status:** `partial` — sage flag pack + conversation compact + localhost HTTP + stdio MCP retrieve shipped. Stay CLI-RAG; do not build Rust TUI / muse / sandbox worktrees. Hosted `:sync` **reject**.
- **Fusion priority:** P1 sage+compact with session **shipped**; P1 MCP with OpenHuman/Proxima **shipped** (HTTP + stdio). Remainder: caveman prompt.
- **Evidence:** deep-dive

## 14. Code graph (Code-Graph-RAG + papers)

- **Links:** https://github.com/vitali87/code-graph-rag · GraphCodeAgent arxiv:2504.10046 · RANGER arxiv:2509.25257 · deep [../deep/code-graph.md](../deep/code-graph.md)
- **Best stealable ideas:** (1) Rich edges: exposes, tested_by, gloss. (2) Beam/MCTS multi-hop vs fixed BFS. (3) Mermaid / endpoint awareness. (4) Later: path templates (`callers_of`), dynamic traces, GraphCodeBERT.
- **Why it matters:** Accuracy on multi-hop “who calls / what exposes / is this tested?” — where pure vectors fail.
- **Map to module:** KG + retriever (`kg_enrich.py`, `expand_beam`, `info --mermaid`) **done** for PR-4 slice. `calls` still import-regex weak.
- **Status:** `partial` — **Delta:** data_flow, NL→Cypher (**defer**), traces, GraphCodeBERT, allowlisted path queries, calls-resolution quality. Wiki consumes Mermaid (see #23).
- **Fusion priority:** P1 path templates + calls quality. P3 graph DB.
- **Evidence:** deep-dive

## 15. TurboVec

- **Links:** https://github.com/RyanCodrai/turbovec · TurboQuant arxiv:2504.19874 · deep [../deep/turbovec.md](../deep/turbovec.md)
- **Best stealable ideas:** (1) Optional 4-bit TurboQuant backend. (2) BM25∪graph **allowlist inside SIMD** then dense. (3) Incremental `sync()` with `watch`.
- **Why it matters:** Latency + RAM on multi-repo indexes. Accuracy **risk** (quantized ANN).
- **Map to module:** vector store (`vector_store.type`); retriever dense stage only — do not replace RRF.
- **Status:** `partial` — `VectorStore` protocol + `chromadb` default + opt-in `turbovec` (`IdMapIndex` / sidecar) + `eval --compare-backends` Recall@k / nDCG@k gate. Default stays Chroma. **Delta:** dual-write spike, TQ+ `calibrate`, default flip after gates, incremental `sync()` cost vs Chroma upsert.
- **Fusion priority:** P1 experimental; stay opt-in until Recall@10 ≥ −2 pts and Recall@30 ≥ −1 vs Chroma (also 5% relative).
- **Evidence:** deep-dive (no extra fusion note)

## 16. OKF — Open Knowledge Format (Google SPEC)

- **Links:** https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md · repo + Cloud blog · Memanto is **implementer** · deep [../deep/okf.md](../deep/okf.md)
- **Best stealable ideas:** (1) Markdown+YAML concepts, path identity, required `type`, preserve unknown keys. (2) `knowledge/` as prefix source. (3) `okf_version: "0.2"` + `x_codeharness` extensions.
- **Why it matters:** Portability across Claude/Cursor/Memanto; git-diffable wiki/memory; tokens via brief pages vs chat logs.
- **Map to module:** `harness/okf.py` (WikiPage + typed memory + vault); CLI `knowledge export|import` (alias `okf`) and `memory export|import`.
- **Status:** `partial` — `wiki generate` writes SPEC v0.2 `WikiPage`; `memory export|import` round-trips `Decision`/`Error`/`Preference`/`Fact`; `knowledge export|import` ships the whole vault (wiki + memory + gloss + local `.code-harness/gloss`) with `okf-manifest.yaml` (`counts`, `okf_version`, `generated`) and unknown-key preservation (`x_memanto` / `x_other`). Shared parser, path identity, `okf_version: "0.2"`, `x_codeharness`. Packer prefix-load of `knowledge/**/*.md` is **opt-in** (`context.knowledge_prefix`, default **off**; `--include-knowledge-prefix` / `CODEHARNESS_KNOWLEDGE_PREFIX=1`) with a hard `context.knowledge_token_budget` (default **800**, `len//4`), keyword rank on title/path, path-dedupe against wiki RRF hits, and skip of memory pages when `--include-memory-brief` is on. **Delta:** chat-over-wiki via CCR, BM25-over-memory channel, Attested Computation **out of scope**.
- **Fusion priority:** P0 (same wave as memory; wiki emits `WikiPage`)
- **Evidence:** deep-dive

## 17. AirLLM

- **Links:** https://github.com/lyogavin/airllm · [notes/airllm.md](notes/airllm.md)
- **Best stealable ideas:** Layer-stream huge models on tiny VRAM — orthogonal to retrieval.
- **Why it matters:** Would **worsen** p50; no Recall@k move. Use Ollama if local generation is needed.
- **Map to module:** —
- **Status:** `reject`
- **Fusion priority:** —
- **Evidence:** fusion-note

## 18. Prompt → Loop → Graph Engineering

- **Links:** https://walkinglabs.github.io/learn-harness-engineering/en/lectures/lecture-14-graph-engineering/ · DesignGurus post · deep [../deep/prompt-loop-graph-engineering.md](../deep/prompt-loop-graph-engineering.md)
- **Best stealable ideas:** (1) Retrieve as a graded loop with stop conditions. (2) Eval anchors before topology fashion. (3) Independent verify node (fresh context, no generator CoT). (4) Do **not** graphify indexing.
- **Why it matters:** Accuracy (retry/deepen); tokens (easy path); ops (replayable traces).
- **Map to module:** eval **done**; loop **done** (opt-in); `--verify` **wired, off**; `path:symbol` system line **done**; session JSONL **done**.
- **Status:** `partial` — **Delta:** verify not in eval (eval is retrieval-only, correctly). No LangGraph.
- **Fusion priority:** P1 citation instruction (tiny) + session (with #3/#13).
- **Evidence:** deep-dive

## 19. AI governance

- **Links:** https://www.agentpatterns.tech/en/governance/governance-overview · Agent Control Plane · OPA/Kite · LLM governance gateway
- **Best stealable ideas:** (1) Secret redaction before the LLM sees chunks. (2) Max-token / max-$ hard stop. (3) Append-only audit JSONL (query, chunk_ids, tokens, model).
- **Why it matters:** Ops/safety; tokens (don’t send keys); local-first (no OPA in v1). Write-path approval only if agent writes land.
- **Map to module:** `harness/redact.py`, `harness/audit.py`, context builder, LLM prepare, session JSONL, CLI `audit show`.
- **Status:** `partial` — **Delta:** optional max-token / max-$ hard stop still open. Redaction + audit JSONL shipped (default-on for outbound LLM; fingerprints only).
- **Fusion priority:** P1 remainder is the token cap (tiny). Retrieve port now binds only after this helper (PR5 uses `redact_and_audit` on HTTP/MCP bodies).
- **Evidence:** first-pass (enough; no SaaS to skim)

## 20. LLM in production

- **Links:** https://github.com/chiphuyen/aie-book · Packt LLM Engineer’s Handbook · Manning *LLMs in Production*
- **Best stealable ideas:** (1) Golden eval first. (2) Versioned prompts + config snapshot. (3) Failure taxonomy. (4) Stage observability.
- **Why it matters:** Accuracy discipline — without it, fusion Goodharts `top_k`.
- **Map to module:** eval **done** (Recall@k, nDCG, citation-path, tokens, stage p50, taxonomy, config snapshot, `--loop` / `--pack-mode`).
- **Status:** `partial` — **Delta:** suite is 8 fixtures on *this* repo only; no prompt version id; no LLM-as-judge (deferred); no online feedback. Session/sage now honor `expand_on`; default config list stays empty.
- **Fusion priority:** P1 grow hard-set + architecture/why questions (unblocks wiki/TurboVec judgment).
- **Evidence:** first-pass

## 21. WorkOS

- **Links:** https://workos.com · Agent Registration / auth.md · [notes/workos.md](notes/workos.md)
- **Best stealable ideas:** Scoped agent tokens, claim ceremony — only for hosted multi-tenant APIs.
- **Why it matters:** None for local CLI.
- **Map to module:** —
- **Status:** `reject` — overkill; lock-in. If remote MCP ever exists, use a local token file first.
- **Fusion priority:** —
- **Evidence:** fusion-note

## 22. Firecrawl

- **Links:** https://github.com/firecrawl/firecrawl · https://docs.firecrawl.dev/ · [notes/firecrawl.md](notes/firecrawl.md)
- **Best stealable ideas:** LLM-ready scrape/crawl → markdown chunks with `source=web`. Prefer Jina; Firecrawl when JS-heavy. Keyless cloud exists — **do not** make it default (egress).
- **Why it matters:** Accuracy on “how do we use library X?” (docs + local wrappers). Tokens only if we pack web chunks via CCR.
- **Map to module:** NEW ingest CLI; chunk metadata; retriever unchanged.
- **Status:** `gap`
- **Fusion priority:** P2 (after doctor).
- **Evidence:** fusion-note

## 23. Google Code Wiki

- **Links:** https://codewiki.google/ · https://developers.googleblog.com/en/introducing-code-wiki-accelerating-your-code-understanding/ · deep [../deep/google-code-wiki.md](../deep/google-code-wiki.md)
- **Best stealable ideas:** (1) Incremental living wiki from the KG (template first, no LLM). (2) Every heading cites `path:symbol`. (3) Chat-over-wiki then CCR expand to source. (4) Diagrams from **edges**, not the model.
- **Why it matters:** Accuracy (stable narrative + grounded links); tokens (summaries first); UX (`info --mermaid` already a precursor).
- **Map to module:** KG Mermaid **done**; CLI `wiki generate` / `list` / `show` **done** (template + OKF WikiPage + `path:symbol`); system prompt cite style **done** (`` `path:symbol` ``); `wiki generate --dirty` + watch hook **done**; RRF `kind=wiki` channel **done** (default `wiki_weight=0.0`).
- **Status:** `partial` — generator + dirty regen + opt-in wiki RRF shipped. **Delta:** chat-over-wiki via CCR; LLM polish; architecture/explain eval fixtures.
- **Fusion priority:** P0
- **Evidence:** deep-dive

---

## Already fused (do not rebuild)

| Mechanism | Where |
|-----------|--------|
| Golden suite + Recall@k / nDCG / citation-path / tokens / stage p50 / failure taxonomy | `harness/eval.py`, `.docs/research/eval/` |
| CCR-lite + cache + retrieve-back | `harness/ccr.py`, `ContextBuilder` |
| Grade / rewrite / HyDE-on-retry / deepen / easy BM25 / `--verify` hook | `harness/loop.py` |
| exposes / tested_by / gloss / beam / Mermaid | `harness/kg_enrich.py`, `KnowledgeGraph` |
| Secret redaction + audit JSONL | `harness/redact.py`, `harness/audit.py` |
| `doctor` + localhost MCP / stdio MCP / `POST /v1/retrieve` | `harness/doctor.py`, `harness/serve.py` |

## Biggest underextracted sources

Sources with the most **material delta** still on the table (not rejects):

1. **Google Code Wiki + OKF** — `wiki generate` + WikiPage emit + `--dirty` / watch hook + opt-in RRF `wiki_weight` + full-vault `knowledge export|import` + opt-in packer prefix-load of `knowledge/**/*.md` shipped; remaining: chat-over-wiki via CCR, LLM polish.
2. **Memanto** — typed store + supersession + brief + heuristic auto-extract shipped (opt-in); remaining: BM25-over-memory and eval “why” fixtures.
3. **Strands + Forge + Claurst** — session `/compact` `/cost` sage + localhost HTTP + stdio MCP retrieve shipped; remaining: caveman, graph-explorer digest.
4. **Agent-Reach + Proxima + OpenHuman** — doctor + query cache + MCP/`POST /v1/retrieve` + **stdio MCP** **shipped** (loopback HTTP / no-bind stdio). Remainder: Jina ingest, serve-cache on eval/interactive.
5. **TurboVec** — protocol + opt-in backend + eval A/B gate shipped; still experimental, not default. Remainder: dual-write, TQ+ calibrate, default flip.
6. **Headroom remainder** — expand-on-explain, stable cache key, type-aware pack.
7. **Code-graph remainder** — path templates, `calls` quality (regex is the real accuracy bug).
