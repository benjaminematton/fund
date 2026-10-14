"""The commit and stop gates (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 2), against a throwaway
git repo. The suite command is injected everywhere, so nothing in this file
ever recurses into `make test`."""

from __future__ import annotations

import io
import json
import os
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
    (tmp_path / "scripts" / "hooks").mkdir(parents=True)
    (tmp_path / "scripts" / "hooks" / "mark_green.py").write_text("# marker\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


@pytest.fixture
def foreign_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n")
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


def test_marker_lives_in_a_linked_worktrees_own_git_dir(repo: Path):
    _git(repo, "worktree", "add", str(repo / "wt"), "-b", "wt")
    assert _tree.marker_path(repo / "wt") == (
        repo / ".git" / "worktrees" / "wt" / "fund-green-tree").resolve()
    _tree.write_marker(repo / "wt", "abc")
    assert _tree.read_marker(repo) is None


def test_git_error_carries_stderr(tmp_path: Path):
    with pytest.raises(_tree.GitError, match="not a git repository"):
        _tree.toplevel(tmp_path)


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


def test_commit_gate_passes_through_a_foreign_repo(foreign_repo: Path, capsys):
    assert commit_gate.main(_payload(foreign_repo, "git commit -m x"),
                            suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_passes_through_outside_git(tmp_path: Path, capsys):
    assert commit_gate.main(_payload(tmp_path, "git commit -m x"),
                            suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_gates_the_minus_c_target(repo: Path, capsys):
    wt = repo / "wt"
    _git(repo, "worktree", "add", str(wt), "-b", "wt")
    (wt / "a.py").write_text("x = 2\n")
    _tree.write_marker(repo, _tree.tree_id(repo))
    assert commit_gate.main(_payload(repo, f"git -C {wt} commit -m x"),
                            suite=OK) == 0
    assert capsys.readouterr().out == ""
    assert _tree.read_marker(wt) == _tree.tree_id(wt)
    # reverse: main stale, worktree green, so the suite never runs
    (repo / "a.py").write_text("x = 3\n")
    assert commit_gate.main(_payload(repo, f"git -C {wt} commit -m x"),
                            suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_commit_gate_marks_the_pre_suite_tree(repo: Path):
    (repo / "a.py").write_text("x = 2\n")
    touch = (sys.executable, "-c",
             "open('a.py','a').write('# touched by suite\\n')")
    assert commit_gate.main(_payload(repo, "git commit -m x"), suite=touch) == 0
    assert not _tree.is_green(repo)
    assert _tree.read_marker(repo) is not None


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
    assert "another session's" in out["reason"]


def test_stop_gate_passes_through_a_foreign_repo(foreign_repo: Path, capsys):
    (foreign_repo / "a.py").write_text("x = 2\n")
    assert stop_gate.main(_payload(foreign_repo, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_passes_through_outside_git(tmp_path: Path, capsys):
    assert stop_gate.main(_payload(tmp_path, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_passes_through_a_deleted_cwd(tmp_path: Path, capsys):
    gone = tmp_path / "gone"
    gone.mkdir()
    gone.rmdir()
    assert stop_gate.main(_payload(gone, stop_hook_active=False)) == 0
    assert capsys.readouterr().out == ""


def test_git_c_target_expands_tilde():
    assert commit_gate._git_c_target(["git", "-C", "~/x", "commit"],
                                     Path("/s")) == Path(os.path.expanduser("~")) / "x"


def test_commit_gate_passes_through_an_unresolvable_minus_c_path(repo: Path, capsys):
    assert commit_gate.main(_payload(repo, "git -C does-not-exist commit -m x"),
                            suite=RED) == 0
    assert capsys.readouterr().out == ""


def test_stop_gate_never_runs_a_suite(repo: Path):
    """There is no suite parameter to inject: the gate has no subprocess
    other than git. Pinned on the source so a future 'helpful' run cannot
    sneak in."""
    src = (HOOKS / "stop_gate.py").read_text()
    assert "subprocess" not in src
    assert "make" not in src.replace("make test", "")


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
    assert [m["matcher"] for m in hooks["PreToolUse"]] == ["Bash"]
    assert any(h["timeout"] == 300 and "commit_gate.py" in h["command"]
               for h in pre)
    assert "Read(//**/.env)" in settings["permissions"]["deny"]
    assert "Read(//**/.env.*)" in settings["permissions"]["deny"]
    stop = [h for m in hooks["Stop"] for h in m["hooks"]]
    assert any("scripts/hooks/stop_gate.py" in h["command"] for h in stop)
    for h in pre + stop:
        assert "$CLAUDE_PROJECT_DIR" in h["command"]
    perms = settings["permissions"]
    assert "deny" in perms and "ask" in perms
    assert "allow" not in perms
    assert "additionalDirectories" not in perms
    assert "/Users/" not in json.dumps(settings)


# --- registered command resolution -----------------------------------------------

def _registered_commands() -> dict:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    pre = [h["command"] for m in settings["hooks"]["PreToolUse"] for h in m["hooks"]][0]
    stop = [h["command"] for m in settings["hooks"]["Stop"] for h in m["hooks"]][0]
    return {"pre": pre, "stop": stop}


def _run_registered(command: str, cwd, project_dir, payload) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(["sh", "-c", command], cwd=str(cwd), env=env,
                          input=stdin, capture_output=True, text=True)


def _reg_payload(cwd, command: str, stop_hook_active: bool = False) -> dict:
    return {"cwd": str(cwd), "tool_name": "Bash",
            "tool_input": {"command": command},
            "stop_hook_active": stop_hook_active}


@pytest.mark.parametrize("which", ["pre", "stop"])
def test_registered_commands_exit_0_when_no_checkout_has_the_script(tmp_path, which):
    cwd = tmp_path / "a"
    cwd.mkdir()
    project_dir = tmp_path / "b"
    project_dir.mkdir()
    r = _run_registered(_registered_commands()[which], cwd, project_dir,
                        _reg_payload(cwd, "git commit -m x"))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


@pytest.mark.parametrize("which", ["pre", "stop"])
def test_registered_commands_resolve_the_cwd_checkouts_copy(tmp_path, which):
    project_dir = tmp_path / "b"
    project_dir.mkdir()
    cmd = _registered_commands()[which]
    r = _run_registered(cmd, ROOT, project_dir,
                        _reg_payload(ROOT, "gh pr list", stop_hook_active=True))
    assert (r.returncode, r.stdout) == (0, "")
    # Malformed stdin makes the script itself raise: proves python ran.
    r = _run_registered(cmd, ROOT, project_dir, "not json")
    assert r.returncode != 0
    assert "json" in r.stderr.lower()


def test_registered_pre_command_passes_a_commit_outside_any_repo(tmp_path):
    cwd = tmp_path / "a"
    cwd.mkdir()
    project_dir = tmp_path / "b"
    project_dir.mkdir()
    r = _run_registered(_registered_commands()["pre"], ROOT, project_dir,
                        _reg_payload(cwd, "git commit -m x"))
    assert (r.returncode, r.stdout) == (0, "")


@pytest.mark.parametrize("which", ["pre", "stop"])
def test_registered_commands_fall_back_to_the_launch_root(tmp_path, which):
    cwd = tmp_path / "a"
    cwd.mkdir()
    r = _run_registered(_registered_commands()[which], cwd, ROOT, "not json")
    assert r.returncode != 0
    assert "json" in r.stderr.lower()
