"""requirements.lock is the single resolution every host installs.

Ranges in pyproject.toml let three environments (local, droplet, CI) resolve
differently on three different days; that is what left `make test` green on
claude-agent-sdk 0.2.116 while the droplet placed orders on 0.2.139. The lock
is the answer, so these tests hold it to being complete, exact, and actually
installed — verified from installed metadata, never from the constraint.
"""

import importlib.metadata as md
import re
import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

import scripts.relock as relock
import scripts.sync_deps as sync_deps

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements.lock"
PYPROJECT = ROOT / "pyproject.toml"

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)$")


def _canon(name: str) -> str:
    """PEP 503 normalization: slack_bolt, Slack-Bolt and slack.bolt are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_lines() -> list[str]:
    return [
        line.strip()
        for line in LOCK.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _locked() -> dict[str, str]:
    out = {}
    for line in _lock_lines():
        m = PIN.match(line)
        assert m, f"requirements.lock: {line!r} is not an exact name==version pin"
        out[_canon(m.group(1))] = m.group(2)
    return out


def _declared() -> list[Requirement]:
    project = tomllib.load(PYPROJECT.open("rb"))["project"]
    deps = project["dependencies"] + project["optional-dependencies"]["dev"]
    return [Requirement(d) for d in deps]


def test_lock_is_exact_for_every_line():
    """A range anywhere in the lock reopens the drift it exists to close."""
    for line in _lock_lines():
        assert PIN.match(line), (
            f"requirements.lock: {line!r} is not an exact pin. Every line must"
            f" be name==version — a range makes the lock a suggestion.")


def test_lock_pins_every_declared_dependency():
    """A dependency added to pyproject.toml but not locked floats again."""
    locked = _locked()
    missing = [r.name for r in _declared() if _canon(r.name) not in locked]
    assert not missing, (
        f"declared in pyproject.toml but absent from requirements.lock:"
        f" {sorted(missing)}. Regenerate the lock — see its header.")


def test_lock_versions_satisfy_pyproject_ranges():
    """The lock is a resolution OF pyproject, not a contradiction of it."""
    locked = _locked()
    for req in _declared():
        pinned = locked[_canon(req.name)]
        assert req.specifier.contains(pinned, prereleases=True), (
            f"requirements.lock pins {req.name}=={pinned}, which is outside"
            f" pyproject.toml's {req.specifier}. One of the two is wrong.")


def test_installed_versions_match_the_lock():
    """The verification that matters: what is imported, not what is requested.

    A matching pyproject.toml is exactly what hid the original drift, so this
    reads installed metadata. `make deps` (run by `make test`) syncs it.
    """
    drift = sync_deps.drift()
    assert not drift, (
        "installed packages do not match requirements.lock:\n  "
        + "\n  ".join(drift)
        + "\nRun `make deps` to sync this environment to the lock.")


def test_stamp_value_changes_when_the_lock_changes(tmp_path):
    """The sync is content-gated; a lock edit that does not re-gate is a no-op."""
    lock = tmp_path / "requirements.lock"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\n")

    lock.write_text("pydantic==2.13.4\n")
    before = sync_deps.stamp_value(lock, pyproject)
    lock.write_text("pydantic==2.13.5\n")
    after = sync_deps.stamp_value(lock, pyproject)

    assert before != after, "stamp ignores requirements.lock content"


def test_stamp_value_changes_when_pyproject_changes(tmp_path):
    """pyproject still gates too: its ranges are what the lock is checked against."""
    lock = tmp_path / "requirements.lock"
    pyproject = tmp_path / "pyproject.toml"
    lock.write_text("pydantic==2.13.4\n")

    pyproject.write_text("[project]\n")
    before = sync_deps.stamp_value(lock, pyproject)
    pyproject.write_text("[project]\nname = 'x'\n")
    after = sync_deps.stamp_value(lock, pyproject)

    assert before != after, "stamp ignores pyproject.toml content"


def test_drift_reports_a_missing_package(tmp_path):
    """Descriptive failure, not a bare False — the message is the fix."""
    lock = tmp_path / "requirements.lock"
    lock.write_text("definitely-not-installed-xyz==1.0.0\n")

    report = sync_deps.drift(lock)

    assert len(report) == 1
    assert "definitely-not-installed-xyz" in report[0]
    assert "1.0.0" in report[0]


def test_lock_is_tracked_by_git():
    """.gitignore carries `*.lock` for run_day's flock handle.

    That rule swallowed requirements.lock on sight — a lock every host is told
    to install but which never reaches another host is worse than no lock, so
    the negation is pinned here rather than left to be rediscovered.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "requirements.lock"],
        cwd=ROOT, capture_output=True, text=True)

    assert tracked.returncode == 0, (
        "requirements.lock is not tracked by git — check .gitignore's *.lock"
        f" rule for a missing `!requirements.lock` negation. {tracked.stderr}")


def test_relock_keeps_the_header_and_drops_the_installer(tmp_path):
    """A regenerated lock the next person can still read and trust.

    pip/setuptools/wheel are the installer, not dependencies of the fund;
    freezing them pins the tool that does the pinning.
    """
    rendered = relock.render_lock(
        header="# header line\n# second line\n",
        freeze="pytest==9.1.1\npip==26.2.1\nsetuptools==80.0.0\nnumpy==2.5.2\n",
    )

    assert rendered.startswith("# header line\n# second line\n")
    assert "pip==" not in rendered
    assert "setuptools==" not in rendered
    assert "pytest==9.1.1" in rendered
    assert "numpy==2.5.2" in rendered


def test_relock_sorts_pins_case_insensitively(tmp_path):
    """Stable order, so a relock diff shows version changes and nothing else."""
    rendered = relock.render_lock(
        header="# h\n", freeze="numpy==2.5.2\nPyYAML==6.0.3\nalpaca-py==0.44.0\n")

    assert [line for line in rendered.splitlines() if "==" in line] == [
        "alpaca-py==0.44.0", "numpy==2.5.2", "PyYAML==6.0.3"]


def test_relock_ignores_editable_and_url_entries(tmp_path):
    """`pip freeze` emits these for local installs; they are not pins."""
    rendered = relock.render_lock(
        header="# h\n",
        freeze="-e git+ssh://x#egg=fund\nnumpy==2.5.2\nfund @ file:///opt/fund\n",
    )

    assert [line for line in rendered.splitlines() if not line.startswith("#")] == [
        "numpy==2.5.2"]


def test_relock_reuses_the_committed_header(tmp_path):
    """Regeneration must not silently discard the file's own instructions."""
    lock = tmp_path / "requirements.lock"
    lock.write_text("# why this file exists\n#\n# REGENERATE: make deps-relock\nnumpy==1.0.0\n")

    assert relock.existing_header(lock) == (
        "# why this file exists\n#\n# REGENERATE: make deps-relock\n")


def test_drift_reports_a_version_mismatch(tmp_path):
    """The case that actually happened: present, importable, wrong version."""
    lock = tmp_path / "requirements.lock"
    installed = md.version("pydantic")
    lock.write_text("pydantic==0.0.0-not-a-real-version\n")

    report = sync_deps.drift(lock)

    assert len(report) == 1
    assert "pydantic" in report[0]
    assert installed in report[0]
