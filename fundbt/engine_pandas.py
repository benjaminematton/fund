"""Deterministic daily-bar portfolio engine. Pure pandas/numpy — no numba, no RNG.

This is the offline/default engine (`make test` runs with it, no deps beyond
numpy+pandas). engine_vbt.py is the drop-in vectorbt adapter for speed at scale.

Semantics — chosen to kill lookahead by construction:
- Signals computed on bar t execute on bar t+1 (positions = state.shift(1)).
- Long-only, equal weight across active positions each bar.
- Costs: per-side bps on traded weight (turnover x cost). Retail-scale, so no
  market-impact term; the floor already prices the spread.
- Entry while in position is ignored; exit while flat is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ENGINE_VERSION = "pandas-1.0"
TRADING_DAYS = 252


@dataclass
class EngineResult:
    returns: pd.Series          # daily net portfolio returns
    equity: pd.Series           # cumulative, start = 1.0
    n_trades: int               # executed entries
    annual_turnover: float
    cost_share: float           # costs paid / gross P&L before costs (0 if gross <= 0)
    total_costs: float          # cumulative cost drag (sum of daily cost fractions)


def _positions(entries: pd.DataFrame, exits: pd.DataFrame) -> pd.DataFrame:
    """Boolean signal pair -> in-position state (0/1), before execution shift."""
    raw = pd.DataFrame(
        np.where(entries.to_numpy(), 1.0, np.where(exits.to_numpy(), 0.0, np.nan)),
        index=entries.index,
        columns=entries.columns,
    )
    return raw.ffill().fillna(0.0)


def run(
    close: pd.DataFrame,
    entries: pd.DataFrame,
    exits: pd.DataFrame,
    cost_bps_per_side: float,
) -> EngineResult:
    """All three frames must share index and columns exactly (wrapper enforces)."""
    if not (close.index.equals(entries.index) and close.index.equals(exits.index)):
        raise ValueError("index_mismatch")
    if not (list(close.columns) == list(entries.columns) == list(exits.columns)):
        raise ValueError("columns_mismatch")
    if close.isna().any().any():
        raise ValueError("nan_in_close")  # wrapper must clean; engine never guesses

    entries = entries.fillna(False).astype(bool)
    exits = exits.fillna(False).astype(bool)

    state = _positions(entries, exits)
    pos = state.shift(1).fillna(0.0)           # next-bar execution: no lookahead

    n_active = pos.sum(axis=1)
    weights = pos.div(n_active.replace(0.0, np.nan), axis=0).fillna(0.0)

    asset_rets = close.pct_change().fillna(0.0)
    gross = (weights * asset_rets).sum(axis=1)

    turnover = weights.diff().abs().sum(axis=1).fillna(0.0)   # one-sided traded weight
    costs = turnover * (cost_bps_per_side / 1e4)
    net = gross - costs

    equity = (1.0 + net).cumprod()
    executed_entries = int(((pos.diff() > 0).sum().sum()))

    years = max(len(close) / TRADING_DAYS, 1e-9)
    gross_pnl = float(gross.sum())
    total_costs = float(costs.sum())
    cost_share = total_costs / gross_pnl if gross_pnl > 0 else 1.0  # conservative

    return EngineResult(
        returns=net,
        equity=equity,
        n_trades=executed_entries,
        annual_turnover=float(turnover.sum() / years),
        cost_share=cost_share,
        total_costs=total_costs,
    )
