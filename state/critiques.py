"""Default critique rows. MVF has no Critic seat, so the orchestrator opens
the decision stage by inserting one `clear` critique per active ticker —
otherwise the critique-row guard in submit_decision (contracts §4) refuses
every PM call. Lives in state/ (not agents/) because orchestrator/ must not
import from agents/ (CLAUDE.md); agents.tools.fund_server re-exports it."""

from __future__ import annotations

import sqlite3


def insert_default_critiques(conn: sqlite3.Connection, run_date: str,
                             tickers: list[str], note: str,
                             now_iso: str) -> None:
    """Idempotent — INSERT OR IGNORE makes a re-run a no-op.

    Attribution is the literal 'none': no charter and no model produced these
    rows, the orchestrator did, and claiming a version would be a fabrication.
    It is not left to the column default either, because that default is
    'unknown' — which means "predates attribution" and would collapse two
    different facts into one value.

    When a real Critic seat exists, its handler binds real values with no
    schema change, and charter_version is then what separates real critiques
    from the placeholder era without parsing `note` strings. That seat is
    blocked on a contract change rather than on implementation: submit_critique
    does not exist, and contracts §4 defines its input as a Slack-only draft
    that invariant 6 forbids reading.
    """
    for ticker in tickers:
        conn.execute(
            "INSERT OR IGNORE INTO critiques (run_date, ticker, verdict,"
            " objections, note, created_at, charter_version, model_id)"
            " VALUES (?, ?, 'clear', '[]', ?, ?, 'none', 'none')",
            (run_date, ticker, note, now_iso))
    conn.commit()
