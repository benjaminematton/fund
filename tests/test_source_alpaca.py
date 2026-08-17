"""Offline coverage for market/source_alpaca.py — the ONLY module that imports
alpaca-py (review A1).

This module had no offline test at all, which is exactly how a live-morning
403 happened: `close_frame` asked for SIP bars ending at "now", the free data
plan forbids the most recent ~15 minutes of SIP, and nothing in `make test`
could have said so. The client is faked here; no network, no keys.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from market.source_alpaca import SIP_DELAY, AlpacaSource


class _FakeData:
    """Captures the StockBarsRequest instead of calling Alpaca."""

    def __init__(self, frame: pd.DataFrame | None = None):
        self.requests = []
        self._frame = frame if frame is not None else _bars_frame()

    def get_stock_bars(self, request):
        self.requests.append(request)
        return type("Resp", (), {"df": self._frame})()


def _bars_frame(tickers=("NVDA", "SPY"), n=90) -> pd.DataFrame:
    """The MultiIndex (symbol, timestamp) shape alpaca-py's .df returns."""
    idx = pd.MultiIndex.from_product(
        [list(tickers), pd.bdate_range("2026-04-01", periods=n)],
        names=["symbol", "timestamp"])
    return pd.DataFrame({"close": [100.0 + i for i in range(len(idx))]}, index=idx)


def _as_request_ts(ts: pd.Timestamp) -> pd.Timestamp:
    """StockBarsRequest stores its bounds as tz-naive UTC, so assertions have
    to be made in that frame rather than the caller's."""
    return ts.tz_convert("UTC").tz_localize(None)


def _source(frame=None) -> AlpacaSource:
    """An AlpacaSource with both clients faked — __init__ is bypassed so no
    env vars, no paper guard, and no network are involved."""
    src = AlpacaSource.__new__(AlpacaSource)
    src._data = _FakeData(frame)
    src._trading = None
    return src


def test_close_frame_never_requests_inside_the_sip_blackout():
    """The live-morning bug. Alpaca's free plan 403s a SIP request whose end
    is inside the most recent ~15 minutes; run_day passes clock.now() as end,
    so every live day would have died in the market-data fetch before any
    stage ran. The request's end must sit at or before end - SIP_DELAY."""
    src = _source()
    supplied_end = pd.Timestamp("2026-08-17 11:00:00", tz="America/New_York")
    src.close_frame(["NVDA", "SPY"], end=supplied_end)

    # the request model normalises to tz-naive UTC, so compare in that frame
    expected = _as_request_ts(supplied_end - SIP_DELAY)
    request = src._data.requests[0]
    assert request.end <= expected
    # ...and the window is still anchored to the requested end, not to a
    # wall-clock read: the Clock stays injected (no datetime.now() anywhere).
    assert request.end == expected
    assert request.start == request.end - timedelta(days=180)


def test_sip_delay_clears_the_documented_blackout_with_slack():
    """15 minutes is the documented blackout; the constant must exceed it, or
    a request landing exactly on the boundary 403s again."""
    assert SIP_DELAY > timedelta(minutes=15)


def test_shifting_the_window_cannot_drop_a_completed_daily_bar():
    """The boundary case. These are 1Day bars, so moving the end back 16
    minutes can never remove a bar that already closed — a historical `end`
    yields the identical frame, clamped or not. That is what makes the clamp
    safe to apply unconditionally instead of comparing against a wall clock
    we deliberately do not read."""
    frame = _bars_frame()
    historical = pd.Timestamp("2026-06-01 00:00:00", tz="America/New_York")

    clamped = _source(frame).close_frame(["NVDA", "SPY"], end=historical)
    src = _source(frame)
    src.close_frame(["NVDA", "SPY"], end=historical)
    request = src._data.requests[0]

    assert request.end == _as_request_ts(historical - SIP_DELAY)
    assert len(clamped) >= 60 and not clamped.isna().all().any()


@pytest.mark.parametrize("tz", ["UTC", "America/New_York"])
def test_the_clamp_survives_either_timezone_the_callers_use(tz):
    """run_day passes America/New_York; test_live_smoke passes UTC. A naive
    subtraction that broke on one of them would fail live, not here."""
    src = _source()
    supplied_end = pd.Timestamp("2026-08-17 11:00:00", tz=tz)
    src.close_frame(["NVDA"], end=supplied_end)
    assert src._data.requests[0].end == _as_request_ts(supplied_end - SIP_DELAY)
