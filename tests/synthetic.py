"""Seeded synthetic market for offline tests and the golden fixture.

20 names, ~10 years of daily bars, geometric random walk with a PLANTED
short-term mean-reversion effect: after a multi-day drop, expected next-day
return gets a positive kick. Deterministic: same seed, same market, forever.
"""

from __future__ import annotations

import json
import sqlite3

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


# --- fund-DB fixtures ------------------------------------------------------
# state/schema.sql declares trial_registry.spec_id REFERENCES
# strategy_specs(spec_id) and state/db.py:22 sets PRAGMA foreign_keys = ON, so
# a trial cannot be logged for a spec that has no row (issue #172). Measured:
# INSERT OR IGNORE does NOT swallow a foreign-key violation — SQLite's ON
# CONFLICT algorithms do not apply to foreign keys — so registry.log() raises
# rather than silently dropping the trial.
#
# make_spec()'s spec_id is hardcoded and is baked into tests/test_golden.py's
# frozen config_hash/run_key, so it cannot move. The row is built to match it.
# Written as a direct INSERT rather than through
# state.specs.insert_strategy_spec because that function content-addresses the
# id (fundbt.hashing.spec_id) and cannot produce this hand-written one.
#
# Every column below except spec_id is filler that satisfies NOT NULL and the
# CHECK constraints. Nothing in fundbt reads this row — run_backtest reads the
# spec DICT from make_spec(); the row exists so the foreign key resolves.
_SPEC_ROW_COLUMNS = (
    "spec_id", "family", "seat", "hypothesis", "mechanism_class", "universe",
    "liquidity_bucket", "signal_rule", "param_ranges", "search_budget",
    "holding_period_d", "rebalance", "expected_turnover", "exit_rule",
    "invalidation", "capacity_usd", "predicted", "llm_in_loop", "created_at")

SPEC_ROW_CREATED_AT = "2026-07-09T00:00:00Z"


def seed_spec_row(conn: sqlite3.Connection, spec: dict | None = None) -> str:
    """INSERT the strategy_specs row that `spec`'s trials will reference,
    plus the `strategies` lifecycle row registration always writes with it.

    The lifecycle row is not optional garnish: state/specs.py's selector INNER
    JOINs `strategies`, so a spec seeded without one looks registered and is
    structurally invisible to G1 — the exact fail-open shape §3.4 forbids.
    This function bypasses insert_strategy_spec (see above) and so has to
    reproduce both of its writes, not just the first.

    Idempotent (INSERT OR IGNORE on both primary keys). Returns the spec_id.
    """
    spec = spec if spec is not None else make_spec()
    values = (
        spec["spec_id"],
        spec["family"],
        "quant",
        "buyers of 5d dips above trend are compensated for absorbing"
        " short-term selling pressure",
        "behavioral",
        json.dumps({"index": "SYN20", "pit_constituents": True, "filters": []},
                   sort_keys=True),
        spec["liquidity_bucket"],
        json.dumps(spec["signal_rule"], sort_keys=True),
        json.dumps(spec["param_ranges"], sort_keys=True),
        max(int(spec["search_budget"]), 1),      # CHECK(search_budget > 0)
        5,
        "daily",
        2.0,
        "exit on trend break or after holding_period_d",
        "no positive next-day drift after a 5% 5-day dip",
        1e8,
        json.dumps({"net_sharpe": 1.0, "max_dd": 0.25, "hit_rate": 0.55},
                   sort_keys=True),
        0,
        SPEC_ROW_CREATED_AT,
    )
    conn.execute(
        f"INSERT OR IGNORE INTO strategy_specs"
        f" ({', '.join(_SPEC_ROW_COLUMNS)})"
        f" VALUES ({', '.join(['?'] * len(_SPEC_ROW_COLUMNS))})",
        values)
    # A literal VALUES, deliberately NOT state/specs.py's gated SELECT form.
    # If the spec INSERT above were ever dropped, that form would write nothing
    # and this fixture would seed a silently empty database; the FK raises here
    # instead (see the file comment above — INSERT OR IGNORE does not swallow a
    # foreign-key violation). Loud is right in a fixture and wrong in the write
    # path, whose caller turns a dropped INSERT into a legible refusal.
    conn.execute(
        "INSERT OR IGNORE INTO strategies (strategy_id, state, updated_at)"
        " VALUES (?, 'SPEC', ?)", (spec["spec_id"], SPEC_ROW_CREATED_AT))
    conn.commit()
    return spec["spec_id"]


def spec_payload(**overrides) -> dict:
    """One valid `submit_strategy_spec` payload — every §2 field EXCEPT `seat`.

    `seat` is deliberately absent: the handler binds it from the calling seat
    (strategy-contracts.md §3.1), so a payload carrying one would be either
    redundant or a seat naming a seat it is not. A caller that passed it would
    hit `StrategySpec(**args, seat=seat)`'s duplicate-keyword TypeError, which
    is the right failure but not one a fixture should manufacture.

    NOT make_spec(): that returns a BACKTEST CONFIG (spec_id, family,
    param_ranges, search_budget — the subset fundbt reads), and its spec_id is
    frozen into tests/test_golden.py. This one is the agent-facing registration
    payload, and it carries no id at all — the id is computed from the content.
    """
    return dict(
        family="F1",
        hypothesis="Reversal pays for absorbing forced selling.",
        mechanism_class="liquidity_provision",
        universe={"index": "Russell 1000", "pit_constituents": True,
                  "filters": []},
        liquidity_bucket="mega_large",
        signal_rule={"entry": "5d return below -1.5 sigma"},
        param_ranges={"sigma": [1.0, 2.5, 0.25]},
        search_budget=24, holding_period_d=5, rebalance="daily",
        expected_turnover=42.0, exit_rule="close at 5 trading days",
        invalidation="12m low-turnover spread negative for two quarters.",
        capacity_usd=4000000.0,
        predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
        llm_in_loop=0) | overrides


def make_registry(spec: dict | None = None) -> "TrialRegistry":
    """A TrialRegistry over a FRESH fund-schema database, spec row seeded.

    In-memory and per call: that rules out CROSS-test pollution by
    construction — no test's family N can carry into another test's registry.
    It does NOT by itself pin fixtures/golden-strategy.md:46's frozen
    `deflated_sharpe (N=1) = 1.000000`: run_backtest computes
    family_n(family) + 1 with no scoping, so a holdout-then-family-trial
    sequence would move N even in a registry this fresh. That number stays
    N=1 because no golden test runs a further family trial after a G3 holdout
    on its own registry — per-test isolation, not construction, is what keeps
    it true (see Task 5 Step 5 and fixtures/golden-strategy.md's own note). A
    registry SHARED across tests would additionally leak N across tests,
    which this function does rule out — but that is a narrower guarantee than
    the docstring here used to claim.

    state.db.connect() applies state/schema.sql and sets
    PRAGMA foreign_keys = ON, so these tests exercise the real fund schema with
    the real foreign keys — which is the whole point of #172. `:memory:` rather
    than a tmp_path file because the guarantee wanted here is a fresh database,
    not a filesystem.

    Deliberately NOT a pytest fixture: tests/run_tests.py is a second,
    zero-dependency runner that calls every test_* with NO arguments, and it is
    not in `make test`. A fixture parameter would break it silently.
    """
    from fundbt.registry import TrialRegistry
    from state.db import connect

    conn = connect(":memory:")
    seed_spec_row(conn, spec)
    return TrialRegistry(conn)
