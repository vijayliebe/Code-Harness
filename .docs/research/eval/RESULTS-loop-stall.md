# Corrective-loop stall metrics

Live `--loop` pass on the golden suite. The 9-way harness ablation in
[RESULTS-ablation.md](RESULTS-ablation.md) was **not** re-run with `--loop`
(cross-encoder cost on this VM; one extra retrieve per hard case × 9 variants).
This file is the practical substitute: one `--loop` (`max_loops=1`,
`stop_on_stall=true`, `stall_overlap=0.8`) run of
`.docs/research/eval/code-harness.fixture.yaml` on the existing index.

A **stall** is a retry whose top-k hit-set Jaccard overlap vs the previous
attempt is ≥ 0.8. **Escape** is a stall followed by a changed, better-graded
attempt. With `stop_on_stall` on, a stall ends the loop, so escape stays 0
unless `--no-stop-on-stall` is set.

## Run

| Field | Value |
|-------|-------|
| Suite | `code-harness` |
| Suite path | `.docs/research/eval/code-harness.fixture.yaml` |
| k | 10 |
| n | 8 |
| Generated | 2026-10-17T13:06:37Z |
| Commit | `87c5ec6a62037a0cdbc7c533d588098e2cd12393` (implementation that produced these numbers) |
| Command | `python3 main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml --loop --output .docs/research/eval/RESULTS-loop-stall.json` |
| Machine | python 3.12.3; Linux-6.12.94+-x86_64-with-glibc2.39; chromadb 1.5.9; sentence-transformers 6.1.0; 4 CPUs |
| Index | existing `.code-harness/chromadb` (4289 chunks); MiniLM + CE already warm from the earlier ablation |
| Chat LLM | none. Answer-level comparison was not run. |

## Summary

| Recall@10 | nDCG@10 | citation-path | loop attempts mean | stalls | stall rate | escape rate |
|-----------|---------|---------------|--------------------|--------|------------|-------------|
| 0.4375 | 0.3879 | 0.7500 | 1.5 | 1 | 0.1250 | 0.0000 |

Failures: `bm25_miss=2`, `dense_miss=2`, `graph_miss=8`, `rerank_drop=3`.

## Per fixture

| id | attempts | stall | stall rate | stop | Recall@10 | citation-path |
|----|----------|-------|------------|------|-----------|---------------|
| q-context-builder | 1 | 0 | 0.0000 | grade | 0.5000 | 1.0000 |
| q-who-calls-index-chunks | 2 | 0 | 0.0000 | grade | 1.0000 | 1.0000 |
| q-parser-discover | 1 | 0 | 0.0000 | citation | 0.5000 | 1.0000 |
| q-chunker | 1 | 0 | 0.0000 | citation | 0.5000 | 1.0000 |
| q-cli-query | 2 | 0 | 0.0000 | budget | 0.0000 | 0.0000 |
| q-rrf-fusion | 2 | 0 | 0.0000 | citation | 0.0000 | 1.0000 |
| q-who-calls-build-context | 1 | 0 | 0.0000 | grade | 1.0000 | 1.0000 |
| q-what-exposes-query | 2 | 1 | 1.0000 | stall | 0.0000 | 0.0000 |

## Caveats

- Fixture size is 8 queries. One run. Do not treat 0.4375 / 0.125 as a stable loop win or a stable stall rate.
- Compared with the one-shot `full` row in RESULTS-ablation.md (Recall@10 0.3125, cite 0.6250), this `--loop` pass scored higher. That is a real measurement on this snapshot, not a default flip. `max_loops` remains 0 in the default query path.
- The single stall was `q-what-exposes-query`. Early-stop fired (`stop=stall`), so this run could not observe an escape.
- Latency is not comparable to the ablation `full` row: the MiniLM/CE weights were already loaded, and four of eight queries took a second retrieve.
- To add stall columns to the 9-way table: `python3 main.py eval-ab . --ablate --loop --skip-index`.
