# Mini-deepen: AirLLM

**First-pass:** [../../airllm.md](../../airllm.md)  
**Live skim (2026-09-25):** [lyogavin/airllm](https://github.com/lyogavin/airllm) README (v3-era: DeepSeek-V3 ~12GB, Kimi K3 <4GB, layer-stream train)  
**Why this note:** confirm it is still **inference**, not retrieval.

## Confirmed mechanism

Stream **one decoder layer** (and routed MoE experts) from disk → GPU, optional 4/8-bit *weight* compression for load-bound speed. Disk-hungry shard step. Generation remains slow vs in-VRAM or Ollama.

## Fusion call

**reject** as a Code-Harness module. Local answering is already `llm.provider: ollama`. Layer-streaming a 70B+ model on `query` would **destroy p50** and not move Recall@k.  
Document in thesis non-goals: “huge local models via AirLLM.” Users who want that wrap Ollama/llama.cpp themselves. Evidence: first-pass + live README.
