"""Execution stage handler (Phase 1). No LLM code, no wall clock, no agents
import — the trader turn arrives as an injected callable, the Slack port as
an injected object. Re-runnable end to end (contracts §5.2):
  done    -> skip (stages 'done' never re-run, contracts §6)
  pending -> CAS to running, run
  running -> crash resume: re-run the idempotent body"""

from __future__ import annotations

import sqlite3
from typing import Callable

from gate.tickets import expire_open_tickets
from orchestrator.clock import Clock, iso
from slackkit.outbox import drain
from state.transition import try_transition

STAGE = "execution"


def run_execution_stage(conn: sqlite3.Connection, *, run_date: str,
                        clock: Clock, run_trader_turn: Callable[[], None],
                        slack) -> str:
    now = iso(clock.now())
    expire_open_tickets(conn, now)  # gate expiry is clock-injected (§0)
    key = {"run_date": run_date, "stage": STAGE, "ticker": "*"}
    conn.execute(
        "INSERT OR IGNORE INTO checkpoints (run_date, stage, ticker, status,"
        " updated_at) VALUES (?, ?, '*', 'pending', ?)",
        (run_date, STAGE, now))
    conn.commit()
    status = conn.execute(
        "SELECT status FROM checkpoints WHERE run_date = ? AND stage = ?"
        " AND ticker = '*'", (run_date, STAGE)).fetchone()["status"]
    if status == "done":
        return "done"
    if status == "pending":
        try_transition(conn, "checkpoints", key, "pending", "running", now)
    # status 'running' falls through: crash resume re-runs the idempotent body
    run_trader_turn()
    done_at = iso(clock.now())
    try_transition(conn, "checkpoints", key, "running", "done", done_at)
    drain(conn, slack, done_at)
    return "done"
