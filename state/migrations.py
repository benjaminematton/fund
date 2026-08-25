"""Schema migrations for databases that already exist.

state/db.py's connect() executes schema.sql only when the DB is empty, so a
column added to that file never reaches a live database — including the
droplet's. This is the repo's first migration; before it, the only way to add a
column was to lose every row.

Every migration is additive and idempotent. Nothing here drops or rewrites a
column: a migration that can destroy data is one nobody dares run.

WHY 'unknown' AND NOT NULL. SQLite permits ADD COLUMN ... NOT NULL only with a
non-null default, so a default is forced. That constraint happens to land on
the honest value: charters were already versioned (pm.md is at v6) and no
record survives of which version wrote which historical row, so 'unknown' is
what those rows mean. NOT NULL is then deliberate rather than incidental — a
NULL drops silently out of a GROUP BY and out of every `=`, which would make
excluding un-attributed rows from a charter comparison an accident of SQL
semantics instead of a clause someone wrote on purpose.

The third value, 'none', is bound by the orchestrator's own writers: a row it
produced because a seat was silent had no charter and no model behind it.
"""

from __future__ import annotations

import sqlite3

# (table, column) pairs added by 0001. Every table here records an agent's
# judgment; a judgment table without attribution is an exception, and one
# exception erodes the rule that made the columns worth adding.
_ATTRIBUTION = (
    ("signals", "charter_version"),
    ("signals", "model_id"),
    ("decisions", "charter_version"),
    ("decisions", "model_id"),
    ("critiques", "charter_version"),
    ("critiques", "model_id"),
)

# The one list of what each migration adds. Both the writer (apply) and the
# reader (pending) consume it, so a migration cannot be applied by one and
# invisible to the other — which is exactly how a preflight comes back green
# against a database that is behind. Adding a migration means adding an entry
# here and nothing else.
MIGRATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "0001_attribution": _ATTRIBUTION,
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def pending(conn: sqlite3.Connection) -> list[str]:
    """Migrations `conn` has not had applied. Reads only — writes nothing.

    The seam scripts/preflight_schema.py needs: it must report whether the
    live database is behind WITHOUT bringing it up to date. connect() applies
    migrations, so asking that question through connect() answers it by
    changing it.

    Reported per migration, not per column: a partially-migrated database (one
    table done, one not) says "0001_attribution" once, because what the caller
    wants to know is whether the schema is current.
    """
    return [name for name, columns in MIGRATIONS.items()
            if any(c not in _columns(conn, t) for t, c in columns)]


def apply(conn: sqlite3.Connection) -> list[str]:
    """Bring `conn` up to date. Returns the migrations applied, [] if current."""
    applied = pending(conn)
    for name in applied:
        for table, column in MIGRATIONS[name]:
            if column not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}"
                             " TEXT NOT NULL DEFAULT 'unknown'")
    if applied:
        conn.commit()
    return applied
