"""Optional vectorbt engine — drop-in for engine_pandas when speed matters
(parameter sweeps, large universes). Verified against vectorbt 1.0.0 API.

Pin in pyproject.toml:
    vectorbt>=1.0.0,<1.1   # numpy>=1.23,<3.0  pandas>=2.0,<3.0  numba>=0.60

vectorbt gotchas this adapter handles (all verified against v1.0.0 source):
- Same-bar fills: from_signals fills at the SAME bar's close by default -> we
  shift signals by 1 bar to match engine_pandas's no-lookahead semantics.
- freq: business-day indexes often fail freq inference; pass freq="1D" always,
  or annualized metrics warn and misscale.
- Fees are % of order value deducted from cash; slippage adjusts fill price.
  We map cost_bps_per_side into fees (cash) — same net effect at daily bars.
- Shared-cash groups: size=np.inf grabs all cash for the first column called;
  use size_type="percent" + call_seq="auto" (sells before buys).
- Determinism: from_signals has no RNG unless reject_prob/call_seq=random are
  set — leave them unset. Pin engine= (numba vs rust) for cross-machine parity.
- NaN: clean close and force strict bool signals BEFORE the call.

License note: vectorbt is Apache-2.0 + Commons Clause (fair-code).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .engine_pandas import EngineResult, TRADING_DAYS

ENGINE_VERSION = "vbt-1.0"


def run(
    close: pd.DataFrame,
    entries: pd.DataFrame,
    exits: pd.DataFrame,
    cost_bps_per_side: float,
    init_cash: float = 1_000_000.0,
) -> EngineResult:
    import vectorbt as vbt  # deferred: offline test suite must not require it

    if close.isna().any().any():
        raise ValueError("nan_in_close")

    n_cols = close.shape[1]
    entries = entries.fillna(False).astype(bool).shift(1).fillna(False)  # next-bar
    exits = exits.fillna(False).astype(bool).shift(1).fillna(False)

    pf = vbt.Portfolio.from_signals(
        close,
        entries,
        exits,
        direction="longonly",
        init_cash=init_cash,
        fees=cost_bps_per_side / 1e4,   # % of order value, deducted from cash
        slippage=0.0,                    # cost floor already prices the spread
        size=1.0 / n_cols,               # equal-weight sleeve per asset
        size_type="percent",
        group_by=True,
        cash_sharing=True,
        call_seq="auto",                 # sells processed before buys each bar
        freq="1D",
    )

    returns = pf.returns()
    equity = pf.value() / init_cash
    orders = pf.orders.records_readable
    gross_traded = float((orders["Size"] * orders["Price"]).sum())
    years = max(len(close) / TRADING_DAYS, 1e-9)
    avg_equity = float(pf.value().mean())

    gross_pnl = float(returns.sum())  # approximation consistent with engine_pandas
    total_costs = float(orders.get("Fees", pd.Series(dtype=float)).sum()) / init_cash

    return EngineResult(
        returns=returns,
        equity=equity,
        n_trades=int(pf.trades.count()),
        annual_turnover=gross_traded / avg_equity / years,
        cost_share=total_costs / gross_pnl if gross_pnl > 0 else 1.0,
        total_costs=total_costs,
    )
