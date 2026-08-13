"""Default critique rows. MVF has no Critic seat, so the orchestrator opens
the decision stage by inserting one `clear` critique per active ticker —
otherwise submit_decision's critique-row guard (contracts §4) refuses every
PM call. Lives in state/ (not agents/) because orchestrator/ must not import
from agents/ (CLAUDE.md); agents.tools.fund_server re-exports it."""

from __future__ import annotations

import sqlite3


def insert_default_critiques(conn: sqlite3.Connection, run_date: str,
                             tickers: list[str], note: str,
                             now_iso: str) -> None:
    """Idempotent — INSERT OR IGNORE makes a re-run a no-op."""
    for ticker in tickers:
        conn.execute(
            "INSERT OR IGNORE INTO critiques (run_date, ticker, verdict,"
            " objections, note, created_at)"
            " VALUES (?, ?, 'clear', '[]', ?, ?)",
            (run_date, ticker, note, now_iso))
    conn.commit()
