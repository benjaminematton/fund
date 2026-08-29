"""run_backtest wrapper tests: determinism, caching, budget, holdout single-touch,
cost-floor monotonicity. Offline — synthetic data, in-memory SQLite, no keys."""

import numpy as np

import fundbt.rules  # noqa: F401  (registers dip_buyer)
from fundbt.registry import TrialRegistry
from fundbt.run_backtest import (BacktestError, evaluate_holdout, run_backtest,
                                 snapshot_hash)
from state.db import connect
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_spec,
                             seed_spec_row)

NOW = "2026-07-09T00:00:00Z"


def setup():
    return make_market(), make_spec(), TrialRegistry(":memory:")


def test_deterministic_and_cached():
    close, spec, reg = setup()
    r1 = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                      registry=reg, seat="quant", now_iso=NOW)
    r2 = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                      registry=reg, seat="quant", now_iso=NOW)
    assert not r1["cached"] and r2["cached"]
    assert r1["run_key"] == r2["run_key"]
    assert r1["net_sharpe"] == r2["net_sharpe"]
    assert reg.family_n("F1") == 1          # cache hit did NOT increment N


def test_snapshot_hash_ignores_platform_float_noise():
    """numpy's macOS wheel FMA-contracts the multiply-add inside
    Generator.uniform; the manylinux wheel rounds twice. That 1-ULP split at
    the first draw grows to ~5e-15 relative across the market, so hashing raw
    float text identifies the platform rather than the data. Perturb by an
    order of magnitude more than the observed drift: the hash must not move."""
    close = make_market()
    noise = 1.0 + 1e-14 * np.random.default_rng(0).standard_normal(close.shape)
    assert snapshot_hash(close * noise) == snapshot_hash(close)


def test_snapshot_hash_detects_a_real_data_change():
    """The tolerance above must not be so wide that a changed snapshot slips
    through — a pinned slice that moved is exactly what this hash is for."""
    close = make_market()
    changed = close.copy()
    changed.iloc[0, 0] *= 1.001
    assert snapshot_hash(changed) != snapshot_hash(close)


def test_param_range_enforced():
    close, spec, reg = setup()
    try:
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_pct": 0.20},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "param_out_of_range" in str(e)


def test_budget_exhaustion_is_logged():
    close, spec, reg = setup()
    spec["search_budget"] = 2
    for dd in (4, 5):
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_days": dd},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
    try:
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_days": 6},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "budget_exhausted" in str(e)
    assert reg.family_n("F1") == 3          # the rejection itself was logged


def test_cost_floors_monotonic():
    close, spec, reg = setup()
    r = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)
    assert r["net_sharpe"] > r["net_sharpe_2x"] > r["net_sharpe_3x"]


def test_planted_edge_detected_and_sane():
    close, spec, reg = setup()
    r = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)
    assert r["n_trades"] >= 100
    assert r["net_sharpe"] > 0.5            # planted reversion is detectable
    assert 0 < r["deflated_sharpe"] <= 1
    assert np.isfinite(r["wfe"])
    assert r["span_years"] > 7.5            # holdout removed ~18 months


def test_holdout_single_touch():
    close, spec, reg = setup()
    d1 = evaluate_holdout(spec=spec, params=GOLDEN_PARAMS, close=close,
                          registry=reg, now_iso=NOW)
    assert np.isfinite(d1["holdout_sharpe"])
    try:
        evaluate_holdout(spec=spec, params=GOLDEN_PARAMS, close=close,
                         registry=reg, now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "holdout_already_consumed" in str(e)


def test_the_golden_spec_has_a_strategy_specs_row():
    """trial_registry.spec_id REFERENCES strategy_specs(spec_id), and
    state/db.py turns foreign keys ON, so registry.log() for the golden spec is
    only possible if this row exists. make_spec()'s id is baked into
    tests/test_golden.py's frozen hashes and cannot be changed, so the row is
    seeded to match the id rather than the other way round (issue #172).
    """
    spec = make_spec()
    conn = connect(":memory:")
    seed_spec_row(conn, spec)
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs WHERE spec_id = ?",
        (spec["spec_id"],)).fetchone()[0] == 1
    seed_spec_row(conn, spec)                    # idempotent: no PK explosion
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs").fetchone()[0] == 1
