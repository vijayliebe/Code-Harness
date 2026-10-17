.PHONY: eval-ab eval-ab-embed eval-ablate test

# Routine Chroma vs TurboVec fixture A/B. Missing turbovec extra → skip (exit 0).
eval-ab:
	python3 main.py eval-ab .

# Optional MiniLM vs Jina-code embedder A/B on Chroma. Missing Jina weights → skip (exit 0).
eval-ab-embed:
	python3 main.py eval-ab . --compare-embedders all-MiniLM-L6-v2,jina-embeddings-v2-base-code

# Harness-on vs harness-off leave-one-out on the golden suite. Eval-only.
eval-ablate:
	python3 main.py eval-ab . --ablate

test:
	python3 -m unittest discover -s tests -v
