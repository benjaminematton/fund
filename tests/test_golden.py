"""Golden-strategy test vectors (fixtures/golden-strategy.md). Exact numbers —
the pipeline is deterministic, so any drift here is a behavior change that
needs a deliberate re-record (same policy as recordings/ in the main repo)."""

import fundbt.rules  # noqa: F401
from fundbt.registry import TrialRegistry
from fundbt.run_backtest import evaluate_holdout, run_backtest
from stratgate.gate import evaluate_g2, evaluate_g3
from tests.synthetic import GOLDEN_PARAMS, make_market, make_spec

NOW = "2026-07-09T00:00:00Z"
FAIL_PARAMS = {"dip_days": 3, "dip_pct": 0.03, "trend_days": 150}


def test_golden_pass_path():
    close, spec, reg = make_market(), make_spec(), TrialRegistry(":memory:")
    r = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)

    assert r["data_snapshot_hash"] == "dat_a4d2ee4153d5df6d"
    assert r["config_hash"] == "cfg_2ad6bd632a066999"
    assert r["run_key"] == "run_549f306118a70c4e"
    assert r["n_trades"] == 740
    assert r["span_years"] == 8.448
    assert round(r["net_sharpe"], 6) == 1.827113
    assert round(r["net_sharpe_2x"], 6) == 1.594162
    assert round(r["net_sharpe_3x"], 6) == 1.360801
    assert round(r["per_period_sharpe"], 6) == 0.115097
    assert round(r["wfe"], 6) == 0.639459
    assert round(r["max_drawdown"], 5) == 0.20406
    assert round(r["cost_share"], 6) == 0.113073
    assert r["n_trials_family"] == 1

    g2 = evaluate_g2(r)
    assert g2.passed and g2.reason == "pass"

    h = evaluate_holdout(spec=spec, params=GOLDEN_PARAMS, close=close,
                         registry=reg, now_iso=NOW)
    assert h["holdout_days"] == 391
    assert h["holdout_trades"] == 160
    assert round(h["holdout_sharpe"], 6) == 3.092703

    g3 = evaluate_g3(h["holdout_sharpe"], r["net_sharpe"], h["regime_sharpe"])
    assert g3.passed


def test_golden_fail_path():
    close, spec, reg = make_market(), make_spec(), TrialRegistry(":memory:")
    r = run_backtest(spec=spec, params=FAIL_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)
    assert r["n_trades"] == 1473
    assert round(r["net_sharpe"], 4) == 1.5917    # looks great...
    assert round(r["wfe"], 3) == 0.307            # ...and is overfit noise-churn
    g2 = evaluate_g2(r)
    assert not g2.passed and g2.reason == "wfe"
