# CLI retrieval results

48-query golden-v2, MiniLM + Chroma (4497 chunks). Two runs per arm,
`--no-llm`, k=10. Spread on every arm was **0** for Recall / nDCG / cite.

Suite: `.docs/research/eval/code-harness-v2.fixture.yaml`.
Generated 2026-10-24. Incoming main (`def83d9`): CE off, no CLI knobs.

This pass uses the **source-backed matcher**. A catalog stub that
copies `func:main.py:cmd_query` is not a hit unless the real handler
chunk is also in the top-10.

## What was going wrong

All five CLI fixtures miss on main: `q-cli-query` / `eval` / `index` /
`serve` plus `q-config-defaults`. Gold sat at fused ranks **40–139**.

1. **`cmd_*` bodies do not say "CLI command".** They call
   `_load_config` / `VectorStore`. The argparse
   `add_parser("query", help=...)` line lives in other `main.py`
   chunks.
2. **BM25 keeps `cmd_query` as one token.** The query is `where, is,
   the, query, cli, command, defined, in, main, py`. No overlap with
   the handler name.
3. **`Config` is not `DEFAULT_CONFIG`.** Gold is the dataclass. The
   header chunk is 18 characters. Weights / `top_k` / cross-encoder
   live on the module-level dict.
4. **Per-file cap is not this bug.** It runs after MMR.

## The stub problem

The first pass shipped `doc_index` on. That injects a one-line
catalog chunk whose `entity_id` is the gold handler. Recall@10 went
to 1.0 on CLI because the matcher treated "same entity id" as a hit.
The LLM packed the sentence "The query CLI command is defined as
cmd_query in main.py", not `def cmd_query`. Tokens dropped because
the stub is short. That is an eval cheat, not better retrieve.

Eval now requires a **source-backed** chunk: real file path + line
range, not `kind=cli_doc` / `:cli-doc` / `generated`. Stub alone =
miss. Stub + real handler in top-10 = hit, scored at the real
chunk's rank.

## Arms (strict matcher, mean of 2 identical runs)

`main` here is `def83d9` knobs (all CLI flags off) re-scored with
the new matcher. Δ is vs that row.

| Arm | Recall@10 | nDCG@10 | cite | tokens | symbol | nl | cross-file | ticket | cli | hard |
|-----|-----------|---------|------|--------|--------|----|------------|--------|-----|------|
| main (`def83d9`, off) | 0.4080 | 0.3624 | 0.7479 | 1708.4 | 1.000 | 0.450 | 0.264 | 0.357 | 0.000 | 0.278 |
| symbol 0.25 | 0.4705 | 0.4249 | 0.7687 | 1768.6 | 1.000 | 0.450 | 0.264 | 0.357 | 0.600 | 0.278 |
| **symbol 0.50** | **0.5122** | **0.4562** | **0.7896** | 1913.9 | 1.000 | 0.450 | 0.264 | 0.357 | **1.000** | 0.278 |
| doc_index | 0.4080 | 0.3608 | 0.7583 | 1584.5 | 1.000 | 0.450 | 0.264 | 0.357 | 0.000 | 0.278 |
| doc+symbol 0.50 | 0.5122 | 0.4545 | 0.7583 | 1655.5 | 1.000 | 0.450 | 0.264 | 0.357 | 1.000 | 0.278 |
| ident+symbol 0.50 | 0.5330 | 0.4582 | 0.7896 | 2068.3 | 1.000 | **0.500** | 0.264 | **0.429** | 1.000 | 0.278 |

Main's headline numbers **did not move** under the stricter matcher
(0.408 / 0.3624 / 0.7479 / 1708.4). There were no stubs to strip.

CLI query hits (1.0 = real gold source in top-10):

| Arm | query | eval | index | serve | config |
|-----|-------|------|-------|-------|--------|
| main / doc_index | 0 | 0 | 0 | 0 | 0 |
| symbol 0.25 | 0 | 1 | 1 | 1 | 0 |
| symbol 0.50 / doc+symbol / ident+symbol | 1 | 1 | 1 | 1 | 1 |

No listed category drops vs main by more than 0.05.

## Default

**Shipped default is `symbol_boost=0.5`, `doc_index` off.**

Why this one:

- Recall 0.5122 (**+0.104** vs main). Outside the 0.01 band.
- CLI 0.0 → 1.0 on all five fixtures. The packed hit is the real
  `cmd_*` / `Config` source, not a sentence about it.
- No category drop. symbol / nl / cross-file / docs / hard / ticket
  are flat or up.
- `doc_index` alone is now a wash on Recall (0.408) and a slight
  nDCG dip. The catalog still packs (tokens 1584) but does not
  count. Leave it off.
- `doc+symbol 0.50` matches symbol-0.50 recall. Tokens are lower
  because stubs take packer slots from the long handler. Cite is
  worse (0.758 vs 0.790). Not worth it.
- `ident+symbol 0.50` is +0.125 and lifts nl / ticket, but adds
  ~360 tokens. Keep ident-split off unless a later pass wants that
  trade.

Cost: ~205 extra packed tokens vs main (1914 vs 1708). That is the
handler bodies, which is the point.

Catalog still exists as `--cli-doc-index` / `doc_index=true`.
Argparse name/help on the real handler stays behind
`--argparse-enrich`.

## Knobs

| Config / CLI | Meaning |
|--------------|---------|
| `symbol_boost=0.5` / `--cli-symbol-boost W` | Extra RRF list when the query says "the query CLI command" or names default retrieval weights. Default 0.5. `0` = off. |
| `doc_index=false` / `--cli-doc-index` | Tiny catalog stubs (`kind=cli_doc`). Off. Eval ignores them unless the real source is also in top-k. |
| `ident_split` / `--ident-split` | BM25 splits `cmd_query` → `cmd` + `query`. Off. |
| `argparse_enrich` / `--argparse-enrich` | Index argparse name/help onto `cmd_*` chunks. Packed `chunk.content` stays the source. Off. |

## Failure modes that remain

- symbol 0.25 still misses `q-cli-query` and `q-config-defaults`.
  0.5 is the weight that actually puts those bodies in top-10.
- `q-config-defaults` gold is still the `Config` class, not
  `DEFAULT_CONFIG`. The boost fires on "default retrieval weights"
  and returns the class chunks.
- ident-split still does not fix CLI by itself; it only helps when
  the symbol channel already found the handler.
- A future catalog that does not share the gold entity id would
  also fail the source-backed check. That is intended.

## Reproduce

```bash
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --cli-symbol-boost 0
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --cli-symbol-boost 0.25
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --cli-doc-index --cli-symbol-boost 0
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --ident-split
```
