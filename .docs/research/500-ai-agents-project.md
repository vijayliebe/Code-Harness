# 500 AI Agents Project

## What it is
Curated collection: [ashishpatel26/500-AI-Agents-Projects](https://github.com/ashishpatel26/500-AI-Agents-Projects) — 500+ agent use cases, tutorials, and runnable examples across LangGraph, CrewAI, AutoGen, Agno, LlamaIndex.

## Links
- https://github.com/ashishpatel26/500-AI-Agents-Projects

## How it works
- Catalog by framework and industry.
- `agents/` directory: self-contained demos (web research, code review, PDF Q&A, SQL, multi-agent debate, …).
- Especially relevant LangGraph RAG tutorials linked: Adaptive RAG, Agentic RAG, CRAG, Self-RAG.

## Relevance to Code-Harness
**Medium.** Best as a **pointer catalog** to RAG agent patterns (CRAG/Self-RAG/Adaptive RAG) to steal for code retrieval quality.

## Concrete ideas to adopt
1. **Corrective RAG (CRAG)** (accuracy): Grade retrieved chunks; if irrelevant, rewrite query / widen graph; else generate.
2. **Self-RAG** (accuracy/tokens): Model decides whether it needs more retrieval before answering — skip LLM calls when retrieval alone suffices (`--no-llm` evolution).
3. **Adaptive routing** (perf/cost): Easy questions → BM25 only; hard → full dense+graph+rerank.
4. **Code-review agent recipe** (product): Thin agent on top of Code-Harness retrieve for PR review use case.

## Risks/caveats
- Catalog quality varies; prefer primary LangGraph tutorials over random forks.
- Many examples are domain chatbots, not code RAG.

## Open questions
- Which CRAG grader model is cheap enough for default on?
