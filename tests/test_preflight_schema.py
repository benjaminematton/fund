"""scripts/preflight_schema.py — preflight opens the LIVE database, read-only.

`make preflight` used to run eval_suite.py alone, which builds a fresh per-trial
DB. It therefore checked schema against a database it had created moments
earlier and never touched `$FUND_DB`, so a green preflight said nothing about
whether a pending migration had reached the droplet (#17). On 2026-08-19 the
attribution columns were 0/6 on the live DB behind a green preflight; the next
`connect()` would have ALTERed production unattended, mid trading day.

Two things are load-bearing here and both are tested rather than commented:

  1. **Reporting must not migrate.** state/db.py's connect() ends with
     _apply_migrations(), so the obvious way to open the DB is the one that
     silently applies the thing being reported on. Preflight opens
     `file:...?mode=ro` instead, and test_reporting_pending_does_not_touch_the_db
     is what stops that from regressing.

  2. **"Run the migration" and "something is wrong" are different alerts.** A
     live DB that has never run migrations.apply() genuinely differs from
     schema.sql, and that difference is expected and actionable. A difference no
     migration explains is a real failure. They get distinct exit codes so the
     operator does not have to read the source to tell them apart.

Ambiguity — FUND_DB unset, file missing, not a database, a WAL that needs
recovery — is RED, never green (invariant 4).

Fully offline: every DB here is a temp file built from state/schema.sql.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight_schema.py"
SCHEMA = ROOT / "state" / "schema.sql"


def _load():
    spec = importlib.util.spec_from_file_location("preflight_schema", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = _load()


def _live_db(path: Path, drop_columns=(), drop_tables=()) -> Path:
    """A stand-in for the droplet's DB: schema.sql, in WAL, minus some pieces.

    Dropping is how a live database that predates a schema.sql edit is
    simulated — the columns/tables simply were never there.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA.read_text())
    for table, column in drop_columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    for table in drop_tables:
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    return path


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _run(db: Path | None):
    """Run the script the way the Makefile does: FUND_DB out of the env."""
    env = {k: v for k, v in os.environ.items() if k != "FUND_DB"}
    if db is not None:
        env["FUND_DB"] = str(db)
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                          text=True, env=env, cwd=str(ROOT))


ATTRIBUTION = (("signals", "charter_version"), ("signals", "model_id"),
               ("decisions", "charter_version"), ("decisions", "model_id"),
               ("critiques", "charter_version"), ("critiques", "model_id"))


# --- GREEN -------------------------------------------------------------------

def test_a_current_db_is_green(tmp_path):
    proc = _run(_live_db(tmp_path / "fund.sqlite"))
    assert proc.returncode == preflight.OK, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_green_requires_every_table_in_schema_sql(tmp_path):
    """11 tables today. The count is read from schema.sql, not restated, so
    adding a table to that file extends the check with no second edit."""
    assert len(preflight.expected_schema()) == 11


# --- RED: migrations pending -------------------------------------------------

def test_a_db_behind_a_migration_says_migrations_pending(tmp_path):
    db = _live_db(tmp_path / "fund.sqlite", drop_columns=ATTRIBUTION)
    proc = _run(db)

    assert proc.returncode == preflight.MIGRATIONS_PENDING
    out = proc.stdout + proc.stderr
    assert "0001_attribution" in out, out
    assert "backup" in out.lower(), out


def test_one_missing_column_is_enough_to_be_pending(tmp_path):
    """A partially-migrated DB — one table done, one not — is still behind."""
    db = _live_db(tmp_path / "fund.sqlite",
                  drop_columns=(("critiques", "model_id"),))
    assert _run(db).returncode == preflight.MIGRATIONS_PENDING


def test_reporting_pending_does_not_touch_the_db(tmp_path):
    """THE trap. connect() applies migrations; preflight must only report.

    A run that migrates turns the next run green without a human ever having
    taken the backup #17 requires — the report would then be evidence of its
    own side effect.
    """
    db = _live_db(tmp_path / "fund.sqlite", drop_columns=ATTRIBUTION)
    before = db.stat().st_mtime_ns

    assert _run(db).returncode == preflight.MIGRATIONS_PENDING

    for table, column in ATTRIBUTION:
        assert column not in _columns(db, table), f"{table}.{column} was added"
    assert db.stat().st_mtime_ns == before
    # Still pending on a second look: the first look changed nothing.
    assert _run(db).returncode == preflight.MIGRATIONS_PENDING


# --- RED: unexplained divergence ---------------------------------------------

def test_a_missing_table_is_unexplained_divergence(tmp_path):
    """No migration creates a table, so an absent one is a real failure —
    not something the operator can fix by running a migration."""
    db = _live_db(tmp_path / "fund.sqlite", drop_tables=("costs",))
    proc = _run(db)

    assert proc.returncode == preflight.UNEXPLAINED_DIVERGENCE
    assert "costs" in proc.stdout + proc.stderr


def test_a_column_no_migration_adds_is_unexplained_divergence(tmp_path):
    db = _live_db(tmp_path / "fund.sqlite",
                  drop_columns=(("critiques", "note"),))
    proc = _run(db)

    assert proc.returncode == preflight.UNEXPLAINED_DIVERGENCE
    assert "critiques.note" in proc.stdout + proc.stderr


def test_unexplained_wins_over_pending(tmp_path):
    """A DB that is both behind AND broken reports the failure. "Run the
    migration" would be wrong advice: it would not fix `critiques.note`."""
    db = _live_db(tmp_path / "fund.sqlite",
                  drop_columns=ATTRIBUTION + (("critiques", "note"),))
    assert _run(db).returncode == preflight.UNEXPLAINED_DIVERGENCE


# --- RED: cannot determine (invariant 4 — ambiguity is never green) ----------

def test_fund_db_unset_cannot_determine():
    proc = _run(None)
    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "FUND_DB" in proc.stdout + proc.stderr


def test_a_blank_fund_db_cannot_determine(tmp_path):
    env = {**os.environ, "FUND_DB": "   "}
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                          text=True, env=env, cwd=str(ROOT))
    assert proc.returncode == preflight.CANNOT_DETERMINE


def test_a_missing_file_cannot_determine(tmp_path):
    """And must not create it — a read-only open never makes a database."""
    db = tmp_path / "absent.sqlite"
    assert _run(db).returncode == preflight.CANNOT_DETERMINE
    assert not db.exists()


def test_a_file_that_is_not_a_database_cannot_determine(tmp_path):
    db = tmp_path / "fund.sqlite"
    db.write_bytes(b"this is not a database")
    assert _run(db).returncode == preflight.CANNOT_DETERMINE


def test_a_wal_needing_recovery_cannot_determine(tmp_path, monkeypatch):
    """The live DB runs in WAL. A read-only open of a DB whose -wal must be
    replayed fails with SQLITE_READONLY_RECOVERY rather than reading a torn
    snapshot, and that is 'cannot determine' — red, not green."""
    db = _live_db(tmp_path / "fund.sqlite")

    def boom(*a, **kw):
        raise sqlite3.OperationalError(
            "attempt to write a readonly database")

    monkeypatch.setattr(preflight.sqlite3, "connect", boom)
    code, report = preflight.check(str(db))
    assert code == preflight.CANNOT_DETERMINE
    assert "readonly" in report


# --- the four outcomes are distinguishable -----------------------------------

def test_the_four_outcomes_have_distinct_codes_and_messages(tmp_path):
    """An operator must tell 'run the migration' from 'something is wrong'
    without reading the source."""
    green = _live_db(tmp_path / "green.sqlite")
    behind = _live_db(tmp_path / "behind.sqlite", drop_columns=ATTRIBUTION)
    broken = _live_db(tmp_path / "broken.sqlite", drop_tables=("costs",))
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"nope")

    results = [preflight.check(str(green)), preflight.check(str(behind)),
               preflight.check(str(broken)), preflight.check(None)]
    codes = [c for c, _ in results]
    assert codes == [preflight.OK, preflight.MIGRATIONS_PENDING,
                     preflight.UNEXPLAINED_DIVERGENCE,
                     preflight.CANNOT_DETERMINE]
    assert len(set(codes)) == 4
    headlines = {r.splitlines()[0] for _, r in results}
    assert len(headlines) == 4, headlines


def test_only_green_is_zero(tmp_path):
    for code in (preflight.MIGRATIONS_PENDING,
                 preflight.UNEXPLAINED_DIVERGENCE,
                 preflight.CANNOT_DETERMINE):
        assert code != 0


# --- scope: $FUND_DB only ----------------------------------------------------

def test_the_registry_tables_are_out_of_scope(tmp_path):
    """fundbt/registry.py owns trial_registry/holdout_evaluations in a
    SEPARATE database. Expecting them in $FUND_DB would fail every run."""
    expected = preflight.expected_schema()
    assert "trial_registry" not in expected
    assert "holdout_evaluations" not in expected


# --- the makefile wiring -----------------------------------------------------

def test_preflight_target_runs_the_schema_check_before_eval_suite():
    """Fail fast and cheap: a bad live schema must stop the deploy before
    ~$0.31 of live LLM trials, not after."""
    target = (ROOT / "Makefile").read_text().split("\npreflight:")[1]
    target = target.split("\n\n")[0]
    assert "preflight_schema.py" in target
    assert target.index("preflight_schema.py") < target.index("eval_suite.py")


@pytest.mark.parametrize("needle", ["EnvironmentFile=/etc/fund/env", "--uid=fund"])
def test_the_schema_step_keeps_the_droplet_run_context(needle):
    """FUND_DB comes from /etc/fund/env and the live DB is owned by `fund`."""
    target = (ROOT / "Makefile").read_text().split("\npreflight:")[1]
    step = target.split("preflight_schema.py")[0]
    assert needle in step
