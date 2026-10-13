"""Shared primitive for the verification hooks: the tree id and the green marker.

The tree id is a content hash of the working tree, tracked and untracked
non-ignored files alike, taken with `git write-tree` against an empty
temporary index. The real index is never touched. `make test` writes the id
to <git-dir>/fund-green-tree on success; the hooks compare against it, so
"green" means "the full suite passed on exactly this tree".

Stdlib only and 3.9-compatible: the hooks run under whatever `python3` is on
PATH, in a worktree that may not have a venv yet.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

MARKER_NAME = "fund-green-tree"


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def toplevel(cwd: Path) -> Path:
    return Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve()


def git_dir(cwd: Path) -> Path:
    """Per-worktree: in a linked worktree this is .git/worktrees/<name>."""
    return Path(_git(cwd, "rev-parse", "--absolute-git-dir")).resolve()


def tree_id(cwd: Path) -> str:
    root = toplevel(cwd)
    fd, index = tempfile.mkstemp(prefix="fund-tree-", suffix=".idx")
    os.close(fd)
    os.unlink(index)                      # git creates the index itself
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        _git(root, "add", "-A", "--", ".", env=env)
        return _git(root, "write-tree", env=env)
    finally:
        if os.path.exists(index):
            os.unlink(index)


def marker_path(cwd: Path) -> Path:
    return git_dir(cwd) / MARKER_NAME


def read_marker(cwd: Path) -> str | None:
    path = marker_path(cwd)
    return path.read_text().strip() if path.exists() else None


def write_marker(cwd: Path, tid: str) -> None:
    marker_path(cwd).write_text(tid + "\n")


def is_green(cwd: Path) -> bool:
    return read_marker(cwd) == tree_id(cwd)


def dirty_tracked(cwd: Path) -> bool:
    """Tracked files modified. Untracked-only changes are the commit gate's
    business, through the tree id."""
    return bool(_git(cwd, "status", "--porcelain", "--untracked-files=no"))
