#!/usr/bin/env python3
"""Report the LIVE database's schema state. Read-only; never migrates (#17).

`make preflight` used to run eval_suite.py alone, which builds a fresh
per-trial DB. Preflight therefore checked schema against a database it had
created moments earlier and never opened `$FUND_DB` — so a green preflight said
nothing about whether a pending migration had reached the droplet. On
2026-08-19 the attribution columns were 0/6 on the live DB behind a green
preflight, and the next connect() would have ALTERed production unattended, at
09:35, mid trading day.

WHY NOT state.db.connect(). connect() ends with _apply_migrations(), so the
obvious way to open the DB is the one that silently applies the very thing
being reported on. #17 puts that out of scope: "Preflight reports; a human
decides when to migrate, with a backup." This opens `file:...?mode=ro` instead.

WHY mode=ro AND NOT immutable=1, measured rather than assumed. The live DB runs
in WAL, so recent commits can live in the `-wal` file and not yet in the main
one. Against a database whose whole DDL was still in an uncheckpointed `-wal`,
`mode=ro` read all 11 tables and `immutable=1` raised "no such table: events" —
it had skipped the WAL and read the stale snapshot behind it. `immutable=1`
would therefore report a fully-migrated DB as empty.

WHAT IS COMPARED — names only, and this is a deliberate limit, not an
oversight. Table names and column names, via PRAGMA table_info. Types, NOT
NULL, DEFAULT, CHECK and UNIQUE are NOT compared: a live DB whose columns were
added by ALTER TABLE stores different `sqlite_master.sql` text than a fresh
CREATE from schema.sql, so a text or constraint comparison would red-flag the
normal post-migration database. Comparing constraints is a real check and a
separate decision; until it is made, a `tickets` table missing its
`UNIQUE (decision_id)` (the constraint behind invariant 5) passes here. Every
message says so rather than implying a match it did not test.

ONE DIRECTION. This asks "does the live DB have everything schema.sql
declares", not "are the two identical". A live DB that is AHEAD — an extra
table, an extra column — is green. That is deliberate: it keeps the code
rollback at ops/README.md § Rollback green, since `git checkout <previous-sha>`
leaves a database that already ran the newer migration.

FOUR OUTCOMES, and the middle two are the point. A live database that has
never run migrations.apply() genuinely differs from schema.sql, and that
difference is expected and actionable. A difference no migration explains is a
real failure. They get distinct exit codes and distinct headlines so an
operator can tell "run the migration" from "something is wrong" without
reading this file:

    0  OK                      — every declared table and column is present
    1  MIGRATIONS PENDING      — behind, and a known migration closes the gap
    2  UNEXPLAINED DIVERGENCE  — differs in a way no migration explains
    3  CANNOT DETERMINE        — unset, missing, unreadable, not the fund DB,
                                 or preflight itself failed

Ambiguity is red, never green (invariant 4) — including a crash in this script.

EXPECTED SCHEMA IS state/schema.sql ALONE. migrations.py is the catch-up path
TO that file, not a second source of truth; a union of a target and the
mechanism for reaching it is just the target. Scope is `$FUND_DB` only —
fundbt/registry.py's trial_registry/holdout_evaluations live in a separate
database with its own DDL home, and expecting them here would fail every run.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import traceback
from pathlib import Path
from urllib.request import pathname2url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from state.migrations import MIGRATIONS, pending  # noqa: E402

SCHEMA = ROOT / "state" / "schema.sql"

OK = 0
MIGRATIONS_PENDING = 1
UNEXPLAINED_DIVERGENCE = 2
CANNOT_DETERMINE = 3

_NAMES_ONLY = ("  Names only: table and column names are compared, not types,"
               " NOT NULL, DEFAULT, CHECK or UNIQUE.")


def expected_schema() -> dict[str, set[str]]:
    """{table: column names} that schema.sql declares.

    Built by running the DDL into an in-memory database rather than parsing
    it: the parser needed to get CHECK constraints, nested parentheses and
    trailing comments right is a second implementation of SQLite, and a schema
    file the real engine accepts is the only definition of what the file
    means. Only names survive — see the module docstring on what that does and
    does not catch.
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA.read_text())
        tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'")]
        return {t: {r[1] for r in ref.execute(f"PRAGMA table_info({t})")}
                for t in tables}
    finally:
        ref.close()


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open `db_path` with no way to write its contents.

    Raises sqlite3.Error when it cannot be read at all — see check() for what
    the failures actually mean.
    """
    uri = f"file:{pathname2url(str(db_path.resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check(db_path: str | None) -> tuple[int, str]:
    """Report on the database at `db_path`. Returns (exit code, report).

    Never writes the database's CONTENTS — no row, no column, no schema, and
    in particular no migration. It is not inert on the filesystem: opening a
    WAL database read-only creates or refreshes the `-shm`/`-wal` sidecars
    next to it, which is SQLite's own bookkeeping and cannot alter what the
    database holds. Measured, not assumed: an ADD-COLUMN-pending DB still
    reports pending after a run, with the main file's mtime unchanged.
    """
    if db_path is None or not db_path.strip():
        return (CANNOT_DETERMINE,
                "CANNOT DETERMINE: FUND_DB is unset or blank, so preflight has"
                " not checked the live schema.\n"
                "  A preflight that did not look is not a green one"
                " (invariant 4). Set FUND_DB (it comes from"
                " /etc/fund/env on the droplet) and re-run.")
    path = Path(db_path.strip())
    if not path.is_file():
        return (CANNOT_DETERMINE,
                f"CANNOT DETERMINE: no database file at {path}.\n"
                "  Nothing was created: preflight opens the live DB read-only"
                " and never brings one into existence. Check FUND_DB.")
    try:
        conn = _open_readonly(path)
        try:
            live_tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'")}
            live_columns = {
                t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
                for t in live_tables}
            behind = pending(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CANNOT_DETERMINE, _unreadable(path, exc)

    expected = expected_schema()
    # NONE of the expected tables present is not drift, it is the wrong file
    # or an uninitialized one — a zero-byte $FUND_DB, a path typo, or the
    # separate fundbt registry DB. Reporting that as divergence would send an
    # operator hunting a schema change that never happened.
    if not (set(expected) & live_tables):
        found = ", ".join(sorted(live_tables)) if live_tables else "none"
        return (CANNOT_DETERMINE,
                f"CANNOT DETERMINE: {path} holds none of the {len(expected)}"
                " tables state/schema.sql declares.\n"
                f"  tables found: {found}\n"
                "  That is a wrong FUND_DB path or a database that was never"
                " initialized, not schema drift — preflight cannot say"
                " anything about the live schema from it.")

    missing_tables = sorted(set(expected) - live_tables)
    missing_columns = sorted(
        (t, c) for t, columns in expected.items() if t in live_tables
        for c in columns - live_columns[t])
    # A missing column is explained when a pending migration adds it. A
    # missing TABLE never is: every migration is an additive ALTER, and
    # nothing here creates a table.
    explained = {pair for name in behind for pair in MIGRATIONS[name]}
    unexplained = [pair for pair in missing_columns if pair not in explained]

    if missing_tables or unexplained:
        lines = ["UNEXPLAINED DIVERGENCE: the live schema is missing something"
                 " state/schema.sql declares, and no migration adds it."]
        if missing_tables:
            lines.append(f"  tables absent: {', '.join(missing_tables)}")
        if unexplained:
            lines.append("  columns absent: "
                         + ", ".join(f"{t}.{c}" for t, c in unexplained))
        lines.append(f"  db: {path}")
        lines.append("  Running a migration will NOT fix this. Investigate"
                     " before deploying — the live DB is not the database this"
                     " code was written against.")
        return UNEXPLAINED_DIVERGENCE, "\n".join(lines)

    if behind:
        lines = ["MIGRATIONS PENDING: the live schema is behind"
                 " state/schema.sql.",
                 f"  pending: {', '.join(behind)}"]
        if missing_columns:
            lines.append("  columns absent: "
                         + ", ".join(f"{t}.{c}" for t, c in missing_columns))
        lines += [
            f"  db: {path}",
            "  Expected and actionable. Take a backup (ops/backup.sh), then"
            " apply the migration deliberately, following ops/README.md"
            " § 'Deploy a code change' step 3 — which has the exact command"
            " and the post-checks to run against the DB afterwards.",
            "  Preflight only reports; a human decides when to migrate. Left"
            " alone, the next connect() applies it unattended at the first"
            " tool call of a trading day."]
        return MIGRATIONS_PENDING, "\n".join(lines)

    return (OK,
            "OK: the live DB has every table and column state/schema.sql"
            f" declares ({len(expected)} tables) and no migrations are"
            f" pending.\n{_NAMES_ONLY}\n  db: {path}")


def _unreadable(path: Path, exc: sqlite3.Error) -> str:
    """Why a read-only open failed, without inventing a cause.

    The comment here used to say a read-only open "refuses rather than
    replaying the log" when the WAL needs recovery. Measured, that is false: a
    `-wal` holding uncommitted-to-main data with no `-shm` beside it opens
    fine and reads the WAL's contents, recreating the `-shm`. What actually
    fails is a DIRECTORY this uid cannot write, because the sidecar cannot be
    created there — reported as either "unable to open database file" or
    "attempt to write a readonly database" depending on whether a `-wal`
    exists. Naming WAL recovery on every sqlite3.Error sent an operator to
    inspect a write-ahead log when FUND_DB simply pointed at the wrong file.
    """
    head = f"CANNOT DETERMINE: cannot read {path} — {exc}"
    if not os.access(path.parent, os.W_OK):
        return (f"{head}\n"
                f"  {path.parent} is not writable by this uid. A WAL database"
                " needs its `-shm` sidecar beside the main file, and a"
                " read-only open still has to create it — so an unwritable"
                " DIRECTORY blocks the read. On the droplet this step runs as"
                " uid `fund`, which owns /var/lib/fund.\n"
                "  Preflight has NOT checked the live schema.")
    return (f"{head}\n"
            "  The file exists but SQLite would not read it: not a database,"
            " a truncated or corrupt one, or unreadable by this uid. Check"
            " that FUND_DB names the fund's SQLite file.\n"
            "  Preflight has NOT checked the live schema.")


def main() -> int:
    try:
        code, report = check(os.environ.get("FUND_DB"))
    except Exception:
        # A crash must not exit 1 — that is MIGRATIONS_PENDING, and an
        # operator reading "run the migration" off a traceback is exactly the
        # mislabelling this script exists to end. Nothing is swallowed: the
        # traceback is printed in full. Ambiguity is red (invariant 4).
        code = CANNOT_DETERMINE
        report = ("CANNOT DETERMINE: preflight itself failed before it could"
                  " report on the live schema.\n"
                  + "".join(f"  {line}\n" for line in
                            traceback.format_exc().splitlines())
                  + "  This is a bug in preflight or a broken"
                    " state/schema.sql, NOT a finding about the live DB.")
    print(report, file=sys.stdout if code == OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
