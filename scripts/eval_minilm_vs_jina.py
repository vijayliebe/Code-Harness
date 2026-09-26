#!/usr/bin/env python3
"""Index MiniLM + Jina-code (when cached) and persist the fixture embedder A/B table.

Equivalent to ``python main.py eval-ab . --compare-embedders all-MiniLM-L6-v2,jina-embeddings-v2-base-code``
or ``make eval-ab-embed``. Missing Jina weights is an honest skip (exit 0).
Selecting Jina while the model cannot be loaded fails.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--compare-embedders" not in argv:
        argv = [
            *argv,
            "--compare-embedders",
            "all-MiniLM-L6-v2,jina-embeddings-v2-base-code",
        ]
    script = os.path.join(ROOT, "main.py")
    os.execv(sys.executable, [sys.executable, script, "eval-ab", *argv])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
