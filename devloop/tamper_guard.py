#!/usr/bin/env python3
"""Devloop tamper guard — structural anti-verification-theater check.

Rejects the current (uncommitted) iteration if it touches anything the loop
must never change. Prompt rules alone don't hold under an agent chasing green
(the reward function is passing tests); this makes the repo's own rules —
"never weaken or delete a red acceptance test", human-commit-only thresholds —
enforceable backpressure instead of a sign.

Policy, checked against `git diff HEAD` + untracked files:
  1. PROTECTED paths (PROTECTED_DIRS + PROTECTED_FILES, so fixtures/ too):
     any change at all -> reject, additions included.
     Exception: specs/acceptance.md may change ONLY by ticking checkboxes
     (`- [ ]` -> `- [x]`, rest of the line identical).
     evals/seats/ is protected against additions too: the measured I5 ceilings
     and the list of enabled invariants move by human commit only.
  2. tests/, evals/cases/, evals/traces/ files that existed at the BASELINE
     commit: anything but an addition -> reject (modify, delete, typechange).
     Additions are free, so a run may still append a whole new label under
     evals/traces/.
     Traces are guarded because freezing evals/traces/recorded-expected.json
     alone protects one half of an equality: tests/test_evals_recorded.py
     asserts `got == expected` where `got` is regraded from the trace files
     under evals/traces/recorded/, so editing those inputs restores equality
     exactly as well as editing the expectation. The rest of the directory is
     guarded for a different reason: every other committed run label under
     evals/traces/ (eleven of them at time of writing) is archived
     measurement evidence, the thing a later run is diffed against by
     `make eval-report RUN=… BASELINE=…` (evals/report_cli.py; note that
     scripts/critic_gate.py also reads evals/traces/<label>, but it grades one
     label against MIN_DETECTION and has no baseline notion). Which label is
     the current comparison point is recorded per seat, in whichever
     evals/seats/*.yaml records one — today that is only evals/seats/pm.yaml,
     which names postfix3; evals/seats/critic.yaml names no baseline at all.
     Not here — it moves as charters change, and an older label can be the
     wrong baseline while still being history worth keeping. No test reads
     them; a human does. Editing one moves the comparison point instead of the
     result.
  3. Everything else (state/, gate/, orchestrator/, agents/, slackkit/, the
     rest of evals/, new tests, devloop/NOTES.md) is the loop's workspace.

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
    "evals/seats/",
)
# Matched by exact repo-relative path, never by prefix or suffix. The last
# four do not exist in the repo, which is the point: creating one is the
# attack, and the guard sees a creation as status "A" and rejects it.
# Measured with pytest 9.1.1, the precedence is pytest.ini > pyproject.toml >
# tox.ini > setup.cfg, so only a root pytest.ini actually displaces
# pyproject.toml's [tool.pytest.ini_options]. setup.cfg and tox.ini are
# guarded as defence in depth, not because they win: with either alongside,
# pytest still reports `configfile: pyproject.toml (WARNING: ignoring pytest
# config in setup.cfg!)` and pyproject's options apply. They are inert only
# while pyproject.toml keeps that table — and pyproject.toml is itself
# protected — so guarding all four is what stops the attack moving sideways to
# another filename. A root conftest.py needs no precedence: it drops tests
# from collection (collect_ignore_glob) or quietly rewrites what the survivors
# mean (autouse fixtures, sys.path shims, monkeypatched module attributes).
# Exact match also keeps evals/conftest.py and tests/conftest.py out of this
# set.
PROTECTED_FILES = {"CLAUDE.md", "KICKOFF.md", "Makefile", "pyproject.toml", ".gitignore",
                   "evals/traces/recorded-expected.json",
                   "pytest.ini", "setup.cfg", "tox.ini", "conftest.py"}
CHECKBOX_FILE = "specs/acceptance.md"
# existing files immutable, additions free — see policy rule 2 for why the
# whole of evals/traces/ is here and not just recorded/.
BASELINE_GUARDED_DIRS = ("tests/", "evals/cases/", "evals/traces/")
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
    # `-z` here for the same reason as on the two commands below: without it a
    # non-ASCII baseline path arrives quoted while the diff path arrives raw,
    # so `path in baseline_files` is silently False and the file is editable.
    # `-z` NUL-terminates every entry, so the trailing field is empty — drop it
    # rather than admitting "" as a phantom baseline path.
    baseline_files = {p for p in
                      git("ls-tree", "-r", "--name-only", "-z", baseline).split("\0")
                      if p}

    changes: list[tuple[str, str]] = []  # (status, path)
    # `-z` on these too: it emits raw bytes instead of git's default
    # core.quotePath escaping, which renders a non-ASCII path as
    # `"specs/d\303\253sign.md"` — leading quote and all — and slips past the
    # prefix tests below; it also survives paths containing a newline.
    # `--name-status -z` is framed as NUL-separated STATUS, PATH fields (not
    # tab-separated lines with the tabs swapped out). `--no-renames` keeps it
    # to exactly those two fields per change: without it a rename is one
    # 3-field `R100, old, new` record, and testing only the destination lets
    # `git mv specs/design.md attic/` or `git mv tests/test_x.py tests/_x.py`
    # move a guarded file out of the way for free. Decomposed into `D old` +
    # `A new`, the `D` is already a violation.
    fields = git("diff", "HEAD", "--name-status", "-z", "--no-renames").split("\0")
    for status, path in zip(fields[::2], fields[1::2]):
        changes.append((status[:1], path))
    for path in git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path:
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
        # `!= "A"` and not a list of letters: `path in baseline_files` already
        # proves the file existed at the baseline, so an addition is the only
        # benign status here and everything else is a change to frozen
        # evidence. A letter list has to be revised every time git grows a
        # status — it was `("M", "D")`, which let `T` (typechange) through:
        # `rm tests/test_x.py; ln -s /dev/null tests/test_x.py` dropped the
        # file from collection and passed. Reachable here today is just
        # A/M/D/T (`--no-renames` kills R and C, -B is not passed, and a
        # conflicted file reports M, A, T or nothing at all depending on the
        # conflict shape — measured: content `UU` -> `M`; delete/modify `DU`
        # -> `A`; modify/delete `UD` -> no record at all; typechange `UA` ->
        # `T` plus an `A` for git's `path~HEAD` sidecar. Never `U`: that letter
        # only appears in the index-relative forms, `git diff --cached` and
        # bare `git diff`, not in the `git diff HEAD` this reads), so
        # `("M", "D", "T")` would be equivalent right now — this form is
        # default-deny, so the next letter is rejected on arrival instead of
        # after the next incident.
        if path.startswith(BASELINE_GUARDED_DIRS) and status != "A" \
                and path in baseline_files:
            verb = {"M": "modified", "D": "deleted",
                    "T": "replaced (typechange)"}.get(status, f"changed ({status})")
            violations.append(f"{path}: pre-existing evidence {verb} — tests, "
                              "eval cases and traces may be added, never weakened")

    if violations:
        print("TAMPER GUARD: iteration rejected", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
