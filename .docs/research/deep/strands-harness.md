# Deep dive: Strands Harness (session + context policy)

**First-pass:** [../strands-harness.md](../strands-harness.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) PR 3 (loop policy); MCP later  
**Seam:** `main.py` interactive mode, future session store; **not** a rewrite onto AWS Strands

## 1. What we confirmed

`strands-agents/harness-sdk` is a **fully assembled** agent factory (`create_harness()` / `createHarness()`): model, tools, prompt caching, automatic context management, long-term memory, `generalist` subagent, todos, sessions under `./.agent/sessions`.

The transferable idea is **policy**, not the SDK:

| Strands default | Code-Harness analog |
|-----------------|---------------------|
| Automatic context management | Interactive history + retrieval packs must have a budget and a summarize/drop policy |
| Session ≠ long-term memory | Chat transcript vs typed repo facts (Memanto/OKF) vs code chunks |
| Prompt caching | Stable prefix (see [headroom.md](headroom.md) CacheAligner) |
| Subagents | Optional later: “graph explorer” returns a digest; do not stuff BFS walks into every prompt |
| Approval modes | Only if/when write/shell tools exist |
| Model-agnostic factory | We already have `LLMInterface` providers |

**Do not** vendor Strands or default to Bedrock. Position: Code-Harness remains the **retrieval backend**; Strands (or Claude/Forge) can call it later via MCP.

## 2. Code-Harness today

- `query` is **one-shot**: retrieve → assemble → (optional) LLM.
- `interactive` is a REPL over the same one-shot path (`/llm`, `/context`, `/clear`).
- No session file, no token budget, no summarization of older retrieval packs.
- `--debug` prints per-source breakdown but is not a persisted trace.
- HyDE exists but is off (`retrieval.hyde.enabled: false`).

The gap vs Strands is not “missing tools.” It is **no runtime policy** when context exceeds `max_context_tokens` (`ContextBuilder` uses `llm.max_tokens * 2`).

## 3. Policy to steal (thin)

### 3.1 Context budget

When assembled context > budget:

1. Never drop: current user query, top-1 packed chunk, project-doc prefix.
2. Summarize older **retrieval packs** from this session (one paragraph + chunk IDs).
3. Drop oldest packs first.

This is Strands-style automatic context management without their memory stack.

### 3.2 Session vs memory vs index

```
.code-harness/
  sessions/{id}.jsonl      # conversation + pack IDs (ephemeral)
  memory/                  # typed OKF-ish facts (PR 5)
  chromadb/ + graph_*.json # code index (durable, not chat)
```

Mixing chat into Chroma is a failure mode Memanto and Strands both warn about.

### 3.3 Loop stop conditions (feeds PR 3)

Strands “just keeps going” with tool calls. We want **explicit stops** (loop engineering):

- grade ≥ τ
- citation coverage ≥ τ
- `max_loops`
- easy-query short-circuit (BM25-only)

See [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md).

### 3.4 Subagent (later, not PR 3)

A “code explorer” that only runs beam graph expansion and returns `{paths, digest, chunk_ids}` is the right use of Strands’ subagent idea. Main interactive turn stays cheap. **Defer** until KG enrichment (PR 4) exists.

## 4. File-level sketch (future)

| File | Change |
|------|--------|
| `harness/session.py` (new) | JSONL session, pack references, summarize hook |
| `main.py` interactive | `/budget`, persist session id, load last |
| `harness/config.py` | `session.dir`, `session.max_packs`, `retrieval.max_loops` |
| `harness/context_builder.py` | honor budget + prefix stability |

PR 3 should implement **max_loops + easy-path + budget**, not the full session product.

## 5. Packaging later (PR 8)

`code-harness mcp serve` exposing `retrieve`, `retrieve_chunk`, `graph_neighbors` lets a Strands/Claude/Forge agent use us as the indexer. That is the intended integration, not `pip install strands-harness` inside this repo.

## 6. Acceptance (when the loop PR lands)

- Easy identifier queries do not start a second retrieve (latency).
- Interactive session of 10 turns does not exceed budget (drops/summarizes old packs).
- No Bedrock/Strands dependency in `requirements.txt`.

## 7. Risks

- Copying “fully assembled harness” scope explodes the CLI into an agent clone (explicitly deprioritized in [../SYNTHESIS.md](../SYNTHESIS.md)).
- Summarization LLMs add cost — heuristic first: keep IDs + first sentences, no extra model call.

## 8. Recommended PR slice

Feed **stop conditions + budget + easy-path** into [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) PR 3. Leave factory/subagents/MCP for later.

## 9. Session record (illustrative)

```json
{
  "id": "20260924-t1",
  "repo": "Code-Harness",
  "profile": "sage",
  "turns": [
    {
      "q": "how does the chunker work?",
      "chunk_ids": ["chunk:harness/chunker.py:CodeChunker"],
      "tokens_packed": 1800,
      "loops": 1,
      "mode": "hybrid"
    }
  ],
  "kept_packs": 3,
  "dropped_packs": 0
}
```

Write as JSONL under `.code-harness/sessions/`. `/context` prints the last pack IDs (already exists as “show last retrieved context”); persist that across process restarts.

## 10. Budget algorithm

```
budget = llm.max_tokens * context_multiplier   # today *2
prefix = system + project_docs + memory_brief  # must fit
remain = budget - tokens(prefix) - tokens(query)

keep = []
for pack in reversed(session.packs):  # newest first
    if tokens(pack) <= remain:
        keep.append(pack); remain -= tokens(pack)
    else:
        keep.append(summarize_heuristic(pack)); remain -= tokens(summary)
        break
older packs dropped
```

`summarize_heuristic`: join `entity_name` + first docstring line + `chunk.id`. No LLM.

## 11. Easy-path detector

Treat as `bm25` mode when **all** hold:

- Query token count ≤ 8 **or** matches `[A-Za-z_][A-Za-z0-9_\.]*` only
- No words in `{explain, why, how, compare, design, tradeoff}`
- Optional: user passed `--no-llm`

Otherwise `hybrid`. Graph-deepen words: `{caller, callee, exposes, unused, dead, inherits}`.

## 12. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Session files grow forever | Cap turns; rotate weekly |
| Summary hides the only good cite | Never summarize the latest pack |
| Users expect Strands tools | Document MCP-later; do not add shell |
| Bedrock default confusion | Never import strands-harness |

## 13. Implementation checklist

- [ ] `retrieval.max_loops` (default 1 extra)
- [ ] Easy-path detector unit tests (identifier vs explain)
- [ ] Budget never drops latest pack
- [ ] Session JSONL optional behind `session.enabled` (default off)
- [ ] Zero new dependencies
- [ ] MCP tools listed but not implemented (PR 8)

## 14. Cross-links

- Loop contract: [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md)
- Sage profile names: [forgecode.md](forgecode.md)
- Memory ≠ session: [memanto.md](memanto.md)
