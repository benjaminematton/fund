"""Statistical validation metrics for the strategy gate.

Pure numpy + stdlib (no scipy, no LLM imports — CI-enforced, design.md invariant 3).

Formulas verified against:
- Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio" (SSRN 2460551).
  Test vector from the paper's own numerical example lives in tests/test_stats.py.
- Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier" (PSR, MinTRL).
- Pardo (2008) walk-forward efficiency.

Conventions (get these wrong and every number is garbage):
- All Sharpe ratios in these functions are PER-PERIOD (native frequency, e.g. daily),
  NOT annualized. Annualize only for display: sr_annual = sr * sqrt(252).
- Kurtosis is RAW (Pearson; normal = 3.0), not excess.
- Std uses ddof=1 (matches the sqrt(n-1) in the PSR formula).
- Moment corrections apply at native frequency, BEFORE annualization.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_NORM = NormalDist()
EULER_MASCHERONI = 0.5772156649015329
TRADING_DAYS = 252


def _clean(returns: np.ndarray) -> np.ndarray:
    r = np.asarray(returns, dtype=float)
    return r[~np.isnan(r)]


def sharpe_ratio(returns: np.ndarray) -> float:
    """Per-period Sharpe: mean / std(ddof=1). NaN-safe. Zero variance -> nan."""
    r = _clean(returns)
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd <= 0 or not np.isfinite(sd):
        return float("nan")
    return float(r.mean() / sd)


def annualized_sharpe(returns: np.ndarray, periods_per_year: int = TRADING_DAYS) -> float:
    sr = sharpe_ratio(returns)
    return sr * math.sqrt(periods_per_year) if np.isfinite(sr) else float("nan")


def skewness(returns: np.ndarray) -> float:
    """Biased sample skewness m3 / m2^1.5 (matches scipy.stats.skew default)."""
    r = _clean(returns)
    if r.size < 3:
        return float("nan")
    d = r - r.mean()
    m2 = np.mean(d**2)
    if m2 <= 0:
        return float("nan")
    return float(np.mean(d**3) / m2**1.5)


def raw_kurtosis(returns: np.ndarray) -> float:
    """Biased RAW kurtosis m4 / m2^2 (normal = 3.0).

    Equals scipy.stats.kurtosis(r, fisher=False). The #1 real-world bug in PSR/DSR
    implementations is feeding excess kurtosis here — don't.
    """
    r = _clean(returns)
    if r.size < 4:
        return float("nan")
    d = r - r.mean()
    m2 = np.mean(d**2)
    if m2 <= 0:
        return float("nan")
    return float(np.mean(d**4) / m2**2)


def probabilistic_sharpe_ratio(
    sr: float, n_obs: int, skew: float, kurt_raw: float, sr_benchmark: float = 0.0
) -> float:
    """PSR(SR*) = Phi( (SR - SR*) * sqrt(n-1) / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) ).

    All inputs per-period. kurt_raw is raw kurtosis (normal = 3). Returns nan on
    any degenerate input (invariant 7: nan at the gate resolves to REJECT).
    """
    if not all(np.isfinite([sr, skew, kurt_raw, sr_benchmark])) or n_obs < 2:
        return float("nan")
    denom_sq = 1.0 - skew * sr + ((kurt_raw - 1.0) / 4.0) * sr**2
    if denom_sq <= 0:  # extreme skew/kurt or data error — asymptotics invalid
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return float(_NORM.cdf(z))


def expected_max_sharpe(n_trials: int, var_trial_sharpes: float) -> float:
    """SR0: expected max per-period Sharpe across N independent noise trials.

    SR0 = sqrt(V) * ((1-g)*ppf(1 - 1/N) + g*ppf(1 - 1/(N*e))), g = Euler-Mascheroni.
    N <= 1 or V <= 0 degenerates to 0 (DSR then reduces to PSR vs 0).
    Approximation is derived for large N; slightly conservative-optimistic for N < 50.
    """
    if n_trials <= 1 or var_trial_sharpes <= 0 or not np.isfinite(var_trial_sharpes):
        return 0.0
    g = EULER_MASCHERONI
    a = _NORM.inv_cdf(1.0 - 1.0 / n_trials)
    b = _NORM.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(var_trial_sharpes) * ((1.0 - g) * a + g * b))


def independent_trials(n_trials: int, avg_correlation: float) -> float:
    """Effective N for correlated trials (paper Appendix A.3): N_eff = rho + (1-rho)*N."""
    rho = min(max(avg_correlation, 0.0), 1.0)
    return rho + (1.0 - rho) * n_trials


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    skew: float,
    kurt_raw: float,
    n_trials: int,
    var_trial_sharpes: float,
) -> float:
    """DSR = PSR(SR0). Probability the true Sharpe > 0 after correcting for
    selection among n_trials and non-normal returns. Everything per-period.

    n_trials MUST be the family-wide trial count from the registry (strategy.md
    invariant 3), never just this spec's runs.
    """
    sr0 = expected_max_sharpe(n_trials, var_trial_sharpes)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurt_raw, sr_benchmark=sr0)


def min_track_record_length(
    sr: float, skew: float, kurt_raw: float, sr_benchmark: float = 0.0, confidence: float = 0.95
) -> float:
    """MinTRL = 1 + (1 - g3*SR + (g4-1)/4*SR^2) * (Z_a / (SR - SR*))^2, in OBSERVATIONS
    at native frequency. inf when SR <= SR* (confidence unreachable)."""
    if not all(np.isfinite([sr, skew, kurt_raw])) or sr <= sr_benchmark:
        return float("inf")
    z = _NORM.inv_cdf(confidence)
    denom_sq = 1.0 - skew * sr + ((kurt_raw - 1.0) / 4.0) * sr**2
    if denom_sq <= 0:
        return float("inf")
    return float(1.0 + denom_sq * (z / (sr - sr_benchmark)) ** 2)


def walk_forward_efficiency(is_metric: float, oos_metric: float) -> float:
    """WFE = pooled OOS / pooled IS (Pardo). Use annualized return or Sharpe —
    consistently. IS <= 0 makes the ratio meaningless -> nan (gate REJECTs)."""
    if not np.isfinite(is_metric) or not np.isfinite(oos_metric) or is_metric <= 0:
        return float("nan")
    return float(oos_metric / is_metric)


def max_drawdown(equity: np.ndarray) -> float:
    """Max peak-to-trough drawdown of an equity curve, as a positive fraction."""
    eq = _clean(equity)
    if eq.size < 2:
        return float("nan")
    peaks = np.maximum.accumulate(eq)
    dd = 1.0 - eq / peaks
    return float(dd.max())
