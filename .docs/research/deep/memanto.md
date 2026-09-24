# Deep dive: Memanto (typed project memory)

**First-pass:** [../memanto.md](../memanto.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) later #5 (after PRs 1–4)  
**Seam:** new store beside Chroma/BM25/KG — do not write decisions into chunk embeddings

## 1. What we confirmed

[moorcheh-ai/memanto](https://github.com/moorcheh-ai/memanto) is a **Memory Agent**: observe → consolidate → reconcile (supersede, not silent overwrite) → forget → brief (`agent bootstrap`) → move (**OKF** export).

Related: **Mem0** is a memory *layer* (SDK/API) with hybrid semantic+BM25+entity linking. Do not conflate the two.

Memanto types (subset we care about): `decision`, `preference`, `fact`, `error`, plus temporal `--as-of` / `--changed-since`.

**OKF note:** first-pass cards implied Memanto *owns* OKF. Correction: OKF is a **Google Cloud SPEC**; Memanto implements export/import (`x_memanto` extensions). See [okf.md](okf.md).

## 2. Why this is not the code index

Code-Harness chunks are **what the code is**. Memories are **what the team decided** and **what failed**. If you embed “we tried FAISS and it OOM’d” as a documentation chunk, it fights `vector_store.py` on every “what index do we use?” query and goes stale the same way `ARCHITECTURE.md` does.

Injection today (`CONTEXT_FILE_NAMES`) is a blunt instrument: whole-file prefix, no types, no supersession, no `as-of`.

## 3. Minimal native memory (do not vendor Memanto)

v1 schema (one markdown file per concept, OKF-compatible):

```yaml
---
type: decision
title: Default vector backend stays Chroma
id: mem/2026-09-24-chroma-default
verified: human
generated: false
supersedes: null
timestamp: 2026-09-24T00:00:00Z
tags: [vector, chroma]
---
Keep Chroma until TurboVec recall@k passes the eval suite.
```

Store under `.code-harness/memory/` (local) and optionally a committed `knowledge/` tree for PR-reviewable facts.

**Reconcile:** a new `decision` with `supersedes: <id>` keeps the old file (`status: superseded`). Retrieval prefers non-superseded.

**Brief:** interactive start injects top-K memories (BM25 over titles/bodies + recency) *before* packed code chunks. Token budget: ~400–800 tokens, not the whole vault.

**Forget:** `ttl` or `status: forgotten` — hide from brief, keep file for `as-of`.

## 4. Conflict with ARCHITECTURE.md

| Source | Role |
|--------|------|
| `ARCHITECTURE.md` / `.docs/architecture.md` | Human-maintained system picture (also a future wiki page) |
| Memory `decision` | Dated, supersedable, small |
| Code chunks | Ground truth of implementation |

When they disagree, **code chunks win for “what does the code do?”**; **latest non-superseded decision wins for “what should we do?”**. The loop’s verify step (PR 3) should cite which it used.

## 5. File-level sketch (future, PR 5)

| File | Change |
|------|--------|
| `harness/memory.py` (new) | load/save/supersede/brief |
| `harness/context_builder.py` | inject brief between project docs and packed hits |
| `main.py` | `memory add|list|brief` |
| OKF import/export | [okf.md](okf.md) — same files, SPEC-compliant frontmatter |

Search: reuse BM25 over memory files (already a project skill). Do not stand up a second Chroma collection until volume hurts.

## 6. What not to copy yet

- 13-type taxonomy in full — start with `decision`, `error`, `preference`, `fact`.
- Cloud Memanto / `memanto connect` as a required dependency.
- Automatic extraction from every query session (`headroom learn` / Memanto observe) — schedule offline; extraction LLMs cost more than they save until the loop exists.

## 7. Acceptance

- Brief ≤ N tokens and contains only non-superseded items.
- Supersession visible in `memory list --as-of`.
- Eval “why did we choose X?” questions improve **without** Recall@k on code chunks dropping.
- Round-trip: native file → OKF bundle → native file preserves `id` and `supersedes`.

## 8. Risks

- Memory store becomes a junk drawer. Mitigate: require `type` + `title`; reject empty bodies.
- Secrets in decisions. Same rule as OKF: no credentials in committed bundles.

## 9. Recommended PR slice

**After** eval, packer, loop, and KG (those change answers more). Memory without a loop just adds another prefix. Pair with [okf.md](okf.md) in the same PR if the SPEC subset is small.

## 10. Brief ranking

Score = `0.5 * bm25(title+body, query)` + `0.3 * recency` + `0.2 * type_prior`

`type_prior`: `decision=1.0`, `error=0.9`, `preference=0.6`, `fact=0.5`. Superseded → 0 (hidden unless `--as-of`).

Cap: 5 items or 800 tokens, whichever first.

## 11. CLI sketch (future)

```
python main.py memory add --type decision --title "..." --body-file -
python main.py memory list [--type decision] [--as-of 2026-01-01]
python main.py memory brief -q "why chroma?"
python main.py memory supersede <old-id> --title "..." 
```

`memory add` writes an OKF concept file; no extra store.

## 12. Conflict example

1. `decisions/2026-01-chroma.md` — “use Chroma”
2. `decisions/2026-10-turbovec.md` — `supersedes: knowledge/decisions/2026-01-chroma.md` — “optional TurboVec after recall gate”

Brief for “what vector store?” returns (2) and a one-liner that (1) is superseded. Code chunks still show `vector_store.py` implementing Chroma — verify step must not call that a contradiction.

## 13. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Duplicate decisions | Require `--supersede` or warn on similar titles (token overlap) |
| Chat dumped into memory | No automatic extract in v1 |
| Brief crowds out code | Hard token cap; packer puts memory in semi-stable prefix |
| Empty `type` | Refuse write (SPEC requires type) |

## 14. Implementation checklist

- [ ] Four types only: decision, error, preference, fact
- [ ] `supersedes` + `status: superseded`
- [ ] Brief token cap enforced
- [ ] Files are valid OKF concepts (see [okf.md](okf.md) §11)
- [ ] Not written into Chroma
- [ ] No Memanto/Mem0 runtime dependency

## 15. Cross-links

- Format owner: [okf.md](okf.md)
- Session vs memory: [strands-harness.md](strands-harness.md)
- Gloss vs decision: [code-graph.md](code-graph.md) (gloss is entity-attached; decision is project-level)
