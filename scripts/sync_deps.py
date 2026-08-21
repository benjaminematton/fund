#!/usr/bin/env python3
"""Sync .venv to requirements.lock, content-hash gated (called by make).

Installs the lock, not pyproject.toml's ranges: ranges resolve to whatever is
newest on the day a venv is built, which is how local, the droplet and CI came
to differ on 20 packages. pyproject.toml still gates the stamp because its
ranges are what tests/test_deps_lock.py checks the lock against.

mtime-based stamp files are unreliable here: Apple's GNU make 3.81 compares
timestamps at 1-second granularity and treats equal mtimes as up-to-date, so
an edit landing in the same second as the previous sync is silently skipped.
The stamp therefore stores a sha256 of both files' content, not time. Runs
under the venv python.
"""

import hashlib
import importlib
import importlib.metadata as md
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / ".venv" / ".deps-synced"
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")


def _canon(name: str) -> str:
    """PEP 503 normalization: slack_bolt and slack-bolt are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(lock: Path = LOCK) -> dict[str, str]:
    """Parse requirements.lock into {canonical name: exact version}."""
    pins = {}
    for raw in lock.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        if not m:
            raise ValueError(f"{lock}: {line!r} is not an exact name==version pin")
        pins[_canon(m.group(1))] = m.group(2)
    return pins


def stamp_value(lock: Path = LOCK, pyproject: Path = PYPROJECT) -> str:
    """Content hash gating the sync — changes if either file changes."""
    h = hashlib.sha256()
    h.update(lock.read_bytes())
    h.update(pyproject.read_bytes())
    return h.hexdigest()


def drift(lock: Path = LOCK) -> list[str]:
    """Lines describing where installed metadata disagrees with the lock.

    Reads what is installed, never what was requested — a satisfied range is
    exactly what hid the original drift. Empty list means the env matches.
    """
    importlib.invalidate_caches()
    installed = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name:
            installed[_canon(name)] = dist.version
    report = []
    for name, want in sorted(read_lock(lock).items()):
        have = installed.get(name)
        if have is None:
            report.append(f"{name}: locked at {want}, not installed")
        elif have != want:
            report.append(f"{name}: locked at {want}, installed {have}")
    return report


def main() -> int:
    want = stamp_value()
    if STAMP.exists() and STAMP.read_text() == want:
        return 0
    rc = subprocess.call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(LOCK)])
    if rc != 0:
        return rc
    report = drift()
    if report:
        print(
            "sync_deps: .venv does not match requirements.lock after install:",
            *(f"  {line}" for line in report),
            sep="\n", file=sys.stderr)
        return 1
    STAMP.write_text(want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
