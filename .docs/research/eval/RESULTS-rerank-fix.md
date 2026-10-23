# Rerank-fix results

48-query golden-v2, same MiniLM + Chroma index as the post-merge
main eval (4497 chunks). Two runs per arm, `--no-llm`, k=10.
Spread on every arm was **0** for Recall / nDCG / cite.

Suite: `.docs/research/eval/code-harness-v2.fixture.yaml`.
Generated 2026-10-23. Default stack on incoming main (`46eb3af`):
CE `replace` on `chunk.content[:2048]`.

## What was going wrong

The default CE (`cross-encoder/ms-marco-MiniLM-L-6-v2`) **overwrites**
fused RRF scores with raw logits, then the whole list is sorted again.
Three things pile up:

1. **Content-only input.** Scored text is `chunk.content[:2048]`. Path
   never appears. The symbol is only there if the def/class line
   survived the cut. CLI / ticket questions that name `main.py` or a
   function do not get that string into the pair.
2. **NL model on code.** ms-marco prefers README prose and test names
   that repeat the query words over the actual implementation.
3. **Score-scale mix.** RRF scores sit around 0.003–0.009. CE logits
   are often negative. After `fused = reranked + tail` and a full
   re-sort, a gold chunk with CE = −3 falls **behind the unreranked
   tail**, not just down a few slots in the window of 30
   (`rerank_top_k * 2`).

`rerank_drop` on eval is a weak label: any gold that appears anywhere
in the fused list and misses top-10 is tagged. The real drops are the
ones that were already in fused top-10.

## Concrete drops

| Query | Fused rank | After CE | What CE scored high instead |
|-------|------------|----------|-----------------------------|
| `q-ticket-eval-uuid` — ABC-106, gold `match_gold_id` | **1** | out of top-10 | Other metrics / eval prose |
| `q-ticket-packer-omit` — ABC-102, gold `pack_chunk` | **2** | out of top-10 | Longer CCR / README text |
| `q-who-calls-index-chunks` | 14 (in window) | CE content **−2.85** (rank 17/30) | `tests/test_query_loop.py` who-calls tests at **+6.3** |
| `q-what-exposes-query` | in window | CE content **−9.96** (rank 29/30) | `test_argparse_add_parser_exposes_cli_commands` at **+5.9** |
| `q-nl-query-cache` | in window | CE content **−9.57** (rank 30/30) | `query_cache.py` file chunk at **+5.5**, not `cache_key` |

Prefixing `path + symbol + text` moved who-calls from −2.85 to +2.08
and still lost to the same tests. Truncation (2048) hit a few class
bodies (`QueryLoop`, `Embedder`); it was not the main failure.

### CLI `cmd_*` (all 5 miss)

`q-cli-query` / `eval` / `index` / `serve` gold `func:main.py:cmd_*`.
Those chunks sit at fused ranks **40–139**. They never enter the CE
window. `q-cli-query` and `q-config-defaults` are dense+BM25+graph
misses. This is not a reranker bug; turning CE off does not find
`cmd_query`. Cite on the CLI row goes from 0.0 (replace) to 0.6
(off) because `main.py` file chunks survive, not because the
function id is retrieved.

## Arms (mean of 2 identical runs)

Current main = `replace`. Δ is vs that row. Category numbers are
Recall@10.

| Arm | Recall@10 | nDCG@10 | cite | tokens | symbol | nl | cross-file | ticket | cli |
|-----|-----------|---------|------|--------|--------|----|------------|--------|-----|
| replace (old default) | 0.3281 | 0.3020 | 0.6500 | 1800.2 | 1.000 | 0.350 | 0.125 | 0.214 | 0.000 |
| blend α=0.2 | 0.4080 | 0.3466 | 0.7271 | 1734.5 | 1.000 | **0.500** | 0.181 | 0.357 | 0.000 |
| blend α=0.3 | 0.3663 | 0.3191 | 0.7062 | 1727.1 | 1.000 | 0.400 | 0.181 | 0.357 | 0.000 |
| blend α=0.5 | 0.3872 | 0.3407 | 0.6854 | 1749.6 | 1.000 | 0.400 | 0.181 | **0.500** | 0.000 |
| blend α=0.7 | 0.3802 | 0.3417 | 0.6708 | 1703.0 | 1.000 | 0.400 | 0.125 | 0.500 | 0.000 |
| gate margin=1.0 | 0.3976 | 0.3566 | 0.7271 | 1777.8 | 1.000 | 0.400 | 0.264 | 0.357 | 0.000 |
| gate margin=2.0 | 0.4080 | 0.3624 | 0.7479 | 1732.7 | 1.000 | 0.450 | 0.264 | 0.357 | 0.000 |
| **off** | **0.4080** | **0.3624** | **0.7479** | **1708.4** | 1.000 | 0.450 | **0.264** | 0.357 | 0.000 |
| replace + path/symbol/text | 0.3594 | 0.3212 | 0.6500 | 1913.1 | 1.000 | 0.350 | 0.125 | 0.286 | 0.000 |

Noise band on this stack is ~0.01. Every arm except path-replace
beats replace by more than that. No listed category drops vs replace
by more than 0.05 (cli stays 0; path-replace is flat on nl / cross-file).

## Default

**Shipped default is now CE off** (`cross_encoder.enabled: false`,
`mode: off`).

Why this one and not blend-0.2 (same headline recall):

- Same Recall@10 as the best arms (0.4080, **+0.080** vs replace).
- Best-tied nDCG and cite. Lowest tokens of the 0.408 group.
- Best-tied cross-file (0.264). blend-0.2 trades that away for a
  higher NL row (0.500 vs 0.450).
- gate-2.0 matches off on ranking because almost no query has a CE
  top-1−top-2 margin ≥ 2. It still loads the model (~800 ms/query).
- No extra failure mode: we are not mixing two score scales or
  trusting a passage model on code.

Cost: NL citation-path falls 1.00 → 0.90 (file still cited; one less
packed path). docs nDCG also dips a bit (0.403 → 0.347) with recall
unchanged. CLI function ids stay at 0.

Old overwrite: `--ce-mode replace` or
`retrieval.cross_encoder.enabled=true` + `mode=replace`.

## Knobs

| Config / CLI | Meaning |
|--------------|---------|
| `mode=replace` / `--ce-mode replace` | Old behaviour. CE logits replace fused scores. |
| `mode=blend` + `blend_alpha` / `--ce-blend-alpha` | `α * norm(CE) + (1-α) * norm(RRF)` on the CE window. |
| `mode=gate` + `gate_margin` / `--ce-gate-margin` | Keep fused order unless CE top1−top2 ≥ margin. |
| `mode=off` / `--ce-mode off` | Skip CE. |
| `input=path_symbol_text` / `--ce-input path_symbol_text` | Score `path symbol\\ncontent`. |

`enabled=false` is the same ranking as `mode=off` and skips the
model load.

## Failure modes that remain

- CLI `cmd_*` and `Config` defaults are not in the fused candidate
  pool. Fix that in retrieve (BM25 / symbol channel), not here.
- Blend still pays CE latency and can bury a fused-top hit when α
  is high (α=0.7 cross-file is back to 0.125).
- Gate at 1.0 still lets some confident-wrong CE reorder through
  (margin on the who-calls tests was only ~0.3; on query-cache ~1.3).
- path+symbol+text does not fix the README/test preference and
  raised tokens.

## Reproduce

```bash
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --ce-mode replace
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --ce-mode blend --ce-blend-alpha 0.2
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --ce-mode gate --ce-gate-margin 2.0
```
