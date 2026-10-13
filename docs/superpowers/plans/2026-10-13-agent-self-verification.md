# Agent Self-Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every coding session on this repo three enforced ways to check its own work: a commit/stop gate on `make test`, a pyright ratchet inside `make lint`, and a rendered sim day it can diff.

**Architecture:** Three independently mergeable PRs, in order. PR 1 adds two stdlib hook scripts under `scripts/hooks/` and commits `.claude/settings.json` so they reach every worktree. PR 2 pins pyright, adds `scripts/check_typing.py` with a committed per-file error baseline that only shrinks, and makes CI run the same lint list as the Makefile. PR 3 moves the sim-day composition into `tests/simday.py` behind a named scenario table and adds `scripts/sim_report.py` to render one scenario as Slack transcript, DB rows, and scorecard.

**Tech Stack:** Python 3.12 (3.9-compatible stdlib for the hook scripts), pytest, pyright 1.1.414 (`pyright[nodejs]`), git plumbing (`write-tree`), Claude Code hooks (`PreToolUse`, `Stop`).

**Spec:** `docs/superpowers/specs/2026-10-13-agent-self-verification-design.md`

## Global Constraints

- Hook scripts in `scripts/hooks/` are stdlib only and must run under Python 3.9 (`/usr/bin/python3` on macOS): no `match`, no PEP 604 unions outside annotations, `from __future__ import annotations` at the top of every file.
- Hooks locate the tree from the stdin `cwd` field, never from `CLAUDE_PROJECT_DIR`.
- `make test` stays the single verification command. No `make check`.
- Makefile comments must not contain the words `third`, `fourth`, `fifth`, `sixth`, `seventh`, nor "three/four/five/six/seven ... legs/jobs/ExecStart" (`tests/test_ops_units.py::_RESTATES`).
- The typing baseline moves only downward, by human commit. `--write-baseline` is the only writer.
- No change under `gate/`, `stratgate/`, `calibration/`, `agents/runtime.py`, or any seat toolset.
- Commit messages: conventional prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`). No AI attribution trailer.
- `make test` must be green before every commit. Run it as `make test` from the worktree root (it bootstraps `.venv` on first run).
- Dates in this plan are absolute. Today is 2026-10-13.

## Branching

1. Merge the spec + plan branch `self-verification-spec` (commits 79b5bdd and the plan commit) to master first. Docs only; still run `make test` after the merge.
2. Each PR is its own worktree off the then-current `master`:
   - PR 1: `git worktree add -b verify-hooks ../fund-wt/verify-hooks master`
   - PR 2: `git worktree add -b typecheck-ratchet ../fund-wt/typecheck-ratchet master` (after PR 1 merges)
   - PR 3: `git worktree add -b sim-report ../fund-wt/sim-report master` (after PR 2 merges)
3. Never `git checkout` in the main checkout at `~/Developer/fund`; peers occupy it.

---

## PR 1 — Enforcement hooks (branch `verify-hooks`)

**File structure**

| Path | Responsibility |
|---|---|
| `scripts/hooks/_tree.py` | Tree id, green marker read/write, dirty check. Shared by the three scripts. |
| `scripts/hooks/mark_green.py` | Called by the `test` recipe on success: writes the marker. |
| `scripts/hooks/commit_gate.py` | `PreToolUse` hook on Bash: gates `git commit` / `gh pr create` on a green tree. |
| `scripts/hooks/stop_gate.py` | `Stop` hook: blocks once when tracked files changed and the tree is not green. |
| `tests/test_verification_hooks.py` | All hook tests, against a throwaway git repo. |
| `.claude/settings.json` | Committed: hook registrations + repo-policy `deny`/`ask`. |
| `Makefile` | `test` recipe gains the marker line. |
| `CLAUDE.md` | One sentence separating harness hooks from SDK seat hooks. |

### Task 1: Tree id and green marker (`_tree.py`)

**Files:**
- Create: `scripts/hooks/_tree.py`
- Create: `tests/test_verification_hooks.py`

**Interfaces:**
- Produces: `tree_id(cwd: Path) -> str`, `toplevel(cwd: Path) -> Path`, `marker_path(cwd: Path) -> Path`, `read_marker(cwd: Path) -> str | None`, `write_marker(cwd: Path, tid: str) -> None`, `is_green(cwd: Path) -> bool`, `dirty_tracked(cwd: Path) -> bool`. All take any path inside the checkout.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verification_hooks.py`:

```python
"""The commit and stop gates (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 2), against a throwaway
git repo. The suite command is injected everywhere, so nothing in this file
ever recurses into `make test`."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "hooks"
sys.path.insert(0, str(HOOKS))

import _tree  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


# --- _tree ------------------------------------------------------------------

def test_tree_id_is_stable_and_sees_untracked_files(repo: Path):
    first = _tree.tree_id(repo)
    assert first == _tree.tree_id(repo)
    (repo / "ignored.txt").write_text("noise\n")
    assert _tree.tree_id(repo) == first            # ignored files don't count
    (repo / "b.py").write_text("y = 2\n")
    assert _tree.tree_id(repo) != first            # untracked files do
    # the real index was never touched: b.py is still untracked, nothing staged
    assert _git(repo, "status", "--porcelain").splitlines() == ["?? b.py"]
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_tree_id_works_from_a_subdirectory(repo: Path):
    sub = repo / "pkg"
    sub.mkdir()
    assert _tree.tree_id(sub) == _tree.tree_id(repo)
    assert _tree.toplevel(sub) == repo.resolve()


def test_marker_lives_in_the_git_dir(repo: Path):
    assert _tree.read_marker(repo) is None
    _tree.write_marker(repo, "abc")
    assert _tree.marker_path(repo) == (repo / ".git" / "fund-green-tree").resolve()
    assert _tree.read_marker(repo) == "abc"


def test_is_green_compares_marker_to_current_tree(repo: Path):
    assert not _tree.is_green(repo)
    _tree.write_marker(repo, _tree.tree_id(repo))
    assert _tree.is_green(repo)
    (repo / "a.py").write_text("x = 2\n")
    assert not _tree.is_green(repo)


def test_dirty_tracked_ignores_untracked_only_changes(repo: Path):
    assert not _tree.dirty_tracked(repo)
    (repo / "b.py").write_text("y = 2\n")
    assert not _tree.dirty_tracked(repo)
    (repo / "a.py").write_text("x = 2\n")
    assert _tree.dirty_tracked(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: `ModuleNotFoundError: No module named '_tree'`

- [ ] **Step 3: Write `_tree.py`**

Create `scripts/hooks/_tree.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the full suite and commit**

Run: `make test`
Expected: all green (1550+ passed)

```bash
git add scripts/hooks/_tree.py tests/test_verification_hooks.py
git commit -m "feat: tree id and green marker for the verification hooks"
```

### Task 2: `mark_green.py` and the `test` recipe

**Files:**
- Create: `scripts/hooks/mark_green.py`
- Modify: `Makefile:38-39` (the `test` target)
- Test: `tests/test_verification_hooks.py`

**Interfaces:**
- Consumes: `_tree.tree_id`, `_tree.write_marker`.
- Produces: the marker file is written by every green `make test`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verification_hooks.py`:

```python
# --- mark_green ---------------------------------------------------------------

def test_mark_green_writes_the_current_tree_id(repo: Path):
    subprocess.run([sys.executable, str(HOOKS / "mark_green.py")],
                   cwd=repo, check=True)
    assert _tree.read_marker(repo) == _tree.tree_id(repo)


def test_make_test_recipe_marks_green():
    """The `test` recipe writes the marker after pytest, so a green run by
    hand is a green run for the hooks. Pinned on the Makefile text because
    running make here would recurse into the whole suite."""
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\ntest: lint\n")[1].split("\n\n")[0]
    lines = [l.strip() for l in recipe.splitlines()]
    assert lines == ["$(PYTHON) -m pytest tests/",
                     "$(PYTHON) scripts/hooks/mark_green.py"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v -k "mark_green or recipe"`
Expected: 2 failed (`No such file` for the script; recipe has one line)

- [ ] **Step 3: Write `mark_green.py`**

Create `scripts/hooks/mark_green.py`:

```python
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
```

- [ ] **Step 4: Edit the Makefile `test` target**

Replace lines 38-39 of `Makefile`:

```makefile
# Full offline suite: no network, no API keys. Must pass before every commit.
# On green, record the tree id so the verification hooks (scripts/hooks/) know
# this exact tree passed — a hand-run `make test` is a green run for them too.
test: lint
	$(PYTHON) -m pytest tests/
	$(PYTHON) scripts/hooks/mark_green.py
```

(The existing comment line "Full offline suite..." stays; the two new comment lines go under it.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py tests/test_ops_units.py -v`
Expected: all passed (test_ops_units confirms the Makefile comment broke no rule)

- [ ] **Step 6: Run the full suite, confirm the marker, commit**

Run: `make test && cat "$(git rev-parse --absolute-git-dir)/fund-green-tree"`
Expected: green, then one 40-hex tree id

```bash
git add scripts/hooks/mark_green.py Makefile tests/test_verification_hooks.py
git commit -m "feat: make test records the green tree id"
```

### Task 3: Commit gate (`commit_gate.py`)

**Files:**
- Create: `scripts/hooks/commit_gate.py`
- Test: `tests/test_verification_hooks.py`

**Interfaces:**
- Consumes: `_tree.is_green`, `_tree.toplevel`, `_tree.tree_id`, `_tree.write_marker`.
- Produces: `gated(command: str) -> bool`; `main(stdin=sys.stdin, suite=("make", "test")) -> int`. Output on deny is the Claude Code `PreToolUse` JSON on stdout; exit code is always 0.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verification_hooks.py`:

```python
# --- commit_gate ---------------------------------------------------------------

import commit_gate  # noqa: E402

OK = (sys.executable, "-c", "pass")
RED = (sys.executable, "-c", "print('boom: 1 failed'); raise SystemExit(1)")


def _payload(cwd: Path, command: str | None = None, **extra) -> io.StringIO:
    data: dict = {"cwd": str(cwd), **extra}
    if command is not None:
        data.update(tool_name="Bash", tool_input={"command": command})
    return io.StringIO(json.dumps(data))


@pytest.mark.parametrize("command,expected", [
    ("git commit -m 'x'", True),
    ("git -C /tmp/wt commit -q -m x", True),
    ("gh pr create --title t --body b", True),
    ("git add -A && git commit -m 'x'", True),
    ("make test; git commit -m x", True),
    ("git status", False),
    ("git log --oneline | head", False),
    ("echo 'git commit'", False),
    ("gh pr list", False),
    ("git commitish", False),
])
def test_gated_matches_commit_and_pr_create_only(command: str, expected: bool):
    assert commit_gate.gated(command) is expected


def test_commit_gate_passes_through_other_commands(repo: Path, capsys):
    assert commit_gate.main(_payload(repo, "git status"), suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_passes_through_other_tools(repo: Path, capsys):
    stdin = io.StringIO(json.dumps({"cwd": str(repo), "tool_name": "Read",
                                    "tool_input": {"file_path": "x"}}))
    assert commit_gate.main(stdin, suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_skips_the_suite_when_the_marker_matches(repo: Path, capsys):
    _tree.write_marker(repo, _tree.tree_id(repo))
    assert commit_gate.main(_payload(repo, "git commit -m x"), suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_runs_the_suite_and_marks_green(repo: Path, capsys):
    (repo / "a.py").write_text("x = 2\n")
    assert _tree.read_marker(repo) is None
    assert commit_gate.main(_payload(repo, "git commit -m x"), suite=OK) == 0
    assert capsys.readouterr().out == ""
    assert _tree.read_marker(repo) == _tree.tree_id(repo)


def test_commit_gate_denies_with_the_tail_on_red(repo: Path, capsys):
    (repo / "a.py").write_text("x = 2\n")
    assert commit_gate.main(_payload(repo, "git commit -m x"), suite=RED) == 0
    out = json.loads(capsys.readouterr().out)
    hook = out["hookSpecificOutput"]
    assert hook["hookEventName"] == "PreToolUse"
    assert hook["permissionDecision"] == "deny"
    assert "boom: 1 failed" in hook["permissionDecisionReason"]
    assert _tree.read_marker(repo) is None


def test_commit_gate_resolves_cwd_from_a_subdirectory(repo: Path, capsys):
    sub = repo / "pkg"
    sub.mkdir()
    assert commit_gate.main(_payload(sub, "git commit -m x"), suite=OK) == 0
    assert _tree.read_marker(repo) == _tree.tree_id(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: collection error `No module named 'commit_gate'`

- [ ] **Step 3: Write `commit_gate.py`**

Create `scripts/hooks/commit_gate.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: all passed (7 from Tasks 1-2 plus 16 here)

- [ ] **Step 5: Confirm it runs under the system interpreter, then the suite, then commit**

Run: `echo '{"cwd":"'$PWD'","tool_name":"Bash","tool_input":{"command":"git status"}}' | /usr/bin/python3 scripts/hooks/commit_gate.py; echo "exit $?"`
Expected: no output, `exit 0`

Run: `make test`
Expected: green

```bash
git add scripts/hooks/commit_gate.py tests/test_verification_hooks.py
git commit -m "feat: commit gate hook runs make test before git commit and gh pr create"
```

### Task 4: Stop gate (`stop_gate.py`)

**Files:**
- Create: `scripts/hooks/stop_gate.py`
- Test: `tests/test_verification_hooks.py`

**Interfaces:**
- Consumes: `_tree.dirty_tracked`, `_tree.is_green`, `_tree.toplevel`.
- Produces: `main(stdin=sys.stdin) -> int`; block is `{"decision": "block", "reason": ...}` on stdout; exit always 0; never runs any suite.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verification_hooks.py`:

```python
# --- stop_gate -------------------------------------------------------------------

import stop_gate  # noqa: E402


def test_stop_gate_allows_when_already_continuing(repo: Path, capsys):
    (repo / "a.py").write_text("x = 2\n")
    assert stop_gate.main(_payload(repo, stop_hook_active=True)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_allows_a_clean_tree(repo: Path, capsys):
    assert stop_gate.main(_payload(repo, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_allows_untracked_only_changes(repo: Path, capsys):
    (repo / "b.py").write_text("y = 2\n")
    assert stop_gate.main(_payload(repo, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_allows_a_dirty_tree_that_is_green(repo: Path, capsys):
    (repo / "a.py").write_text("x = 2\n")
    _tree.write_marker(repo, _tree.tree_id(repo))
    assert stop_gate.main(_payload(repo, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_blocks_a_dirty_stale_tree_once(repo: Path, capsys):
    (repo / "a.py").write_text("x = 2\n")
    assert stop_gate.main(_payload(repo, stop_hook_active=False)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "make test" in out["reason"]


def test_stop_gate_never_runs_a_suite(repo: Path):
    """There is no suite parameter to inject: the gate has no subprocess
    other than git. Pinned on the source so a future 'helpful' run cannot
    sneak in."""
    src = (HOOKS / "stop_gate.py").read_text()
    assert "subprocess" not in src
    assert "make" not in src.replace("make test", "")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: collection error `No module named 'stop_gate'`

- [ ] **Step 3: Write `stop_gate.py`**

Create `scripts/hooks/stop_gate.py`:

```python
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

from _tree import dirty_tracked, is_green, toplevel  # noqa: E402

REASON = ("stop gate: tracked files changed since the last green `make test`"
          " on this tree. Run `make test` and report the result before"
          " ending the turn.")


def main(stdin=sys.stdin) -> int:
    payload = json.load(stdin)
    if payload.get("stop_hook_active"):
        return 0
    cwd = toplevel(Path(payload.get("cwd") or os.getcwd()))
    if not dirty_tracked(cwd) or is_green(cwd):
        return 0
    print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: all passed

- [ ] **Step 5: Full suite and commit**

Run: `make test`
Expected: green

```bash
git add scripts/hooks/stop_gate.py tests/test_verification_hooks.py
git commit -m "feat: stop gate hook blocks once on a dirty tree without a green make test"
```

### Task 5: Commit `.claude/settings.json` and the CLAUDE.md sentence

**Files:**
- Create (tracked): `.claude/settings.json`
- Modify: `CLAUDE.md` (Conventions, the hooks bullet)
- Test: `tests/test_verification_hooks.py`

Note: in the `verify-hooks` worktree this path does not exist yet (the main checkout's copy is untracked and never reached worktrees). You are creating the committed one. Do not copy the main checkout's `allow` or `additionalDirectories` blocks; they are personal and move to `settings.local.json` in Task 6.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_verification_hooks.py`:

```python
# --- the committed settings file ------------------------------------------------

def test_settings_file_registers_both_hooks_and_carries_no_personal_allow():
    """`.claude/settings.json` is tracked so the hooks reach every worktree.
    It carries repo policy (hooks, deny, ask) and nothing personal: an
    absolute path in an allow entry would be one machine's approvals handed
    to everyone (see .gitignore)."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    hooks = settings["hooks"]
    pre = [h for m in hooks["PreToolUse"] if m["matcher"] == "Bash"
           for h in m["hooks"]]
    assert any("scripts/hooks/commit_gate.py" in h["command"] for h in pre)
    stop = [h for m in hooks["Stop"] for h in m["hooks"]]
    assert any("scripts/hooks/stop_gate.py" in h["command"] for h in stop)
    for h in pre + stop:
        assert "$CLAUDE_PROJECT_DIR" in h["command"]
    perms = settings["permissions"]
    assert "deny" in perms and "ask" in perms
    assert "allow" not in perms
    assert "additionalDirectories" not in perms
    assert "/Users/" not in json.dumps(settings)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v -k settings_file`
Expected: FAIL `FileNotFoundError`

- [ ] **Step 3: Write `.claude/settings.json`**

Create `.claude/settings.json` with exactly this content. The `deny` and `ask` lists are the main checkout's current repo-policy lists with three entries changed: the two `fund-wt/**/.env` denies are dropped because this file now lives in every worktree and `Read(./.env)` covers each one, and the `.ssh` deny uses `~/` instead of one machine's home path.

```json
{
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(~/.ssh/**)",
      "Bash(git push --force*)",
      "Bash(git push -f *)",
      "Bash(git push --force-with-lease*)",
      "Bash(git push origin :*)",
      "Bash(git push --delete *)",
      "Bash(git push origin --delete *)",
      "Bash(git reset --hard*)",
      "Bash(git checkout -- *)",
      "Bash(git checkout .*)",
      "Bash(git restore *)",
      "Bash(git clean *)",
      "Bash(git branch -D *)",
      "Bash(git branch -f *)",
      "Bash(git branch --force *)",
      "Bash(git worktree remove --force*)",
      "Bash(git worktree prune*)",
      "Bash(git filter-branch*)",
      "Bash(git update-ref -d *)",
      "Bash(rm -rf *)",
      "Bash(rm -r *)",
      "Bash(rm -fr *)"
    ],
    "ask": [
      "Bash(ssh *)",
      "Bash(scp *)",
      "Bash(rsync *)",
      "Bash(systemctl *)",
      "Bash(curl *)",
      "Bash(gh api *)",
      "Bash(gh repo *)",
      "Bash(make live-paper*)",
      "Bash(make deploy*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR\"/scripts/hooks/commit_gate.py",
            "timeout": 300
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR\"/scripts/hooks/stop_gate.py",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Edit CLAUDE.md**

In `CLAUDE.md`, Conventions, find:

```
- All hooks (`PreToolUse` order gate, cost recording) are defined in `agents/runtime.py` only.
```

Replace with:

```
- All SDK seat hooks (`PreToolUse` order gate, cost recording) are defined in `agents/runtime.py` only. Claude Code harness hooks (`.claude/settings.json`, committed; scripts in `scripts/hooks/`) are a separate mechanism that gates the *coding session's* commits and turn ends; they never touch a seat.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_verification_hooks.py -v`
Expected: all passed

- [ ] **Step 6: Exercise the real hooks once in this worktree**

Start a fresh Claude Code session in the worktree (`claude` from `../fund-wt/verify-hooks`), edit any tracked file trivially, and ask it to end the turn without running tests. Expected: one block with the stop-gate reason, then it runs `make test`, then the turn ends. Then ask it to `git commit` the trivial change: expected no suite re-run (marker matches). Revert the trivial change afterwards (`git checkout` of a single file is in the deny list; use `git stash push -u -m verify-hooks-trial` then `git stash drop` on that entry by its SHA, as the worktree notice describes).

- [ ] **Step 7: Full suite and commit**

Run: `make test`
Expected: green

```bash
git add .claude/settings.json CLAUDE.md tests/test_verification_hooks.py
git commit -m "feat: commit the verification hooks in .claude/settings.json; CLAUDE.md names the two hook mechanisms"
```

- [ ] **Step 8: Open PR 1**

```bash
git push -u origin verify-hooks
gh pr create --title "feat: enforce make test with commit and stop hooks" --body "Implements piece 2 of docs/superpowers/specs/2026-10-13-agent-self-verification-design.md. Plan: docs/superpowers/plans/2026-10-13-agent-self-verification.md, Tasks 1-5. Task 6 (main-checkout migration) is Benjamin's, at merge time."
```

### Task 6: Main-checkout migration (Benjamin, at merge time)

**Files (main checkout `~/Developer/fund` only):**
- Modify: `.claude/settings.local.json` (ignored, personal)
- Delete: the untracked `.claude/settings.json`

**Why this is separate.** The main checkout holds an untracked `.claude/settings.json` with 112 `allow` entries and 5 `additionalDirectories`. When master lands with a tracked file at that path, git refuses to update the working tree over an untracked file. And if the committed hooks arrived in that file *before* `scripts/hooks/` exists in that working tree, every Bash call would be blocked (a missing script exits 2, which `PreToolUse` reads as deny). So: merge PR 1, then do this, then update the main checkout's working tree.

- [ ] **Step 1: Merge PR 1 to master.**

- [ ] **Step 2: Move the personal blocks into the local file**

Run from `~/Developer/fund`:

```bash
python3 - <<'EOF'
import json, pathlib
root = pathlib.Path(".claude")
shared = json.loads((root / "settings.json").read_text())
local_path = root / "settings.local.json"
local = json.loads(local_path.read_text())
lp = local.setdefault("permissions", {})
for key in ("allow", "additionalDirectories"):
    merged = list(dict.fromkeys(lp.get(key, []) + shared["permissions"].get(key, [])))
    lp[key] = merged
local_path.write_text(json.dumps(local, indent=2) + "\n")
print({k: len(v) for k, v in lp.items()})
EOF
```

Expected: a dict showing `allow` around 200 entries and `additionalDirectories` around 30.

- [ ] **Step 3: Remove the untracked file and update the working tree**

```bash
rm .claude/settings.json
git status --short .claude/      # expect nothing listed for settings.json
```

Then bring the main checkout's working tree up to master by whatever means you use there (never `git checkout` while peers occupy it). After that: `ls scripts/hooks/ && git ls-files .claude/settings.json` shows the three scripts and the tracked file.

- [ ] **Step 4: Verify in a fresh session**

Start a new Claude Code session in `~/Developer/fund`; `git status` and `make test` must still be pre-approved (from the local allow list) and a trivial dirty-tree stop must block once.

---

## PR 2 — Typecheck ratchet (branch `typecheck-ratchet`)

**File structure**

| Path | Responsibility |
|---|---|
| `pyproject.toml` | `dev` extra gains `pyright[nodejs]`; new `[tool.pyright]` section. |
| `requirements.lock` | Regenerated by `make deps-relock`. |
| `scripts/check_typing.py` | Runs pyright, compares per-file counts to the baseline, writes the baseline on request. |
| `scripts/typing_baseline.json` | Committed per-file error counts. Only shrinks. |
| `tests/test_check_typing.py` | Compare logic, main() outcomes, one real pyright run on a tmp package. |
| `Makefile` | `lint` gains the checker. |
| `.github/workflows/ci.yml` | Runs alert-codes lint and the checker. |
| spec | One correction: `scripts/` is already a tamper-guard protected dir. |

### Task 7: Pin pyright and configure it

**Files:**
- Modify: `pyproject.toml:18` (dev extra) and append `[tool.pyright]`
- Modify: `requirements.lock` (via `make deps-relock`)

**Interfaces:**
- Produces: `python -m pyright` available in `.venv`; `[tool.pyright].include` is the single list of checked packages.

- [ ] **Step 1: Edit `pyproject.toml`**

Change line 18:

```toml
dev = ["pytest>=8", "pyright[nodejs]>=1.1.400,<2"]
```

Append at the end of the file:

```toml
[tool.pyright]
# scripts/check_typing.py runs pyright over exactly this list and ratchets
# the per-file error counts in scripts/typing_baseline.json (only down).
include = ["agents", "orchestrator", "gate", "state", "stratgate", "calibration",
           "fundbt", "scripts", "evals", "market", "slackkit", "devcheck"]
typeCheckingMode = "standard"
```

- [ ] **Step 2: Relock and install**

Run: `make deps-relock && make deps`
Expected: `requirements.lock` now contains `pyright==1.1.4xx` and `nodejs-wheel-binaries==...`; `git diff --stat requirements.lock` shows a small change.

- [ ] **Step 3: Confirm pyright runs from the venv without fetching node**

Run: `.venv/bin/python3 -m pyright --version`
Expected: `pyright 1.1.4xx` with no "downloading node" line.

Run: `.venv/bin/python3 -m pyright --outputjson --pythonpath .venv/bin/python3 | python3 -c "import json,sys; print(json.load(sys.stdin)['summary'])"`
Expected: a summary dict with `errorCount` near 114 and `filesAnalyzed` near 103. Note the exact number; Task 9 records it.

- [ ] **Step 4: Full suite (the lock test must agree) and commit**

Run: `make test`
Expected: green; `tests/test_deps_lock.py` passes against the new lock.

```bash
git add pyproject.toml requirements.lock
git commit -m "chore: pin pyright[nodejs] in the dev extra and lock; [tool.pyright] names the checked packages"
```

### Task 8: `check_typing.py` compare and main (no pyright in these tests)

**Files:**
- Create: `scripts/check_typing.py`
- Create: `tests/test_check_typing.py`

**Interfaces:**
- Produces: `run_pyright(root: Path = ROOT, python: str = sys.executable) -> dict[str, list[str]]` (file → diagnostic lines; only files with errors); `counts(diags) -> dict[str, int]`; `compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]`; `main(argv, run=run_pyright, baseline_path=BASELINE) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_typing.py`:

```python
"""The typecheck ratchet (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 1). The pyright run is
injected in every test but one, so the ratchet's rules are pinned without
spending two seconds of pyright each."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.check_typing as ct

ROOT = Path(__file__).resolve().parents[1]


def test_counts_collapses_diagnostics_to_per_file_totals():
    diags = {"a.py": ["1: x", "7: y"], "b/c.py": ["3: z"]}
    assert ct.counts(diags) == {"a.py": 2, "b/c.py": 1}


def test_compare_reports_regressions_and_improvements_separately():
    current = {"a.py": 3, "b.py": 1, "new.py": 1}
    baseline = {"a.py": 2, "b.py": 2, "gone.py": 1}
    regressions, improvements = ct.compare(current, baseline)
    assert regressions == ["a.py: 3 error(s), baseline 2",
                           "new.py: 1 error(s), baseline 0"]
    assert improvements == ["b.py: 1 error(s), baseline 2",
                            "gone.py: 0 error(s), baseline 1"]


def test_compare_is_quiet_when_equal():
    assert ct.compare({"a.py": 1}, {"a.py": 1}) == ([], [])


def _baseline(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "typing_baseline.json"
    p.write_text(json.dumps(data))
    return p


def test_main_fails_on_a_new_error_and_prints_the_diagnostic(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 1})
    run = lambda: {"a.py": ["1: old", "9: brand new error"]}
    assert ct.main([], run=run, baseline_path=base) == 1
    err = capsys.readouterr().err
    assert "a.py: 2 error(s), baseline 1" in err
    assert "9: brand new error" in err


def test_main_fails_when_the_baseline_was_lowered_below_reality(tmp_path):
    base = _baseline(tmp_path, {"a.py": 0})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 1


def test_main_passes_and_nags_when_a_file_improved(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 2})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 0
    out = capsys.readouterr().out
    assert "baseline can shrink" in out
    assert "a.py: 1 error(s), baseline 2" in out


def test_main_passes_quietly_at_baseline(tmp_path, capsys):
    base = _baseline(tmp_path, {"a.py": 1})
    assert ct.main([], run=lambda: {"a.py": ["1: x"]}, baseline_path=base) == 0
    assert "baseline can shrink" not in capsys.readouterr().out


def test_write_baseline_records_sorted_counts(tmp_path):
    base = tmp_path / "typing_baseline.json"
    run = lambda: {"z.py": ["1: a"], "a.py": ["1: b", "2: c"]}
    assert ct.main(["--write-baseline"], run=run, baseline_path=base) == 0
    assert base.read_text() == '{\n  "a.py": 2,\n  "z.py": 1\n}\n'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_check_typing.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.check_typing'`

- [ ] **Step 3: Write `check_typing.py`**

Create `scripts/check_typing.py`:

```python
#!/usr/bin/env python3
"""Typecheck ratchet, run by `make lint` (CLAUDE.md Commands) and CI.

Runs pyright over the packages in pyproject's [tool.pyright] include list and
compares per-file error counts to scripts/typing_baseline.json. Any file
above its baseline, or any file with errors the baseline does not list,
fails with the offending diagnostics. Counts below baseline pass and print a
reminder to commit the lower file. The baseline only moves down, by human
commit: `--write-baseline` is the one writer, and scripts/ is a tamper-guard
protected dir so the unattended loop cannot raise it.

The interpreter is handed to pyright explicitly, so one invocation resolves
the installed packages on a laptop (.venv), in CI (global pip) and on the
droplet.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "typing_baseline.json"


def run_pyright(root: Path = ROOT, python: str = sys.executable) -> dict[str, list[str]]:
    """Error diagnostics per file, repo-relative posix paths. Files with no
    errors are absent. Raises if pyright produced no report at all."""
    proc = subprocess.run(
        [python, "-m", "pyright", "--outputjson", "--pythonpath", python],
        cwd=str(root), capture_output=True, text=True)
    if not proc.stdout.strip():
        raise RuntimeError(
            f"pyright produced no JSON (exit {proc.returncode}):\n{proc.stderr}")
    report = json.loads(proc.stdout)
    diags: dict[str, list[str]] = {}
    for d in report["generalDiagnostics"]:
        if d["severity"] != "error":
            continue
        rel = Path(d["file"]).resolve().relative_to(root.resolve()).as_posix()
        line = d["range"]["start"]["line"] + 1
        diags.setdefault(rel, []).append(f"{line}: {d['message'].splitlines()[0]}")
    return diags


def counts(diags: dict[str, list[str]]) -> dict[str, int]:
    return {path: len(lines) for path, lines in diags.items()}


def compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """(regressions, improvements), one line per file, sorted by path."""
    regressions: list[str] = []
    improvements: list[str] = []
    for path in sorted(set(current) | set(baseline)):
        now, was = current.get(path, 0), baseline.get(path, 0)
        line = f"{path}: {now} error(s), baseline {was}"
        if now > was:
            regressions.append(line)
        elif now < was:
            improvements.append(line)
    return regressions, improvements


def main(argv: list[str], run=run_pyright, baseline_path: Path = BASELINE) -> int:
    diags = run()
    current = counts(diags)
    if "--write-baseline" in argv:
        baseline_path.write_text(
            json.dumps(dict(sorted(current.items())), indent=2) + "\n")
        print(f"check_typing: wrote {baseline_path.name}: "
              f"{sum(current.values())} error(s) in {len(current)} file(s)")
        return 0
    baseline = json.loads(baseline_path.read_text())
    regressions, improvements = compare(current, baseline)
    if regressions:
        print("check_typing: type errors above baseline:", file=sys.stderr)
        for line in regressions:
            print("  " + line, file=sys.stderr)
            for diag in diags.get(line.split(":")[0], []):
                print("      " + diag, file=sys.stderr)
        return 1
    if improvements:
        print("check_typing: baseline can shrink — commit the regenerated file"
              " (`.venv/bin/python3 scripts/check_typing.py --write-baseline`):")
        for line in improvements:
            print("  " + line)
    print(f"check_typing: ok ({sum(current.values())} known error(s),"
          f" baseline {sum(baseline.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_check_typing.py -v`
Expected: 9 passed

- [ ] **Step 5: Full suite and commit**

Run: `make test`
Expected: green (the checker is not wired into lint yet)

```bash
git add scripts/check_typing.py tests/test_check_typing.py
git commit -m "feat: check_typing ratchet compares pyright per-file errors to a committed baseline"
```

### Task 9: Real pyright run, baseline, Makefile, CI, spec correction

**Files:**
- Create: `scripts/typing_baseline.json`
- Modify: `Makefile:43-46` (`lint` target)
- Modify: `.github/workflows/ci.yml:23-26`
- Modify: `tests/test_check_typing.py`
- Modify: `docs/superpowers/specs/2026-10-13-agent-self-verification-design.md` (one bullet)

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_check_typing.py`:

```python
def test_run_pyright_finds_a_real_error_in_a_tmp_package(tmp_path):
    """One real pyright run, on a package of one file, so the JSON parsing
    and the --pythonpath handoff are exercised against the pinned binary."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ninclude = ["pkg"]\ntypeCheckingMode = "standard"\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_text('x: int = "not an int"\n')
    (tmp_path / "pkg" / "good.py").write_text("y: int = 1\n")
    diags = ct.run_pyright(root=tmp_path)
    assert list(diags) == ["pkg/bad.py"]
    assert len(diags["pkg/bad.py"]) == 1
    assert diags["pkg/bad.py"][0].startswith("1: ")


def test_committed_baseline_matches_the_tree():
    """The ratchet's own precondition: the baseline is never above reality.
    Below is allowed (someone fixed errors and forgot to shrink it)."""
    baseline = json.loads(ct.BASELINE.read_text())
    current = ct.counts(ct.run_pyright())
    regressions, _ = ct.compare(current, baseline)
    assert regressions == []
```

- [ ] **Step 2: Run the tests to verify the second fails**

Run: `.venv/bin/python3 -m pytest tests/test_check_typing.py -v -k "real_error or committed_baseline"`
Expected: first passes, second fails with `FileNotFoundError` for the baseline.

- [ ] **Step 3: Write the baseline**

Run: `.venv/bin/python3 scripts/check_typing.py --write-baseline && head -5 scripts/typing_baseline.json`
Expected: `check_typing: wrote typing_baseline.json: N error(s) in M file(s)` with N near 114, then sorted JSON.

- [ ] **Step 4: Wire into the Makefile**

Replace the `lint` target (Makefile lines 43-46) with:

```makefile
# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
# Alert-code lint: every alert carries a stable code (docs/agents/devops.md).
# Typecheck ratchet: pyright per-file errors never exceed scripts/typing_baseline.json.
lint: deps
	$(PYTHON) scripts/check_purity.py
	$(PYTHON) scripts/check_alert_codes.py
	$(PYTHON) scripts/check_typing.py
```

- [ ] **Step 5: Wire into CI**

In `.github/workflows/ci.yml`, after the purity-lint step (line 23-24) and before the pytest step, insert:

```yaml
      - name: Alert-code lint (every alert carries a stable code — same list as make lint)
        run: python3 scripts/check_alert_codes.py
      - name: Typecheck ratchet (pyright per-file errors vs scripts/typing_baseline.json)
        run: python3 scripts/check_typing.py
```

- [ ] **Step 6: Correct the spec**

In `docs/superpowers/specs/2026-10-13-agent-self-verification-design.md`, Piece 1, the "Only down, by human commit" bullet, replace:

```
the file is
  added to `devloop/tamper_guard.py`'s `PROTECTED_FILES`, so the unattended
  loop cannot raise it
```

with:

```
the file lives
  under `scripts/`, already in `devloop/tamper_guard.py`'s `PROTECTED_DIRS`,
  so the unattended loop cannot raise it (verified at plan time; no guard
  edit needed)
```

- [ ] **Step 7: Run the tests, then the full suite with timing**

Run: `.venv/bin/python3 -m pytest tests/test_check_typing.py tests/test_ops_units.py -v`
Expected: all passed

Run: `time make test`
Expected: green, with a `check_typing: ok (N known error(s), baseline N)` line, total under 35 s.

- [ ] **Step 8: Prove the ratchet bites**

Run:
```bash
printf 'def _ratchet_probe() -> int:\n    return "no"\n' > state/_ratchet_probe.py
.venv/bin/python3 scripts/check_typing.py; echo "exit $?"
rm state/_ratchet_probe.py
git status --short state/
```
Expected: `exit 1` with `state/_ratchet_probe.py: 1 error(s), baseline 0` and the diagnostic line `2: Type "Literal['no']" is not assignable to return type "int"`; the final `git status` prints nothing.

- [ ] **Step 9: Commit and open PR 2**

```bash
git add scripts/typing_baseline.json Makefile .github/workflows/ci.yml tests/test_check_typing.py docs/superpowers/specs/2026-10-13-agent-self-verification-design.md
git commit -m "feat: make lint and CI run the pyright ratchet against a committed baseline"
git push -u origin typecheck-ratchet
gh pr create --title "feat: pyright ratchet in make lint and CI" --body "Implements piece 1 of docs/superpowers/specs/2026-10-13-agent-self-verification-design.md (Tasks 7-9 of the plan). Baseline: N errors; only shrinks. Also closes the make lint / CI drift: check_alert_codes.py now runs in CI."
```

**After PR 2 merges (separate small PRs, not in this plan):** pay down `gate/tickets.py` (2), `orchestrator/daily.py` (3), `orchestrator/reconcile.py:213` (the unguarded `cur['qty']` after the `cur is None` branch at line 171: fix with a test that drives the missing-row + partial-fill path), each shrinking the baseline in the same commit.

---

## PR 3 — `make sim-report` (branch `sim-report`)

**File structure**

| Path | Responsibility |
|---|---|
| `tests/simday.py` | The sim composition (`sim_day`, `SimResult`, fixtures, constants) and the named `SCENARIOS` table + `run_scenario`. Moved out of the test module. |
| `tests/test_sim_day.py` | The eight assertions, now each built from `run_scenario`. |
| `tests/test_audit_day.py`, `tests/test_score_day.py`, `tests/test_dispatch.py`, `scripts/replay_day.py` | Import from `tests.simday`. |
| `scripts/sim_report.py` | Renders one scenario: Slack transcript, DB rows, scorecard. |
| `tests/test_sim_report.py` | Golden report content, unknown scenario, determinism. |
| `Makefile`, `CLAUDE.md` | `sim-report` target and its Commands line. |

### Task 10: Move the sim composition into `tests/simday.py` with a scenario table

**Files:**
- Create: `tests/simday.py`
- Modify: `tests/test_sim_day.py` (lines 16-181 removed; each test's `sim_day(...)` call replaced)
- Modify: `tests/test_audit_day.py:17`, `tests/test_score_day.py:24`, `tests/test_dispatch.py:231`, `scripts/replay_day.py:48`

**Interfaces:**
- Produces: `tests.simday.SCENARIOS: dict[str, Callable[[], dict]]` (name → factory of `sim_day` keyword arguments; a factory, because `sim_day` mutates `market` under `feed_break` and `_id_sequence` is one-shot); `run_scenario(name: str, tmp_path: Path) -> SimResult`; plus everything that moves: `sim_day`, `golden_day`, `SimResult`, `_nvda`, `_msft`, `_id_sequence`, `RECORDINGS`, `START`, `TID`, `TID2`, `PRICES`, `FILL_PRICES`, `SIM_TURN_COST_USD`, `STAGES`.

- [ ] **Step 1: Write the failing test**

Append to the bottom of `tests/test_sim_day.py`:

```python
# --- the scenario table --------------------------------------------------------

def test_every_scenario_runs_and_completes_or_alerts(tmp_path):
    """Every name in SCENARIOS is a day the sim can run. Each test above
    builds its day from this table, so a scenario sim-report can render is a
    scenario a test pins — the table cannot drift from the assertions."""
    from tests.simday import SCENARIOS, run_scenario
    assert set(SCENARIOS) == {
        "golden", "all_hold", "mixed", "gate_reject", "circuit_breaker",
        "two_orders", "pm_brief", "stop_expires", "hold_only_ticker"}
    for name in SCENARIOS:
        sim = run_scenario(name, tmp_path / name)
        assert sim.run_date, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_sim_day.py -v -k every_scenario`
Expected: `ModuleNotFoundError: No module named 'tests.simday'`

- [ ] **Step 3: Create `tests/simday.py`**

Move lines 18-181 of `tests/test_sim_day.py` (the imports after `from __future__` through `golden_day`) plus `_id_sequence` (lines 241-247) into the new file verbatim, under this docstring and its own `from __future__` line, then append the scenario table:

```python
"""The sim-day composition: one simulated trading day through the REAL gate,
tools, hooks, DB, outbox and fill-poll, against RECORDED LLM decisions — the
LLM is the only thing replaced (acceptance §0). No network, no API keys.

Shared by tests/test_sim_day.py (the assertions), tests/test_audit_day.py,
tests/test_score_day.py, tests/test_dispatch.py, scripts/replay_day.py and
scripts/sim_report.py. SCENARIOS is the one list of day shapes: every entry
is pinned by a test and renderable by `make sim-report SCENARIO=<name>`.

Time is injected (SimClock) and so is the poll sleep: `_sleep` advances the
clock and ticks the broker, which is how an order placed as 'accepted'
becomes 'filled' during the reconciliation stage rather than by fiat."""

from __future__ import annotations

# ... lines 18-181 of the old tests/test_sim_day.py, verbatim ...
# ... then _id_sequence, verbatim ...


# --- scenarios ------------------------------------------------------------------

# name -> factory of sim_day keyword arguments. Factories, not dicts: sim_day
# mutates `market` under feed_break, and _id_sequence is a one-shot iterator.
SCENARIOS = {
    "golden": lambda: dict(market={"NVDA": _nvda()}),
    "all_hold": lambda: dict(market={"NVDA": _nvda()},
                             pm_recs=("mvf_pm_hold.jsonl",)),
    "mixed": lambda: dict(market={"NVDA": _nvda(), "MSFT": _msft()},
                          pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_hold.jsonl")),
    "gate_reject": lambda: dict(market={"NVDA": _nvda()},
                                feed_break={"NVDA": {"vol_60d": float("nan")}}),
    "circuit_breaker": lambda: dict(market={"NVDA": _nvda()},
                                    feed_break={"NVDA": {"daily_pnl_pct": -0.05}}),
    "two_orders": lambda: dict(market={"NVDA": _nvda(), "MSFT": _msft()},
                               pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_sell.jsonl"),
                               exec_recs=("mvf_exec_two.jsonl",),
                               id_factory=_id_sequence([TID, TID2])),
    "pm_brief": lambda: dict(market={"NVDA": _nvda()},
                             analyst_recs=("mvf_analyst_brief.jsonl",),
                             pm_recs=("mvf_pm_brief.jsonl",)),
    "stop_expires": lambda: dict(market={"NVDA": _nvda()},
                                 pm_recs=("mvf_pm_stop.jsonl",),
                                 exec_recs=("mvf_exec_stop_gtc.jsonl",),
                                 broker_mode="stop_expires_at_the_bell"),
    "hold_only_ticker": lambda: dict(
        market={"NVDA": _nvda(),
                "AAPL": _nvda(ticker="AAPL", price=232.0, cash=0.0, held_qty=0)},
        analyst_recs=("mvf_analyst_brief.jsonl",),
        pm_recs=("mvf_pm_brief.jsonl",)),
}


def run_scenario(name: str, tmp_path: Path) -> SimResult:
    """One named day. Unknown names raise KeyError, listing the valid ones."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; valid: {', '.join(sorted(SCENARIOS))}")
    tmp_path.mkdir(parents=True, exist_ok=True)
    return sim_day(tmp_path, **SCENARIOS[name]())
```

- [ ] **Step 4: Rewrite the head of `tests/test_sim_day.py`**

Replace lines 16-181 with:

```python
from __future__ import annotations

import json
import sqlite3

from tests.simday import (STAGES, TID, TID2, SimResult, _nvda, golden_day,
                          run_scenario, sim_day)
```

Keep `_id_sequence` out of this file (it moved). Keep the helpers `_checkpoints` … `_assert_day_completed` as they are, minus `_id_sequence`.

- [ ] **Step 5: Build each test's day from the table**

Replace the `sim = sim_day(...)` call in each test with the matching `run_scenario` (the `golden_day(tmp_path)` calls stay; `golden_day` is the `golden` scenario and remains the shared name for other test files):

| Test | Replacement |
|---|---|
| `test_all_hold_day` | `sim = run_scenario("all_hold", tmp_path)` |
| `test_mixed_day` | `sim = run_scenario("mixed", tmp_path)` |
| `test_gate_reject_day` | `sim = run_scenario("gate_reject", tmp_path)` |
| `test_gate_reject_day_circuit_breaker` | `sim = run_scenario("circuit_breaker", tmp_path)` |
| `test_two_orders_same_day` | `sim = run_scenario("two_orders", tmp_path)` |
| `test_pm_brief_carries_the_signal_and_the_budget_the_gate_enforces` | `sim = run_scenario("pm_brief", tmp_path)` |
| `test_the_stop_that_expired_at_the_bell_is_caught` | `sim = run_scenario("stop_expires", tmp_path)` |
| `test_a_hold_only_ticker_never_reaches_the_pipeline` | `sim = run_scenario("hold_only_ticker", tmp_path)` |

`test_the_pm_brief_renders_the_row_the_scoring_job_wrote` keeps its direct `sim_day(...)` call: it pre-seeds the database before the day runs, which the kwargs table cannot express.

Also change `golden_day`'s docstring in `tests/simday.py` to:

```python
def golden_day(tmp_path) -> SimResult:
    """The golden-day sim: SCENARIOS["golden"], by its shared name."""
    return run_scenario("golden", tmp_path)
```

(`run_scenario` is defined below `golden_day` in the file; that is fine at call time.)

- [ ] **Step 6: Update the other importers**

- `tests/test_audit_day.py:17`: `from tests.simday import _nvda, golden_day, sim_day`
- `tests/test_score_day.py:24`: `from tests.simday import golden_day`
- `tests/test_dispatch.py:231`: `from tests.simday import golden_day`
- `scripts/replay_day.py:48`: `from tests.simday import _nvda, sim_day              # noqa: E402`

- [ ] **Step 7: Run sim-day and the importers**

Run: `make sim-day && .venv/bin/python3 -m pytest tests/test_audit_day.py tests/test_score_day.py tests/test_dispatch.py tests/test_replay_day.py -q`
Expected: 9 passed in sim-day (8 + the table test); the others green.

- [ ] **Step 8: Full suite and commit**

Run: `make test`
Expected: green

```bash
git add tests/simday.py tests/test_sim_day.py tests/test_audit_day.py tests/test_score_day.py tests/test_dispatch.py scripts/replay_day.py
git commit -m "refactor: move the sim-day composition to tests/simday.py behind a named scenario table"
```

### Task 11: `scripts/sim_report.py`, Makefile target, CLAUDE.md line

**Files:**
- Create: `scripts/sim_report.py`
- Create: `tests/test_sim_report.py`
- Modify: `Makefile` (new target after `sim-day`, and the `.PHONY` list on line 3)
- Modify: `CLAUDE.md` (Commands)

**Interfaces:**
- Consumes: `tests.simday.SCENARIOS`, `tests.simday.run_scenario`, `SimResult.slack.posts`, `SimResult.conn`, `SimResult.run_date`; `scripts/score_day.py <db> <run_date>`.
- Produces: `render(name: str, tmp: Path) -> str`; `main(argv) -> int` (0 ok, 2 unknown scenario).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sim_report.py`:

```python
"""`make sim-report` (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 3): the agent's
'browser' for a system whose UI is Slack and a database."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.simday import TID

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sim_report.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_golden_report_shows_the_day_from_its_three_sources():
    out = _run("golden")
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert text.startswith("SIM-REPORT scenario=golden run_date=2026-07-06\n")
    assert "== slack ==" in text
    assert "-- #trade-log --" in text and "-- #pnl --" in text
    assert "bought *66 NVDA* at *$180.14*" in text
    for table in ("signals", "decisions", "tickets", "orders", "events"):
        assert f"== {table} (" in text, table
    assert TID in text
    assert "== scorecard ==" in text
    assert "SCORECARD 2026-07-06" in text


def test_default_scenario_is_golden():
    assert _run().stdout.startswith("SIM-REPORT scenario=golden ")


def test_unknown_scenario_exits_2_and_lists_the_valid_names():
    out = _run("nope")
    assert out.returncode == 2
    assert "unknown scenario 'nope'" in out.stderr
    for name in ("golden", "all_hold", "stop_expires"):
        assert name in out.stderr


def test_report_is_deterministic():
    assert _run("two_orders").stdout == _run("two_orders").stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_sim_report.py -v`
Expected: 4 failed (`No such file` for the script)

- [ ] **Step 3: Write `sim_report.py`**

Create `scripts/sim_report.py`:

```python
#!/usr/bin/env python3
"""Render one simulated trading day the way a human would read it.

    make sim-report SCENARIO=golden

Runs one of tests/simday.py's scenarios into a temporary directory — the same
composition `make sim-day` asserts on: injected clock, FakeSlack, recorded
LLM decisions, real gate/tools/DB — and prints, in fixed order:

  1. the Slack transcript as FakeSlack recorded it, by channel, in post order;
  2. the day's rows from signals, decisions, tickets, orders, events, in
     rowid order (alerts are events of kind 'alert');
  3. the scorecard, via scripts/score_day.py on the sim's database.

Every line comes from the DB, the fake Slack or the fake broker; nothing is
re-derived. stdout only, never a fixture: the suite already pins the numbers.
Run it on master and on a branch and diff the two — the scenarios are
deterministic, so the diff is the change.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))                              # from anywhere

from tests.simday import SCENARIOS, run_scenario           # noqa: E402

TABLES = ("signals", "decisions", "tickets", "orders", "events")
SCORE_DAY = ROOT / "scripts" / "score_day.py"


def render_slack(posts: dict[str, list[dict]]) -> list[str]:
    lines = ["== slack =="]
    for channel in sorted(posts):
        lines.append(f"-- {channel} --")
        for p in posts[channel]:
            who = f" [{p['username']}]" if p.get("username") else ""
            thread = f" (thread {p['thread_ts']})" if p.get("thread_ts") else ""
            lines.append(f"{p['ts']}{who}{thread}")
            lines.extend("    " + t for t in p["text"].splitlines())
    return lines


def render_tables(conn: sqlite3.Connection) -> list[str]:
    lines: list[str] = []
    for table in TABLES:
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        lines.append(f"== {table} ({len(rows)}) ==")
        for row in rows:
            lines.append("  " + "  ".join(
                f"{c}={row[c]!r}" for c in cols if row[c] is not None))
    return lines


def render(name: str, tmp: Path) -> str:
    sim = run_scenario(name, tmp)
    lines = [f"SIM-REPORT scenario={name} run_date={sim.run_date}"]
    lines += render_slack(sim.slack.posts)
    lines += render_tables(sim.conn)
    sim.conn.close()
    score = subprocess.run(
        [sys.executable, str(SCORE_DAY), str(tmp / "fund.sqlite"), sim.run_date],
        capture_output=True, text=True, check=True)
    lines.append("== scorecard ==")
    lines += score.stdout.rstrip("\n").splitlines()
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 and argv[1] else "golden"
    if name not in SCENARIOS:
        print(f"sim_report: unknown scenario {name!r}; valid:"
              f" {', '.join(sorted(SCENARIOS))}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        sys.stdout.write(render(name, Path(tmp)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_sim_report.py -v`
Expected: 4 passed

- [ ] **Step 5: Add the Makefile target and CLAUDE.md line**

In `Makefile`, add `sim-report` to the `.PHONY` list on line 3 (after `sim-day`), and insert after the `sim-day` target:

```makefile
# Render one sim-day scenario as a human reads it: the Slack transcript, the
# day's rows, the scorecard. stdout only, never a fixture — run it on master
# and on a branch and diff. Scenario names: tests/simday.py SCENARIOS.
sim-report: deps
	$(PYTHON) scripts/sim_report.py $(SCENARIO)
```

In `CLAUDE.md` Commands, after the `make sim-day` line, add:

```
- `make sim-report SCENARIO=<name>` — render one sim-day scenario (default `golden`) as Slack transcript, DB rows and scorecard. Diff master vs branch to *see* a change; the suite pins the numbers.
```

- [ ] **Step 6: Run the target and the Makefile tests**

Run: `make sim-report | head -20 && make sim-report SCENARIO=gate_reject | grep -c "blocked" && .venv/bin/python3 -m pytest tests/test_ops_units.py -q`
Expected: the golden header and Slack section; a count of at least 1; test_ops_units green.

- [ ] **Step 7: Full suite, commit, open PR 3**

Run: `make test`
Expected: green

```bash
git add scripts/sim_report.py tests/test_sim_report.py Makefile CLAUDE.md
git commit -m "feat: make sim-report renders a sim-day scenario as Slack transcript, rows and scorecard"
git push -u origin sim-report
gh pr create --title "feat: make sim-report" --body "Implements piece 3 of docs/superpowers/specs/2026-10-13-agent-self-verification-design.md (Tasks 10-11 of the plan). Moves the sim composition to tests/simday.py behind a named scenario table; every scenario is pinned by a test and renderable."
```

---

## Done when

- PR 1, 2, 3 merged in order; Task 6 done in the main checkout.
- A fresh session in any worktree: a dirty tracked tree blocks the stop once; `git commit` on a stale tree runs `make test` and is denied on red.
- `make test` prints `check_typing: ok` and CI runs the same lint list as the Makefile.
- `make sim-report SCENARIO=<any of the nine>` prints a deterministic day.
