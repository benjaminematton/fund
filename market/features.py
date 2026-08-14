"""Pure compute: DataFrames/dicts in, numbers/dicts out. No I/O, no network,
no clock, no Alpaca. NEVER rejects — gate/risk.py owns all fail-closed
validation (review decision C3). build_gate_inputs returns a plain dict,
not a GateInputs model, so garbage (NaN vol, missing sector) flows through
to the gate's validator instead of being caught here."""
from __future__ import annotations

import numpy as np
import pandas as pd


MIN_HISTORY_RETURNS = 40
"""Minimum daily returns before a ticker may enter a vol or correlation
computation. HUMAN-COMMIT-ONLY — the same rule as the gate thresholds, for
the same reason: it decides how large a position may be, so an agent must
never edit it.

Fisher gives se(r) ~= 1/sqrt(n-3) ~= 0.16 at n=40, adequate against the
0.2-wide correlation tier bands in gate/risk.py. Below that a correlation
is noise wearing a sign — and at 2 returns Pearson's rho is exactly +/-1.0
BY CONSTRUCTION, not as an estimate, so a 3-bar holding would drag the
basket mean a half-point and cross a tier boundary on nothing at all.

The same constant governs vol: annualizing a 5-return tail is the identical
defect one field over, so both computations draw from the same puddle rule.
"""


def _vol_of_returns(rets: pd.Series) -> float:
    """The arithmetic alone, with no admission check.

    Split out so the hand-derived golden vector can pin the annualization
    factor and ddof on a short exact series (a wrong factor mis-sizes every
    position on every day, so it must stay pinned to a literal). Production
    never calls this — it enters through annualized_vol(), which enforces
    MIN_HISTORY_RETURNS first."""
    return rets.std(ddof=1) * np.sqrt(252)


def annualized_vol(series: pd.Series) -> float:
    """Std dev (ddof=1) of the last 60 daily pct-change returns, annualized,
    or NaN when there are fewer than MIN_HISTORY_RETURNS of them.
    Gaps are dropped BEFORE differencing, never forward-filled: pandas'
    default pad turns a missing bar into a fake 0% return, which understates
    vol and sizes UP (and is deprecated besides)."""
    rets = series.dropna().pct_change().dropna().tail(60)
    if len(rets) < MIN_HISTORY_RETURNS:
        return float("nan")
    return _vol_of_returns(rets)


def _has_return_history(close_df: pd.DataFrame, ticker: str) -> bool:
    """True when close_df carries at least MIN_HISTORY_RETURNS returns for
    `ticker` — m non-NaN closes yield m-1 returns. Anything thinner is a
    puddle: see the constant's rationale."""
    return (ticker in close_df.columns
            and int(close_df[ticker].count()) - 1 >= MIN_HISTORY_RETURNS)


def unpriceable_book_tickers(close_df: pd.DataFrame, book_tickers) -> list[str]:
    """Sorted book tickers that avg_corr_vs_book DROPS from its basket: a
    column the frame carries but with too little usable price history —
    fewer than MIN_HISTORY_RETURNS returns. That covers both the empty case
    (AlpacaSource's _reshape_close_frame gives a ticker with zero bars an
    all-NaN column) and the thin case, which is the more dangerous of the
    two because it silently produces a confident-looking number.

    The caller MUST alert on these. A silently-shrunken basket understates
    correlation and therefore sizes UP, which is the dangerous direction.

    A ticker the frame does not carry AT ALL is deliberately absent here: that
    is a caller/wiring bug rather than a feed gap (run_day fetches exactly the
    universe it prices), and avg_corr_vs_book still fails it closed as NaN."""
    return sorted({t for t in book_tickers
                   if t in close_df.columns and not _has_return_history(close_df, t)})


def avg_corr_vs_book(close_df: pd.DataFrame, ticker: str, book_tickers: list[str]) -> float:
    """Mean pairwise return-correlation of `ticker` vs each ticker in the book.

    An EMPTY book -> 0.0, the most permissive multiplier tier. That is the
    correct answer rather than a fallback: with no holdings there is no
    correlation risk to measure.

    Book tickers the frame carries but cannot price are EXCLUDED from the
    basket instead of poisoning the mean with NaN: every candidate correlates
    against the SAME book, so one unpriceable holding used to reject the whole
    universe and cost the entire trading day. The exclusion is never silent —
    unpriceable_book_tickers() names them for the caller's alert.

    But a non-empty book with EVERY member excluded -> NaN, not 0.0. "We hold
    things and can price none of them" is a data outage, not an absence of
    correlation risk; treating it as the empty book would size UP on missing
    data, the one direction this fail-closed pipeline must never fail.

    A book ticker missing from close_df entirely -> NaN (a caller/wiring bug).
    `ticker`'s OWN missing history -> NaN, which rejects that one ticker."""
    if not book_tickers:
        return 0.0
    if ticker not in close_df.columns or any(t not in close_df.columns for t in book_tickers):
        return float("nan")
    if not _has_return_history(close_df, ticker):
        return float("nan")
    basket = [t for t in book_tickers if _has_return_history(close_df, t)]
    if not basket:
        return float("nan")   # see the "all excluded" paragraph above
    rets = close_df[ticker].pct_change()
    corrs = [rets.corr(close_df[t].pct_change()) for t in basket]
    return float(np.mean(corrs))


def unmapped_holdings(positions: dict, sectors: dict) -> list[str]:
    """Sorted held tickers with no config/sectors.yaml entry (a missing key
    and an explicit null both count). sector_book_value fails closed on
    these; the caller MUST alert, because the fix is a one-line yaml commit
    and until it lands every buy of the day is gate_error."""
    return sorted(t for t in positions if sectors.get(t) is None)


def sector_book_value(positions: dict, prices: dict, sectors: dict, sector: str) -> float:
    """Sum of qty * current price for held positions whose sector matches.
    Fails CLOSED on either missing input -> NaN (gate rejects; never silently
    drop a position and understate sector book value):
      * a held position with no entry in `sectors` — dropping it hid real
        concentration and let the 60% post-trade sector cap approve more than
        it should
      * a matching position with no entry in `prices`"""
    if unmapped_holdings(positions, sectors):
        return float("nan")
    matching = [t for t, qty in positions.items() if sectors[t] == sector]
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
