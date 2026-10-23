# Retrieval quick wins (R1–R5) — golden A/B

Scored on this repo with the committed suite `.docs/research/eval/code-harness.fixture.yaml`.

| Field | Value |
|-------|--------|
| Base SHA | `788196391bcdaee11b04a1e238e27275d7c940ac` (`origin/main`) |
| Embedder | `all-MiniLM-L6-v2` (local) |
| Vector | Chroma (default) |
| Decision | off |
| max_loops | 0 |
| verify | off |
| n | 8 |
| k | 10 |

Each on/off arm is one flag at a time against the same unenriched index, except R1 (needs a re-index). `eval-ab` was run after the default flip; TurboVec was skipped (extra not installed).

## Table

| Arm | Recall@10 | nDCG@10 | citation-path hit | packed tokens (full / CCR-lite mean) |
|-----|-----------|---------|-------------------|--------------------------------------|
| Baseline (all off) | 0.25 | 0.2246 | 0.5 | 1529.2 / 1191.4 |
| R1 enrich-index-text on | 0.25 | 0.2246 | 0.625 | 2304.1 / 1284.4 |
| R2 per-file cap 3 | 0.25 | 0.2246 | 0.5 | 1517.4 / 1176.1 |
| R3 multi-query | 0.3125 | 0.2477 | 0.5 | 1505.2 / 1196.0 |
| R5 expansion | 0.4375 | 0.4263 | 0.875 | 1754.5 / 938.1 |
| Shipped (R2+R3+R5 on, R1 off) | 0.5 | 0.4646 | 1.0 | 1272.1 / 681.6 |

R4 is MCP hygiene (unknown-filter rejection, `{error, results:[]}`, `list_filters`, `skip_query_expansion`). It does not change retrieval metrics.

## Defaults after this A/B

- **R1 off.** Recall and nDCG did not move. Cite rose, but full packed tokens jumped (one case packed 7537). Needs a re-index; not a free win.
- **R2 on** (`context.per_file_chunk_cap: 3`). Ranking unchanged; tokens slightly down.
- **R3 on** (`retrieval.multi_query.enabled: true`). Recall and nDCG up; cite flat; tokens flat-to-down. Extra dense/BM25 calls (sequential today).
- **R4 always on** for the MCP/HTTP retrieve surface (validation + envelope). Not a ranking default.
- **R5 on** (`retrieval.expansion.enabled: true`). Recall, nDCG, and cite up. Tokens up on the isolated arm, down when combined with the per-file cap. Heuristic only unless `decision.enabled` is true.

## Caveats

- n=8 is a small, this-repo suite. Do not treat these as a general retrieval leaderboard.
- Chunk count drifted 4366 → 4372 between the R1 re-index and the shipped re-index because this branch added files. Isolate arms still used the index built for that arm.
- R1-on vectors are a different embedding space than the unenriched index.
- Multi-query latency is extra embed + BM25 work (not parallelised).
- Expansion can pull neighbouring chunks that help cite/recall and can also raise `packer_drop` (one shipped case).
- `make eval-ab` / TurboVec gate was not measured (turbovec extra missing). Chroma-only `eval-ab` reproduced the shipped Chroma numbers.
