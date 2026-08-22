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


# ---- account_state: last_equity is the denominator of every P&L ----

def test_account_state_carries_last_equity():
    """daily_pnl_pct is derived from (equity, last_equity) and then the pair is
    thrown away, so a caller wanting P&L in DOLLARS had to invert the
    percentage to recover the denominator. Carrying last_equity through costs
    one key and keeps the digest's "$" and "%" reading off the same two
    numbers the circuit breaker does."""
    class Trading:
        def get_account(self):
            return _Clock(equity="101500", last_equity="101000", cash="30000")
        def get_all_positions(self):
            return []

    src = _bare_source()
    src._trading = Trading()
    state = src.account_state()

    assert state["equity"] == 101_500.0
    assert state["last_equity"] == 101_000.0
    assert state["daily_pnl_pct"] == pytest.approx(0.004950495)


def test_account_state_last_equity_is_nan_when_unparseable():
    """Same posture as every other number off this API: NaN, not a guess, so
    a P&L computed from it fails closed instead of reporting a wrong day."""
    class Trading:
        def get_account(self):
            return _Clock(equity="101500", last_equity=None, cash="30000")
        def get_all_positions(self):
            return []

    src = _bare_source()
    src._trading = Trading()
    assert math.isnan(src.account_state()["last_equity"])


# ---- open_positions / open_orders: the protection assertion's broker reads ----

def test_installed_alpaca_py_exposes_the_read_apis_we_call():
    """Same posture as the cancel-wiring pin: a wrong alpaca-py method name
    can otherwise only fail LIVE, and this read is what stands between a naked
    position and nobody noticing."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    assert callable(TradingClient.get_all_positions)
    assert callable(TradingClient.get_orders)
    for field in ("status", "nested", "limit"):
        assert field in GetOrdersRequest.model_fields


def test_open_positions_unwraps_alpaca_enums():
    """THE trap. alpaca-py returns (str, Enum) members and str(PositionSide.
    LONG) is 'PositionSide.LONG', not 'long'. A plain str() here makes every
    position unclassifiable and every stop unmatchable, so the protection
    check would alert on a fully protected book every day — and a fake built
    from plain strings would never show it. This fake uses the REAL enums."""
    from alpaca.trading.enums import PositionSide

    class Trading:
        def get_all_positions(self):
            return [_Clock(symbol="NVDA", qty="80", side=PositionSide.LONG)]
    src = _bare_source()
    src._trading = Trading()
    assert src.open_positions() == [
        {"symbol": "NVDA", "qty": "80", "side": "long"}]


def test_open_positions_propagates_broker_errors():
    """Unlike get_order_by_client_order_id (which swallows because its caller
    re-polls), this read has no retry behind it. A swallowed error would read
    as "no positions held", which is a silent pass on exactly the condition
    the check exists to catch."""
    class Trading:
        def get_all_positions(self): raise ConnectionError("down")
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(ConnectionError):
        src.open_positions()


def test_open_orders_requests_open_status_and_flattens_legs():
    """nested=False (the default) is deliberate: an OTO's stop leg must come
    back as its OWN row, because the leg is the protective order the check is
    looking for. Grouped under the parent it would be invisible. Built from
    the REAL enums for the same reason as the positions test above.

    The four id/price/expiry keys were added when the port's contract widened
    to carry what a protection row references. expires_at goes through iso(),
    so a datetime comes back with a T separator rather than a space — the
    shape every other timestamp in the database has."""
    from datetime import datetime, timezone

    from alpaca.trading.enums import OrderSide, OrderStatus, OrderType

    seen = {}
    class Trading:
        def get_orders(self, filter=None):
            seen["status"] = str(filter.status)
            seen["nested"] = filter.nested
            seen["limit"] = filter.limit
            return [_Clock(symbol="NVDA", side=OrderSide.SELL, qty="80",
                           order_type=OrderType.STOP, status=OrderStatus.NEW,
                           id="5abc139f-4817-4a34-aedd-f2ca28203c5c",
                           client_order_id="manual-protective-stop-nvda",
                           stop_price="215.0",
                           expires_at=datetime(2026, 11, 17, 21, 0,
                                               tzinfo=timezone.utc))]
    src = _bare_source()
    src._trading = Trading()
    assert src.open_orders() == [
        {"symbol": "NVDA", "side": "sell", "qty": "80",
         "type": "stop", "status": "new",
         "id": "5abc139f-4817-4a34-aedd-f2ca28203c5c",
         "client_order_id": "manual-protective-stop-nvda",
         "stop_price": "215.0",
         "expires_at": "2026-11-17T21:00:00+00:00"}]
    assert "open" in seen["status"].lower()
    assert not seen["nested"]
    assert seen["limit"] == 500


def test_open_orders_carries_none_for_an_order_with_no_stop_or_expiry():
    """A trailing stop has no stop_price and a DAY order has no expires_at.
    Both must come back as None rather than raising or being dropped — the log
    records a trailing stop, and _covering_qty counts one toward cover."""
    from alpaca.trading.enums import OrderSide, OrderStatus, OrderType

    class Trading:
        def get_orders(self, filter=None):
            return [_Clock(symbol="NVDA", side=OrderSide.SELL, qty="80",
                           order_type=OrderType.TRAILING_STOP,
                           status=OrderStatus.NEW, id="alp-1",
                           client_order_id="c1", stop_price=None,
                           expires_at=None)]
    src = _bare_source()
    src._trading = Trading()
    row = src.open_orders()[0]
    assert row["stop_price"] is None and row["expires_at"] is None


def test_open_orders_raises_rather_than_returning_a_truncated_page():
    """A full page means orders were dropped, and a dropped protective order
    reads as 'nothing is protecting this'. The caller turns this raise into an
    'unverified' alert — the honest answer. No silent caps."""
    from alpaca.trading.enums import OrderSide, OrderStatus, OrderType

    class Trading:
        def get_orders(self, filter=None):
            return [_Clock(symbol="NVDA", side=OrderSide.SELL, qty="1",
                           order_type=OrderType.STOP, status=OrderStatus.NEW)
                    for _ in range(500)]
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(RuntimeError, match="page limit"):
        src.open_orders()


def test_open_orders_propagates_broker_errors():
    class Trading:
        def get_orders(self, filter=None): raise ConnectionError("down")
    src = _bare_source()
    src._trading = Trading()
    with pytest.raises(ConnectionError):
        src.open_orders()


# ---- account_config: the live read behind orchestrator/preconditions.py ----

def test_account_config_returns_every_field_as_plain_values():
    """No filtering: a setting Alpaca adds must reach the drift check rather
    than be dropped by a hand-written field list here. Three of the real
    model's nine fields are (str, Enum) members (dtbp_check, pdt_check,
    trade_confirm_email) -- built from the REAL model + REAL enums, same
    posture as test_open_positions_unwraps_alpaca_enums above, because a fake
    built from plain strings can't catch a missing enum coercion."""
    from alpaca.trading.enums import DTBPCheck, PDTCheck, TradeConfirmationEmail
    from alpaca.trading.models import AccountConfiguration
    from market.source_alpaca import AlpacaSource

    config = AccountConfiguration(
        dtbp_check=DTBPCheck.ENTRY,
        fractional_trading=True,
        max_margin_multiplier="4",
        no_shorting=False,
        pdt_check=PDTCheck.BOTH,
        suspend_trade=False,
        trade_confirm_email=TradeConfirmationEmail.ALL,
        ptp_no_exception_entry=True,
        max_options_trading_level=2,
    )

    class _Trading:
        def get_account_configurations(self):
            return config

    src = AlpacaSource.__new__(AlpacaSource)
    src._trading = _Trading()
    result = src.account_config()

    assert result == {
        "dtbp_check": "entry",
        "fractional_trading": True,
        "max_margin_multiplier": "4",
        "no_shorting": False,
        "pdt_check": "both",
        "suspend_trade": False,
        "trade_confirm_email": "all",
        "ptp_no_exception_entry": True,
        "max_options_trading_level": 2,
    }
    # Equality alone would pass even without coercion, since these enums
    # subclass str ("entry" == DTBPCheck.ENTRY is True) -- assert the TYPE
    # too, so the test genuinely fails if the coercion is removed.
    for field in ("dtbp_check", "pdt_check", "trade_confirm_email"):
        assert type(result[field]) is str, (
            f"{field} is {type(result[field])!r}, not a plain str")
