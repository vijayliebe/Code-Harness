.PHONY: eval-ab test

# Routine Chroma vs TurboVec fixture A/B. Missing turbovec extra → skip (exit 0).
eval-ab:
	python3 main.py eval-ab .

test:
	python3 -m unittest discover -s tests -v
