"""ops/backup.sh — a snapshot appears only once it is proven restorable."""
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "backup.sh"


def _seed_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, note TEXT)")
    conn.executemany("INSERT INTO signals (note) VALUES (?)",
                     [(f"n{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def _run(db: Path, backups: Path, journals: Path | None = None):
    env = {**os.environ, "FUND_DB": str(db), "FUND_BACKUP_DIR": str(backups)}
    if journals is not None:
        env["FUND_JOURNALS"] = str(journals)
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)


def test_snapshot_is_valid_and_row_counts_match(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db, rows=5)
    proc = _run(db, backups)
    assert proc.returncode == 0, proc.stderr

    snaps = list(backups.glob("fund-*.sqlite"))
    assert len(snaps) == 1, snaps
    conn = sqlite3.connect(snaps[0])
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 5
    conn.close()


def test_leaves_no_tmp_file_behind(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db)
    assert _run(db, backups).returncode == 0
    assert list(backups.glob("*.tmp")) == []


def test_a_corrupt_source_produces_no_snapshot(tmp_path):
    """The dated file must never appear unless it passed integrity_check."""
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    db.write_bytes(b"this is not a database")
    proc = _run(db, backups)
    assert proc.returncode != 0
    assert list(backups.glob("fund-*.sqlite")) == []


def test_never_deletes_existing_snapshots(tmp_path):
    """No prune step: 86 KB a day does not justify the only destructive op."""
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db)
    backups.mkdir()
    ancient = backups / "fund-1999-01-01.sqlite"
    ancient.write_text("keep me")
    assert _run(db, backups).returncode == 0
    assert ancient.exists(), "backup.sh must never delete anything"


def test_tars_the_journals_when_present(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    journals = tmp_path / "journals"
    journals.mkdir()
    (journals / "pm.md").write_text("# pm journal")
    _seed_db(db)
    assert _run(db, backups, journals).returncode == 0
    assert len(list(backups.glob("journals-*.tar.gz"))) == 1


def test_missing_env_fails_fast(tmp_path):
    """Fail fast with a descriptive error; never silently skip a backup."""
    proc = subprocess.run([str(SCRIPT)], capture_output=True, text=True,
                          env={k: v for k, v in os.environ.items()
                               if k not in ("FUND_DB", "FUND_BACKUP_DIR")})
    assert proc.returncode != 0
    assert "FUND_DB" in proc.stderr
