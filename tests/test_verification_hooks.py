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
    ("git add a\ngit commit -m x", True),
    ("git commit -m x &", True),
    ("(git commit -m x)", True),
    ("FOO=1 git commit -m x", True),
    ("git -c user.name=t commit -m x", True),
    ("git --no-pager commit -m x", True),
    ("git --git-dir=/x/.git commit -m x", True),
    ("git commit -m 'a; b'", True),
    ("FOO=1 gh pr create --title t", True),
    ("git -c k=v status", False),
    ("git add a\ngit status", False),
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
