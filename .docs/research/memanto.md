# Memanto (and related: Mem0 / Memento)

## What it is
**Best match:** [moorcheh-ai/memanto](https://github.com/moorcheh-ai/memanto) — a **Memory Agent** (companion agent) that manages other agents’ memories: extract, consolidate, reconcile conflicts, forget/expire, brief agents, export via **OKF**.

**Related candidates:**
- **Mem0** (https://github.com/mem0ai/mem0) — popular memory *layer* (SDK/API); hybrid semantic+BM25+entity linking in v3.
- “Memento” — often used generically; less clear single product match.

## Links
- Memanto: https://github.com/moorcheh-ai/memanto · https://docs.memanto.ai
- Paper: https://arxiv.org/abs/2604.22085
- Mem0: https://github.com/mem0ai/mem0 · https://docs.mem0.ai

## How it works (Memanto)
Six behaviors: observe/extract → consolidate → reconcile (supersede, not silent overwrite) → forget (policy) → brief (`agent bootstrap`) → move (OKF export/migrate).
- Typed memories (13 types: decision, preference, fact, error, …).
- Temporal recall: `--as-of`, `--changed-since`.
- Local Docker+Ollama or free cloud; `memanto connect` for Claude/Cursor/Codex/etc.
- Claims ~89.8% LongMemEval / 87.1% LoCoMo (directional; configs vary).

Mem0 v3: ADD-only extraction; hybrid search (semantic+BM25+entity); built-in entity linking graph.

## Relevance to Code-Harness
**High.** Code-Harness indexes *code*; Memanto/Mem0 pattern covers *durable project knowledge* (decisions, prefs, failed approaches) that shouldn’t live only in chat or raw chunks.

## Concrete ideas to adopt
1. **Typed project memory alongside code index** (accuracy/tokens): Store `decision`/`error`/`preference` memories separately; inject a short brief before RAG context. Why: fewer tokens than re-retrieving narrative docs; higher precision on “why did we…”.
2. **Conflict supersession** (accuracy): When two decisions conflict, mark superseded + keep history (`as-of`). Why: stale ARCHITECTURE.md is a common RAG failure mode.
3. **Agent bootstrap brief** (tokens): On interactive start, pull top-K typed memories + ARCHITECTURE.md, not full history.
4. **Hybrid memory search** (accuracy): Mirror Mem0 — semantic + BM25 + entity boost for memory (Code-Harness already does this for code).
5. **OKF export of project memory** (portability): See OKF item.

## Risks/caveats
- Don’t conflate chat memory with code chunks — separate stores.
- Memory extraction LLMs add cost; schedule offline.

## Open questions
- Build thin native memory or integrate Memanto/Mem0 via MCP?

## Deep dive
Design note: [deep/memanto.md](deep/memanto.md)
