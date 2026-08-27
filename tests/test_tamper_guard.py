"""devloop/tamper_guard.py — what the build loop may and may not touch.

Driven end-to-end against a throwaway git repo with the real script copied in,
because the guard's answer depends on git's own status letters: a new eval
trace arrives as an *untracked* file (`ls-files --others`), not as a diff, and
that is the path an over-broad glob breaks.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "devloop" / "tamper_guard.py"

# Ignore the developer's global/system git config so a personal excludesfile
# can't hide an untracked file from the guard mid-test.
ENV = {**os.environ,
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

BASELINE = (
    ".gitignore",
    "evals/cases/pm/a01.yaml",
    "evals/cases/pm/ä01.yaml",
    "evals/cases.py",
    "evals/conftest.py",
    "evals/seats/pm.yaml",
    "evals/seatsmap.py",
    "evals/runner.py",
    "evals/traces/recorded-expected.json",
    "evals/traces/control/abc123/a01/1.json",
    "specs/design.md",
    "specs/dësign.md",
    "tests/test_thing.py",
    "tests/tëst_thing.py",
    "gate/risk.py",
)


def _write(repo: Path, path: str, text: str) -> None:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def _typechange(repo: Path, path: str) -> None:
    """Swap a regular file for a symlink. git reports this as status `T`, not
    `M` or `D`, and a symlink to /dev/null collects as an empty file."""
    f = repo / path
    f.unlink()
    f.symlink_to(os.devnull)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=ENV,
                   capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    """A committed baseline tree with the real guard installed at devloop/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for path in BASELINE:
        _write(repo, path, "baseline\n")
    (repo / "devloop").mkdir(exist_ok=True)
    shutil.copy(GUARD, repo / "devloop" / "tamper_guard.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _run(repo: Path, baseline: str = "HEAD") -> subprocess.CompletedProcess:
    """`baseline` is HEAD for most tests; the loop pins it to the run's first
    commit and lets HEAD advance, which only matters where a test commits."""
    return subprocess.run(
        [sys.executable, str(repo / "devloop" / "tamper_guard.py"), baseline],
        capture_output=True, text=True, env=ENV)


def _reject(repo: Path, path: str, baseline: str = "HEAD") -> None:
    proc = _run(repo, baseline)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert path in proc.stderr, proc.stderr


def _allow(repo: Path, baseline: str = "HEAD") -> None:
    proc = _run(repo, baseline)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_untouched_tree_is_clean(tmp_path):
    _allow(_repo(tmp_path))


# --- evals/cases/: baseline files immutable, additions free ---------------

def test_editing_an_existing_eval_case_is_rejected(tmp_path):
    """The cheapest way to turn a red case green is to edit its expectation."""
    repo = _repo(tmp_path)
    _write(repo, "evals/cases/pm/a01.yaml", "expects: whatever passes\n")
    _reject(repo, "evals/cases/pm/a01.yaml")


def test_deleting_an_existing_eval_case_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "evals/cases/pm/a01.yaml").unlink()
    _reject(repo, "evals/cases/pm/a01.yaml")
    # the message has to name what happened; it once rendered "Dodified".
    assert "deleted" in _run(repo).stderr


def test_adding_a_new_eval_case_is_allowed(tmp_path):
    """Adding a case must stay cheap."""
    repo = _repo(tmp_path)
    _write(repo, "evals/cases/pm/b09.yaml", "id: b09\n")
    _allow(repo)


# --- evals/seats/: frozen outright ---------------------------------------

def test_editing_a_seat_ceiling_is_rejected(tmp_path):
    """max_turns / max_cost_usd are measured numbers; human commit only."""
    repo = _repo(tmp_path)
    _write(repo, "evals/seats/pm.yaml", "max_turns: 99\n")
    _reject(repo, "evals/seats/pm.yaml")


def test_adding_a_seat_file_is_rejected(tmp_path):
    """Unlike cases, seats are frozen against additions too — switching on a
    new invariant is a human-commit step."""
    repo = _repo(tmp_path)
    _write(repo, "evals/seats/analyst.yaml", "seat: analyst\n")
    _reject(repo, "evals/seats/analyst.yaml")


# --- evals/traces/: baseline traces frozen, new runs writable ------------

def test_editing_the_recorded_expectations_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "evals/traces/recorded-expected.json", "{}\n")
    _reject(repo, "evals/traces/recorded-expected.json")


def test_re_creating_the_recorded_expectations_is_rejected(tmp_path):
    """Additions under evals/traces/ are otherwise free, so the baseline guard
    cannot cover this one: with the file gone from HEAD, writing a fresh
    expectation is an ADDITION. Only the PROTECTED_FILES entry stops it."""
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "evals/traces/recorded-expected.json")
    _git(repo, "commit", "-qm", "expectations dropped")
    _write(repo, "evals/traces/recorded-expected.json", '{"a01": "pass"}\n')
    _reject(repo, "evals/traces/recorded-expected.json")


def test_editing_an_existing_trace_is_rejected(tmp_path):
    """`got == expected` has two sides; freezing the expectation alone leaves
    the graded-from inputs as the cheaper way to restore equality."""
    repo = _repo(tmp_path)
    _write(repo, "evals/traces/control/abc123/a01/1.json", '{"turns": 1}\n')
    _reject(repo, "evals/traces/control/abc123/a01/1.json")


def test_a_new_trace_file_is_allowed(tmp_path):
    """The regression that matters: runs append a whole new label here every
    iteration, and it arrives untracked. Guarding traces must not wedge that."""
    repo = _repo(tmp_path)
    _write(repo, "evals/traces/somerun/deadbee/a01/1.json", '{"turns": 4}\n')
    _allow(repo)


def test_the_rest_of_evals_stays_the_workspace(tmp_path):
    """Only cases/, seats/, traces/ are guarded — and by directory, not by
    prefix: `evals/cases.py` and `evals/seatsmap.py` must stay editable even
    though they start with `evals/cases` and `evals/seats`."""
    repo = _repo(tmp_path)
    _write(repo, "evals/cases.py", "# refactored\n")
    _write(repo, "evals/seatsmap.py", "# refactored\n")
    _allow(repo)


def test_a_non_root_conftest_is_not_rejected(tmp_path):
    """`conftest.py` is protected by exact path, so only the root one. Loosen
    that to a suffix match and evals/conftest.py stops being editable."""
    repo = _repo(tmp_path)
    _write(repo, "evals/conftest.py", "# fixture tweak\n")
    _allow(repo)


# --- the pytest configuration surface ------------------------------------

def test_creating_a_root_pytest_ini_is_rejected(tmp_path):
    """It overrides pyproject.toml's [tool.pytest.ini_options]; a testpaths or
    ignore line there drops the eval suites from collection."""
    repo = _repo(tmp_path)
    _write(repo, "pytest.ini", "[pytest]\ntestpaths = tests/unit\n")
    _reject(repo, "pytest.ini")


def test_creating_a_root_conftest_is_rejected(tmp_path):
    """collect_ignore_glob removes tests outright; an autouse fixture or a
    sys.path shim changes what the survivors mean."""
    repo = _repo(tmp_path)
    _write(repo, "conftest.py", 'collect_ignore_glob = ["*evals*"]\n')
    _reject(repo, "conftest.py")


def test_creating_a_root_setup_cfg_is_rejected(tmp_path):
    """Defence in depth, not an override: measured on pytest 9.1.1 the order is
    pytest.ini > pyproject.toml > tox.ini > setup.cfg, so [tool:pytest] here
    loses to pyproject.toml and pytest says so ("WARNING: ignoring pytest
    config in setup.cfg!"). It is inert only while pyproject.toml keeps its
    [tool.pytest.ini_options] table — guarded so the attack cannot move
    sideways to this filename if that ever stops being true."""
    repo = _repo(tmp_path)
    _write(repo, "setup.cfg", "[tool:pytest]\naddopts = -k not_eval\n")
    _reject(repo, "setup.cfg")


def test_creating_a_root_tox_ini_is_rejected(tmp_path):
    """Same standing as setup.cfg: [pytest] in tox.ini also loses to
    pyproject.toml, and is guarded for the same sideways-move reason. All four
    filenames, so only pytest.ini is a live override and none is a spare."""
    repo = _repo(tmp_path)
    _write(repo, "tox.ini", "[pytest]\ntestpaths = tests/unit\n")
    _reject(repo, "tox.ini")


# --- .gitignore: it decides what the guard can even see -------------------

def test_editing_the_gitignore_is_rejected(tmp_path):
    """The guard finds new files with `ls-files --others --exclude-standard`,
    so an ignore rule is a blind spot it would honour."""
    repo = _repo(tmp_path)
    _write(repo, ".gitignore", "evals/traces/\n")
    _reject(repo, ".gitignore")


# --- renames and non-ASCII paths -----------------------------------------

def test_renaming_a_protected_path_is_rejected(tmp_path):
    """`--name-status` reports a rename as one record whose last field is the
    destination; read that alone and `git mv` walks anything out of scope.
    Pinned so `--no-renames` cannot be tidied away."""
    repo = _repo(tmp_path)
    (repo / "attic").mkdir()
    _git(repo, "mv", "specs/design.md", "attic/design.md")
    _reject(repo, "specs/design.md")
    # `--no-renames` is what turns the move into a deletion of the source;
    # an `(R)` here means the flag went missing.
    assert "specs/design.md: protected path (D)" in _run(repo).stderr


def test_renaming_a_pre_existing_test_out_of_collection_is_rejected(tmp_path):
    """The weaponised form: a red test renamed to a non-`test_` name is gone
    from collection without a single line of it being edited."""
    repo = _repo(tmp_path)
    _git(repo, "mv", "tests/test_thing.py", "tests/_thing_helpers.py")
    _reject(repo, "tests/test_thing.py")


# --- typechange: the third status-letter bypass ---------------------------

def test_typechanging_a_pre_existing_test_is_rejected(tmp_path):
    """`rm tests/test_thing.py; ln -s /dev/null tests/test_thing.py` empties a
    red test without editing a line of it, and git calls that `T`, not `M`.
    Pinned so the baseline clause cannot go back to a list of status letters."""
    repo = _repo(tmp_path)
    _typechange(repo, "tests/test_thing.py")
    _reject(repo, "tests/test_thing.py")
    assert "typechange" in _run(repo).stderr


def test_typechanging_an_existing_eval_case_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    _typechange(repo, "evals/cases/pm/a01.yaml")
    _reject(repo, "evals/cases/pm/a01.yaml")


def test_typechanging_an_existing_trace_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    _typechange(repo, "evals/traces/control/abc123/a01/1.json")
    _reject(repo, "evals/traces/control/abc123/a01/1.json")


def test_additions_under_the_guarded_dirs_survive_the_typechange_fix(tmp_path):
    """The predicate is `status != "A"`, so the one status that must stay free
    is the addition. All three guarded directories at once, because widening
    from ("M", "D") is exactly the change that could have caught them."""
    repo = _repo(tmp_path)
    _write(repo, "tests/test_added.py", "def test_x(): pass\n")
    _write(repo, "evals/cases/pm/z99.yaml", "id: z99\n")
    _write(repo, "evals/traces/newrun/deadbee/a01/1.json", '{"turns": 2}\n')
    _allow(repo)


def test_a_non_ascii_protected_path_is_rejected(tmp_path):
    """core.quotePath is on by default, so git renders this path as
    `"specs/d\\303\\253sign.md"` — a leading quote defeats startswith()."""
    repo = _repo(tmp_path)
    _write(repo, "specs/dësign.md", "rewritten\n")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "sign.md" in proc.stderr and '"specs/' not in proc.stderr, proc.stderr


def test_a_non_ascii_untracked_protected_path_is_rejected(tmp_path):
    """Same escaping applies to `ls-files --others`."""
    repo = _repo(tmp_path)
    _write(repo, "specs/nöuveau.md", "smuggled\n")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "uveau.md" in proc.stderr and '"specs/' not in proc.stderr, proc.stderr


def test_a_non_ascii_pre_existing_test_is_rejected(tmp_path):
    """The protected-path tests above exit at `startswith(PROTECTED_DIRS)` and
    never reach `baseline_files`. This is the branch that does: the baseline
    listing has to be unquoted too, or the membership test is silently False
    and a red test with an accented name is free to become `assert True`."""
    repo = _repo(tmp_path)
    _write(repo, "tests/tëst_thing.py", "assert True\n")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    # matched on the ASCII tail: the filesystem may hand the accent back in a
    # different unicode normalisation than this source file uses.
    assert "st_thing.py: pre-existing evidence modified" in proc.stderr
    assert '"tests/' not in proc.stderr, proc.stderr


def test_deleting_a_non_ascii_eval_case_is_rejected(tmp_path):
    """Same branch, the delete side, under the other guarded directory."""
    repo = _repo(tmp_path)
    (repo / "evals/cases/pm/ä01.yaml").unlink()
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "01.yaml: pre-existing evidence deleted" in proc.stderr
    assert '"evals/' not in proc.stderr, proc.stderr


# --- pre-existing behaviour still holds ----------------------------------

def test_a_specs_change_is_still_rejected(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "specs/design.md", "rewritten\n")
    _reject(repo, "specs/design.md")


def test_editing_a_pre_existing_test_is_still_rejected(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "tests/test_thing.py", "assert True\n")
    _reject(repo, "tests/test_thing.py")


def test_a_new_test_is_still_allowed(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "tests/test_new.py", "def test_x(): pass\n")
    _allow(repo)


def test_a_test_the_run_wrote_itself_stays_editable(tmp_path):
    """What `path in baseline_files` buys: a test added by an earlier green
    iteration is tracked in HEAD but absent from the baseline tree, so the run
    may keep working on it. Drop that clause and the loop cannot edit its own
    output — every iteration after the first would be rejected."""
    repo = _repo(tmp_path)
    _git(repo, "tag", "base")
    _write(repo, "tests/test_new.py", "def test_x(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "iteration 1")
    _write(repo, "tests/test_new.py", "def test_x(): assert compute() == 3\n")
    _allow(repo, "base")


def test_runtime_code_is_still_the_workspace(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, "gate/risk.py", "# implemented\n")
    _allow(repo)
