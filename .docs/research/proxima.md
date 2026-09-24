# Proxima

## What it is
**Best match:** [Zen4-bit/Proxima](https://github.com/Zen4-bit/Proxima) — local multi-AI **MCP server / OpenAI-compatible gateway** plus a Python coding agent that analyzes codebases, edits, tests, browser control, self-healing debug loops; SQLite cross-session memory.

## Links
- https://github.com/Zen4-bit/Proxima
- Related RAG alternative if user meant codebase RAG: https://github.com/shashvat-singham/repomind

## How it works
- Gateway routes models; agent tools: analyze_file, solve, fix_error, review_code, write_tests, deep_search.
- Self-healing: run → error → fix loop.
- SQLite memory across sessions.

## Relevance to Code-Harness
**Medium.** Gateway + self-heal patterns; less about hybrid retrieval quality.

## Concrete ideas to adopt
1. **OpenAI-compatible `/v1` retrieve endpoint** (perf/product): `POST /retrieve` returning ranked chunks — drop-in for agents.
2. **Self-heal query loop** (accuracy): If LLM answer has low citation coverage, auto re-query with HyDE/graph expand.
3. **SQLite sidecar for query cache** (perf/tokens): Cache (query_hash → chunk_ids) for repeated questions.

## Risks/caveats
- Early/small project; validate quality before depending.
- RepoMind may be better RAG reference than Proxima itself.

## Open questions
- Confirm user meant Zen4-bit/Proxima vs another Proxima (several forks exist).
