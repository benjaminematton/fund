"""The run_backtest tool (strategy.md §4, strategy-contracts.md §3.2).

Wraps the engine with the seven non-overridable enforcements:
  1. registered spec + params inside declared ranges
  2. search-budget check (exceeded -> logged rejection)
  3. holdout quarantine (last N months invisible)
  4. cost floors by liquidity bucket + automatic 2x/3x stress
  5. pinned, hash-verified data snapshot
  6. auto-logged trial registry row (idempotent via run_key)
  7. deterministic: seeded, clock-injected, content-addressed

Signal rules are REGISTERED CODE (RULES dict), never free text: the agent picks a
rule name + params within the spec's pre-declared ranges. This is what makes
historical backtests contamination-safe (strategy.md invariant 5): code replays
the rule; no LLM decides on historical days it may have memorized.
"""

from __future__ import annotations

import hashlib
from typing import Callable

import numpy as np
import pandas as pd

from stratgate import stats as S
from . import costs, hashing
from .engine_pandas import ENGINE_VERSION, run as engine_run
from .registry import TrialRegistry

TRADING_DAYS = 252
HOLDOUT_MONTHS = 18
WF_FOLDS = 4
WF_IS_FRACTION = 0.70

# rule_name -> fn(close: DataFrame, params: dict) -> (entries, exits) bool DataFrames
RULES: dict[str, Callable] = {}


def register_rule(name: str):
    def deco(fn):
        RULES[name] = fn
        return fn
    return deco


def snapshot_hash(close: pd.DataFrame) -> str:
    """SHA256 of the pinned data slice. Recorded with every trial.

    Six significant digits, not raw float text: numpy's macOS and manylinux
    wheels disagree by ~1 ULP on FMA-contracted multiply-adds, so hashing the
    exact decimals would identify the platform rather than the data.
    """
    payload = close.to_csv(float_format="%.6g").encode()
    return "dat_" + hashlib.sha256(payload).hexdigest()[:16]


class BacktestError(Exception):
    """Tool error: nothing advances, reason in .args[0] (invariant 7)."""


def _annualized_sharpe(returns: pd.Series) -> float:
    return S.annualized_sharpe(returns.to_numpy())


def _wf_efficiency(net_returns: pd.Series) -> float:
    """Pooled OOS Sharpe / pooled IS Sharpe over contiguous folds (fixed config).

    With per-fold optimization this is Pardo's WFE; with a fixed config it is a
    stability ratio — same threshold semantics (OOS should look like IS).
    """
    n = len(net_returns)
    fold = n // WF_FOLDS
    if fold < 40:
        return float("nan")
    is_parts, oos_parts = [], []
    for k in range(WF_FOLDS):
        seg = net_returns.iloc[k * fold: (k + 1) * fold if k < WF_FOLDS - 1 else n]
        cut = int(len(seg) * WF_IS_FRACTION)
        is_parts.append(seg.iloc[:cut])
        oos_parts.append(seg.iloc[cut:])
    is_sr = _annualized_sharpe(pd.concat(is_parts))
    oos_sr = _annualized_sharpe(pd.concat(oos_parts))
    return S.walk_forward_efficiency(is_sr, oos_sr)


def _regime_sharpes(net_returns: pd.Series, close: pd.DataFrame) -> dict:
    """Bucket days by trailing 63d equal-weight universe return: bull/bear/chop."""
    mkt = close.pct_change().mean(axis=1)
    trail = mkt.rolling(63).sum()
    out = {}
    for name, mask in {
        "bull": trail > 0.025,
        "bear": trail < -0.025,
        "chop": (trail >= -0.025) & (trail <= 0.025),
    }.items():
        seg = net_returns[mask.reindex(net_returns.index).fillna(False)]
        out[name] = _annualized_sharpe(seg) if len(seg) >= 40 else 0.0
        if not np.isfinite(out[name]):
            out[name] = 0.0
    return out


def _neighbor_params(params: dict, ranges: dict) -> dict:
    """{param: {'up': params', 'down': params'}} one step each way, clipped in-range."""
    out = {}
    for p, (lo, hi, step) in ranges.items():
        if step == 0:
            continue
        sides = {}
        for side, delta in (("up", step), ("down", -step)):
            v = params[p] + delta
            if lo <= v <= hi and v != params[p]:
                sides[side] = {**params, p: v}
        if sides:
            out[p] = sides
    return out


def run_backtest(
    *,
    spec: dict,
    params: dict,
    close: pd.DataFrame,
    registry: TrialRegistry,
    seat: str,
    now_iso: str,            # injected Clock — never datetime.now() (CI-enforced)
    seed: int = 0,
    holdout_months: int = HOLDOUT_MONTHS,
) -> dict:
    """Returns the BacktestResult dict (contracts §3.2). Raises BacktestError on
    any violation — nothing partial is ever written except budget rejections,
    which ARE logged (a spent trial is a spent trial)."""

    # 1. spec + params validation
    rule_name = spec["signal_rule"]["name"]
    if rule_name not in RULES:
        raise BacktestError("unknown_rule")
    ranges = spec["param_ranges"]
    for p, v in params.items():
        if p not in ranges:
            raise BacktestError(f"undeclared_param:{p}")
        lo, hi, _ = ranges[p]
        if not (lo <= v <= hi):
            raise BacktestError(f"param_out_of_range:{p}")

    sid = spec["spec_id"]
    cfg = hashing.config_hash(sid, params)
    dhash = snapshot_hash(close)
    rkey = hashing.run_key(cfg, dhash, ENGINE_VERSION, seed)

    # 6/7. idempotency: identical run returns cached result, N unchanged
    cached = registry.get(rkey)
    if cached is not None:
        return {**cached, "cached": True}

    # 2. search budget (family N counts this attempt even if over budget)
    if registry.spec_trial_count(sid) >= spec["search_budget"]:
        registry.log(run_key=rkey, spec_id=sid, family=spec["family"],
                     config_hash=cfg, data_snapshot_hash=dhash,
                     engine_version=ENGINE_VERSION, seed=seed, seat=seat,
                     stats={"rejected": "budget_exhausted"}, is_holdout=False,
                     created_at=now_iso)
        raise BacktestError("budget_exhausted")

    # 3. holdout quarantine
    if close.isna().any().any():
        raise BacktestError("nan_in_close")
    cutoff = close.index.max() - pd.DateOffset(months=holdout_months)
    visible = close.loc[close.index <= cutoff]
    if len(visible) < TRADING_DAYS * 2:
        raise BacktestError("insufficient_data")

    # 4. cost floor + stress; run
    floor = costs.floor_for(spec["liquidity_bucket"])
    entries, exits = RULES[rule_name](visible, params)
    runs = {
        m: engine_run(visible, entries, exits, cost_bps_per_side=floor * m)
        for m in costs.STRESS_MULTIPLIERS
    }
    base = runs[1.0]
    rets = base.returns.to_numpy()

    # DSR with family-wide N (this trial included) and family Sharpe variance
    per_period_sr = S.sharpe_ratio(rets)
    n_trials = registry.family_n(spec["family"]) + 1
    var_trials = registry.family_sharpe_variance(spec["family"]) or (
        0.5 * max(per_period_sr, 0.01) ** 2  # cold-start prior until >= 2 trials
    )
    dsr = S.deflated_sharpe_ratio(
        per_period_sr, len(rets), S.skewness(rets), S.raw_kurtosis(rets),
        n_trials=int(n_trials), var_trial_sharpes=var_trials,
    )

    # neighbors (not separately budgeted — same config family, reported not logged)
    neighbors = {}
    for p, sides in _neighbor_params(params, ranges).items():
        neighbors[p] = {}
        for side, nparams in sides.items():
            ne, nx = RULES[rule_name](visible, nparams)
            nres = engine_run(visible, ne, nx, cost_bps_per_side=floor)
            neighbors[p][side] = _annualized_sharpe(nres.returns)

    result = {
        "run_key": rkey,
        "spec_id": sid,
        "config_hash": cfg,
        "data_snapshot_hash": dhash,
        "n_trades": base.n_trades,
        "span_years": round(len(visible) / TRADING_DAYS, 3),
        "net_sharpe": _annualized_sharpe(base.returns),
        "net_sharpe_2x": _annualized_sharpe(runs[2.0].returns),
        "net_sharpe_3x": _annualized_sharpe(runs[3.0].returns),
        "per_period_sharpe": per_period_sr,
        "deflated_sharpe": dsr,
        "n_trials_family": int(n_trials),
        "wfe": _wf_efficiency(base.returns),
        "max_drawdown": S.max_drawdown(base.equity.to_numpy()),
        "turnover_annual": base.annual_turnover,
        "cost_share": base.cost_share,
        "param_neighbors": neighbors,
        "regime_sharpe": _regime_sharpes(base.returns, visible),
        "cached": False,
    }

    registry.log(run_key=rkey, spec_id=sid, family=spec["family"], config_hash=cfg,
                 data_snapshot_hash=dhash, engine_version=ENGINE_VERSION, seed=seed,
                 seat=seat, stats=result, is_holdout=False, created_at=now_iso)
    return result


def evaluate_holdout(
    *,
    spec: dict,
    params: dict,
    close: pd.DataFrame,
    registry: TrialRegistry,
    now_iso: str,
    holdout_months: int = HOLDOUT_MONTHS,
    warmup_days: int = 260,
) -> dict:
    """G3's one-shot holdout run. Signals get warmup context from before the
    cutoff, but ONLY post-cutoff returns are scored. Single-touch is enforced by
    registry.consume_holdout — a second call returns holdout_already_consumed."""
    floor = costs.floor_for(spec["liquidity_bucket"])
    cutoff = close.index.max() - pd.DateOffset(months=holdout_months)
    window = close.loc[close.index > cutoff]
    warm_start = close.index[close.index <= cutoff][-warmup_days:]
    ctx = close.loc[warm_start.min():]

    entries, exits = RULES[spec["signal_rule"]["name"]](ctx, params)
    res = engine_run(ctx, entries, exits, cost_bps_per_side=floor)
    holdout_rets = res.returns.loc[res.returns.index > cutoff]

    cfg = hashing.config_hash(spec["spec_id"], params)
    rkey = hashing.run_key(cfg, snapshot_hash(close), ENGINE_VERSION + "+holdout", 0)
    detail = {
        "holdout_sharpe": _annualized_sharpe(holdout_rets),
        "holdout_days": int(len(holdout_rets)),
        "holdout_trades": res.n_trades,
        "regime_sharpe": _regime_sharpes(holdout_rets, window),
    }
    fresh = registry.consume_holdout(
        spec_id=spec["spec_id"], run_key=rkey,
        passed=bool(np.isfinite(detail["holdout_sharpe"]) and detail["holdout_sharpe"] > 0),
        detail=detail, created_at=now_iso,
    )
    if not fresh:
        raise BacktestError("holdout_already_consumed")  # p-hacking alarm -> #risk
    return detail
