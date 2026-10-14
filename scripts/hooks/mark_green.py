#!/usr/bin/env python3
"""Record that the full suite passed on exactly this tree.

Appended to the `test` recipe in the Makefile, so it runs only after pytest
exited 0. Never call it by hand: a marker written without a green run is a
lie the hooks will believe.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tree import tree_id, write_marker  # noqa: E402


def main() -> int:
    cwd = Path.cwd()
    write_marker(cwd, tree_id(cwd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
