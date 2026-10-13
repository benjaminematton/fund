#!/usr/bin/env python3
"""PreToolUse hook on Bash: a `git commit` or `gh pr create` runs only on a
tree the full suite has passed on.

Reads the Claude Code hook JSON on stdin and takes the checkout from its
`cwd` field — never CLAUDE_PROJECT_DIR, which stays pinned to the starting
root after a session enters a worktree. Then one of four outcomes:

  * the command is not a commit or PR creation: pass through;
  * the green marker matches the current tree id: allow, nothing run;
  * otherwise run the suite (`make test`); green writes the marker and allows;
  * red: deny, with the last TAIL_LINES of output as the reason the model sees.

Exit code is always 0; a denial is the JSON on stdout.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tree import is_green, toplevel, tree_id, write_marker  # noqa: E402

TAIL_LINES = 40


# Segment separators: `&&` and `||` are two adjacent separators and yield an
# empty segment, which is harmless. Parens cover subshells. Newline covers
# multi-line commands, which shlex would otherwise fold into one segment.
SPLIT = re.compile(r"[;&|\n()]")
# git global options that take a separate value argument.
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--exec-path", "--super-prefix", "--config-env"}
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_env(toks: list) -> list:
    i = 0
    while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
        i += 1
    return toks[i:]


def _git_subcommand(toks: list):
    """The git subcommand after any global options, or None if not git."""
    if not toks or toks[0] != "git":
        return None
    i = 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in _GIT_VALUE_OPTS else 1
    return toks[i] if i < len(toks) else None


def gated(command: str) -> bool:
    """True when any shell segment is a `git commit` (with any env-assignment
    or git global-option prefix) or a `gh pr create`."""
    for segment in SPLIT.split(command):
        try:
            toks = shlex.split(segment)
        except ValueError:
            toks = segment.split()
        toks = _strip_env(toks)
        if _git_subcommand(toks) == "commit":
            return True
        if toks[:3] == ["gh", "pr", "create"]:
            return True
    return False


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))


def main(stdin=sys.stdin, suite=("make", "test")) -> int:
    payload = json.load(stdin)
    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    if not gated(command):
        return 0
    cwd = toplevel(Path(payload.get("cwd") or os.getcwd()))
    if is_green(cwd):
        return 0
    run = subprocess.run(list(suite), cwd=str(cwd),
                         capture_output=True, text=True)
    if run.returncode == 0:
        write_marker(cwd, tree_id(cwd))
        return 0
    tail = "\n".join((run.stdout + run.stderr).splitlines()[-TAIL_LINES:])
    _deny("commit gate: `make test` is red on this tree, so the command was"
          " not run. Fix the failure, re-run `make test`, then retry.\n" + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
