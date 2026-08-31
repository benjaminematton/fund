"""orchestrator/improve.py — the nightly scoring job (specs/improvement.md
§2.1) and the reads the briefs make of the `weights` table.

Pure by construction: an injected SimClock, an injected WeightsConfig, a temp
SQLite. The fixture below is built so every number in the scoreboard row can
be checked by hand — see test_the_weights_row_carries_the_calibration_values
for the arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from orchestrator.clock import SimClock, iso
from orchestrator.improve import (WeightsConfig, behaviour, inputs_hash,
                                  window_dates)
from state.db import connect

NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)   # 16:35 ET
AS_OF = "2026-07-13"
CFG = WeightsConfig(window_days=20, horizon_days=5)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fund.sqlite")
    yield c
    c.close()


@pytest.fixture
def clock():
    return SimClock(NIGHTLY)


def _dates(n: int, start: date = date(2026, 4, 1)) -> list[str]:
    """`n` consecutive run_dates, oldest first, all before AS_OF."""
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _day(conn, run_date: str, alpha: float, signals: dict, *,
         ticker: str = "NVDA", offered: tuple[str, ...] = ("NVDA",),
         charter_version: str = "v1", cost: dict | None = None) -> None:
    """One graded trading day: `offered` rows, one signal per seat in
    `signals` ({seat: (direction, confidence)}), one held PM decision on
    `ticker`, and its resolution at `alpha`. calibration/rows.py fans the
    resolution back out to every seat's signal on (run_date, ticker)."""
    stamp = f"{run_date}T15:00:00+00:00"
    for t in offered:
        conn.execute("INSERT OR IGNORE INTO offered (run_date, ticker, created_at)"
                     " VALUES (?, ?, ?)", (run_date, t, stamp))
    for seat, (direction, confidence) in signals.items():
        conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
            " summary, created_at, charter_version, model_id)"
            " VALUES (?, ?, ?, ?, ?, 's', ?, ?, 'm')",
            (run_date, seat, ticker, direction, confidence, stamp, charter_version))
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES (?, ?, 'hold', 0, 't', 'i',"
        " 'held', ?)", (run_date, ticker, stamp))
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?, 5, ?, ?, 0, ?)",
        (cur.lastrowid, alpha, alpha, stamp))
    for seat, usd in (cost or {}).items():
        conn.execute(
            "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
            " recorded_at) VALUES (?, ?, 's', ?, ?)", (run_date, seat, usd, stamp))
    conn.commit()


def _two_seat_history(conn, n_days: int = 60) -> list[str]:
    """Seat `a` calls every day right at 80 (bullish on an up day, bearish on
    a down day); seat `b` abstains every day at 50. Alternating ±1% alpha,
    so outcomes are not degenerate. Seat `a` costs $0.05 a day."""
    dates = _dates(n_days)
    for i, d in enumerate(dates):
        alpha = 0.01 if i % 2 == 0 else -0.01
        _day(conn, d, alpha,
             {"a": ("bullish" if alpha > 0 else "bearish", 80),
              "b": ("neutral", 50)},
             cost={"a": 0.05})
    return dates


# --- config ---------------------------------------------------------------

def test_config_rejects_a_window_or_horizon_below_one():
    for bad in (dict(window_days=0, horizon_days=5),
                dict(window_days=20, horizon_days=0)):
        with pytest.raises(ValueError, match="must be >= 1"):
            WeightsConfig(**bad)


# --- windows --------------------------------------------------------------

def test_window_dates_are_the_trailing_trading_days_oldest_first(conn):
    dates = _two_seat_history(conn)
    assert window_dates(conn, AS_OF, 20) == dates[-20:]
    assert window_dates(conn, AS_OF, 100) == dates          # fewer than asked: all
    assert window_dates(conn, dates[9], 5) == dates[5:10]   # bounded by as_of
    assert window_dates(conn, "2026-01-01", 5) == []


def test_behaviour_counts_the_window_only(conn):
    dates = _two_seat_history(conn)
    window = dates[-20:]
    a = behaviour(conn, "a", window)
    assert a == {"n_signalled": 20, "n_offered": 20, "n_distinct_conf": 1,
                 "abstention_rate": 0.0, "coverage": 1.0,
                 "cost_usd": pytest.approx(1.0)}
    b = behaviour(conn, "b", window)
    assert (b["n_signalled"], b["abstention_rate"], b["cost_usd"]) == (20, 1.0, 0.0)


def test_behaviour_over_no_dates_is_all_zero(conn):
    assert behaviour(conn, "a", []) == {
        "n_signalled": 0, "n_offered": 0, "n_distinct_conf": 0,
        "abstention_rate": 0.0, "coverage": 0.0, "cost_usd": 0.0}


def test_defaulted_rows_are_offered_but_not_signalled(conn):
    """run_research writes neutral/0 rows with charter_version='none' for a
    silent seat. They are graded (calibration invariant 2) but the seat did
    not speak, so they count toward n_offered and not toward n_signalled —
    otherwise coverage is 1.0 by construction (improvement.md §2.1)."""
    dates = _dates(3)
    for d in dates:
        _day(conn, d, 0.01, {"c": ("neutral", 0)}, charter_version="none")
    c = behaviour(conn, "c", dates)
    assert (c["n_offered"], c["n_signalled"]) == (3, 0)
    assert (c["coverage"], c["abstention_rate"], c["n_distinct_conf"]) == (0.0, 0.0, 0)


def test_coverage_is_signalled_over_offered(conn):
    """Two tickers offered, the seat spoke on one."""
    d = _dates(1)[0]
    _day(conn, d, 0.01, {"a": ("bullish", 70)}, offered=("NVDA", "MSFT"))
    assert behaviour(conn, "a", [d])["coverage"] == 0.5


def test_coverage_is_zero_when_signals_predate_the_offered_table(conn):
    """`offered` is a brand-new table; production `signals` rows go back
    before it existed. The first nightly run's trailing window can therefore
    span a date with `signals` rows and zero `offered` rows: the seat spoke
    (n_signalled > 0) yet n_offered == 0. coverage must degrade to 0.0, not
    divide by zero (improvement.md §2.1)."""
    d = _dates(1)[0]
    _day(conn, d, 0.01, {"a": ("bullish", 70)}, offered=())
    b = behaviour(conn, "a", [d])
    assert (b["n_signalled"], b["n_offered"]) == (1, 0)
    assert b["coverage"] == 0.0


# --- hash -----------------------------------------------------------------

def test_inputs_hash_is_stable_and_sensitive():
    rows = [{"seat": "a", "direction": "long", "confidence": 80, "alpha": 0.01}]
    beh = {"n_signalled": 1, "n_offered": 1, "n_distinct_conf": 1,
           "abstention_rate": 0.0, "coverage": 1.0, "cost_usd": 0.0}
    h = inputs_hash(rows, beh)
    assert h == inputs_hash(list(rows), dict(beh))
    assert h != inputs_hash(rows, {**beh, "cost_usd": 0.01})
    assert h != inputs_hash(rows + rows, beh)
