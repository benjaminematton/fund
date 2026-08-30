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

Ambiguity — FUND_DB unset, file missing, not a database, holding none of the
expected tables, an unwritable directory, or preflight crashing — is RED, never
green (invariant 4). A crash must be red as CANNOT_DETERMINE specifically: an
unhandled exception exits 1, which is the MIGRATIONS_PENDING code, and telling
an operator to run a migration off a traceback is the mislabelling this script
exists to end.

What is compared is NAMES ONLY — tables and columns, never types or
constraints. test_only_column_names_are_compared pins that limit, and pins that
the OK message admits it.

Fully offline: every DB here is a temp file built from state/schema.sql.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import textwrap
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


def _drop_sidecars(db: Path) -> None:
    """Remove -wal/-shm so the next open has to recreate them."""
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _wal_only_db(path: Path) -> Path:
    """A DB whose schema lives entirely in an uncheckpointed `-wal`.

    Built in a child that exits via os._exit, so SQLite never checkpoints on
    close — the shape a live droplet DB has between commits, and the one that
    separates mode=ro from immutable=1.
    """
    child = path.parent / "_build_wal.py"
    child.write_text(textwrap.dedent(f"""
        import os, sqlite3, pathlib
        conn = sqlite3.connect({str(path)!r})
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(pathlib.Path({str(SCHEMA)!r}).read_text())
        conn.commit()
        os._exit(0)
    """))
    subprocess.run([sys.executable, str(child)], check=True)
    child.unlink()
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


def test_the_expected_table_count_is_pinned(tmp_path):
    """15 tables today. expected_schema() reads state/schema.sql, so a table
    added there is checked with no edit to the script — this assertion is the
    tripwire that makes such an addition deliberate. It IS a second edit, on
    purpose: bump it in the same commit that adds the table.

    12 -> 14 on 2026-08-29 — issue #172
    (https://github.com/benjaminematton/fund/issues/172#issuecomment-5460532548)
    — Group 2 unification landed. trial_registry and holdout_evaluations moved
    out of fundbt/registry.py's standalone DDL into state/schema.sql.

    14 -> 15 on 2026-08-30 — issue #197
    (https://github.com/benjaminematton/fund/issues/197) — the `strategies`
    lifecycle table landed, character-exact to strategy-contracts.md §2.
    Registration writes a row in state SPEC (state/specs.py); nothing moves
    one, because the table has no transition machine yet.
    """
    assert len(preflight.expected_schema()) == 15


def test_only_column_names_are_compared(tmp_path):
    """The limit, stated by a test so nobody has to trust the docstring.

    A `tickets` rebuilt without `UNIQUE (decision_id)` — schema.sql:79, the
    constraint behind invariant 5's order idempotency — is GREEN here.
    Comparing constraints is a separate, deferred decision; what is not
    optional is that the OK message admits the limit instead of claiming a
    match it never tested.
    """
    db = _live_db(tmp_path / "fund.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE tickets")
    conn.execute("CREATE TABLE tickets (id TEXT PRIMARY KEY,"
                 " decision_id INTEGER NOT NULL, ticker TEXT NOT NULL,"
                 " side TEXT NOT NULL, max_qty INTEGER NOT NULL,"
                 " stop_price REAL, expires_at TEXT NOT NULL,"
                 " status TEXT NOT NULL DEFAULT 'open', reason TEXT,"
                 " created_at TEXT NOT NULL)")   # same names, no UNIQUE
    conn.commit()
    conn.close()

    code, report = preflight.check(str(db))
    assert code == preflight.OK
    assert "Names only" in report
    assert "UNIQUE" in report


def test_a_db_ahead_of_the_repo_is_green(tmp_path):
    """One-directional on purpose: `git checkout <previous-sha>` for a code
    rollback (ops/README.md § Rollback) leaves a DB that already ran the newer
    migration, and that must not read as a failure."""
    db = _live_db(tmp_path / "fund.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE signals ADD COLUMN from_the_future TEXT")
    conn.execute("CREATE TABLE a_later_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    assert _run(db).returncode == preflight.OK


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


def test_an_uninitialized_or_wrong_db_cannot_determine(tmp_path):
    """Zero of the 15 tables is a wrong FUND_DB or a database nothing has
    initialized — not drift. Reporting UNEXPLAINED DIVERGENCE would send an
    operator hunting a schema change that never happened."""
    db = tmp_path / "fund.sqlite"
    db.touch()                                    # zero bytes, valid to open
    proc = _run(db)

    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "none of the 15 tables" in proc.stderr


def test_a_database_that_is_not_the_fund_db_cannot_determine(tmp_path):
    """A SQLite file holding NONE of state/schema.sql's tables — someone
    else's database sitting on the FUND_DB path.

    Premise inverted by issue #172
    (https://github.com/benjaminematton/fund/issues/172#issuecomment-5460532548)
    — Group 2 unification landed. This test built its stand-in out of
    `trial_registry`, on the (then correct) reading that fundbt's trial
    registry lived in a separate database. `trial_registry` is now one of
    $FUND_DB's own 15 tables, so it can no longer stand for "not the fund DB"
    — it would be read as drift and reported as UNEXPLAINED DIVERGENCE. What
    this test asserts is unchanged; only the file it points at moved.
    """
    db = tmp_path / "someone_elses.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    proc = _run(db)
    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "alembic_version" in proc.stderr        # names what it did find


needs_mode_bits = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores directory mode. NOTE: master's ci.yml runs on"
           " ubuntu-latest with no `container:` key, so CI is uid 1001 and"
           " this DOES execute there. Adding a `container:` key would make it"
           " root and turn this green-by-skipping.")


@needs_mode_bits
def test_a_sidecar_that_cannot_be_created_cannot_determine(tmp_path):
    """The measured failure mode: SQLite must CREATE a `-wal`/`-shm` beside
    the main file and the directory does not allow it. This is the droplet's
    between-runs shape — WAL-mode header, sidecars cleanly checkpointed away.

    Replaces a test that monkeypatched sqlite3.connect to raise and blamed WAL
    recovery; the code never had that behaviour, so it pinned a fiction.
    """
    home = tmp_path / "locked"
    home.mkdir()
    db = _live_db(home / "fund.sqlite")
    _drop_sidecars(db)
    home.chmod(0o555)
    try:
        proc = _run(db)
    finally:
        home.chmod(0o755)                         # so tmp_path can be cleaned

    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "not writable" in proc.stderr
    assert "sidecar" in proc.stderr


@needs_mode_bits
def test_an_unwritable_directory_alone_is_not_a_failure(tmp_path):
    """The non-trigger, pinned so the rule stops drifting. With both sidecars
    already on disk there is nothing to create, and the open succeeds in an
    unwritable directory. "Unwritable dir fails the read" was asserted twice
    in review of this file and is false on its own."""
    home = tmp_path / "locked"
    home.mkdir()
    db = _wal_only_db(home / "fund.sqlite")       # leaves -wal AND -shm
    assert db.with_name(db.name + "-shm").exists()
    home.chmod(0o555)
    try:
        proc = _run(db)
    finally:
        home.chmod(0o755)

    assert proc.returncode == preflight.OK, proc.stderr


@needs_mode_bits
def test_a_corrupt_file_is_never_blamed_on_the_directory(tmp_path):
    """Headline and explanation must not disagree about the cause.

    Testing os.access before the SQLite error printed "file is not a database"
    over an explanation about directory permissions — sending an operator to
    chmod a directory when FUND_DB pointed at the wrong file. "file is not a
    database" comes back identically in either directory mode, so it can never
    mean a permission problem.
    """
    home = tmp_path / "locked"
    home.mkdir()
    db = home / "fund.sqlite"
    db.write_bytes(b"this is not a database")
    home.chmod(0o555)
    try:
        proc = _run(db)
    finally:
        home.chmod(0o755)

    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "not a database" in proc.stderr
    assert "not writable" not in proc.stderr
    assert "sidecar" not in proc.stderr


def test_a_wal_holding_the_only_copy_of_the_schema_is_read(tmp_path):
    """Why mode=ro and not immutable=1, pinned. With the DDL still in an
    uncheckpointed `-wal`, mode=ro reads all 15 tables; immutable=1 skips the
    WAL and sees the stale snapshot behind it, which would report a fully
    migrated database as empty. It also documents that a `-wal` needing replay
    does NOT fail the open — the old comment claimed it did."""
    db = _wal_only_db(tmp_path / "fund.sqlite")
    assert db.with_name(db.name + "-wal").exists()

    assert _run(db).returncode == preflight.OK

    stale = sqlite3.connect(
        f"file:{db}?immutable=1", uri=True)
    try:
        found = {r[0] for r in stale.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        stale.close()
    assert "events" not in found, "immutable=1 would have seen the WAL"


def test_a_crash_is_cannot_determine_not_migrations_pending(tmp_path,
                                                            monkeypatch):
    """An unhandled exception used to exit 1 — the MIGRATIONS_PENDING code —
    so a broken state/schema.sql told the operator to run a migration. Still
    red either way, but the wrong red, and distinguishable outcomes are the
    whole point of this script."""
    def boom(*a, **kw):
        raise sqlite3.OperationalError("incomplete input")

    monkeypatch.setattr(preflight, "expected_schema", boom)
    monkeypatch.setenv("FUND_DB", str(_live_db(tmp_path / "fund.sqlite")))
    out = io.StringIO()
    monkeypatch.setattr(preflight.sys, "stderr", out)

    assert preflight.main() == preflight.CANNOT_DETERMINE
    assert "preflight itself failed" in out.getvalue()
    assert "incomplete input" in out.getvalue()   # nothing swallowed
    assert "Traceback" in out.getvalue()


def test_a_broken_schema_sql_exits_cannot_determine(tmp_path):
    """End to end, through the real process: the reviewer's reproduction."""
    broken = tmp_path / "schema.sql"
    broken.write_text("CREATE TABLE oops (")
    db = _live_db(tmp_path / "fund.sqlite")

    env = {**os.environ, "FUND_DB": str(db)}
    proc = subprocess.run(
        [sys.executable, "-c",
         "import runpy, sys, pathlib;"
         f" sys.argv=['preflight_schema'];"
         f" import importlib.util;"
         f" spec=importlib.util.spec_from_file_location('pf', {str(SCRIPT)!r});"
         " m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
         f" m.SCHEMA=pathlib.Path({str(broken)!r}); sys.exit(m.main())"],
        capture_output=True, text=True, env=env, cwd=str(ROOT))

    assert proc.returncode == preflight.CANNOT_DETERMINE, proc.stderr
    assert proc.returncode != preflight.MIGRATIONS_PENDING


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

def test_the_registry_tables_are_in_scope(tmp_path):
    """trial_registry and holdout_evaluations ARE $FUND_DB tables, so preflight
    must expect them.

    Premise inverted by issue #172
    (https://github.com/benjaminematton/fund/issues/172#issuecomment-5460532548)
    — Group 2 unification landed. Until then this test read:

        fundbt/registry.py owns trial_registry/holdout_evaluations in a
        SEPARATE database. Expecting them in $FUND_DB would fail every run.

    That was true and this test was right to pin it. What #172 changed is the
    PREMISE, not the assertion's job: preflight's scope is still exactly
    state/schema.sql, and this still pins where that scope's edge falls — now
    from the other side. Recorded this way, and not by deleting the test, so a
    future reader sees a human decision rather than a session that edited a
    guard until it passed (CLAUDE.md: "Do not weaken or delete a red
    acceptance test to make it pass").
    """
    expected = preflight.expected_schema()
    assert "trial_registry" in expected
    assert "holdout_evaluations" in expected


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
