"""Build the DB/journal state a seat's turn starts from.

The rule that matters here: preconditions MIRROR the production stage body
that runs immediately before the seat's turn, and reuse its helpers rather
than hand-rolling equivalents. `submit_decision` refuses without a critiques
row (agents/tools/fund_server.py:95), so a hand-rolled PM fixture fails every
case for a reason that has nothing to do with the agent. Rather than patch
that one discovery, each seat gets a named precondition function pointing at
the production body it mirrors — so the next seat's hidden precondition is
found by reading one map, not by debugging a suite of red cases.

Rows are written through the production HANDLERS (handle_submit_signal,
insert_default_critiques), never through hand-written INSERTs: a fixture that
can construct a state production cannot is a fixture that tests nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agents.tools.fund_server import handle_submit_signal
from orchestrator.clock import et_run_date, iso
from state.critiques import insert_default_critiques
from state.db import connect
from state.journal import append_entry

from evals.cases import Case


@dataclass
class CaseState:
    conn: sqlite3.Connection
    db_path: Path
    journals_root: Path
    run_date: str
    snapshot: Callable[[], dict]
    events_watermark: int      # only events ABOVE this id are the seat's own


def _pm_preconditions(conn, case: Case, now_iso: str, run_date: str) -> None:
    """Mirrors the pre-turn half of orchestrator/daily.py:161 run_decision:
    default `clear` critiques for every active ticker, inserted BEFORE the PM
    turn because without them every submit_decision call errors."""
    insert_default_critiques(conn, run_date, case.tickers, "no_critic_seat",
                             now_iso)


def _analyst_preconditions(conn, case: Case, now_iso: str,
                           run_date: str) -> None:
    """Mirrors the pre-turn half of orchestrator/daily.py:139 run_research —
    which writes NOTHING before the turn. The neutral/0 "no report" defaults
    that function inserts run AFTER the analyst turn, so they must not be
    seeded here: seeding them would hand the seat a fait accompli and hide
    exactly the silent-seat failure the rig exists to catch."""
    return


def _news_preconditions(conn, case: Case, now_iso: str,
                        run_date: str) -> None:
    """Mirrors the same production body as the analyst — orchestrator/daily.py
    run_research writes nothing before either research turn, and since
    2026-08-19 that stage runs both seats from one `research_seats` list.

    Named separately rather than aliased to the analyst's function so that the
    day the two seats' preconditions diverge, the divergence has somewhere to
    go. An alias would make that a shared-function edit affecting both seats.

    The neutral/0 "no report" defaults run AFTER the turn and must not be
    seeded: seeding them hands the seat a fait accompli and hides the silent-
    seat failure I4 exists to catch."""
    return


PRECONDITIONS = {"pm": _pm_preconditions, "analyst": _analyst_preconditions,
                 "news": _news_preconditions}


def build_case_state(case: Case, db_path: Path | str,
                     journals_root: Path | str) -> CaseState:
    """Fresh DB + fresh journals for ONE trial (never shared across trials —
    shared state produces correlated failures and lets a trial read the
    previous one's artifacts)."""
    if case.seat not in PRECONDITIONS:
        raise ValueError(
            f"no precondition mirror for seat {case.seat!r} — expected one of"
            f" {sorted(PRECONDITIONS)}. Add one that names the production"
            " stage body it mirrors; do not fall through to 'no setup'.")
    db_path, journals_root = Path(db_path), Path(journals_root)
    conn = connect(db_path)
    run_date = et_run_date(case.clock)
    now_iso = iso(case.clock)

    for sig in case.signals:
        args = {k: sig[k] for k in ("ticker", "direction", "confidence",
                                    "summary")}
        result = handle_submit_signal(conn, seat=sig.get("agent", "analyst"),
                                      args=args, run_date=run_date,
                                      now_iso=now_iso)
        if not result.get("ok"):        # fail fast: a half-built brief would
            raise ValueError(           # grade as an agent failure
                f"case {case.id!r}: could not seed signal {args!r} —"
                f" {result.get('error')}")

    PRECONDITIONS[case.seat](conn, case, now_iso, run_date)

    if case.journal:
        append_entry(journals_root, case.seat, run_date, case.journal)

    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
    snapshot = dict(case.snapshot)
    return CaseState(conn=conn, db_path=db_path, journals_root=journals_root,
                     run_date=run_date, snapshot=lambda: dict(snapshot),
                     events_watermark=row["m"])
