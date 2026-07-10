"""stratgate.stats tests. The DSR test vector is from the Bailey & Lopez de Prado
(2014) paper's own numerical example — if this fails, the math is wrong."""

import math

import numpy as np

from stratgate import stats as S


def test_dsr_paper_example():
    # Paper: annualized SR 2.5 over 5y daily (250/yr) -> per-period inputs:
    sr = 2.5 / math.sqrt(250)          # T = 1250 observations
    n_obs, skew, kurt = 1250, -3.0, 10.0
    n_trials, var_trials = 100, 0.5 / 250

    sr0 = S.expected_max_sharpe(n_trials, var_trials)
    assert abs(sr0 - 0.1132) < 0.002, sr0          # hand-verified from Eq. 1

    dsr = S.deflated_sharpe_ratio(sr, n_obs, skew, kurt, n_trials, var_trials)
    assert abs(dsr - 0.900) < 0.005, dsr           # paper: "0.90"; Marti: 0.8997


def test_psr_gaussian_reduces_to_lo_mertens():
    # skew=0, raw kurt=3 -> denominator sqrt(1 + SR^2/2)
    sr, n = 0.1, 500
    psr = S.probabilistic_sharpe_ratio(sr, n, 0.0, 3.0, 0.0)
    from statistics import NormalDist
    expected = NormalDist().cdf(sr * math.sqrt(n - 1) / math.sqrt(1 + 0.5 * sr**2))
    assert abs(psr - expected) < 1e-12


def test_min_trl_unreachable_when_sr_below_benchmark():
    assert S.min_track_record_length(0.01, 0.0, 3.0, sr_benchmark=0.05) == float("inf")


def test_min_trl_matches_formula():
    sr, skew, kurt = 0.1, -0.5, 5.0
    got = S.min_track_record_length(sr, skew, kurt, 0.0, 0.95)
    from statistics import NormalDist
    z = NormalDist().inv_cdf(0.95)
    want = 1 + (1 - skew * sr + (kurt - 1) / 4 * sr**2) * (z / sr) ** 2
    assert abs(got - want) < 1e-9


def test_edge_cases_resolve_to_nan_or_degenerate():
    assert math.isnan(S.sharpe_ratio(np.zeros(100)))                 # zero variance
    assert math.isnan(S.sharpe_ratio(np.array([0.01])))              # n < 2
    assert S.expected_max_sharpe(1, 0.5) == 0.0                      # N <= 1
    assert S.expected_max_sharpe(100, 0.0) == 0.0                    # V <= 0
    assert math.isnan(S.probabilistic_sharpe_ratio(5.0, 100, 10.0, 3.0))  # denom <= 0
    assert math.isnan(S.walk_forward_efficiency(-0.1, 0.2))          # IS <= 0
    r = np.array([0.01, np.nan, -0.02, 0.03, np.nan])
    assert math.isfinite(S.skewness(np.concatenate([r] * 10)))       # NaN dropped


def test_kurtosis_is_raw_not_excess():
    rng = np.random.default_rng(7)
    r = rng.normal(0, 0.01, 200_000)
    assert abs(S.raw_kurtosis(r) - 3.0) < 0.1                        # normal -> 3


def test_max_drawdown():
    eq = np.array([1.0, 1.2, 0.9, 1.1, 1.3, 1.0])
    assert abs(S.max_drawdown(eq) - 0.25) < 1e-12                    # 1.2 -> 0.9
