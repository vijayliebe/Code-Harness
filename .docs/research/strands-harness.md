# Strands Harness (AWS Strands Agents)

## What it is
Official **Strands Agents** “fully assembled” agent harness from AWS/community: `create_harness()` (Python) / `createHarness()` (TypeScript) with benchmarked defaults for model, tools, memory, sessions, and **automatic context management**.

## Links
- Monorepo: https://github.com/strands-agents/harness-sdk
- Docs: https://strandsagents.com/docs/user-guide/harness/
- Quickstart: https://strandsagents.com/docs/user-guide/harness/quickstart/
- PyPI: `strands-harness` · npm: `@strands-agents/harness`

## How it works
- One factory call yields an agent with shell/file/web tools, reasoning-on defaults, **prompt caching**, **automatic context management**, long-term memory, `generalist` subagent, `todos` tracker.
- Sessions persist under `./.agent/sessions` by default; long-term memory is separate from session transcripts.
- Model-agnostic (Bedrock default; Anthropic/OpenAI/Gemini/Ollama).
- CLI wizard (`strands`) exports to code.
- Lower-level SDK underneath if defaults don’t fit.

## Relevance to Code-Harness
**High (architectural).** Code-Harness is the *retrieval/context* half of a harness; Strands shows the production packaging: sessions, context management, memory, subagents, approval modes. Natural pairing: Strands agent + Code-Harness as code-retrieval tool/MCP.

## Concrete ideas to adopt
1. **Automatic context management policy** (tokens): When context exceeds budget, summarize older retrieval packs; never drop latest query+top chunks. Why: Strands ships this as a default — Code-Harness interactive mode needs it.
2. **Session vs long-term memory split** (accuracy/tokens): Session = conversation; long-term = durable repo facts (`decisions`, `gotchas`) separate from RAG chunks. Why: avoids polluting vector store with chat noise.
3. **`create_harness`-style defaults** (perf): One opinionated `CodeHarness()` with good embedding/rerank/top_k defaults + escape hatches. Why: lowers time-to-value.
4. **Subagent for deep graph walks** (accuracy/tokens): Main agent gets brief context; specialized “code explorer” subagent does multi-hop graph expansion and returns a digest. Why: cheaper than stuffing full walks into every prompt.
5. **Tool-approval mode** (governance): Confirm before write/shell when Code-Harness grows agent features.

## Risks/caveats
- Pulling in full Strands may be heavier than needed if Code-Harness stays CLI-RAG.
- Bedrock-centric defaults may confuse non-AWS users.

## Open questions
- Integrate as MCP tool behind Strands, or reimplement a thin subset of harness patterns?
