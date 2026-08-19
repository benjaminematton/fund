"""Nightly resolutions producer (design.md §8) — the middle of the feedback
loop.

The `resolutions` table and its consumer (calibration/scoreboard.py) both
existed; nothing wrote the rows between them. This job does, and only that:
resolutions are the calibration INPUT, and "scoring -> PM weights is Phase 5".

Deterministic by construction — SQLite plus daily closes, injected Clock,
injected source. No LLM, so `reflection` stays NULL here; it is an agent write
and belongs to whatever builds the reflection turn.

TRADING DAYS, NOT CALENDAR DAYS. `horizon_days: 5` means five sessions. The
repo has no trading-day calendar and does not need one: daily bars exist only
on trading days, so five sessions forward is five ROWS forward in close_frame's
index. Counting calendar days would resolve across a holiday and make every
alpha quietly wrong.

Purity-linted like the rest of orchestrator/: nothing here reads the wall clock
or the network.
"""

from __future__ import annotations

from orchestrator.clock import et_run_date, iso
from orchestrator.pnl import _finite

BENCHMARK = "SPY"

# Every decision that has not resolved yet, with the fill price behind it when
# one exists. LEFT JOINs throughout: a held or rejected decision has no ticket
# and no order, and it still gets graded — the scoring event is "the TICKER
# beats SPY over the horizon" (calibration.md §0.4), which is true or false
# whether or not the fund took the position. Resolving only what we traded
# would feed the scoreboard a sample selected by the PM's own convictions.
_DUE = """
SELECT d.id, d.run_date, d.ticker, o.filled_avg_price AS fill
  FROM decisions d
  LEFT JOIN tickets t ON t.decision_id = d.id
  LEFT JOIN orders  o ON o.client_order_id = t.id AND o.filled_qty > 0
  LEFT JOIN resolutions r ON r.decision_id = d.id
 WHERE r.id IS NULL
 ORDER BY d.run_date, d.id
"""


def resolve_due(conn, source, clock, horizon_days: int = 5) -> dict:
    """Write a `resolutions` row for every decision that has reached its
    horizon. Returns `{resolved, skipped, pending}` for the job log.

    MUST run after the close has settled — close_frame shifts its end back
    SIP_DELAY (16 min), so an earlier fire asks for a bar the closing auction
    has not finished writing, and the horizon session simply reads as absent.

    A decision whose numbers cannot be computed produces NO ROW, never a zero
    (invariant 4): an unmeasured call and a call that exactly matched SPY are
    the same row in a scoreboard and mean opposite things. Such a decision
    stays due and resolves on a later run once the data is there.
    """
    now = clock.now()
    due = conn.execute(_DUE).fetchall()
    if not due:
        return {"resolved": 0, "skipped": 0, "pending": 0}

    tickers = sorted({r["ticker"] for r in due} | {BENCHMARK})
    frame = source.close_frame(tickers, end=now)
    sessions = [et_run_date(ts) for ts in frame.index]

    counts = {"resolved": 0, "skipped": 0, "pending": 0}
    for row in due:
        outcome = _measure(frame, sessions, row, horizon_days)
        if outcome is None:
            # Not yet due and not measurable are both "no row". They are
            # counted apart only so an operator does not read a young decision
            # as a data-pipeline fault.
            counts["pending" if _too_young(sessions, row["run_date"],
                                           horizon_days) else "skipped"] += 1
            continue
        realized, alpha = outcome
        conn.execute(
            "INSERT INTO resolutions (decision_id, horizon_days,"
            " realized_return, alpha_vs_spy, invalidated, resolved_at)"
            " VALUES (?,?,?,?,0,?)",
            (row["id"], horizon_days, realized, alpha, iso(now)))
        counts["resolved"] += 1
    conn.commit()
    return counts


def _measure(frame, sessions: list[str], row, horizon_days: int):
    """`(realized_return, alpha_vs_spy)` for one decision.

    `invalidated` is not decided here. The scoring event is fixed to the
    ticker's move over the full horizon, so an early exit must not shorten the
    window; and the two invalidation signals the fund actually has — a
    broker-enforced stop leg, and Ops' watch on the free-text condition — are
    neither of them readable from this job. Writing 0 is the honest value
    until one of them has a feed.
    """
    ticker = row["ticker"]
    if ticker not in frame.columns or row["run_date"] not in sessions:
        return None
    start = sessions.index(row["run_date"])
    end = start + horizon_days
    if end >= len(sessions):
        return None                       # horizon has not been reached yet

    # A held or rejected decision has no fill, so its entry is the close of
    # the day the call was made — the price the PM was looking at.
    entry = _finite(row["fill"]) or _finite(frame[ticker].iloc[start])
    exit_ = _finite(frame[ticker].iloc[end])
    spy_in = _finite(frame[BENCHMARK].iloc[start])
    spy_out = _finite(frame[BENCHMARK].iloc[end])
    if not entry or entry <= 0 or exit_ is None or not spy_in or spy_in <= 0 \
            or spy_out is None:
        return None                       # a ticker with no bars arrives NaN

    realized = (exit_ - entry) / entry
    return realized, realized - (spy_out - spy_in) / spy_in


def _too_young(sessions: list[str], run_date: str, horizon_days: int) -> bool:
    """True when the decision has simply not reached its horizon yet, as
    opposed to being unmeasurable."""
    return run_date in sessions \
        and sessions.index(run_date) + horizon_days >= len(sessions)
