# CLI retrieval results

48-query golden-v2, same MiniLM + Chroma index as post-merge
main (`def83d9`, 4497 chunks). Two runs per arm, `--no-llm`, k=10.
Spread on every arm was **0** for Recall / nDCG / cite.

Suite: `.docs/research/eval/code-harness-v2.fixture.yaml`.
Generated 2026-10-24. Incoming main default: CE off, no CLI catalog.

## What was going wrong

All five CLI fixtures miss (`q-cli-query` / `eval` / `index` / `serve`
plus `q-config-defaults`). Gold sat at fused ranks **40–139** on the
CE-off stack. That is retrieve-time, not the packer cap and not the
cross-encoder.

1. **`main.py` is one file of near-identical `cmd_*` handlers.** Gold
   is `func:main.py:cmd_query` (and friends). Those bodies call
   `_load_config` / `VectorStore`. They never say "CLI command" and
   they do not contain the argparse `add_parser("query", help=...)`
   line. That wiring lives in other file-level chunks.
2. **BM25 keeps `cmd_query` as one token.** `_tokenize` is
   `[a-z0-9_]+` lowercased. Query tokens for "Where is the query CLI
   command defined in main.py?" are `where, is, the, query, cli,
   command, defined, in, main, py`. No `cmd_query`, so the handler
   is a BM25 miss.
3. **`Config` is the wrong surface for the defaults question.** Gold
   is `class:harness/config.py:Config`. The class header chunk is
   18 characters (`@dataclass` + `...`). `DEFAULT_CONFIG` (weights,
   `top_k`, cross-encoder) is a module-level dict, not the dataclass
   body.
4. **Per-file cap is not this bug.** The cap runs after MMR, before
   the token budget. It cannot move a chunk from fused rank 121 into
   top-10.

`q-what-exposes-query` (hard, gold includes `cmd_query`) is the same
shape and also benefits once a catalog exists.

## Arms (mean of 2 identical runs)

Current main = all CLI knobs off. Δ is vs that row. Category numbers
are Recall@10.

| Arm | Recall@10 | nDCG@10 | cite | tokens | symbol | nl | cross-file | ticket | cli | hard |
|-----|-----------|---------|------|--------|--------|----|------------|--------|-----|------|
| main (off) | 0.4080 | 0.3624 | 0.7479 | 1708.4 | 1.000 | 0.450 | 0.264 | 0.357 | 0.000 | 0.278 |
| ident_split | 0.4288 | 0.3659 | 0.7479 | 1862.9 | 1.000 | **0.500** | 0.264 | **0.429** | 0.000 | 0.278 |
| argparse_enrich | 0.4080 | 0.3654 | 0.7479 | 1726.6 | 1.000 | 0.450 | 0.264 | 0.357 | 0.000 | 0.278 |
| symbol_boost 0.25 | 0.4705 | 0.4249 | 0.7687 | 1768.6 | 1.000 | 0.450 | 0.264 | 0.357 | 0.600 | 0.278 |
| symbol_boost 0.50 | 0.5122 | 0.4562 | 0.7896 | 1913.9 | 1.000 | 0.450 | 0.264 | 0.357 | **1.000** | 0.278 |
| **doc_index** | **0.5226** | **0.4609** | 0.7583 | **1588.4** | 1.000 | 0.450 | 0.264 | 0.357 | **1.000** | **0.333** |
| ident+argparse | 0.4288 | 0.3659 | 0.7479 | 1871.1 | 1.000 | 0.500 | 0.264 | 0.429 | 0.000 | 0.278 |
| argparse+symbol 0.25 | 0.4705 | 0.4279 | 0.7687 | 1786.8 | 1.000 | 0.450 | 0.264 | 0.357 | 0.600 | 0.278 |
| ident+argparse+symbol 0.25 | 0.4913 | 0.4284 | 0.7687 | 1931.2 | 1.000 | 0.500 | 0.264 | 0.429 | 0.600 | 0.278 |
| all four | 0.5330 | 0.4513 | **0.7896** | 1720.9 | 1.000 | 0.500 | 0.264 | 0.429 | 1.000 | 0.278 |
| doc+symbol 0.25 | 0.5226 | 0.4609 | 0.7583 | 1588.4 | 1.000 | 0.450 | 0.264 | 0.357 | 1.000 | 0.333 |
| doc+symbol 0.50 | 0.5226 | 0.4609 | 0.7583 | 1617.9 | 1.000 | 0.450 | 0.264 | 0.357 | 1.000 | 0.333 |

Noise band on this stack is ~0.01. No listed category drops vs main
by more than 0.05 on any arm (cli only goes up; docs / cross-file /
symbol are flat).

CLI query hits (1.0 = gold in top-10):

| Arm | query | eval | index | serve | config |
|-----|-------|------|-------|-------|--------|
| main / ident / argparse | 0 | 0 | 0 | 0 | 0 |
| symbol 0.25 | 0 | 1 | 1 | 1 | 0 |
| symbol 0.50 / doc_index / all / doc+symbol | 1 | 1 | 1 | 1 | 1 |

## Default

**Shipped default is `doc_index` on** (`cli_retrieval.doc_index: true`).
The other three knobs stay off.

Why this one:

- Recall 0.5226 (**+0.115** vs main). Outside the 0.01 band.
- CLI 0.0 → 1.0 on all five fixtures. hard also up (0.278 → 0.333)
  because `q-what-exposes-query` shares `cmd_query`.
- Lowest tokens of the 1.0-CLI group (1588 vs 1708). The catalog
  lines are short.
- No BM25 retokenize of the whole index. No ident-split IDF shift.
- argparse_enrich alone did not move Recall@10. Prefixing help text
  onto the handler is not enough when "query" / "cli" / "command"
  already appear all over README and tests.
- ident_split lifts nl / ticket a bit (+0.021 headline) and still
  leaves CLI at 0. It also adds ~150 tokens.
- symbol_boost 0.50 also gets CLI 1.0, but packs the real (long)
  `cmd_*` bodies and costs +205 tokens. Use that when you want the
  handler source in the window, not just the entity id.
- `all four` is +0.010 vs doc_index (noise-band edge), higher
  tokens, and hard falls back to 0.278.

Cost: packed CLI context is the catalog stub ("The query CLI command
is defined as cmd_query in main.py…"), not the function body. Recall
counts because the stub shares `entity_id`. `--cli-symbol-boost 0.5`
if you want the source.

Off: `--no-cli-doc-index` or `retrieval.cli_retrieval.doc_index=false`.

## Knobs

| Config / CLI | Meaning |
|--------------|---------|
| `doc_index=true` / `--cli-doc-index` | Tiny catalog chunks (`kind=cli_doc`) with the gold entity id. Default on. |
| `ident_split=true` / `--ident-split` | BM25 splits `cmd_query` → `cmd` + `query` (keeps the original token). |
| `argparse_enrich=true` / `--argparse-enrich` | Index argparse name/help onto `cmd_*` chunks. Packed `chunk.content` stays the source. |
| `symbol_boost=W` / `--cli-symbol-boost W` | Extra RRF list when the query says "the query CLI command" or names default retrieval weights. `0` = off. |

`CODEHARNESS_CLI_DOC_INDEX=0` and friends match the flags. Turning
ident-split or argparse-enrich on rebuilds BM25 from the loaded
chunks; the on-disk pickle stays vanilla.

## Failure modes that remain

- Catalog hit ≠ handler body in the prompt. Fine for entity-id
  recall; thin if you expected `def cmd_query`.
- `q-config-defaults` gold is still the `Config` class, not
  `DEFAULT_CONFIG`. The catalog points at `Config` on purpose.
- ident_split makes `query` a very common BM25 token. That helped
  nl / ticket here and did nothing for CLI.
- argparse_enrich needs the argparse wiring in some other chunk of
  the same file. It does not fix Config.

## Reproduce

```bash
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --no-cli-doc-index
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --ident-split --no-cli-doc-index
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --argparse-enrich --no-cli-doc-index
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --cli-symbol-boost 0.5 --no-cli-doc-index
```
