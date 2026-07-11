#!/usr/bin/env python3
"""Sync .venv deps from pyproject.toml, content-hash gated (called by make).

mtime-based stamp files are unreliable here: Apple's GNU make 3.81 compares
timestamps at 1-second granularity and treats equal mtimes as up-to-date, so
a pyproject.toml edit landing in the same second as the previous sync is
silently skipped. The stamp therefore stores a sha256 of pyproject.toml and
this script compares content, not time. Runs under the venv python.
"""

import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / ".venv" / ".deps-synced"
PYPROJECT = ROOT / "pyproject.toml"


def main() -> int:
    want = hashlib.sha256(PYPROJECT.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text() == want:
        return 0
    project = tomllib.load(PYPROJECT.open("rb"))["project"]
    deps = project["dependencies"] + project.get(
        "optional-dependencies", {}).get("dev", [])
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", *deps])
    if rc == 0:
        STAMP.write_text(want)
    return rc


if __name__ == "__main__":
    sys.exit(main())
