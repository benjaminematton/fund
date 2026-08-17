"""Alpaca I/O (live only). Implements BrokerPort + market/account reads.
Env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER_TRADE=true (always)."""
from __future__ import annotations
import math
import os
from datetime import timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

# Alpaca's free data plan excludes the most recent ~15 minutes of SIP data: a
# bars request ending at "now" 403s with "subscription does not permit
# querying recent SIP data" (measured 2026-08-14). Shifting the window back
# keeps the consolidated tape rather than dropping to a single venue's feed,
# which would change the prices every number in this repo is derived from.
SIP_DELAY = timedelta(minutes=16)


def _paper_guard() -> bool:
    if os.environ.get("ALPACA_PAPER_TRADE", "").lower() != "true":
        raise RuntimeError("ALPACA_PAPER_TRADE must be 'true' (invariant 1)")
    return True

def _safe_float(v) -> float:
    """float(v), or NaN if v is None/unparseable (gate rejects NaN -> HOLD)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")

def _pnl_pct(equity, last_equity) -> float:
    """(equity - last_equity) / last_equity, or NaN if either side is
    missing/unparseable or last_equity is zero. NaN, not 0.0: a fresh paper
    account's last_equity == "0" must fail the gate closed, not read as a
    flat day and let the daily-loss circuit breaker pass (invariant 4)."""
    e, le = _safe_float(equity), _safe_float(last_equity)
    if le == 0 or math.isnan(e) or math.isnan(le):
        return float("nan")
    return (e - le) / le

def _clock_dict(c) -> dict:
    """Normalize alpaca's Clock model to a plain dict. is_open is True ONLY
    when the broker says so explicitly -- a missing/None/truthy-string field
    reads as CLOSED, so an odd payload skips the day instead of trading
    against a shut market (invariant 4)."""
    return {"is_open": getattr(c, "is_open", None) is True,
            "next_open": str(getattr(c, "next_open", "") or ""),
            "next_close": str(getattr(c, "next_close", "") or "")}

def _reshape_close_frame(bars_df, tickers: list[str], days: int):
    """Reshape raw StockBarsRequest bars.df into one close-price column per
    requested ticker, tail-limited to `days` rows. A ticker with zero bars in
    the window, or a completely empty response, becomes a NaN column instead
    of raising -- downstream features.py already treats NaN as
    gate-rejects-> HOLD (invariant 4)."""
    import pandas as pd
    if "close" not in bars_df.columns:
        return pd.DataFrame(columns=tickers, dtype=float)
    close = bars_df["close"].unstack(level=0).reindex(columns=tickers)
    return close.tail(days)

class AlpacaSource:
    def __init__(self) -> None:
        _paper_guard()
        key, sec = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
        self._trading = TradingClient(key, sec, paper=True)
        self._data = StockHistoricalDataClient(key, sec)

    def get_order_by_client_order_id(self, coid: str) -> dict | None:
        try:
            o = self._trading.get_order_by_client_id(coid)
        except Exception:
            return None                          # fail closed; poll retries
        d = o.model_dump() if hasattr(o, "model_dump") else dict(o)
        return {k: (str(v) if k in ("qty", "filled_qty", "filled_avg_price")
                    and v is not None else v) for k, v in d.items()}

    def cancel_order(self, coid: str) -> None:
        """Request cancellation of a still-working order. alpaca-py 0.44 has
        no cancel-by-client-id, so resolve the client id to the broker's order
        id (`get_order_by_client_id`) and cancel by that
        (`cancel_order_by_id(order_id)` -> None). Deliberately does NOT
        swallow: a failed cancel must reach the caller, which re-queries and
        records only what the broker confirms (invariant 4)."""
        o = self._trading.get_order_by_client_id(coid)
        oid = o["id"] if isinstance(o, dict) else o.id
        self._trading.cancel_order_by_id(oid)

    def market_clock(self) -> dict:
        """Broker's market clock: {is_open, next_open, next_close}. The live
        day's skip-if-closed guard reads this."""
        return _clock_dict(self._trading.get_clock())

    def close_frame(self, tickers: list[str], end, days: int = 90):
        """Daily closes for `tickers`, ending SIP_DELAY before `end`.

        The shift is unconditional and applied to the CALLER's end — never to
        a wall-clock read, so the injected Clock stays the only source of
        time. Safe to apply to a historical end too: these are 1Day bars, and
        moving the end back 16 minutes cannot drop a bar that already closed.
        """
        end = end - SIP_DELAY
        bars = self._data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=tickers, timeframe=TimeFrame.Day,
            start=end - timedelta(days=days * 2), end=end)).df
        return _reshape_close_frame(bars, tickers, days)

    def account_state(self) -> dict:
        a = self._trading.get_account()
        pos = self._trading.get_all_positions()
        return {
            "equity": _safe_float(a.equity), "cash": _safe_float(a.cash),
            # last_equity is carried, not just consumed by _pnl_pct: the EOD
            # digest reports P&L in dollars as well as percent, and inverting
            # the percentage to recover the denominator would report a figure
            # derived from a different pair of numbers than the gate's.
            "last_equity": _safe_float(a.last_equity),
            "daily_pnl_pct": _pnl_pct(a.equity, a.last_equity),
            "positions": {p.symbol: int(float(p.qty)) for p in pos},
            "prices": {p.symbol: float(p.current_price) for p in pos},
        }
