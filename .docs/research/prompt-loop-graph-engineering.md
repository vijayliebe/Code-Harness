# Prompt → Loop → Graph Engineering

## What it is
Conceptual stack (popularized/organized ~2025–2026; see Learn Harness Engineering lecture 14 and DesignGurus/FutureAGI posts):

| Layer | Shapes | Artifacts |
|-------|--------|-----------|
| **Prompt engineering** | Instructions | roles, examples, formats |
| **Context engineering** | Information | docs, memory, tools, env |
| **Loop engineering** | Runtime | observe→reason→act→verify→stop |
| **Graph engineering** | System | nodes, edges, shared state, routing |

Harness sits as the foundation these layers run on.

## Links
- https://walkinglabs.github.io/learn-harness-engineering/en/lectures/lecture-14-graph-engineering/
- https://www.designgurus.io/blog/prompt-engineering-vs-loop-engineering-vs-graph-engineering
- Anthropic “Building Effective Agents” patterns; LangGraph as implementation

## How it works
- Prompt ⊂ every node; context reassembled each loop turn; loops become graph nodes when you need specialization, parallelism, rollback, shared state, independent verification.
- Graph ≠ classical workflow: nodes can be full agents; edges can be dynamic.
- Caveats from lecture: viral fake “+18%/−85%” numbers; **orchestration tax** (human review is the GIL); prefer replayability/observability over topology fashion.

## Relevance to Code-Harness
**High.** Code-Harness today is strong **context engineering**. Next upgrades are loop (retrieve→grade→re-retrieve→answer→verify citations) and optional graph (research/retrieve/verify/merge nodes).

## Concrete ideas to adopt
1. **Make the retrieval pipeline an explicit loop** (accuracy/tokens): retrieve → grade → conditional expand/HyDE → rerank → answer → citation check.
2. **Independent verify node** (accuracy): Fresh context that only sees `code` chunks + answer, not the generator’s chain-of-thought.
3. **Shared state object** (perf): `{requirements, chunks, review, attempts}` persisted per session for resume.
4. **Don’t graphify linear indexing** (perf): Indexing stays a workflow/script; querying becomes the loop/graph.
5. **Anchors** (accuracy): Golden Q&A eval set pin metrics to reality (avoid Goodhart on “chunks retrieved”).

## Risks/caveats
- Over-orchestration burns tokens and latency.
- Buzzword churn — implement capabilities, not labels.

## Open questions
- Minimal viable loop before any multi-agent graph?
