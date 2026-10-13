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
