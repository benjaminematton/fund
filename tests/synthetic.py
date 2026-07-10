"""Seeded synthetic market for offline tests and the golden fixture.

20 names, ~10 years of daily bars, geometric random walk with a PLANTED
short-term mean-reversion effect: after a multi-day drop, expected next-day
return gets a positive kick. Deterministic: same seed, same market, forever.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

N_ASSETS = 20
N_DAYS = 2520          # ~10 years
SEED = 42
REVERSION_KICK = 0.005   # planted edge: E[next ret] after a 5d dip of 5%+
DIP_DAYS, DIP_PCT = 5, 0.05


def make_market(seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2016-01-04", periods=N_DAYS)
    drift = 0.0003
    vol = rng.uniform(0.012, 0.025, N_ASSETS)

    rets = np.empty((N_DAYS, N_ASSETS))
    prices = np.full(N_ASSETS, 100.0)
    hist = np.ones((DIP_DAYS, N_ASSETS))  # rolling window of (1 + r)

    for t in range(N_DAYS):
        base = rng.normal(drift, vol)
        window_ret = hist.prod(axis=0) - 1.0
        kick = np.where(window_ret <= -DIP_PCT, REVERSION_KICK, 0.0)
        r = base + kick
        rets[t] = r
        hist = np.vstack([hist[1:], 1.0 + r])
        prices *= 1.0 + r

    close = 100.0 * (1.0 + pd.DataFrame(rets, index=dates)).cumprod()
    close.columns = [f"SYN{i:02d}" for i in range(N_ASSETS)]
    return close


def make_spec(spec_id: str = "spec_golden000000f1") -> dict:
    return {
        "spec_id": spec_id,
        "family": "F1",
        "liquidity_bucket": "mega_large",
        "signal_rule": {"name": "dip_buyer"},
        "param_ranges": {
            "dip_days": [3, 8, 1],
            "dip_pct": [0.03, 0.08, 0.01],
            "trend_days": [150, 250, 50],
        },
        "search_budget": 20,
    }


GOLDEN_PARAMS = {"dip_days": 5, "dip_pct": 0.05, "trend_days": 200}
