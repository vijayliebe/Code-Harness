# Code Graph (knowledge graphs for code RAG)

## What it is
Family of systems that parse code into a **graph** (AST entities + relationships) and retrieve via graph traversal ± vectors ± Cypher.

**Best practical matches for Code-Harness:**
1. **Code-Graph-RAG** — https://github.com/vitali87/code-graph-rag (tree-sitter → Memgraph/Neo4j → NL→Cypher + semantic + MCP)
2. **GraphRepo** — https://github.com/gh0st-inth3-sh3ll/GraphRepo
3. Research: GraphCodeAgent (arxiv 2504.10046), RANGER (arxiv 2509.25257), GraphCoder
4. Encoder (not repo RAG): **GraphCodeBERT** (data-flow-aware code embeddings, 2021)

Code-Harness already has NetworkX KG + graph expansion (max_depth=3).

## Links
- https://github.com/vitali87/code-graph-rag
- https://arxiv.org/html/2504.10046 (GraphCodeAgent)
- https://arxiv.org/pdf/2509.25257 (RANGER)

## How it works (Code-Graph-RAG)
- Tree-sitter multi-language parse → language-agnostic schema in Memgraph (Neo4j option).
- NL query → LLM generates Cypher → graph results → code fetch.
- MCP tools: endpoints, callers, remote_dependencies, gloss notes.
- Dead-code via call/reference walks; `cgr trace` merges dynamic calls (tests/eBPF) into graph.
- Optional Qdrant semantic search.

GraphCodeAgent: dual graphs (requirement graph + code graph) for multi-hop repo generation.
RANGER: Cypher + MCTS for retrieval.

## Relevance to Code-Harness
**High.** Direct competitor/peer. Upgrade path for Code-Harness’s already-present KG.

## Concrete ideas to adopt
1. **Richer edge types** (accuracy): imports, calls, inheritance, **EXPOSES/RESOLVES_TO** (API endpoints), data-flow, test→code. Why: better multi-hop answers.
2. **NL→structured graph query** (accuracy): Optional Cypher/path query for “who calls X” instead of only neighbor expansion.
3. **Dynamic trace overlay** (accuracy): Merge runtime call graphs from test runs into static KG.
4. **Gloss/notes nodes** (accuracy): Agent-authored notes on entities.
5. **Endpoint-aware dead code / reachability** (accuracy): Useful for “is this used?” queries.
6. **GraphCodeBERT / code-specific embeddings** (accuracy): Upgrade from `all-MiniLM-L6-v2` for dense path.
7. **MCTS / beam multi-hop** (accuracy): Expand paths by value, not fixed depth=3 BFS.

## Risks/caveats
- Memgraph/Neo4j ops burden vs NetworkX JSON — keep NetworkX default; optional Neo4j.
- NL→Cypher can hallucinate queries — validate/allowlist.

## Open questions
- When to graduate from NetworkX to a graph DB?
