# LLM in Production (notable resources)

## What it is
Canonical books/repos on shipping LLM systems: evaluation, RAG, agents, inference, ops.

## Links
- Chip Huyen — AI Engineering companion: https://github.com/chiphuyen/aie-book (book: O’Reilly *AI Engineering*)
- Designing ML Systems: https://github.com/chiphuyen/dmls-book
- LLM Engineer’s Handbook (Packt code): https://github.com/PacktPublishing/LLM-Engineers-Handbook
- Brousseau & Sharp — *LLMs in Production* (Manning) — notable book title match
- Made With ML (Goku Mohandas) — production ML curriculum (recommended alongside)

## How it works (themes relevant to Code-Harness)
From aie-book/resources emphasis:
- **Evaluation first** — offline golden sets + online feedback.
- RAG failure modes: retrieval miss, context stuffing, outdated docs.
- Prompt/context caching, routing, guardrails.
- Inference optimization separate from app logic.
- Architecture: gateway, observability, versioned prompts.

## Relevance to Code-Harness
**Medium–High (methodology).** Not a drop-in library; shapes how to harden Code-Harness for real users.

## Concrete ideas to adopt
1. **Golden eval harness** (accuracy): Fixed questions per sample repo + recall@k / citation accuracy / answer judge.
2. **Versioned prompts + config** (accuracy): Prompt/config git tags; A/B retrieval weights.
3. **Observability** (perf/cost): Structured traces for each retrieval stage latency + tokens.
4. **Failure taxonomy** (accuracy): Label misses as sparse/dense/graph/rerank failures (`--debug` already helps — formalize).
5. **Context caching strategy** (cost): Documented in production LLM guides — apply to system+ARCHITECTURE prefix.

## Risks/caveats
- Generic advice can distract from code-specific retrieval work.

## Open questions
- Public leaderboard for code-RAG harnesses to compare against?
