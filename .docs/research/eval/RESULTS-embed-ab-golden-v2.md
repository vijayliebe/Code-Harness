# MiniLM vs Jina-code on golden-v2

48-query golden-v2, current main knobs: CE off, `symbol_boost=0.5`,
`doc_index` off, source-backed matcher. k=10. `--no-llm`. Generated
2026-10-24.

Suite: `.docs/research/eval/code-harness-v2.fixture.yaml`.
Base: `59e45f3`. Default embedder stays `all-MiniLM-L6-v2`.

Both arms were rebuilt on the same tree this run: 91 files, **4727
chunks**. Isolated Chroma dirs. Jina is local HF
`jinaai/jina-embeddings-v2-base-code` (768-d), not the Jina API.

## Headline

| embedder | Recall@10 | nDCG@10 | cite | tokens | dense p50 | eval | index |
|----------|-----------|---------|------|--------|-----------|------|-------|
| all-MiniLM-L6-v2 | 0.4844 | 0.4408 | 0.8063 | 2033.1 | 23 ms | 12 s | ~2 min |
| jina-embeddings-v2-base-code | 0.5260 | 0.4974 | 0.7479 | 2158.0 | 49 ms | 11 s | 736 s |

Δ Jina − MiniLM: Recall **+0.0416**, nDCG **+0.0566**, cite **−0.0584**,
tokens **+125**.

The post-merge MiniLM persist (4497 chunks, the 0.5122 / 0.4562 /
0.7896 / 1914 row) was an older snapshot. Rebuilding MiniLM on this
tree moved Recall to 0.4844 (hard 0.278 → 0.111). The table above is
the same-corpus A/B.

## Per-category Recall@10

| Category | n | MiniLM | Jina | Δ |
|----------|---|--------|------|---|
| symbol | 7 | 1.000 | 1.000 | 0 |
| nl | 10 | 0.450 | 0.300 | **−0.150** |
| cross-file | 6 | 0.208 | 0.292 | +0.083 |
| cli | 5 | 1.000 | 1.000 | 0 |
| docs | 4 | 0.375 | 0.375 | 0 |
| hard | 9 | 0.111 | 0.278 | +0.167 |
| ticket | 7 | 0.429 | 0.643 | +0.214 |

nDCG@10 by category (MiniLM → Jina): symbol 1.000 / 1.000, nl 0.333 /
0.288, cross-file 0.170 / 0.306, cli 1.000 / 1.000, docs 0.347 /
0.311, hard 0.102 / 0.208, ticket 0.357 / 0.578.

Cite by category: symbol 1.0 / 1.0, nl 1.0 / 0.8, cross-file 0.617 /
0.483, cli 1.0 / 1.0, docs 0.5 / 0.5, hard 0.556 / 0.556, ticket
0.857 / 0.857.

## Gate

Do **not** flip the default embedder.

- Recall is +0.042, outside the ~0.01 noise band.
- **nl drops 0.15**, above the ~0.05 category cap.
- Cite is worse (−0.058). Tokens are higher. Dense p50 is ~2×.

Jina helps ticket / hard / cross-file and holds CLI at 1.0 (real
`cmd_*` / `Config` source, same as MiniLM). It loses the “how does X
work?” row. That is not a clean win.

## Cost and blockers

| | MiniLM | Jina-code |
|--|--------|-----------|
| dims / persist | 384 / `.code-harness/chromadb` (~66 MB) | 768 / `chromadb-jina-embeddings-v2-base-code` (86 MB) |
| index wall | ~2 min CPU | **736 s** CPU |
| first load | already cached | weights + remote BERT code from HF |
| encode window | 256 | **512** (see below) |

Stack here: `sentence-transformers 6.2.0`, `transformers 5.20.2`,
CPU, ~16 GB RAM.

Jina’s remote `jina-bert-v2-qk-post-norm` still talks transformers
4.x. On 5.20.2 it failed until `harness/embedder.py` restored:

- `find_pruneable_heads_and_indices` (removed from `pytorch_utils`)
- `config.is_decoder` / `add_cross_attention`
- `get_extended_attention_mask` / `invert_attention_mask` / `get_head_mask`
- `position_ids` / `token_type_ids` buffers (TF5 left them uninitialized)
- `dtype=float32` (hub default is fp16; CPU)

Uncapped encode then tried to allocate **103 GB**: ALiBi + SDPA at
`max_position_embeddings=8192`, batch 32, fp32. Hub max is 8192; this
run caps Jina at **512** tokens and batch 16. MiniLM is 256. Chunker
`max_chunk_size` is 1500 characters, so 512 covers the corpus. It does
**not** measure Jina’s long-context claim.

No OOM after the cap. No download failure once the weights were
cached.

## Default

**Keep `all-MiniLM-L6-v2`.** Jina stays an opt-in local candidate
(`--embed-model jina-code`). `RESULTS-embed.md` is still the small
8-query placeholder; this file is the golden-v2 record.

## Reproduce

```bash
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml
python3 main.py index . --embed-model jina-embeddings-v2-base-code
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml \
  --embed-model jina-embeddings-v2-base-code
```
