"""Gates G2 (statistical) and G3 (holdout + robustness) from specs/strategy.md §5.

Pure functions: stats dict in, verdict out. No LLM imports, no I/O, no clock.
Thresholds are code constants changed only by human commit (invariant: the gate
is deterministic; agents cannot approve, score, or waive — including themselves).

Unifying rule (strategy.md invariant 7): any missing, NaN, or malformed input
resolves to REJECT with reason gate_error. The gate never guesses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class G2Thresholds:
    min_trades: int = 100
    min_span_years: float = 8.0
    min_sharpe_floor_costs: float = 0.5   # annualized, at floor costs
    min_sharpe_2x_costs: float = 0.3
    min_sharpe_3x_costs: float = 0.0      # must remain positive
    min_deflated_sharpe: float = 0.95
    min_wfe: float = 0.5
    hard_reject_wfe: float = 0.3          # below this: overfit, no appeal
    max_drawdown: float = 0.25
    max_cost_share: float = 0.50
    max_neighbor_sharpe_loss: float = 0.40  # param cliff = curve-fit


@dataclass(frozen=True)
class G3Thresholds:
    min_holdout_sharpe: float = 0.0            # strictly greater than
    min_holdout_vs_wf_ratio: float = 0.5       # holdout SR >= 50% of walk-forward SR
    min_nonnegative_regimes: int = 2           # of 3 buckets (bull/bear/chop)


@dataclass
class Check:
    name: str
    ok: bool
    value: float | int | None
    limit: str


@dataclass
class Verdict:
    passed: bool
    reason: str                      # "pass" | first failing check | "gate_error"
    checks: list[Check] = field(default_factory=list)


# Fields evaluate_g2 requires in the stats dict (BacktestResult, contracts §3.2).
G2_REQUIRED = (
    "n_trades", "span_years", "net_sharpe", "net_sharpe_2x", "net_sharpe_3x",
    "deflated_sharpe", "wfe", "max_drawdown", "cost_share", "param_neighbors",
)


def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and not math.isfinite(x))


def evaluate_g2(stats: dict, t: G2Thresholds = G2Thresholds()) -> Verdict:
    """G2 statistical gate. stats = BacktestResult dict from run_backtest
    (walk-forward numbers, floor costs). Missing/NaN anything -> gate_error."""
    for key in G2_REQUIRED:
        if key not in stats or (key != "param_neighbors" and _bad(stats[key])):
            return Verdict(False, "gate_error", [Check(key, False, None, "present & finite")])

    neighbors = stats["param_neighbors"] or {}
    base_sr = stats["net_sharpe"]
    worst_loss = 0.0
    for param, sides in neighbors.items():
        for side, sr in sides.items():
            if _bad(sr):
                return Verdict(False, "gate_error",
                               [Check(f"neighbor:{param}:{side}", False, None, "finite")])
            if base_sr > 0:
                worst_loss = max(worst_loss, (base_sr - sr) / base_sr)

    checks = [
        Check("n_trades", stats["n_trades"] >= t.min_trades,
              stats["n_trades"], f">= {t.min_trades}"),
        Check("span_years", stats["span_years"] >= t.min_span_years,
              stats["span_years"], f">= {t.min_span_years}"),
        Check("net_sharpe", stats["net_sharpe"] >= t.min_sharpe_floor_costs,
              stats["net_sharpe"], f">= {t.min_sharpe_floor_costs}"),
        Check("net_sharpe_2x", stats["net_sharpe_2x"] >= t.min_sharpe_2x_costs,
              stats["net_sharpe_2x"], f">= {t.min_sharpe_2x_costs}"),
        Check("net_sharpe_3x", stats["net_sharpe_3x"] > t.min_sharpe_3x_costs,
              stats["net_sharpe_3x"], f"> {t.min_sharpe_3x_costs}"),
        Check("deflated_sharpe", stats["deflated_sharpe"] >= t.min_deflated_sharpe,
              stats["deflated_sharpe"], f">= {t.min_deflated_sharpe}"),
        Check("wfe", stats["wfe"] >= t.min_wfe, stats["wfe"], f">= {t.min_wfe}"),
        Check("wfe_hard", stats["wfe"] >= t.hard_reject_wfe,
              stats["wfe"], f">= {t.hard_reject_wfe} (hard)"),
        Check("max_drawdown", stats["max_drawdown"] <= t.max_drawdown,
              stats["max_drawdown"], f"<= {t.max_drawdown}"),
        Check("cost_share", stats["cost_share"] <= t.max_cost_share,
              stats["cost_share"], f"<= {t.max_cost_share}"),
        Check("param_cliff", worst_loss <= t.max_neighbor_sharpe_loss,
              round(worst_loss, 4), f"<= {t.max_neighbor_sharpe_loss}"),
    ]
    failed = [c for c in checks if not c.ok]
    return Verdict(not failed, "pass" if not failed else failed[0].name, checks)


def evaluate_g3(
    holdout_sharpe: float,
    walkforward_sharpe: float,
    regime_sharpes: dict,   # {"bull": x, "bear": y, "chop": z}
    t: G3Thresholds = G3Thresholds(),
) -> Verdict:
    """G3 one-shot holdout + regime robustness. The single-touch rule (one holdout
    evaluation per spec, ever) is enforced by the holdout_evaluations PRIMARY KEY
    in the DB layer — this function only computes the verdict."""
    if _bad(holdout_sharpe) or _bad(walkforward_sharpe) or walkforward_sharpe <= 0:
        return Verdict(False, "gate_error",
                       [Check("inputs", False, None, "finite, wf_sharpe > 0")])
    if set(regime_sharpes) != {"bull", "bear", "chop"} or any(
        _bad(v) for v in regime_sharpes.values()
    ):
        return Verdict(False, "gate_error",
                       [Check("regimes", False, None, "bull/bear/chop finite")])

    ratio = holdout_sharpe / walkforward_sharpe
    n_ok = sum(1 for v in regime_sharpes.values() if v >= 0)
    checks = [
        Check("holdout_sharpe", holdout_sharpe > t.min_holdout_sharpe,
              holdout_sharpe, f"> {t.min_holdout_sharpe}"),
        Check("holdout_vs_wf", ratio >= t.min_holdout_vs_wf_ratio,
              round(ratio, 4), f">= {t.min_holdout_vs_wf_ratio}"),
        Check("regimes_nonneg", n_ok >= t.min_nonnegative_regimes,
              n_ok, f">= {t.min_nonnegative_regimes} of 3"),
    ]
    failed = [c for c in checks if not c.ok]
    return Verdict(not failed, "pass" if not failed else failed[0].name, checks)
