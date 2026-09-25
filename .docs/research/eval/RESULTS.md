# TurboVec vs Chroma fixture A/B

Committed retrieval A/B on the project fixture suite. TurboVec stays
experimental; this table is the honesty record, not a default flip.

| Field | Value |
|-------|-------|
| Suite | `code-harness` |
| Suite path | `.docs/research/eval/code-harness.fixture.yaml` |
| k | 10 |
| Generated | 2026-09-25T20:22:58Z |
| Gate | PASS |
| Notes | Live native TurboQuant (turbovec 1.0.0, 4-bit), not the exact-cosine stub. embedding=all-MiniLM-L6-v2. n=8 fixtures — treat as a gate record, not a default flip. |

## Metrics

| backend | Recall@10 | nDCG@10 | citation-path | dense p50 | n |
|---------|------------|----------|---------------|-----------|---|
| chromadb | 0.2500 | 0.2246 | 0.5000 | 24.9ms | 8 |
| turbovec | 0.3125 | 0.2468 | 0.5000 | 8.8ms | 8 |

## Gate

- relative tolerance: 5%
- Recall@10 absolute: −2 pts; Recall@30 absolute: −1 pt
- status: **PASS**

## How to run (local)

```bash
# Core retrieve stack (Chroma + local embedder)
pip install -r requirements.txt

# Optional TurboVec extra (Rust wheel). Unittest stays green without it.
pip install -r requirements-turbovec.txt

# Routine recipe: index both persist dirs, compare, write this file
python main.py eval-ab .

# Equivalent explicit commands
python main.py index .
python main.py index . --vector-backend turbovec
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \
  --compare-backends chromadb,turbovec \
  --compare-markdown .docs/research/eval/RESULTS.md \
  --compare-output .docs/research/eval/RESULTS.json
```

Make target: `make eval-ab`. Script: `python scripts/eval_chroma_vs_turbovec.py`.

Run this on a machine that can install `turbovec` plus the local embedder
(for example the developer workstation that owns this checkout). CI and
default unittest skip the live TurboVec column when the extra is absent
(exit 0). Selecting `--vector-backend turbovec` while the wheel is missing
fails clearly (exit 1). A failed recall gate still exits 2 unless
`--force-experimental`.

## Policy

Default dense store remains Chroma. Dual-write, TQ+ calibrate, and flipping
the default are later work. Do not treat a skipped or placeholder row as
TurboQuant recall.

## Live run provenance

Recorded by `python main.py eval-ab .` after `pip install -r requirements.txt`
and `pip install -r requirements-turbovec.txt`. Native `turbovec` 1.0.0
(4-bit TurboQuant) vs Chroma 1.5.9; local `all-MiniLM-L6-v2`; 8-fixture
`code-harness` suite at k=10. Chroma indexed 3060 chunks; TurboVec 3061.
This is **not** `turbovec-stub` exact cosine. Gate **PASS** on this snapshot;
default remains Chroma (n=8 is too small to flip).
