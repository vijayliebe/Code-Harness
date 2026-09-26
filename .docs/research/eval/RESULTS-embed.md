# MiniLM vs Jina-code fixture A/B

Committed retrieval A/B on the project fixture suite. jina-embeddings-v2-base-code stays an optional local candidate; this table is the honesty record, not a default flip.

| Field | Value |
|-------|-------|
| Suite | `code-harness` |
| Suite path | `.docs/research/eval/code-harness.fixture.yaml` |
| k | 10 |
| Generated | — |
| Gate | SKIP |
| Notes | Optional jina-embeddings-v2-base-code weights are not cached. Unittest stays green. Run `make eval-ab-embed` on a machine that can download the Hugging Face model. |

## Metrics

| embedder | Recall@10 | nDCG@10 | citation-path | dense p50 | n |
|---------|------------|----------|---------------|-----------|---|
| all-MiniLM-L6-v2 | n/a | n/a | n/a | n/a | n/a |
| jina-embeddings-v2-base-code | SKIP | SKIP | SKIP | SKIP | — |

## Gate

- relative tolerance: 5%
- Recall@10 absolute: −2 pts; Recall@30 absolute: −1 pt
- status: **SKIP**
- skip: Optional jina-embeddings-v2-base-code weights are not cached. Unittest stays green. Run `make eval-ab-embed` on a machine that can download the Hugging Face model.

## How to run (local)

```bash
# Core retrieve stack (Chroma + default MiniLM)
pip install -r requirements.txt

# Routine recipe: index isolated Chroma dirs, compare, write this file
python main.py eval-ab . --compare-embedders all-MiniLM-L6-v2,jina-embeddings-v2-base-code
# or
make eval-ab-embed

# Same recipe, one embedder at a time
python main.py index .
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python main.py index . --embed-model jina-embeddings-v2-base-code
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \
  --embed-model jina-embeddings-v2-base-code
```

Make target: `make eval-ab-embed`. Script: `python scripts/eval_minilm_vs_jina.py`.

`all-MiniLM-L6-v2` is 384-d, small, and fast (production default).
`jina-embeddings-v2-base-code` is 768-d, code-specialized, slower to
download/load (~161M params), and uses a separate Chroma persist dir
because dimensions cannot share a collection. CI and default unittest
skip the live Jina column when the weights are absent (exit 0).
Selecting `--embed-model jina-embeddings-v2-base-code` while the model
cannot be loaded fails clearly (exit 1).

## Policy

Default embedder remains `all-MiniLM-L6-v2`. Default dense store remains
Chroma. Do not flip either until this table plus a larger fixture set
justify it. `decision.enabled` stays false.
