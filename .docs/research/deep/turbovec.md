# Deep dive: TurboVec (optional vector backend)

**First-pass:** [../turbovec.md](../turbovec.md)  
**Plan slot:** [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) later #7 — **blocked on PR 1 recall@k**  
**Seam:** `harness/vector_store.py`, `harness/config.py` `vector_store.type`

## 1. What we confirmed

TurboVec (Python bindings over a Rust index) implements Google Research **TurboQuant** (arxiv 2504.19874): data-oblivious online quantization, no train step, 2- or 4-bit Lloyd-Max after random rotation, length-renormalized inner product. Incremental `add`/`remove`, `sync()` durable saves. **Allowlist filtering inside the SIMD kernel** — BM25/SQL candidate IDs → dense search without over-fetch.

Claims: 10M×1536-d FP32 ~31GB → ~4GB at 2-bit; search often faster than FAISS PQ FastScan in their benches.

Code-Harness default: ChromaDB HNSW (`hnsw_ef_search=256`, `m=32`, cosine). Fine for single repos; RAM and disk grow with multi-repo + large `all-MiniLM` or Voyage/Jina dims.

## 2. Why this is not PR 1–4

Quantization **changes recall**. Headroom packer and the query loop do not. If we swap the backend before a golden suite exists, we cannot tell “TurboVec lost the chunk” from “the loop failed to re-retrieve.”

[../SYNTHESIS.md](../SYNTHESIS.md) already said: keep Chroma until recall benches pass on **code** embeddings. This note only designs the switch.

## 3. Backend interface (when we do it)

`VectorStore` today is Chroma-shaped. Introduce a thin protocol:

```
add(ids, embeddings, documents, metadatas)
query(embedding, n, where=None, allowlist_ids=None) -> ids, distances
delete(ids)
persist() / sync()
```

Implementations: `ChromaVectorStore` (current), `TurboVecStore` (experimental).

Config:

```yaml
vector_store:
  type: chromadb   # or turbovec
  turbovec:
    bits: 4
    persist_directory: .code-harness/turbovec
```

**Sidecar:** TurboVec is index-only. Keep chunk text/metadata in the existing pickle/JSON or a sqlite sidecar (Chroma currently holds documents). Do not drop `chunk.content`.

## 4. Hybrid that matches our architecture

We already run dense + BM25 + graph then RRF. TurboVec’s allowlist shines as:

```
BM25 top N (e.g. 200) ∪ graph seed IDs
    → TurboVec.query(emb, n=top_k, allowlist=those IDs)
    → RRF with BM25/graph scores as today
```

That *reduces* ANN error (search in a lexical neighborhood) and is closer to “filtered dense” than “replace hybrid with quantized ANN.”

If allowlist is empty (purely semantic question), fall back to unfiltered TurboVec or Chroma.

## 5. Incremental watch

`watch` today re-embeds changed files into Chroma. Map to `add`/`remove` + `sync()`. Measure whether `sync()` is cheaper than Chroma upsert on this repo before advertising it.

## 6. Eval gates (must pass before default)

On the PR 1 suite + at least one larger fixture (when available):

| Metric | Gate vs Chroma |
|--------|----------------|
| Recall@10 | ≥ −2 points |
| Recall@30 | ≥ −1 point |
| p50 query (dense stage) | ≤ 1.0× (equal or faster) |
| RSS of index | documented; hope ≤ 0.5× at 4-bit |

Fail any recall gate → stay experimental (`type: turbovec` opt-in only).

## 7. Risks

- Voyage/Jina/OpenAI dims vs MiniLM 384 — requantize per model; store `embedding.model` next to the index; refuse to query on mismatch.
- Metadata filters (`repo_name`, path prefix) must be reimplemented (allowlist or sidecar scan).
- License/ops: extra Rust wheel in `requirements.txt` optional extra (`turbovec`). Keep core install small.

## 8. Recommended PR slice

Do **not** start in this research wave. After PR 1 has numbers, a spike PR can add `type: turbovec` behind a flag and print recall. Default remains Chroma.

## 9. Dual-write spike (safest experiment)

1. Keep writing Chroma as today.
2. Also `add()` embeddings to TurboVec in a side directory.
3. Eval runs **both** query paths and diffs Recall@k / latency / RSS.
4. No user-facing `type: turbovec` until gates pass.

This doubles write time during the spike; acceptable on a fixture repo.

## 10. Allowlist sizes

| Stage | N |
|-------|---|
| BM25 candidate pool | 200 |
| Graph seed IDs | ≤ 50 |
| Union allowlist | ≤ 250 |
| Dense return | `top_k` (30) |

If union < 20, skip allowlist (too tight; semantic questions starve).

## 11. Failure modes

| Symptom | Mitigation |
|---------|------------|
| Recall cliff at 2-bit | Default 4-bit; 2-bit only if RSS requires |
| Model swap without rebuild | Store embedding model name; refuse query |
| `watch` delete misses | IdMap must use our `chunk.id` |
| Users set turbovec as default in config copied from a blog | README + `doctor` warning until gates pass |

## 12. Dependency stance

`requirements.txt` stays Chroma-only. Extra: `requirements-turbovec.txt` or extras_require. Aligns with “core install small” and with deprioritizing a backend swap before eval.

## 13. Implementation checklist (when unblocked)

- [x] PR 1 suite exists and is green on Chroma
- [x] Protocol extracted from `VectorStore`
- [x] Fixture-suite A/B table (`eval-ab` → `.docs/research/eval/RESULTS.md`; dual-write not)
- [x] Embedding model name persisted
- [x] Allowlist union/min-size rules (BM25 pool ≥20; graph seeds still open)
- [x] Default remains `chromadb`
- [x] Optional extra dependency only

## 14. Cross-links

- Eval gates: [prompt-loop-graph-engineering.md](prompt-loop-graph-engineering.md)
- Why not before packer/loop: [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md)
- Hybrid already in `retriever.py` — do not replace RRF, only the dense stage
