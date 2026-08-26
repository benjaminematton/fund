"""ops/pull-backups.sh — Mac-side pull. Fails fast rather than silently
leaving the off-box copy stale."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "pull-backups.sh"


def _fake_rsync(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in rsync that records the argv it was handed."""
    argv_log = tmp_path / "rsync-argv.txt"
    fake = tmp_path / "rsync"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_log}\nexit 0\n')
    fake.chmod(0o755)
    return fake, argv_log


def _fake_filer(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path]:
    """A stand-in filer recording its argv and exiting with `exit_code`."""
    argv_log = tmp_path / "filer-argv.txt"
    fake = tmp_path / "filer"
    fake.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {argv_log}\nexit {exit_code}\n')
    fake.chmod(0o755)
    return fake, argv_log


def _snapshots(local: Path, *names: str) -> Path:
    local.mkdir(parents=True, exist_ok=True)
    for n in names:
        (local / n).write_text("")
    return local


def _run(tmp_path, droplet="fund@203.0.113.10", local=None, rsync=None,
         filer=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("FUND_DROPLET", "FUND_LOCAL_BACKUPS", "FUND_RSYNC",
                        "FUND_FILER")}
    if droplet is not None:
        env["FUND_DROPLET"] = droplet
    if local is not None:
        env["FUND_LOCAL_BACKUPS"] = str(local)
    if rsync is not None:
        env["FUND_RSYNC"] = str(rsync)
    if filer is not None:
        env["FUND_FILER"] = str(filer)
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)


def test_missing_droplet_fails_fast(tmp_path):
    proc = _run(tmp_path, droplet=None, local=tmp_path / "b")
    assert proc.returncode != 0
    assert "FUND_DROPLET" in proc.stderr


def test_missing_local_dir_fails_fast(tmp_path):
    proc = _run(tmp_path, local=None)
    assert proc.returncode != 0
    assert "FUND_LOCAL_BACKUPS" in proc.stderr


def test_pulls_from_the_droplet_backup_dir_into_the_local_dir(tmp_path):
    fake, argv_log = _fake_rsync(tmp_path)
    local = tmp_path / "backups-from-vm"
    proc = _run(tmp_path, local=local, rsync=fake)
    assert proc.returncode == 0, proc.stderr

    argv = argv_log.read_text().splitlines()
    assert "fund@203.0.113.10:/var/lib/fund/backups/" in argv
    assert f"{local}/" in argv
    # --ignore-existing is what makes a re-run cheap and non-destructive:
    # already-pulled snapshots are never re-fetched or overwritten.
    assert "--ignore-existing" in argv
    assert "-az" in argv


def test_creates_the_local_dir_when_absent(tmp_path):
    fake, _ = _fake_rsync(tmp_path)
    local = tmp_path / "does" / "not" / "exist"
    assert _run(tmp_path, local=local, rsync=fake).returncode == 0
    assert local.is_dir()


# --- the chained filer (docs/agents/devops.md) ---------------------------
#
# The pull is the only thing that runs daily on this machine and knows a fresh
# snapshot just landed, so it is where the filer is invoked. `gh` is not
# installed on the droplet and putting a repo-write token on the box that
# holds broker keys is the thing this arrangement exists to avoid.


def test_the_filer_runs_against_the_newest_snapshot_after_a_successful_pull(tmp_path):
    rsync, _ = _fake_rsync(tmp_path)
    filer, argv_log = _fake_filer(tmp_path)
    local = _snapshots(tmp_path / "b", "fund-2026-08-24.sqlite",
                       "fund-2026-08-26.sqlite", "fund-2026-08-25.sqlite")

    proc = _run(tmp_path, local=local, rsync=rsync, filer=filer)
    assert proc.returncode == 0, proc.stderr

    argv = argv_log.read_text().splitlines()
    assert str(local / "fund-2026-08-26.sqlite") in argv
    assert "--apply" in argv
    # The window is the snapshot's own date. Deliberately not a rolling one:
    # a wider window is a design question this run exists to inform, and a
    # narrow window that misses a skipped day makes that visible.
    assert "--since" in argv and "2026-08-26" in argv


def test_a_predeploy_snapshot_never_wins(tmp_path):
    """`fund-predeploy-*` sorts after every dated snapshot, so the obvious
    `tail -1` would hand the filer an arbitrary-age preflight copy."""
    rsync, _ = _fake_rsync(tmp_path)
    filer, argv_log = _fake_filer(tmp_path)
    local = _snapshots(tmp_path / "b", "fund-2026-08-26.sqlite",
                       "fund-predeploy-20260818T120000.sqlite")

    assert _run(tmp_path, local=local, rsync=rsync, filer=filer).returncode == 0
    argv = argv_log.read_text().splitlines()
    assert str(local / "fund-2026-08-26.sqlite") in argv
    # On the basename only: pytest's tmp_path is named after the test, so the
    # substring "predeploy" is in every path here regardless of the answer.
    assert not any(Path(a).name.startswith("fund-predeploy") for a in argv)


def test_sidecar_files_are_not_mistaken_for_snapshots(tmp_path):
    """The mirror carries `-wal`/`-shm` sidecars from past read-write opens."""
    rsync, _ = _fake_rsync(tmp_path)
    filer, argv_log = _fake_filer(tmp_path)
    local = _snapshots(tmp_path / "b", "fund-2026-08-26.sqlite",
                       "fund-2026-08-26.sqlite-wal",
                       "fund-2026-08-26.sqlite-shm")

    assert _run(tmp_path, local=local, rsync=rsync, filer=filer).returncode == 0
    argv = argv_log.read_text().splitlines()
    assert str(local / "fund-2026-08-26.sqlite") in argv


def test_a_failing_filer_does_not_fail_the_pull(tmp_path):
    """`main()` returns 1 on a malformed payload — a routine data condition,
    not a failure of the pull. `set -eu` would otherwise kill the job, and a
    blanket `|| true` would hide the tracker-unavailable case too."""
    rsync, _ = _fake_rsync(tmp_path)
    filer, _ = _fake_filer(tmp_path, exit_code=1)
    local = _snapshots(tmp_path / "b", "fund-2026-08-26.sqlite")

    proc = _run(tmp_path, local=local, rsync=rsync, filer=filer)
    assert proc.returncode == 0, proc.stderr
    # Asserted verbatim: pytest's tmp_path carries the test name, so a looser
    # substring check passes on the path alone and proves nothing.
    assert "pull-backups: filer exited 1" in proc.stdout


def test_the_pull_reports_which_snapshot_it_read(tmp_path):
    """`--ignore-existing` means a stalled droplet backup transfers nothing and
    exits 0, so a file count cannot distinguish stale from fresh (#110). The
    snapshot name is what makes the run falsifiable by a human reading the log."""
    rsync, _ = _fake_rsync(tmp_path)
    filer, _ = _fake_filer(tmp_path)
    local = _snapshots(tmp_path / "b", "fund-2026-08-26.sqlite")

    proc = _run(tmp_path, local=local, rsync=rsync, filer=filer)
    assert "fund-2026-08-26.sqlite" in proc.stdout


def test_no_snapshot_skips_the_filer_without_failing(tmp_path):
    """A first run against an empty mirror, or a pull that fetched nothing."""
    rsync, _ = _fake_rsync(tmp_path)
    filer, argv_log = _fake_filer(tmp_path)
    local = _snapshots(tmp_path / "b")

    proc = _run(tmp_path, local=local, rsync=rsync, filer=filer)
    assert proc.returncode == 0, proc.stderr
    assert not argv_log.exists()
    assert "no snapshot" in proc.stdout.lower()


def test_the_filer_is_optional(tmp_path):
    """FUND_FILER unset leaves the pull exactly as it was — the chaining must
    not be able to break a backup pull on a machine that never files."""
    rsync, _ = _fake_rsync(tmp_path)
    local = _snapshots(tmp_path / "b", "fund-2026-08-26.sqlite")
    assert _run(tmp_path, local=local, rsync=rsync).returncode == 0
