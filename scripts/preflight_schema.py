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
decides when to migrate, with a backup." This opens `file:...?mode=ro`
instead — and `mode=ro`, not `immutable=1`: the live DB is in WAL, and
`immutable` bypasses the WAL to read a snapshot that can be torn.

FOUR OUTCOMES, and the middle two are the point. A live database that has
never run migrations.apply() genuinely differs from schema.sql, and that
difference is expected and actionable. A difference no migration explains is a
real failure. They get distinct exit codes and distinct headlines so an
operator can tell "run the migration" from "something is wrong" without
reading this file:

    0  OK                      — every table present, nothing pending
    1  MIGRATIONS PENDING      — behind, and a known migration closes the gap
    2  UNEXPLAINED DIVERGENCE  — differs in a way no migration explains
    3  CANNOT DETERMINE        — unset/missing/unreadable/WAL needs recovery

Ambiguity is red, never green (invariant 4).

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


def expected_schema() -> dict[str, set[str]]:
    """{table: columns} that schema.sql declares.

    Built by running the DDL into an in-memory database rather than parsing
    it: the parser that would be needed to get CHECK constraints and trailing
    comments right is a second implementation of SQLite, and a schema file the
    real engine accepts is the only definition of what the file means.
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


def _open_readonly(db_path: str) -> sqlite3.Connection:
    """Open `db_path` with no way to write it. Raises sqlite3.Error if it
    cannot be read — a missing file, a non-database, or a WAL needing replay
    all land here, and all mean 'cannot determine'."""
    uri = f"file:{pathname2url(str(Path(db_path).resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check(db_path: str | None) -> tuple[int, str]:
    """Report on the database at `db_path`. Returns (exit code, report).

    Writes nothing, ever — including to the database it is reporting on.
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
        conn = _open_readonly(str(path))
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
        # A WAL that needs replaying reports SQLITE_READONLY_RECOVERY here
        # rather than handing back a torn snapshot. Unreadable is red.
        return (CANNOT_DETERMINE,
                f"CANNOT DETERMINE: cannot read {path} — {exc}\n"
                "  The live DB runs in WAL; a read-only open refuses rather"
                " than replaying the log or reading a torn snapshot. Preflight"
                " has NOT checked the live schema.")

    expected = expected_schema()
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
        lines = ["UNEXPLAINED DIVERGENCE: the live schema differs from"
                 " state/schema.sql in a way no migration explains."]
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
        return (MIGRATIONS_PENDING,
                "MIGRATIONS PENDING: the live schema is behind"
                " state/schema.sql.\n"
                f"  pending: {', '.join(behind)}\n"
                "  columns absent: "
                + ", ".join(f"{t}.{c}" for t, c in missing_columns) + "\n"
                f"  db: {path}\n"
                "  Expected and actionable. Take a backup"
                " (ops/backup.sh), then apply the migration deliberately —"
                " preflight only reports, and a human decides when to"
                " migrate. Left alone, the next connect() applies it"
                " unattended at the first tool call of a trading day.")

    return (OK,
            f"OK: live schema matches state/schema.sql ({len(expected)} tables)"
            f" and no migrations are pending.\n  db: {path}")


def main() -> int:
    code, report = check(os.environ.get("FUND_DB"))
    print(report, file=sys.stdout if code == OK else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
