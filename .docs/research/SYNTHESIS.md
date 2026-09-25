# Code-Harness Improvement Roadmap — SYNTHESIS

Prioritized by expected impact on **(P) performance**, **(A) accuracy/results**, **(T) tokens/cost**.  
Each proposal cites inspiring sources from this research set.

---

## Top recommendations (ranked)

### 1. Post-retrieval reversible context compression (CCR-style)
**Impact:** T ★★★★★ · P ★★★☆☆ · A ★★★★☆ (if reversible)  
**Sources:** Headroom (#1), Google Code Wiki (#23), ForgeCode compact (#13)

After MMR assembly, send **signatures + docstrings + key spans** to the LLM; keep full chunk text in a local cache keyed by `chunk_id`; expose `retrieve_chunk(id)` (CLI tool or MCP). Stabilize system prompt + `ARCHITECTURE.md` as a KV-cache-friendly prefix; put volatile chunks last.

**Why:** Largest immediate token saving without abandoning hybrid retrieval quality. Headroom’s own docs warn raw code compression is dangerous — reversibility is the safety valve.

---

### 2. Corrective / Adaptive retrieval loop (not a one-shot pipeline)
**Impact:** A ★★★★★ · T ★★★★☆ · P ★★★☆☆  
**Sources:** Prompt→Loop→Graph (#18), 500-AI-Agents CRAG/Self-RAG (#10), Strands context mgmt (#3), Proxima self-heal (#9)

Replace linear `dense|BM25|graph → RRF → rerank → MMR → LLM` with:

`retrieve → grade relevance → (rewrite / HyDE / deepen graph | proceed) → answer → citation-coverage check → maybe re-retrieve`

Easy queries: BM25-only or skip LLM (`--no-llm` path). Hard queries: full stack. Independent **verify** step with fresh context (no generator CoT).

**Why:** Code-Harness already has good components; the missing piece is *conditional re-retrieval* and explicit stop conditions (loop engineering).

---

### 3. Upgrade the knowledge graph (edges, traces, gloss, optional Cypher)
**Impact:** A ★★★★★ · P ★★☆☆☆ · T ★★★☆☆  
**Sources:** Code-Graph-RAG (#14), GraphCodeAgent/RANGER papers (#14), Obsidian gloss (#6), Google Code Wiki diagrams (#23)

Keep NetworkX default, but add: endpoint/expose edges, test→code, data-flow; **gloss/note nodes**; optional dynamic call overlay from test traces; Mermaid export; beam/MCTS multi-hop instead of fixed `max_depth=3` BFS. Optional Neo4j/Memgraph later.

**Why:** Multi-hop “who calls / what exposes” questions are where pure vector RAG fails; Code-Harness already has a KG — deepen it.

---

### 4. Living wiki generation + typed project memory (OKF)
**Impact:** A ★★★★☆ · T ★★★★☆ · P ★★☆☆☆  
**Sources:** Google Code Wiki (#23), Memanto (#5), OKF (#16), Headroom learn (#1), Forge AGENTS.md (#13)

Add `code-harness wiki generate` (incremental on `watch`) producing module pages + ARCHITECTURE.md + Mermaid from the KG; index wiki pages as high-priority chunks. Parallel **typed memory** store (`decision`, `error`, `preference`) with conflict supersession and OKF import/export; `agent bootstrap` brief on interactive start.

**Why:** Stops ARCHITECTURE.md drift; durable decisions shouldn’t compete with raw chunk soup; OKF keeps knowledge portable across Claude/Cursor/etc.

---

### 5. Vector backend option: TurboVec + BM25 allowlist hybrid
**Impact:** P ★★★★★ · T ★☆☆☆☆ · A ★★★☆☆ (must validate recall)  
**Sources:** TurboVec (#15), Agent-Reach channel pattern (#11)

Optional `vector_store.provider = turbovec` (4-bit TurboQuant). Stage-1 BM25/SQL → allowlist → dense search inside SIMD kernel. Incremental `sync()` paired with `watch`. Keep Chroma as default until recall benches pass on code embeddings.

**Why:** Multi-repo indexes blow RAM; TurboVec’s compression + filtered search matches Code-Harness’s hybrid design.

---

### 6. MCP / OpenAI-compatible retrieve API + doctor/fallback providers
**Impact:** P ★★★★☆ (product) · A ★★★☆☆ · T ★★☆☆☆  
**Sources:** ForgeCode (#13), OpenHuman (#8), Strands (#3), Claurst ACP (#2), Agent-Reach (#11), Firecrawl MCP (#22)

Ship `code-harness mcp serve` and/or `POST /v1/retrieve`. `code-harness doctor` probes embedding/LLM backends with ordered fallbacks (local → Voyage → OpenAI). Position as the local indexer behind Forge/Claude/Strands (“sage” read-only profile).

**Why:** Distribution: other agents bring the loop; Code-Harness wins on retrieval.

---

### 7. Eval harness + observability (production discipline)
**Impact:** A ★★★★☆ · P ★★★☆☆ · T ★★★☆☆  
**Sources:** LLM in production / Chip Huyen (#20), AI governance audit (#19), Medium question packs (#12), Loop anchors (#18)

Golden Q→chunk_id / answer suites per sample repo; stage latency + token traces; failure taxonomy (dense/BM25/graph/rerank); versioned retrieval config. Optional max-$ budget and secret redaction in context assembly.

**Why:** Without anchors, weight tweaks Goodhart. `--debug` is a start — formalize it.

---

### 8. External doc ingest (Jina → Firecrawl fallback) beside code
**Impact:** A ★★★☆☆ · T ★★☆☆☆ · P ★★☆☆☆  
**Sources:** Firecrawl (#22), Agent-Reach (#11)

`index-url` / `index-site` → Markdown chunks with `source=web` + version metadata; joint retrieve with code. Prefer free Jina Reader; Firecrawl for JS-heavy sites.

**Why:** “How do we use X?” needs upstream docs + local wrappers.

---

## Explicitly deprioritize (for now)

| Item | Why |
|------|-----|
| WorkOS (#21) | Enterprise auth only matters for SaaS |
| OpenMontage (#4) | Video domain |
| AirLLM (#17) | Local huge-model inference; orthogonal & slow |
| Jitro (#7) | No public implementation |
| Interview Medium repos (#12) | Pedagogy, not retrieval tech |
| Full Strands/Forge rewrite | Integrate as backend; don’t become another agent clone |

---

## Recommended first PRs (deep pack)

Eval **before** packer/loop/KG so later diffs have anchors. Details: [deep/INTEGRATION_PLAN.md](deep/INTEGRATION_PLAN.md).

`eval metrics → CCR-lite packer → query loop → KG enrichment` — **shipped**. Next five: [fusion/GAP_AUDIT.md](fusion/GAP_AUDIT.md).

## Suggested 90-day sequence

| Phase | Weeks | Deliverables |
|-------|-------|--------------|
| A | 1–3 | Eval suite + token/stage traces; then CCR-lite packer + cache-friendly prompt layout |
| B | 2–5 | CRAG-style grade+retry loop; adaptive BM25-only path; citation check |
| C | 4–8 | KG edge enrichment + gloss notes + Mermaid; wiki generate MVP |
| D | 6–10 | Typed memory + OKF export (Google SPEC subset); interactive bootstrap brief |
| E | 8–12 | TurboVec experimental backend + allowlist hybrid (blocked on recall@k) |
| F | ongoing | MCP serve + doctor; optional Firecrawl/Jina ingest |

---

## Mapping: Code-Harness today → gaps

```
SHIPPED SPINE (this branch family):
  parse → chunk → Chroma + BM25 + NetworkX
       → RRF → CE → MMR → CCR-lite packer
       → opt-in graded loop (grade / rewrite / HyDE / deepen)
       → KG: exposes / tested_by / gloss + beam + Mermaid
       → eval: Recall@k / citation-path / tokens / stage p50

STILL OPEN (see fusion/GAP_AUDIT.md):
  LLM polish | memory LLM-extract / BM25-over-memory
  | session LLM compact | optional token-cap
  | doctor + MCP retrieve
  | TurboVec (blocked) | path templates / calls quality | web ingest
```

This file’s 90-day table is **historical first-pass intent**. Execution order and honesty about what landed live in [fusion/](fusion/README.md) and [deep/INTEGRATION_PLAN.md](deep/INTEGRATION_PLAN.md).

---

## File map
All first-pass write-ups live beside this file; start at [INDEX.md](INDEX.md). HIGH-source design notes and the sequenced PR plan are under [deep/](deep/README.md).

**Fusion backlog (steal matrix, next-5 PRs, scorecard):** [fusion/README.md](fusion/README.md) — [STEAL_MATRIX.md](fusion/STEAL_MATRIX.md) · [GAP_AUDIT.md](fusion/GAP_AUDIT.md) · [FUSION_THESIS.md](fusion/FUSION_THESIS.md).

HIGH items **partially fused:** Headroom (CCR-lite), Strands (loop policy), Forge (pack knob), Code graph (enrichment), Prompt→Loop→Graph (eval+loop), Code Wiki (Mermaid only).

HIGH items **still design-only:** Memanto, OKF, TurboVec. Wiki generate remains design-only aside from the Mermaid precursor.
