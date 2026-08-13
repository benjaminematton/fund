"""Offline unit tests for market/source_alpaca.py's pure helpers.
Never construct AlpacaSource -- its __init__ requires env vars and builds
real network clients. These test the decision logic only."""
import math
import pandas as pd
import pytest

from market.source_alpaca import _pnl_pct, _reshape_close_frame


# ---- _pnl_pct: invariant 4, fresh/degraded account must fail closed ----

def test_pnl_pct_valid_values():
    assert _pnl_pct(110.0, 100.0) == pytest.approx(0.10)

def test_pnl_pct_last_equity_zero_string():
    """Fresh paper account: last_equity == '0' -> NaN, not 0.0 (0.0 would
    read as a flat day and let the -3% circuit breaker pass)."""
    assert math.isnan(_pnl_pct("105", "0"))

def test_pnl_pct_last_equity_none():
    assert math.isnan(_pnl_pct("105", None))

def test_pnl_pct_last_equity_empty_string():
    assert math.isnan(_pnl_pct("105", ""))

def test_pnl_pct_equity_unparseable():
    assert math.isnan(_pnl_pct(None, "100"))


# ---- _reshape_close_frame: invariant 4, missing bars must not crash ----

def _bars_df(data: dict[str, list[float]]) -> pd.DataFrame:
    """Build a frame shaped like alpaca BarSet.df: MultiIndex(symbol,
    timestamp) with a 'close' column, for the given {symbol: [closes]}."""
    rows = []
    for sym, closes in data.items():
        idx = pd.bdate_range("2026-01-01", periods=len(closes))
        for ts, c in zip(idx, closes):
            rows.append({"symbol": sym, "timestamp": ts, "close": c})
    df = pd.DataFrame(rows).set_index(["symbol", "timestamp"])
    return df

def test_reshape_close_frame_missing_ticker_becomes_nan_column():
    bars = _bars_df({"AAPL": [100.0, 101.0], "MSFT": [200.0, 201.0]})
    out = _reshape_close_frame(bars, ["AAPL", "MSFT", "ZZZZ"], days=90)
    assert list(out.columns) == ["AAPL", "MSFT", "ZZZZ"]
    assert out["ZZZZ"].isna().all()
    assert out["AAPL"].notna().all()

def test_reshape_close_frame_completely_empty_response():
    """BarSet.df on a zero-bar response has no 'close' column at all."""
    bars = pd.DataFrame()
    out = _reshape_close_frame(bars, ["AAPL", "MSFT"], days=90)
    assert list(out.columns) == ["AAPL", "MSFT"]
    assert len(out) == 0

def test_reshape_close_frame_tail_limits_to_days():
    bars = _bars_df({"AAPL": [float(i) for i in range(10)]})
    out = _reshape_close_frame(bars, ["AAPL"], days=3)
    assert len(out) == 3
    assert list(out["AAPL"]) == [7.0, 8.0, 9.0]
