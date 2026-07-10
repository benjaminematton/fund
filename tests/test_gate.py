"""Gate G2/G3 tests: passing stats pass, each violated threshold fails with the
right reason, and anything malformed resolves to gate_error (invariant 7)."""

from stratgate.gate import evaluate_g2, evaluate_g3


def good_stats():
    return {
        "n_trades": 250,
        "span_years": 9.5,
        "net_sharpe": 0.9,
        "net_sharpe_2x": 0.6,
        "net_sharpe_3x": 0.3,
        "deflated_sharpe": 0.97,
        "wfe": 0.72,
        "max_drawdown": 0.18,
        "cost_share": 0.30,
        "param_neighbors": {"dip_days": {"up": 0.8, "down": 0.75}},
    }


def test_g2_passes_good_stats():
    v = evaluate_g2(good_stats())
    assert v.passed and v.reason == "pass"


def test_g2_each_threshold_fails_with_named_reason():
    cases = {
        "n_trades": 99,
        "span_years": 7.9,
        "net_sharpe": 0.49,
        "net_sharpe_2x": 0.29,
        "net_sharpe_3x": 0.0,          # must be strictly > 0
        "deflated_sharpe": 0.94,
        "max_drawdown": 0.26,
        "cost_share": 0.51,
    }
    for field, bad in cases.items():
        s = good_stats()
        s[field] = bad
        v = evaluate_g2(s)
        assert not v.passed and v.reason == field, (field, v.reason)


def test_g2_wfe_soft_and_hard():
    s = good_stats()
    s["wfe"] = 0.45
    assert evaluate_g2(s).reason == "wfe"
    s["wfe"] = 0.25
    v = evaluate_g2(s)
    assert not v.passed  # trips both wfe and wfe_hard


def test_g2_param_cliff():
    s = good_stats()
    s["param_neighbors"] = {"dip_pct": {"up": 0.9, "down": 0.5}}  # 44% loss vs 0.9
    v = evaluate_g2(s)
    assert not v.passed and v.reason == "param_cliff"


def test_g2_malformed_is_gate_error():
    s = good_stats()
    del s["deflated_sharpe"]
    assert evaluate_g2(s).reason == "gate_error"
    s = good_stats()
    s["wfe"] = float("nan")
    assert evaluate_g2(s).reason == "gate_error"
    s = good_stats()
    s["param_neighbors"] = {"x": {"up": float("nan")}}
    assert evaluate_g2(s).reason == "gate_error"


def test_g3():
    ok = evaluate_g3(0.55, 0.9, {"bull": 0.8, "bear": -0.1, "chop": 0.2})
    assert ok.passed
    weak = evaluate_g3(0.30, 0.9, {"bull": 0.8, "bear": 0.1, "chop": 0.2})
    assert not weak.passed and weak.reason == "holdout_vs_wf"
    regime = evaluate_g3(0.55, 0.9, {"bull": 0.8, "bear": -0.1, "chop": -0.2})
    assert not regime.passed and regime.reason == "regimes_nonneg"
    err = evaluate_g3(0.55, -0.1, {"bull": 1, "bear": 1, "chop": 1})
    assert err.reason == "gate_error"
