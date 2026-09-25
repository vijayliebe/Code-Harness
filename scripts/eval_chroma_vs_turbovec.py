#!/usr/bin/env python3
"""Index Chroma + TurboVec (when installed) and persist the fixture A/B table.

Equivalent to ``python main.py eval-ab .``. Missing TurboVec extra is an
honest skip (exit 0). Selecting TurboVec while the wheel is missing fails.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    script = os.path.join(ROOT, "main.py")
    os.execv(sys.executable, [sys.executable, script, "eval-ab", *argv])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
