"""Offline unit tests for market/source_alpaca.py's pure helpers.
Never construct AlpacaSource -- its __init__ requires env vars and builds
real network clients. These test the decision logic only."""
import math
import pandas as pd
import pytest

from market.source_alpaca import _clock_dict, _pnl_pct, _reshape_close_frame


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


# ---- _clock_dict: invariant 4, an odd clock payload must read as CLOSED ----

class _Clock:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

def test_clock_dict_open():
    c = _clock_dict(_Clock(is_open=True, next_open="a", next_close="b"))
    assert c == {"is_open": True, "next_open": "a", "next_close": "b"}

def test_clock_dict_closed():
    assert _clock_dict(_Clock(is_open=False, next_open=None,
                              next_close=None))["is_open"] is False

def test_clock_dict_missing_or_non_bool_is_open_reads_closed():
    """'true' as a string, or no field at all, must not be trusted as open."""
    assert _clock_dict(_Clock())["is_open"] is False
    assert _clock_dict(_Clock(is_open="true"))["is_open"] is False
    assert _clock_dict(_Clock(is_open=1))["is_open"] is False


# ---- cancel_order: the BrokerPort cancel wiring (C2) ----

def test_installed_alpaca_py_exposes_the_cancel_api_we_call():
    """The one thing that can otherwise only fail LIVE: a wrong alpaca-py
    method name. Pin both calls against the installed TradingClient (0.44 has
    no cancel-by-client-id — hence the two-step lookup-then-cancel)."""
    from alpaca.trading.client import TradingClient
    assert callable(TradingClient.get_order_by_client_id)
    assert callable(TradingClient.cancel_order_by_id)
    assert not hasattr(TradingClient, "cancel_order_by_client_id")


def _bare_source():
    """AlpacaSource without __init__ (no env vars, no network clients) — the
    only way to exercise the cancel wiring offline. Nothing here touches the
    real broker; `_trading` is replaced by the caller."""
    from market.source_alpaca import AlpacaSource
    return AlpacaSource.__new__(AlpacaSource)


def test_cancel_order_resolves_client_id_then_cancels_by_order_id():
    calls = []
    class Trading:
        def get_order_by_client_id(self, cid):
            calls.append(("get", cid))
            return _Clock(id="alp-0001")
        def cancel_order_by_id(self, oid):
            calls.append(("cancel", oid))
    src = _bare_source()
    src._trading = Trading()
    src.cancel_order("tid-1")
    assert calls == [("get", "tid-1"), ("cancel", "alp-0001")]


def test_cancel_order_propagates_broker_errors():
    """Unlike the read path, cancel must NOT swallow: reconcile re-queries and
    records only what the broker confirms, so a silent 'ok' would let it write
    a cancel that never happened (invariants 4/6)."""
    class Trading:
        def get_order_by_client_id(self, cid): raise ConnectionError("down")
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(ConnectionError):
        src.cancel_order("tid-1")
