"""SQLite is the source of truth (invariant 6). One connect() for app + tests."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")
# Parsed, not restated: a hand-maintained list is a second source of truth
# that drifts the first time someone adds a table and forgets this line.
_TABLES = frozenset(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                               _SCHEMA.read_text()))


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the fund DB at `db_path`, creating the schema when absent."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    # Guard on EVERY expected table, not one sentinel. The old `tickets` check
    # meant a table added to schema.sql later never reached an existing DB —
    # fresh eval trial DBs would have it and the droplet's live
    # /var/lib/fund/fund.sqlite would not, a silent divergence between what is
    # tested and what runs. `_TABLES` is parsed from the schema itself, so a
    # new table is picked up with no second list to keep in sync.
    #
    # One cheap query, and the script runs only when something is missing:
    # connect() is called per TOOL CALL (agents/seats.py hands
    # build_fund_server a conn_factory), so an unconditional executescript
    # would take a write lock on every submit_signal and every gate hook.
    #
    # New TABLES only. CREATE TABLE IF NOT EXISTS is a no-op against an
    # existing table, so a new COLUMN needs an ALTER and is not covered here.
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not _TABLES <= have:
        conn.executescript(_SCHEMA.read_text())
        conn.commit()
    # Apply schema, THEN migrate. The block above adds missing TABLES only —
    # CREATE TABLE IF NOT EXISTS is a no-op against a table that already
    # exists, so a COLUMN added to schema.sql never reaches an existing
    # database and this is what carries it there. Imported here rather than at
    # module scope to keep the import graph acyclic.
    #
    # State the guard's ACTUAL condition, never a paraphrase of it. This
    # comment once said "fires only on an empty file" — close enough to be
    # unremarkable, wrong enough to mislead, and load-bearing the moment the
    # guard above it changed. A paraphrased condition is a second
    # implementation with no test.
    from state.migrations import apply as _apply_migrations
    _apply_migrations(conn)
    return conn
