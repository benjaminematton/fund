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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> list[str]:
    """Bring `conn` up to date. Returns the migrations applied, [] if current.

    Reported per migration, not per column: a partially-migrated database (one
    table done, one not) completes and says "0001_attribution" once, because
    what the caller wants to know is whether the schema moved.
    """
    applied: list[str] = []
    missing = [(t, c) for t, c in _ATTRIBUTION if c not in _columns(conn, t)]
    if missing:
        for table, column in missing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}"
                         " TEXT NOT NULL DEFAULT 'unknown'")
        conn.commit()
        applied.append("0001_attribution")
    return applied
