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

SPLIT = re.compile(r"\|\||&&|;|\|")
TAIL_LINES = 40


def gated(command: str) -> bool:
    """True when any shell segment is `git commit`, `git -C <path> commit`,
    or `gh pr create`."""
    for segment in SPLIT.split(command):
        try:
            toks = shlex.split(segment)
        except ValueError:
            toks = segment.split()
        if toks[:2] == ["git", "commit"]:
            return True
        if len(toks) >= 4 and toks[0] == "git" and toks[1] == "-C" \
                and toks[3] == "commit":
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
