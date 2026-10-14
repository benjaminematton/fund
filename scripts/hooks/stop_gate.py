#!/usr/bin/env python3
"""Stop hook: a turn may not end on a tree with modified tracked files that
the full suite has not passed on.

Never runs the suite, so turn ends stay fast. Blocks once, with the reason,
and lets the next stop through (`stop_hook_active`): one block rather than
the harness cap of eight, because in the shared main checkout a dirty tree
can be a peer's work and a red suite can be outside this session's lane.

Untracked-only changes do not trigger this gate; the commit gate covers them
through the tree id. Exit code is always 0; a block is the JSON on stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _tree import (GitError, dirty_tracked, is_green,  # noqa: E402
                   is_hooked_checkout, toplevel)

REASON = ("stop gate: tracked files changed since the last green `make test`"
          " on this tree. Run `make test` and report the result before"
          " ending the turn. If you changed no tracked files this turn (the"
          " edits are another session's, in a shared checkout), say so and"
          " end the turn without running it.")


def main(stdin=sys.stdin) -> int:
    payload = json.load(stdin)
    if payload.get("stop_hook_active"):
        return 0
    try:
        cwd = toplevel(Path(payload.get("cwd") or os.getcwd()))
    except GitError:
        return 0                          # not a git repo: pass through
    if not is_hooked_checkout(cwd):
        return 0
    if not dirty_tracked(cwd) or is_green(cwd):
        return 0
    print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
