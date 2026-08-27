"""_primary_checkout_env() must return None rather than raise, always.

scripts/eval_one.py resolves ENV at module scope, so an exception escaping this
helper is an import-time crash that takes eval_one, scripts/eval_suite.py and
tests/test_eval_env_cannot_trade.py down with it — a failure mode with no
relation to what the caller was doing. Its contract is "None when git cannot
say where the primary checkout is"; these pin the ways it can fail that are
NOT OSError, which an `except OSError` would let through.
"""

from __future__ import annotations

import subprocess
import types

import pytest

from scripts.eval_one import _primary_checkout_env


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
    assert _primary_checkout_env() is None


def test_nul_bearing_path_returns_none(monkeypatch):
    """The other ValueError: .resolve() refuses an embedded null character.
    Exercises the real Path arithmetic, not a mocked exception."""
    _fake_run(monkeypatch, stdout="/tmp/re\0po/.git\n")
    assert _primary_checkout_env() is None


def test_missing_git_returns_none(monkeypatch):
    _fake_run(monkeypatch, raises=FileNotFoundError("git"))
    assert _primary_checkout_env() is None


def test_hung_git_returns_none(monkeypatch):
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired("git", 5))
    assert _primary_checkout_env() is None


@pytest.mark.parametrize("returncode, stdout", [(128, ""), (0, "  \n")])
def test_git_that_names_nothing_returns_none(monkeypatch, returncode, stdout):
    """Not a repo, or a success that carried no path."""
    _fake_run(monkeypatch, returncode=returncode, stdout=stdout)
    assert _primary_checkout_env() is None


def test_the_happy_path_still_derives_the_parent(monkeypatch, tmp_path):
    """The guard rails must not have eaten the behaviour they protect: the
    primary checkout is the git common dir's parent. tmp_path, not a literal
    — macOS resolves /tmp through a symlink to /private/tmp."""
    primary = tmp_path / "primary"
    _fake_run(monkeypatch, stdout=f"{primary / '.git'}\n")
    assert _primary_checkout_env() == primary / ".env"
