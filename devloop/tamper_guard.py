#!/usr/bin/env python3
"""Devloop tamper guard — structural anti-verification-theater check.

Rejects the current (uncommitted) iteration if it touches anything the loop
must never change. Prompt rules alone don't hold under an agent chasing green
(the reward function is passing tests); this makes the repo's own rules —
"never weaken or delete a red acceptance test", human-commit-only thresholds —
enforceable backpressure instead of a sign.

Policy, checked against `git diff HEAD` + untracked files:
  1. PROTECTED paths: any change at all -> reject.
     Exception: specs/acceptance.md may change ONLY by ticking checkboxes
     (`- [ ]` -> `- [x]`, rest of the line identical).
  2. tests/, fixtures files that existed at the BASELINE commit: modify or
     delete -> reject. New test files are welcome.
  3. Everything else (state/, gate/, orchestrator/, agents/, slackkit/, new
     tests, devloop/NOTES.md) is the loop's workspace.

Usage: tamper_guard.py <baseline-ref>   (exit 0 = clean, 1 = violations)
Zero dependencies. Dev tooling only — not part of the fund runtime.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROTECTED_DIRS = (
    "specs/", "fixtures/", "charters/", "scripts/", "research/",
    "fundbt/", "stratgate/", "calibration/", "devloop/", ".github/",
)
PROTECTED_FILES = {"CLAUDE.md", "KICKOFF.md", "Makefile", "pyproject.toml", ".gitignore"}
CHECKBOX_FILE = "specs/acceptance.md"
BASELINE_GUARDED_DIRS = ("tests/",)  # existing files immutable, additions free
DEVLOOP_SCRATCH = {"devloop/NOTES.md"}  # agent memory; exempt from PROTECTED


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def checkbox_only_diff() -> bool:
    """True iff the acceptance.md diff is purely `- [ ]` -> `- [x]` ticks."""
    diff = git("diff", "HEAD", "--", CHECKBOX_FILE).splitlines()
    minus = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
    if len(minus) != len(plus) or not minus:
        return False
    for old, new in zip(minus, plus):
        o, n = old.strip(), new.strip()
        if not (o.startswith("- [ ]") and n.startswith("- [x]")
                and o[5:] == n[5:]):
            return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: tamper_guard.py <baseline-ref>", file=sys.stderr)
        return 2
    baseline = sys.argv[1]
    baseline_files = set(git("ls-tree", "-r", "--name-only", baseline).splitlines())

    changes: list[tuple[str, str]] = []  # (status, path)
    for line in git("diff", "HEAD", "--name-status").splitlines():
        status, path = line.split("\t", 1)[0][:1], line.split("\t")[-1]
        changes.append((status, path))
    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        changes.append(("A", path))

    violations: list[str] = []
    for status, path in changes:
        if path in DEVLOOP_SCRATCH:
            continue
        if path == CHECKBOX_FILE and status == "M":
            if not checkbox_only_diff():
                violations.append(f"{path}: edits beyond ticking checkboxes")
            continue
        if path in PROTECTED_FILES or path.startswith(PROTECTED_DIRS):
            violations.append(f"{path}: protected path ({status})")
            continue
        if path.startswith(BASELINE_GUARDED_DIRS) and status in ("M", "D") \
                and path in baseline_files:
            violations.append(f"{path}: pre-existing test {status}odified — "
                              "tests may be added, never weakened")

    if violations:
        print("TAMPER GUARD: iteration rejected", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
