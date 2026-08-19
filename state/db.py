"""SQLite is the source of truth (invariant 6). One connect() for app + tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the fund DB at `db_path`, creating the schema when absent."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "tickets" not in have:
        conn.executescript(_SCHEMA.read_text())
        conn.commit()
    # Apply schema, THEN migrate. The block above fires only when `tickets` is
    # absent — i.e. on a database this code has never created — so a column
    # added to schema.sql never reaches one that already exists. This is what
    # carries it there. Imported here rather than at module scope to keep the
    # import graph acyclic.
    #
    # State the guard's ACTUAL condition, not an approximation of it: this
    # comment said "only fires on an empty file", which was close enough to be
    # unremarkable and wrong enough to mislead. The two diverge the moment the
    # sentinel changes, and a reader checking whether a new table reaches a
    # live DB gets the wrong answer from the comment while the code is right.
    from state.migrations import apply as _apply_migrations
    _apply_migrations(conn)
    return conn
