# Fusion thesis — what “best of its kind” means here

This pack is a **map**, not a medal. Code-Harness is not yet the best hybrid code-RAG of its kind. The shipped spine (eval → CCR-lite → corrective loop → KG enrichment) is the substrate. Fusion is how we steal remaining mechanisms without becoming Forge, Strands, or a Headroom proxy.

## Product shape

**Code-Harness is a local-first retrieval engine for repositories.** It parses with tree-sitter, chunks by entity, and answers (or packs context) from **dense + BM25 + typed KG**, fused with RRF, optional cross-encoder, MMR, then a reversible packer. Other agents may *call* it. It does not need to *be* those agents.

## Differentiators (honest, current vs target)

| Peer | They optimize | We already own | Target delta |
|------|---------------|----------------|--------------|
| **Plain Chroma RAG** | Cosine over chunks | Hybrid RRF + CE + MMR + KG beam + eval suite | Keep hybrid; do not regress to “embed the repo and pray” |
| **Headroom-as-proxy** | Token compression in front of *any* app | In-process CCR-lite + `retrieve_chunk` + prefix hash | Stay reversible and packer-local; no Kompress sidecar unless eval says tool-JSON dominates |
| **Forge** | Terminal agent + hosted `:sync` | Local index, sage-shaped `--no-llm` / read-only query | Beat hosted search on privacy + hybrid recall; expose MCP retrieve so Forge can keep the TUI |
| **Strands Harness** | Assembled agent factory, sessions, memory | Loop stop conditions + easy BM25 path | Steal session budget + memory *split*; do not vendor Bedrock defaults |
| **Code-Graph-RAG / Memgraph** | Cypher + graph DB | NetworkX + exposes/tested_by/gloss + beam + Mermaid | Path templates + wiki, not a required graph server |
| **Google Code Wiki** | Hosted living docs + grounded chat | Mermaid export, gloss, weak path cites | Template `wiki generate` + `path:symbol` + chat-over-wiki via CCR |

## Non-goals

- Forking or wrapping Forge / Strands / Claurst / OpenHuman as the product.
- Hosted default index (Forge `:sync` cautionary tale).
- NL→Cypher, Neo4j/Memgraph, eBPF traces, GraphCodeBERT — until eval hard-set demands them.
- AirLLM layer-streamed 70B+ on the query path (latency antithesis).
- WorkOS / SSO / agent claim ceremonies (no SaaS).
- Video studios, interview-prep curricula, LinkedIn scraping, browser-session LLM ToS bypass.
- Claiming viral “+18% / −85%” loop numbers (lecture caveat).

## Success scorecard

Gates are **relative to a frozen suite + config snapshot** (already written by `python main.py eval`). Flip a flag only after a before/after JSON exists under `.code-harness/eval/`.

| Gate | Metric (existing harness) | Target vs current spine |
|------|---------------------------|-------------------------|
| **Accuracy — retrieve** | Recall@k, nDCG@k | No silent drop when adding packer/backends. Hard subset (`who calls` / `what exposes`) **up** after wiki / path templates |
| **Accuracy — grounded** | citation-path hit rate; optional `--verify` later | `must_cite_paths` ≥ baseline; packed headers stay within **5 points** of `full` |
| **Tokens / cost** | `prompt_tokens_packed` vs `prompt_tokens_full`; `prompt_token_drop` | CCR-lite **≥30%** drop on this repo’s suite without Recall@k change. Wiki/memory briefs **lower** mean tokens on “explain architecture / why did we…” vs stuffing bodies |
| **Latency** | stage p50 (`dense` / `bm25` / `graph` / `ce` / `mmr`); loop `attempts` | Easy queries: BM25 path, p50 **≤ +15%** vs one-shot. `max_loops=0` bit-identical to one-shot |
| **Local-first** | doctor + config | Index and graph never leave the machine. Cloud embed/LLM are **opt-in**. No new required hosted service |
| **Replay** | `config` snapshot in eval JSON | Every fusion PR pastes a table; weights are not tuned by folklore |

**Default posture:** `pack_mode=full`, `max_loops=0`, Chroma HNSW. Fusion features ship **off** until the suite says otherwise.

## How we will know we failed

- A “fusion” PR that adds a remote default or an agent TUI without a Recall@k / token table.
- Memory or wiki written into the code embedding collection (chat/decision soup).
- TurboVec (or any quantizer) becoming default without the recall gates in [../deep/turbovec.md](../deep/turbovec.md).
- Calling the product best-of-kind because the research folder is large.
