# TurboVec (turbovec)

## What it is
Rust vector index with Python bindings implementing Google Research **TurboQuant** (arxiv 2504.19874): data-oblivious online quantization, no train step, high compression, SIMD search often faster than FAISS PQ FastScan.

## Links
- https://github.com/RyanCodrai/turbovec
- Paper: https://arxiv.org/abs/2504.19874
- PyPI: `turbovec`

## How it works
1. Normalize vector; store norm.
2. Random orthogonal rotation → coordinates ~ known Beta/Gaussian.
3. Optional TQ+ per-coordinate calibration.
4. Lloyd-Max scalar quantization (2- or 4-bit).
5. Bit-pack (e.g. 1536-d FP32 6144B → 384B at 2-bit).
6. Length-renormalized scoring for unbiased IP.
- Online `add` / O(1) `remove` with IdMapIndex; `sync()` incremental durable saves.
- **Allowlist filtering inside SIMD kernel** — hybrid: BM25/SQL candidates → dense rerank without over-fetch.
- Claim: 10M docs ~31GB FP32 → ~4GB quantized; search faster than FAISS in measured configs.

## Relevance to Code-Harness
**High (perf).** Code-Harness uses ChromaDB HNSW. TurboVec is a strong local alternative/complement for large multi-repo indexes and RAM-constrained machines.

## Concrete ideas to adopt
1. **Optional TurboVec backend** (perf): Behind `vector_store.provider = turbovec` with 4-bit default.
2. **Two-stage hybrid** (perf/accuracy): BM25 top-N allowlist → TurboVec dense search within allowlist (kernel filter). Why: faster + naturally hybrid.
3. **Watch/incremental sync** (perf): Pair `watch` command with `index.sync()` instead of full Chroma rebuild.
4. **Memory footprint reporting** in `info` (ops).

## Risks/caveats
- ANN recall ≠ exact HNSW; evaluate on code chunk corpus.
- Still need sidecar for original text/metadata (TurboVec is index-only).
- Chroma ecosystem features (metadata filters) must be reimplemented.

## Open questions
- Recall@k on voyage-code-2 / code embeddings specifically?
