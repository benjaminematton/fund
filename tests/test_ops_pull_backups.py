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


def _run(tmp_path, droplet="fund@203.0.113.10", local=None, rsync=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("FUND_DROPLET", "FUND_LOCAL_BACKUPS", "FUND_RSYNC")}
    if droplet is not None:
        env["FUND_DROPLET"] = droplet
    if local is not None:
        env["FUND_LOCAL_BACKUPS"] = str(local)
    if rsync is not None:
        env["FUND_RSYNC"] = str(rsync)
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
