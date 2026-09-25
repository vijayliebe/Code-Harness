# Code-Harness External Research — INDEX

Research date: **2026-09-24** (Europe/Dublin / IST).  
Target project: [vijayliebe/Code-Harness](https://github.com/vijayliebe/Code-Harness) — Python hybrid code-RAG CLI (tree-sitter → chunk → ChromaDB + BM25 + KG → RRF / cross-encoder / MMR → LLM).

**Status legend:** Resolved = primary URL identified with workable detail. Partial = best-effort match with ambiguity noted. Unresolved = no credible primary source.

| # | Item | Slug / file | Status | Resolved URL / primary source | 1-line takeaway | Relevance |
|---|------|-------------|--------|-------------------------------|-----------------|-----------|
| 1 | headroom | [headroom.md](headroom.md) | Resolved | https://github.com/headroomlabs-ai/headroom | Local reversible context compression (CCR) before the LLM — big token wins on tools/logs. | **High** |
| 2 | Claurest | [claurest.md](claurest.md) | Resolved* | https://github.com/Kuberwastaken/claurst | Rust Claude-Code-style agent; steal `/compact`, cost telemetry, ACP — not the RAG core. | **Med** |
| 3 | strands harness | [strands-harness.md](strands-harness.md) | Resolved | https://github.com/strands-agents/harness-sdk | AWS Strands assembled harness: sessions, auto context mgmt, memory, subagents. | **High** |
| 4 | openmontage | [openmontage.md](openmontage.md) | Resolved | https://github.com/calesthio/OpenMontage | Agentic video studio; pattern value only (skills, scored pipelines). | **Low** |
| 5 | Memanto | [memanto.md](memanto.md) | Resolved | https://github.com/moorcheh-ai/memanto | Memory *agent* (curate/conflict/forget/brief) + OKF; Mem0 is related storage layer. | **High** |
| 6 | obsidian | [obsidian.md](obsidian.md) | Resolved | https://obsidian.md (+ Smart Connections / code-graph plugins) | Local Markdown KG UX; gloss notes + vault export overlay for code graphs. | **Med** |
| 7 | jitro | [jitro.md](jitro.md) | Partial | Jules SDK / press on “Project Jitro”; no public Jitro repo | Rumored Google coding-agent evolution; inspirational only. | **Low** |
| 8 | open human | [open-human.md](open-human.md) | Resolved | https://github.com/tinyhumansai/openhuman | Desktop agent with local memory MCP — expose Code-Harness the same way. | **Med** |
| 9 | proxima | [proxima.md](proxima.md) | Resolved* | https://github.com/Zen4-bit/Proxima | Local MCP gateway + self-heal coding agent; OpenAI-compatible retrieve endpoint idea. | **Med** |
| 10 | 500 ai agents | [500-ai-agents-project.md](500-ai-agents-project.md) | Resolved | https://github.com/ashishpatel26/500-AI-Agents-Projects | Catalog pointing to CRAG / Self-RAG / Adaptive RAG patterns. | **Med** |
| 11 | agent-reach | [agent-reach.md](agent-reach.md) | Resolved | https://github.com/Panniantong/Agent-Reach | No-key platform access (Jina/LinkedIn browser MCP); doctor + fallback channels. | **Med** |
| 12 | Medium 4 repos | [medium-interview-prep-repos.md](medium-interview-prep-repos.md) | Resolved* | Article + 4 interview repos (see file) | Interview-prep curricula; low RAG transfer; useful as eval question packs. | **Low** |
| 13 | forgecode | [forgecode.md](forgecode.md) | Resolved | https://github.com/tailcallhq/forgecode | Terminal coding agent with `:sync` semantic search, sage/muse agents, compact. | **High** |
| 14 | code graph | [code-graph.md](code-graph.md) | Resolved | https://github.com/vitali87/code-graph-rag (+ papers) | Tree-sitter→Memgraph RAG + MCP; richer edges, NL→Cypher, dynamic traces. | **High** |
| 15 | turboVec | [turbovec.md](turbovec.md) | Resolved | https://github.com/RyanCodrai/turbovec | TurboQuant compressed vector index; RAM↓ + SIMD search + allowlist hybrid. | **High** |
| 16 | OKF | [okf.md](okf.md) | Resolved | https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md | **Google SPEC** (v0.2) for markdown+YAML knowledge bundles; Memanto implements export/import. | **High** |
| 17 | AirLLM | [airllm.md](airllm.md) | Resolved | https://github.com/lyogavin/airllm | Layer-streamed local inference for huge models on tiny VRAM. | **Low** |
| 18 | Prompt→Loop→Graph | [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md) | Resolved | Learn Harness Eng. L14 + DesignGurus | Stack: prompt ⊂ context ⊂ loop ⊂ graph; retrieval should become a graded loop. | **High** |
| 19 | AI governance | [ai-governance.md](ai-governance.md) | Resolved | Agent Control Plane / OPA patterns / gateways | Budgets, redaction, audit, approval — progressive for CLI→agent. | **Med** |
| 20 | LLM in production | [llm-in-production.md](llm-in-production.md) | Resolved | chiphuyen/aie-book + LLM Engineer’s Handbook | Eval-first, observability, versioned prompts for production RAG. | **Med** |
| 21 | WorkOS | [workos.md](workos.md) | Resolved | https://workos.com (Agent Auth) | Enterprise SSO + agent tokens — only if SaaS; skip for local CLI. | **Low** |
| 22 | Firecrawl | [firecrawl.md](firecrawl.md) | Resolved | https://github.com/firecrawl/firecrawl | LLM-ready web scrape/crawl/MCP — ingest external docs beside code. | **Med** |
| 23 | Google Code Wiki | [google-code-wiki.md](google-code-wiki.md) | Resolved | https://codewiki.google/ · Google Developers Blog | Living AI wiki + diagrams + grounded chat — inspiration for `wiki generate`. | **High** |

\* Ambiguity documented in the item file (alternate candidates listed).

## Counts
- **Resolved:** 21  
- **Partial:** 1 (`jitro`)  
- **Unresolved:** 0  

## See also
- [SYNTHESIS.md](SYNTHESIS.md) — prioritized Code-Harness roadmap
- [deep/README.md](deep/README.md) — HIGH-source design notes
- [deep/INTEGRATION_PLAN.md](deep/INTEGRATION_PLAN.md) — sequenced PRs (`eval metrics → CCR-lite packer → query loop → KG enrichment`) — **spine shipped**; remaining work in the fusion pack
- [fusion/README.md](fusion/README.md) — steal matrix + gap audit + thesis (path to best-of-kind, not a claim it is achieved)

### Fusion pack (post-spine)

| File | What |
|------|------|
| [fusion/STEAL_MATRIX.md](fusion/STEAL_MATRIX.md) | All 23 INDEX rows: steal / module / `done`·`partial`·`gap`·`reject` / P0–P3 |
| [fusion/GAP_AUDIT.md](fusion/GAP_AUDIT.md) | Open gaps only; recommended next 5 PRs |
| [fusion/FUSION_THESIS.md](fusion/FUSION_THESIS.md) | Differentiators + non-goals + eval/token/latency scorecard |
| [fusion/notes/](fusion/notes/) | Mini-deepens for thin Medium / weak-primary cards |

### Deep dives (HIGH only)

| Item | Deep dive |
|------|-----------|
| headroom | [deep/headroom.md](deep/headroom.md) |
| strands harness | [deep/strands-harness.md](deep/strands-harness.md) |
| Memanto | [deep/memanto.md](deep/memanto.md) |
| forgecode | [deep/forgecode.md](deep/forgecode.md) |
| code graph | [deep/code-graph.md](deep/code-graph.md) |
| turboVec | [deep/turbovec.md](deep/turbovec.md) |
| OKF | [deep/okf.md](deep/okf.md) |
| Prompt→Loop→Graph | [deep/prompt-loop-graph-engineering.md](deep/prompt-loop-graph-engineering.md) |
| Google Code Wiki | [deep/google-code-wiki.md](deep/google-code-wiki.md) |

**Correction:** OKF is a Google Cloud specification, not a Memanto-owned format. First-pass `okf.md` was updated.

### HIGH fusion status (vs design-only)

Spine on the KG-enrichment tip: eval harness, CCR-lite packer, corrective loop, KG enrichment. That is **not** full fusion.

| Item | Deep dive | Fusion |
|------|-----------|--------|
| headroom | [deep/headroom.md](deep/headroom.md) | **Partially fused** — CCR-lite + retrieve-back. Delta: expand-on-explain, stable cache key, type-aware pack |
| strands harness | [deep/strands-harness.md](deep/strands-harness.md) | **Partially fused** — loop stops + easy path. Delta: session budget, MCP |
| Memanto | [deep/memanto.md](deep/memanto.md) | **Design-only** |
| forgecode | [deep/forgecode.md](deep/forgecode.md) | **Partially fused** — pack env knob. Delta: sage profile, `/compact`, MCP-as-`:sync` |
| code graph | [deep/code-graph.md](deep/code-graph.md) | **Partially fused** — exposes/tested_by/gloss + beam + Mermaid. Delta: path templates, `calls` quality |
| turboVec | [deep/turbovec.md](deep/turbovec.md) | **Design-only** (blocked on recall gates) |
| OKF (Google SPEC) | [deep/okf.md](deep/okf.md) | **Design-only** |
| Prompt→Loop→Graph | [deep/prompt-loop-graph-engineering.md](deep/prompt-loop-graph-engineering.md) | **Partially fused** — eval + loop. Delta: session state, `path:symbol` cites |
| Google Code Wiki | [deep/google-code-wiki.md](deep/google-code-wiki.md) | **Partially fused** — Mermaid precursor. Delta: `wiki generate` |
