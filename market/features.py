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
    Empty book -> 0.0 (most permissive multiplier tier). A book ticker missing
    from close_df -> NaN (gate rejects; never silently drop and understate corr)."""
    if not book_tickers:
        return 0.0
    if ticker not in close_df.columns or any(t not in close_df.columns for t in book_tickers):
        return float("nan")
    rets = close_df[ticker].pct_change()
    corrs = [rets.corr(close_df[t].pct_change()) for t in book_tickers]
    return float(np.mean(corrs))


def sector_book_value(positions: dict, prices: dict, sectors: dict, sector: str) -> float:
    """Sum of qty * current price for held positions whose sector matches.
    A position with no entry in prices -> NaN (gate rejects; never silently
    drop and understate sector book value)."""
    matching = [t for t, qty in positions.items() if sectors.get(t) == sector]
    if any(t not in prices for t in matching):
        return float("nan")
    return sum(positions[t] * prices[t] for t in matching)


def _last_close(close_df: pd.DataFrame, ticker: str) -> float:
    """Most recent non-NaN close, or NaN if the ticker has no bars at all
    (gate rejects NaN -> HOLD; never substitute a guess)."""
    if ticker not in close_df.columns:
        return float("nan")
    series = close_df[ticker].dropna()
    return float(series.iloc[-1]) if len(series) else float("nan")


def build_market_inputs(watchlist: list[str], account: dict,
                        close_df: pd.DataFrame, sectors: dict) -> dict:
    """One gate-inputs dict per ticker, keyed by ticker — the market snapshot
    orchestrator/daily.py's StageCtx consumes. Covers today's watchlist UNION
    the tickers currently held (design §3, 08:45: "per watchlist/position
    ticker"), so an open position outside the watchlist can still be sold.

    `account` is market/source_alpaca.AlpacaSource.account_state()'s shape:
    equity, cash, daily_pnl_pct, positions {ticker: qty}, prices {ticker:
    current_price} — prices covers HELD tickers only, so an unheld watchlist
    ticker is marked at its last close instead.

    Pure and non-rejecting like the rest of this module (review C3): missing
    bars, an unknown sector, or a NaN P&L flow straight through to
    gate/risk.py's validator."""
    positions = account.get("positions") or {}
    prices = account.get("prices") or {}
    tickers = sorted(set(watchlist) | set(positions))
    inputs = {}
    for ticker in tickers:
        sector = sectors.get(ticker)
        inputs[ticker] = build_gate_inputs(
            ticker=ticker, side="buy",       # side is set per-shape by the gate
            equity=account.get("equity", float("nan")),
            cash=account.get("cash", float("nan")),
            price=prices.get(ticker, _last_close(close_df, ticker)),
            vol_60d=(annualized_vol(close_df[ticker])
                     if ticker in close_df.columns else float("nan")),
            avg_corr=avg_corr_vs_book(
                close_df, ticker, [t for t in positions if t != ticker]),
            held_qty=positions.get(ticker, 0),
            position_count=len(positions),
            sectors=sectors,
            sector_value=sector_book_value(positions, prices, sectors, sector),
            daily_pnl_pct=account.get("daily_pnl_pct", float("nan")))
    return inputs


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
