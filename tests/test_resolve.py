"""Offline coverage for orchestrator/resolve.py — the nightly resolutions job.

design.md §7 makes Phase 2 the phase where "memory is load-bearing", and the
feedback loop it needs had both ends and no middle: the `resolutions` table
existed and calibration/scoreboard.py consumed it, but nothing wrote it.

This is the producer. It is deterministic — no LLM, injected Clock, injected
market source — and it writes the calibration INPUT only (design §8:
"scoring -> PM weights is Phase 5").

The anchor is fixtures/golden-day.md's T+5 vector, which CLAUDE.md designates
the Phase-2 test vector. Its numbers are frozen: entry $180.14, NVDA $191.20
at horizon, SPY +1.1% over the window -> +6.14% realized, +5.04pp alpha.

No network, no keys — the source is faked.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from orchestrator.clock import SimClock
from orchestrator.resolve import resolve_due
from state.db import connect

# Golden day: run_date 2026-07-06 (Mon). Five TRADING days later is Mon
# 2026-07-13 — calendar arithmetic would say Jul 11, a Saturday with no bar.
RUN_DATE = "2026-07-06"
HORIZON_DATE = "2026-07-13"

# The nightly fire, 16:35 ET on the horizon date. Same reasoning as close_pnl:
# close_frame shifts its end back SIP_DELAY (16 min), so an earlier fire asks
# for a bar the closing auction has not finished writing.
NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)

# fixtures/golden-day.md: NVDA $191.20 at T+5, SPY +1.1% over the window.
SPY_OPEN, SPY_CLOSE = 640.0, 640.0 * 1.011
NVDA_ENTRY, NVDA_CLOSE = 180.14, 191.20


def _frame(nvda_last=NVDA_CLOSE, spy_last=SPY_CLOSE, sessions=6,
           end=HORIZON_DATE):
    """The close frame close_frame returns, one column per ticker.

    Daily bars are stamped tz-aware like the real feed, and exist only on
    trading days — which is what makes "five trading days forward" five rows
    forward rather than calendar arithmetic the repo has no helper for.
    """
    idx = pd.date_range(end=end, periods=sessions, freq="B",
                        tz="America/New_York")
    nvda = [180.0] * (sessions - 1) + [nvda_last]
    spy = [SPY_OPEN] * (sessions - 1) + [spy_last]
    return pd.DataFrame({"NVDA": nvda, "SPY": spy}, index=idx)


class _Source:
    """close_frame is the only read a resolution needs."""

    def __init__(self, frame=None):
        self._frame = _frame() if frame is None else frame
        self.calls: list = []

    def close_frame(self, tickers, end):
        self.calls.append((list(tickers), end))
        return self._frame.reindex(columns=list(tickers))


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "fund.sqlite")


@pytest.fixture
def clock():
    return SimClock(NIGHTLY)


def _decision(conn, *, run_date=RUN_DATE, ticker="NVDA", action="buy", qty=96,
              status="executed"):
    """One PM decision row. Returns its id."""
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (run_date, ticker, action, qty, "capex re-acceleration",
         "top-2 hyperscaler guides capex flat QoQ", status,
         f"{run_date}T15:00:00+00:00"))
    conn.commit()
    return cur.lastrowid


def _fill(conn, decision_id, *, ticker="NVDA", side="buy", qty=66,
          price=NVDA_ENTRY):
    """The ticket + filled order behind an executed decision. The fill price
    is the entry the golden day resolves against."""
    tid = f"ticket-{decision_id}"
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " expires_at, status, created_at) VALUES (?,?,?,?,?,?,'consumed',?)",
        (tid, decision_id, ticker, side, qty, f"{RUN_DATE}T16:00:00+00:00",
         f"{RUN_DATE}T15:15:00+00:00"))
    conn.execute(
        "INSERT INTO orders (client_order_id, alpaca_order_id, symbol, side,"
        " qty, status, filled_qty, filled_avg_price, submitted_at)"
        " VALUES (?,?,?,?,?,'filled',?,?,?)",
        (tid, f"alpaca-{decision_id}", ticker, side, qty, qty, price,
         f"{RUN_DATE}T15:30:00+00:00"))
    conn.commit()
    return tid


# --- the anchor -------------------------------------------------------------

def test_golden_day_t5_resolution_reproduces_the_frozen_fixture(conn, clock):
    """fixtures/golden-day.md's worked T+5 example, which CLAUDE.md names the
    Phase-2 test vector: entry $180.14 -> $191.20 is +6.14% realized, and SPY's
    +1.1% over the same window leaves +5.04pp of alpha.

    CLAUDE.md: never update a golden fixture to make a test pass.
    """
    did = _decision(conn)
    _fill(conn, did)

    resolve_due(conn, _Source(), clock)

    row = conn.execute(
        "SELECT * FROM resolutions WHERE decision_id = ?", (did,)).fetchone()
    assert row is not None, "the T+5 decision should have resolved"
    assert row["horizon_days"] == 5
    assert row["realized_return"] == pytest.approx(0.0614, abs=5e-5)
    assert row["alpha_vs_spy"] == pytest.approx(0.0504, abs=5e-5)
    assert row["invalidated"] == 0
    assert row["reflection"] is None


# --- invariant 4: no row beats a wrong row ----------------------------------

def test_a_decision_short_of_its_horizon_is_left_for_a_later_run(conn, clock):
    """Four sessions in and the fifth has not happened. The answer is not a
    four-day return relabelled as five — it is silence, and the decision
    resolves tomorrow."""
    did = _decision(conn)
    _fill(conn, did)

    counts = resolve_due(conn, _Source(_frame(sessions=5, end="2026-07-10")),
                         clock)

    assert conn.execute("SELECT COUNT(*) c FROM resolutions").fetchone()["c"] \
        == 0
    assert counts["pending"] == 1


def test_a_ticker_with_no_bars_produces_no_row_rather_than_a_zero(conn, clock):
    """close_frame hands a ticker with no bars in the window back as a NaN
    column rather than raising (_reshape_close_frame). A NaN that reaches the
    table lands as a resolution reading "exactly matched SPY" — invariant 4's
    exact failure: an unmeasured call and a flat one are the same row and mean
    opposite things."""
    blank = _frame()
    blank["NVDA"] = float("nan")
    did = _decision(conn)
    _fill(conn, did)

    counts = resolve_due(conn, _Source(blank), clock)

    assert conn.execute("SELECT COUNT(*) c FROM resolutions").fetchone()["c"] \
        == 0
    assert counts["skipped"] == 1


def test_a_hold_decision_resolves_against_the_decision_day_close(conn, clock):
    """A HOLD has no ticket and no fill, and is still a PM call about the
    ticker. calibration.md §0.4 fixes the event as "the ticker beats SPY over
    the horizon" — true or false whether or not the fund took the position.
    Resolving only what we traded would hand the scoreboard a sample selected
    by the PM's own convictions.
    """
    did = _decision(conn, action="hold", qty=0, status="held")

    resolve_due(conn, _Source(), clock)

    row = conn.execute(
        "SELECT * FROM resolutions WHERE decision_id = ?", (did,)).fetchone()
    assert row is not None, "a hold is a call, and calls get graded"
    # entry is the decision-day close ($180.00), not a fill that never existed
    assert row["realized_return"] == pytest.approx((191.20 - 180.0) / 180.0)


def test_rerunning_the_job_does_not_double_write(conn, clock):
    """The timer can fire twice (manual re-run after a failed drain). One
    decision is one resolution, forever — the table's UNIQUE(decision_id) says
    so and the job must not need it to say so."""
    did = _decision(conn)
    _fill(conn, did)

    resolve_due(conn, _Source(), clock)
    counts = resolve_due(conn, _Source(), clock)

    assert conn.execute("SELECT COUNT(*) c FROM resolutions").fetchone()["c"] \
        == 1
    assert counts["resolved"] == 0


def test_the_horizon_counts_trading_days_across_a_holiday(conn, clock):
    """Five TRADING days, not five calendar days. With a market holiday inside
    the window the two answers are different sessions, and the calendar one
    has no bar at all."""
    # Sessions with 2026-07-09 missing (holiday): 06, 07, 08, 10, 13, 14.
    idx = pd.DatetimeIndex([
        "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-10", "2026-07-13",
        "2026-07-14"]).tz_localize("America/New_York")
    frame = pd.DataFrame(
        {"NVDA": [180.0, 180.0, 180.0, 180.0, 180.0, NVDA_CLOSE],
         "SPY": [SPY_OPEN] * 5 + [SPY_CLOSE]}, index=idx)
    did = _decision(conn)
    _fill(conn, did)

    resolve_due(conn, _Source(frame), SimClock(
        datetime(2026, 7, 14, 20, 35, tzinfo=timezone.utc)))

    row = conn.execute(
        "SELECT * FROM resolutions WHERE decision_id = ?", (did,)).fetchone()
    # T+5 sessions is 2026-07-14 ($191.20). Five CALENDAR days is 07-11, a day
    # with no bar; counting rows on a calendar basis would land on 07-13's
    # $180.00 and report a flat call.
    assert row["realized_return"] == pytest.approx(0.0614, abs=5e-5)
