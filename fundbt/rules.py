"""Registered signal rules — the coded, replayable strategy library.

Rules are deterministic functions of (close, params). This registry is the
contamination firewall (strategy.md invariant 5): agents choose a rule + params;
code replays history; no LLM decides on days it may have memorized.

Starter rule: F1 dip-buyer (short-term mean reversion above a trend filter).
"""

from __future__ import annotations

import pandas as pd

from .run_backtest import register_rule


@register_rule("dip_buyer")
def dip_buyer(close: pd.DataFrame, params: dict):
    """Long a name when it has dropped `dip_pct` over `dip_days` while above its
    `trend_days` moving average; exit when price closes above the prior day's
    high-water of the entryless short window (proxy: `dip_days` rolling max).

    params: dip_days (int), dip_pct (float, e.g. 0.05), trend_days (int)
    """
    dip_days = int(params["dip_days"])
    dip_pct = float(params["dip_pct"])
    trend_days = int(params["trend_days"])

    drop = close / close.shift(dip_days) - 1.0
    trend = close > close.rolling(trend_days).mean()
    entries = (drop <= -dip_pct) & trend

    rebound = close >= close.rolling(dip_days).max().shift(1)
    exits = rebound | ~trend            # exit on recovery or trend break

    return entries.fillna(False), exits.fillna(False)
