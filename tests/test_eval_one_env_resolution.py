"""_primary_checkout() must return None rather than raise, always — and
_resolve_env() must never reach outside the checkout it is given.

The git helper runs at collection of tests/test_eval_env_cannot_trade.py and
inside missing_env_message(); an exception escaping it crashes collection of
that module or replaces the no-env message with a traceback — a failure mode
with no relation to what the caller was doing. Its contract is "None when git
cannot say where the primary checkout is"; these pin the ways it can fail that
are NOT OSError, which an `except OSError` would let through.

The resolution tests pin the 2026-09-13 ruling on #135 (Option A): evals are
unsupported from a worktree, so resolution stops at this checkout and main()
names the primary checkout instead of reading its credentials.
"""

from __future__ import annotations

import subprocess
import types

import pytest

import scripts.eval_one
import scripts.eval_suite
from scripts.eval_one import (_primary_checkout, _resolve_env, main,
                              missing_env_message)


def _fake_run(monkeypatch, *, raises=None, returncode=0, stdout=""):
    """Point the helper's subprocess.run at a canned outcome."""
    def run(*args, **kwargs):
        if raises is not None:
            raise raises
        return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                     stderr="")
    monkeypatch.setattr("scripts.eval_one.subprocess.run", run)


def test_undecodable_git_output_returns_none(monkeypatch):
    """text=True decodes strict. A non-UTF-8 byte in the repo path raises
    UnicodeDecodeError, whose MRO is UnicodeError -> ValueError — neither
    OSError nor SubprocessError. Unreachable on APFS, reachable on Linux."""
    boom = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    _fake_run(monkeypatch, raises=boom)
    assert _primary_checkout() is None


def test_nul_bearing_path_returns_none(monkeypatch):
    """The other ValueError: .resolve() refuses an embedded null character.
    Exercises the real Path arithmetic, not a mocked exception."""
    _fake_run(monkeypatch, stdout="/tmp/re\0po/.git\n")
    assert _primary_checkout() is None


def test_missing_git_returns_none(monkeypatch):
    _fake_run(monkeypatch, raises=FileNotFoundError("git"))
    assert _primary_checkout() is None


def test_hung_git_returns_none(monkeypatch):
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired("git", 5))
    assert _primary_checkout() is None


@pytest.mark.parametrize("returncode, stdout", [(128, ""), (0, "  \n")])
def test_git_that_names_nothing_returns_none(monkeypatch, returncode, stdout):
    """Not a repo, or a success that carried no path."""
    _fake_run(monkeypatch, returncode=returncode, stdout=stdout)
    assert _primary_checkout() is None


def test_the_happy_path_still_derives_the_parent(monkeypatch, tmp_path):
    """The guard rails must not have eaten the behaviour they protect: the
    primary checkout is the git common dir's parent. tmp_path, not a literal
    — macOS resolves /tmp through a symlink to /private/tmp.

    Contract changed under the 2026-09-13 ruling on #135 (Option A): the
    helper names the checkout directory, not a file in it."""
    primary = tmp_path / "primary"
    _fake_run(monkeypatch, stdout=f"{primary / '.git'}\n")
    assert _primary_checkout() == primary


def test_resolution_never_leaves_its_own_checkout(monkeypatch, tmp_path):
    """A primary checkout holding both files must not be reachable from a
    worktree that holds neither: resolution stops at the root it is given."""
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / ".env").write_text("PLACEHOLDER=1\n")
    (primary / ".env.eval").write_text("PLACEHOLDER=1\n")
    _fake_run(monkeypatch, stdout=f"{primary / '.git'}\n")
    root = tmp_path / "worktree"
    root.mkdir()

    resolved = _resolve_env(root)
    assert resolved == root / ".env"
    assert not resolved.exists()

    (root / ".env").write_text("PLACEHOLDER=1\n")
    assert _resolve_env(root) == root / ".env"

    (root / ".env.eval").write_text("PLACEHOLDER=1\n")
    assert _resolve_env(root) == root / ".env.eval"


def test_env_is_resolved_inside_this_checkout():
    """The module-level wiring, not just the helper: ENV never leaves ROOT,
    and eval_suite shares the same binding."""
    assert scripts.eval_one.ENV == _resolve_env(scripts.eval_one.ROOT)
    assert scripts.eval_one.ENV.parent == scripts.eval_one.ROOT
    assert scripts.eval_suite.ENV is scripts.eval_one.ENV


def test_main_names_the_primary_checkout_when_no_env(monkeypatch, tmp_path,
                                                     capsys):
    primary = tmp_path / "primary"
    _fake_run(monkeypatch, stdout=f"{primary / '.git'}\n")
    monkeypatch.setattr("scripts.eval_one.ENV", tmp_path / "worktree" / ".env")

    assert main() == 2
    out, err = capsys.readouterr()
    assert str(primary) in err
    assert "worktree" in err
    assert "#135" in err
    assert out == ""


def test_main_still_fails_closed_when_git_names_nothing(monkeypatch, tmp_path,
                                                        capsys):
    _fake_run(monkeypatch, returncode=128)
    monkeypatch.setattr("scripts.eval_one.ENV", tmp_path / "worktree" / ".env")

    assert main() == 2
    out, err = capsys.readouterr()
    assert "primary checkout" in err
    assert out == ""


def test_main_does_not_blame_a_worktree_from_the_primary(monkeypatch, tmp_path,
                                                          capsys):
    """From the primary checkout itself a missing env file is the normal
    fresh-clone case, not a worktree problem."""
    _fake_run(monkeypatch, stdout=f"{scripts.eval_one.ROOT / '.git'}\n")
    monkeypatch.setattr("scripts.eval_one.ENV", tmp_path / ".env")

    assert main() == 2
    out, err = capsys.readouterr()
    # ROOT itself may live under .claude/worktrees/; judge the wording only.
    assert "worktree" not in err.replace(str(scripts.eval_one.ROOT), "")
    assert "primary checkout" in err
    assert out == ""


def test_eval_suite_prints_the_same_message(monkeypatch, tmp_path, capsys):
    """eval_suite holds its own binding of ENV, so patch it there."""
    primary = tmp_path / "primary"
    _fake_run(monkeypatch, stdout=f"{primary / '.git'}\n")
    monkeypatch.setattr("scripts.eval_suite.ENV",
                        tmp_path / "worktree" / ".env")

    assert scripts.eval_suite.main([]) == 2
    out, err = capsys.readouterr()
    assert missing_env_message() in err
    assert str(primary) in err
    assert out == ""
