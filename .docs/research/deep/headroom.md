# Deep dive: Headroom (CCR-lite packer)

**First-pass:** [../headroom.md](../headroom.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) PR 2  
**Seam:** `harness/context_builder.py` → `harness/llm.py` (post-MMR, pre-LLM)

## 1. What we confirmed

Headroom is a **local context optimization layer**, not a retriever. Pipeline:

`CacheAligner → ContentRouter → compressor → CCR (Compress-Cache-Retrieve)`

- **ContentRouter** classifies JSON / logs / code / prose / search hits and picks a compressor.
- **CodeCompressor** is AST-aware (tree-sitter signatures kept, bodies collapsed) and **gated**: recent-code protection, analysis-intent protection. Default for raw code/RAG docs is often **passthrough** because silent deletion of bodies is a correctness bug.
- **CCR** stores originals locally (hash-addressed); the model gets a `headroom_retrieve` tool to expand.
- **CacheAligner** marks volatile spans that would bust provider **KV-cache prefixes**. It does not rewrite the system prompt; it tells you what to put last.
- Published directional savings: code search ~21%, codebase exploration ~42%, SRE logs ~57%. JSON/logs can hit 60–95%. Latency claimed sub-ms for typical payloads.

Implication for Code-Harness: steal **reversible packing + cache-friendly layout**, not the full proxy/`headroom wrap` product.

## 2. Code-Harness today

```
Retriever (RRF → CE → MMR)
    → ContextBuilder.build_context()
        → inject ARCHITECTURE.md / AGENTS.md / CLAUDE.md
        → dedup overlapping ranges
        → assemble full chunk.source into one string
    → LLMInterface
```

`Chunk` already has `id`, `docstring`, `source_code` on the parent entity, `file_path`, `start_line`/`end_line`, and `CodeEntity.signature`. `ContextBuilder` still ships **full `chunk.content`** for every survivor. There is no retrieve-back channel. Interactive mode has no compact.

Token cost is therefore linear in `rerank_top_k` × average chunk size (`chunking.max_chunk_size` default 1500 chars).

## 3. CCR-lite (what to build, not Headroom-the-product)

**CCR-lite** = a 80-line packer with Headroom’s safety valve and none of the extra compressors.

Packed record per chunk:

```
### {chunk.id}  {entity_type}  {file_path}:{start_line}-{end_line}
{signature}
{docstring or first sentence}

```{lang}
{first N lines}
… ({omitted_line_count} lines omitted; retrieve_chunk {id})
{last M lines}
```
```

Defaults: `N=12`, `M=8`, omit only if body lines > `N+M+8`. Keep full text in `CCRCache[chunk.id]`.

**Reversibility contract**

| Actor | Behavior |
|-------|----------|
| Packer | Never drops a `chunk.id` that was in the MMR set |
| LLM / user | May call `retrieve_chunk(id)` (CLI flag or future MCP tool) |
| Cache | Process-local; optional spill to `.code-harness/ccr/{id}` for interactive |
| Fallback | `pack_mode=full` restores today’s string exactly |

**Do not** (v1): run Kompress-v2, SmartCrusher, or a sidecar proxy. Those are optional later if eval shows JSON/tool dumps dominating tokens (they do not, on this CLI).

## 4. KV-cache layout (CacheAligner lesson)

Order the assembled prompt as:

1. **Stable prefix** (hash-stable across queries in a session): system role, style rules, `ARCHITECTURE.md` / `AGENTS.md` / `CLAUDE.md` if unchanged.
2. **Semi-stable:** typed memory / wiki summary (when those PRs land).
3. **Volatile suffix:** packed retrieval hits for *this* query.

Today `_assemble_context` interleaves project docs and hits without promising prefix stability. Fixing order is a one-function change and is what makes provider prompt caching real.

## 5. File-level sketch (future implementation)

| File | Change |
|------|--------|
| `harness/config.py` | `context.pack_mode: full \| ccr_lite`, `ccr.first_lines`, `ccr.last_lines`, `ccr.spill_dir` |
| `harness/context_builder.py` | `pack_chunks()`, `CCRCache`, prefix/suffix split in `_assemble_context` |
| `harness/models.py` | optional `PackedChunk` dataclass (`id`, `preview`, `omitted`) |
| `main.py` | `--pack-mode`, `--expand-chunk ID` (prints original and exits or injects) |
| `harness/eval.py` (PR 1) | `prompt_tokens_full` vs `prompt_tokens_packed` |

No change to `retriever.py` ranking. Packer is strictly post-MMR.

## 6. Acceptance

Use the PR 1 suite:

- Recall@k **unchanged** (same IDs enter the packer).
- Estimated prompt tokens **≥30% lower** on this repo’s own questions (directional Headroom “code search ~21%” is a floor; we pack harder than their default passthrough because we keep retrieve-back).
- With one allowed expand, citation-path hit rate within **5 points** of `full`.
- `pack_mode=full` golden-string compatible with current `build_context` (modulo trailing whitespace).

## 7. Risks

- Models that cannot follow “call retrieve_chunk” will answer from signatures only — fine for “where is X defined?”, bad for “explain this algorithm.” Mitigate: expand automatically when query matches `explain|why|how does|walk through` **or** when packed body omits >50% of lines and no LLM tool exists yet (CLI `--expand-on=explain`).
- Cache identity: `chunk.id` must be stable across `watch` reindexes or expands 404. If IDs churn, key by `(repo, file_path, start_line, end_line, content_hash)`.
- Secrets in packed first/last lines: reuse any future redaction hook; do not invent one in this PR.

## 8. Open questions (keep small)

- Default `ccr_lite` vs `full` after the first eval week? Recommend `full` until the suite exists, then flip if token delta is real.
- Interactive `/compact` (Forge) vs packer: compact is *conversation* history; packer is *retrieval* payload. Both can exist; packer first.

## 9. Recommended PR slice

Ship with [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) **PR 2**, after eval metrics. Pair with wiki later: packed wiki summaries + retrieve-back to source is the Code Wiki “chat over wiki, expand to code” pattern ([google-code-wiki.md](google-code-wiki.md)).

## 10. Worked example (this repo)

Query: `how does the chunker work?`

MMR survivors (illustrative): `chunker.py:CodeChunker`, `chunker.py:CodeChunker.chunk`, `models.py:Chunk`, `parser.py:CodeParser`.

**`full` pack** (~4–6k tokens): entire method bodies, including `_chunk_file` loops.

**`ccr_lite` pack** (~1.5–2.5k tokens):

```
### chunk:harness/chunker.py:CodeChunker  class  harness/chunker.py:10-80
CodeChunker
Smart code chunking (entity-type aware).

```python
class CodeChunker:
    def __init__(self, config: Config):
        ...
    def chunk(self, entities):
… (42 lines omitted; retrieve_chunk chunk:harness/chunker.py:CodeChunker)
        return chunks
```
```

If the model needs the overlap algorithm, it expands one id. Eval should count that as one extra tool/CLI fetch, not a retrieval miss.

## 11. Config sketch

```yaml
context:
  pack_mode: full          # flip to ccr_lite after eval week
  prefix_files: [ARCHITECTURE.md, AGENTS.md, CLAUDE.md]
ccr:
  first_lines: 12
  last_lines: 8
  omit_threshold: 8        # only pack if body > first+last+this
  spill_dir: .code-harness/ccr
  expand_on: [explain, why, "how does", "walk through"]
```

## 12. Failure modes

| Symptom | Cause | Mitigation |
|---------|-------|------------|
| Answer cites omitted lines | Model invents body | Expand-on-explain; system: “do not invent omitted lines” |
| `retrieve_chunk` 404 after watch | ID churn | Key by path+range+hash |
| No token win | Chunks already tiny | Suite will show it; keep `full` |
| Prefix cache miss every query | ARCHITECTURE.md rewritten each turn | Only inject if file hash unchanged |
| Packed JSON/tool dumps still huge | Not code | Out of scope (SmartCrusher later) |

## 13. Test plan (docs → future pytest)

- `pack_mode=full` byte-stable vs current `_assemble_context` on a frozen `RetrievalResult` list.
- Tiny chunk (< threshold) is not wrapped with omit markers.
- Large chunk always includes `chunk.id` in the packed header.
- Cache hit after pack returns original `chunk.content` unchanged.
- Prefix order: project docs then packed hits (never the reverse).

## 14. Implementation checklist

- [ ] `context.pack_mode` in `DEFAULT_CONFIG` (`full` default)
- [ ] `pack_chunks(results) -> (text, cache)`
- [ ] Stable prefix hash logged in `--debug`
- [ ] `--expand-chunk` prints original or errors clearly
- [ ] Eval columns `tokens_full` / `tokens_packed`
- [ ] No imports of `headroom-ai` (optional extra later)
- [ ] README one-liner: packer is reversible

## 15. Cross-links

- Loop calls packer every attempt: [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md)
- Wiki chat-over-summary: [google-code-wiki.md](google-code-wiki.md)
- Compact ≠ packer: [forgecode.md](forgecode.md)
