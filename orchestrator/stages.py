"""Execution stage handler (Phase 1). No LLM code, no wall clock, no agents
import — the trader turn arrives as an injected callable, the Slack port as
an injected object. The body now lives in orchestrator/daily.py's run_execution
(checkpoint CAS via run_stage, zero-ticket skip); this is the Phase 1 entry
point kept for callers that hold a bare connection rather than a StageCtx."""

from __future__ import annotations

import sqlite3
from typing import Callable

from orchestrator.clock import Clock
from orchestrator.daily import StageCtx, run_execution

def run_execution_stage(conn: sqlite3.Connection, *, run_date: str,
                        clock: Clock, run_trader_turn: Callable[[], None],
                        slack) -> str:
    return run_execution(
        StageCtx(conn=conn, run_date=run_date, clock=clock, slack=slack),
        run_trader_turn)
