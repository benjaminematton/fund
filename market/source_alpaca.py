"""Alpaca I/O (live only). Implements BrokerPort + market/account reads.
Env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER_TRADE=true (always)."""
from __future__ import annotations
import os
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

def _paper_guard() -> bool:
    if os.environ.get("ALPACA_PAPER_TRADE", "").lower() != "true":
        raise RuntimeError("ALPACA_PAPER_TRADE must be 'true' (invariant 1)")
    return True

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

    def close_frame(self, tickers: list[str], end, days: int = 90):
        import pandas as pd
        bars = self._data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=tickers, timeframe=TimeFrame.Day,
            start=end - pd.Timedelta(days=days * 2), end=end)).df
        return bars["close"].unstack(level=0)[tickers].tail(days)

    def account_state(self) -> dict:
        a = self._trading.get_account()
        pos = self._trading.get_all_positions()
        return {
            "equity": float(a.equity), "cash": float(a.cash),
            "daily_pnl_pct": (float(a.equity) - float(a.last_equity))
                             / float(a.last_equity),
            "positions": {p.symbol: int(float(p.qty)) for p in pos},
            "prices": {p.symbol: float(p.current_price) for p in pos},
        }
