# AirLLM

## What it is
Inference/training library that runs huge LLMs on tiny VRAM by **streaming one layer at a time** from disk to GPU (plus optional block-wise weight compression). Apache-2.0.

## Links
- https://github.com/lyogavin/airllm
- PyPI: `airllm`

## How it works
- Split HF checkpoint into layer shards; load layer N, compute, free, load N+1.
- Prefetch overlap; 4/8-bit weight compression for ~3× speedups on load-bound path.
- MoE: stream only routed experts (e.g. DeepSeek-V3 ~12GB, Kimi K3 claims <4GB).
- Recent: LoRA training with frozen base streamed layerwise.

## Relevance to Code-Harness
**Low–Medium.** Helps **local LLM answering** on small GPUs, not retrieval quality. Relevant if Code-Harness Ollama/local path wants larger models.

## Concrete ideas to adopt
1. **Optional AirLLM local provider** (cost): Run 70B-class models locally for `query` without API spend (slow but free).
2. **Document tradeoff** (perf): Layer streaming ⇒ high latency; use for offline batch, not interactive RAG.
3. **Prefer retrieval quality over huge local models** (tokens/accuracy): Better chunks + small model often beats huge model + bad context.

## Risks/caveats
- Disk space for shards; slow generation; not production QPS.
- Orthogonal to Chroma/BM25/graph work.

## Open questions
- Worth integrating vs telling users to use Ollama/llama.cpp?
