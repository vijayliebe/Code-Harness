# Harness-on vs harness-off ablation

Leave-one-out of the default retrieve+pack stack on the committed
golden suite. Inspired by the with-vs-without harness contrast in
[Harness-Aware Distillation for Small Language Model Agents]
(https://arxiv.org/abs/2610.02858). This table is an honesty record,
not a default flip.

| Field | Value |
|-------|-------|
| Suite | `code-harness` |
| Suite path | `.docs/research/eval/code-harness.fixture.yaml` |
| k | 10 |
| n | 8 |
| Generated | 2026-10-17T12:57:33Z |
| Commit | `bccb0d492dc581a9d60469b9ee5764199a93b091` |
| Machine | python 3.12.3; Linux-6.12.94+-x86_64-with-glibc2.39; chromadb 1.5.9; sentence-transformers 6.1.0; 4 CPUs |
| Index | 4289 Chroma chunks, local `all-MiniLM-L6-v2`, CE `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Notes | Live one-shot retrieve+pack on this checkout. Defaults unchanged. Command: `python3 main.py eval-ab . --ablate --skip-index` after `python3 main.py index .`. |

## Metrics

| variant | Recall@10 | nDCG@10 | citation-path | dense ms | bm25 ms | graph ms | ce ms | mmr ms | tokens full | tokens packed | failures |
|---------|------------|----------|---------------|----------|---------|----------|-------|--------|-------------|---------------|----------|
| full | 0.3125 | 0.2629 | 0.6250 | 541.3 | 6.7 | 1.3 | 983.8 | 0.1 | 1713.0 | 1269.1 | bm25_miss=5, dense_miss=1, graph_miss=7, rerank_drop=4 |
| no_dense | 0.1875 | 0.1916 | 0.3750 | 0.0 | 6.8 | 1.0 | 755.7 | 0.1 | 1428.0 | 1176.8 | bm25_miss=5, dense_miss=8, graph_miss=4, rerank_drop=5 |
| no_bm25 | 0.5000 | 0.4152 | 0.7500 | 25.8 | 0.0 | 1.2 | 660.7 | 0.2 | 2121.8 | 1579.4 | bm25_miss=8, dense_miss=1, graph_miss=8, rerank_drop=2 |
| no_graph | 0.3125 | 0.2629 | 0.6250 | 26.1 | 6.7 | 0.0 | 757.4 | 0.1 | 1809.6 | 1340.4 | bm25_miss=5, dense_miss=1, graph_miss=8, rerank_drop=4 |
| no_rerank | 0.4375 | 0.3769 | 0.7500 | 21.5 | 5.5 | 1.2 | 0.0 | 0.2 | 1901.2 | 1480.0 | bm25_miss=5, dense_miss=1, graph_miss=7, packer_drop=1 |
| no_mmr | 0.3125 | 0.2629 | 0.6250 | 25.3 | 6.8 | 1.4 | 763.7 | 0.0 | 1713.0 | 1269.1 | bm25_miss=5, dense_miss=1, graph_miss=7, rerank_drop=4 |
| hyde | 0.3750 | 0.2851 | 0.6250 | 27.0 | 6.9 | 1.4 | 769.4 | 0.1 | 1701.9 | 1268.9 | bm25_miss=5, dense_miss=3, graph_miss=6, rerank_drop=3 |
| ccr_lite | 0.3125 | 0.2629 | 0.6250 | 23.1 | 6.9 | 1.5 | 766.2 | 0.1 | 1713.0 | 1269.1 | bm25_miss=5, dense_miss=1, graph_miss=7, rerank_drop=4 |
| no_retrieval | 0.0000 | 0.0000 | 0.0000 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 5.0 | 5.0 | bm25_miss=8, dense_miss=8, graph_miss=8 |

## Variant notes

- `full` (baseline): Default retrieve+pack: dense + BM25 + graph expand + CE + MMR; HyDE off; pack_mode=full.
- `no_dense` (leave_one_out): Skip dense search; dense_weight=0.
- `no_bm25` (leave_one_out): Skip BM25 search; sparse_weight=0.
- `no_graph` (leave_one_out): Skip neighbor expansion; expand_neighbors=0; graph_weight=0.
- `no_rerank` (leave_one_out): retrieval.cross_encoder.enabled=false.
- `no_mmr` (leave_one_out): Skip diversity rerank after lexical boost.
- `hyde` (optional_on): Default HyDE is off; this row enables the local template (no LLM).
- `ccr_lite` (optional_on): Default pack_mode is full; this row packs signatures + key spans.
- `no_retrieval` (baseline_off): Empty hit set. Retrieval recall/nDCG/citation-path are zero by construction.

## Answer-level citation (full vs no-retrieval)

No chat-LLM key in the environment. Answer-level comparison was not run.

## How to run (local)

```bash
pip install -r requirements.txt
python main.py index .
python main.py eval-ab . --ablate
# or
make eval-ablate
```

Eval-only. Does not change MiniLM, Chroma, RRF weights, `max_loops=0`,
`decision.enabled=false`, or verify defaults. `make test` stays offline.

## Caveats

- Fixture n is small (8 queries). Treat deltas as directional, not a default flip.
- One run, no bootstrap. The `full` row paid cold MiniLM/CE load (dense 541ms, ce 984ms); later rows are warm (~25ms dense, ~760ms ce).
- `no_bm25` and `no_rerank` scored *above* `full` on this n=8 snapshot. That is a real measurement, not a reason to change defaults.
- `no_retrieval` recall/nDCG/citation-path are zero by construction.
- `hyde` and `ccr_lite` are optional-on rows: those stages are off in `full`.
- No chat-LLM key (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` unset). Answer-level citation was not run.
