"""Pure compute: DataFrames/dicts in, numbers/dicts out. No I/O, no network,
no clock, no Alpaca. NEVER rejects — gate/risk.py owns all fail-closed
validation (review decision C3). build_gate_inputs returns a plain dict,
not a GateInputs model, so garbage (NaN vol, missing sector) flows through
to the gate's validator instead of being caught here."""
from __future__ import annotations

import numpy as np
import pandas as pd


def annualized_vol(series: pd.Series) -> float:
    """Std dev (ddof=1) of the last 60 daily pct-change returns, annualized."""
    rets = series.pct_change().dropna().tail(60)
    return rets.std(ddof=1) * np.sqrt(252)


def avg_corr_vs_book(close_df: pd.DataFrame, ticker: str, book_tickers: list[str]) -> float:
    """Mean pairwise return-correlation of `ticker` vs each ticker in the book.
    Empty book -> 0.0 (most permissive multiplier tier)."""
    if not book_tickers:
        return 0.0
    rets = close_df[ticker].pct_change()
    corrs = [rets.corr(close_df[t].pct_change()) for t in book_tickers]
    return float(np.mean(corrs))


def sector_book_value(positions: dict, prices: dict, sectors: dict, sector: str) -> float:
    """Sum of qty * current price for held positions whose sector matches."""
    return sum(
        qty * prices[t]
        for t, qty in positions.items()
        if sectors.get(t) == sector
    )


def build_gate_inputs(
    ticker: str, side: str, equity: float, cash: float, price: float,
    vol_60d: float, avg_corr: float, held_qty: int, position_count: int,
    sectors: dict, sector_value: float, daily_pnl_pct: float,
) -> dict:
    """Dumb assembler -> plain dict (not GateInputs). Passes everything
    through as-is, including garbage, for gate/risk.py to validate."""
    return {
        "ticker": ticker,
        "side": side,
        "equity": equity,
        "cash": cash,
        "price": price,
        "vol_60d": vol_60d,
        "avg_corr": avg_corr,
        "held_qty": held_qty,
        "position_count": position_count,
        "sector": sectors.get(ticker),
        "sector_value": sector_value,
        "daily_pnl_pct": daily_pnl_pct,
    }
