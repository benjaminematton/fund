"""Proper scoring of analyst signals. Pure numpy + stdlib — no LLM imports.

Event convention (fixed, symmetric): "ticker's return beats SPY over the
signal's horizon". A signal (direction, confidence 0-100) maps to one binary
probability of that event:

    long  conf c  ->  p = c/100
    short conf c  ->  p = 1 - c/100
    neutral       ->  p = 0.5   (abstains ARE scored — see below)

Why abstains score 0.5: skipping them lets agents protect their average by
abstaining on hard calls (arXiv:2106.11248). Scoring 0.5 makes abstaining
exactly "saying 50%", and TOTAL skill (not average) rewards genuine volume.

Scores are strictly proper (Brier): an agent's expected score is optimized by
reporting its true belief — confidence shading is self-defeating by design.
Evidence base: strategy-research-report companion; scoring defaults from the
Good Judgment Project / Murphy (1973) / empirical-Bayes literature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Defaults (see specs/calibration.md for provenance; change by human commit)
SHRINK_PSEUDO_COUNTS = 30      # prior strength: new agents start at pool average
MIN_GRADED_FOR_WEIGHT = 50     # soft floor before a non-default weight
RECENCY_HALF_LIFE = 75         # graded calls (~ 3-4 months at daily signals)
WEIGHT_FLOOR_FRACTION = 0.5    # no agent below 0.5x mean weight
MAX_BINS = 10                  # equal-mass calibration bins
MIN_PER_BIN = 20


def signal_probability(direction: str, confidence: float) -> float:
    """(direction, confidence 0-100) -> p(beats benchmark). Malformed -> ValueError."""
    if not (0.0 <= confidence <= 100.0):
        raise ValueError("confidence_out_of_range")
    c = confidence / 100.0
    if direction == "long":
        return c
    if direction == "short":
        return 1.0 - c
    if direction == "neutral":
        return 0.5
    raise ValueError("unknown_direction")


def brier(p: np.ndarray, o: np.ndarray) -> float:
    """Mean squared error of probabilities vs outcomes in {0,1}. Lower = better."""
    p, o = np.asarray(p, float), np.asarray(o, float)
    if p.size == 0:
        return float("nan")
    return float(np.mean((p - o) ** 2))


def brier_skill_score(p: np.ndarray, o: np.ndarray, base_rate: float | None = None) -> float:
    """BSS = 1 - BS/BS_ref, ref = always-forecast-the-base-rate. >0 means skill."""
    o = np.asarray(o, float)
    if o.size == 0:
        return float("nan")
    br = float(o.mean()) if base_rate is None else base_rate
    bs_ref = brier(np.full_like(o, br), o)
    if bs_ref <= 0:
        return float("nan")  # degenerate outcomes (all same) — no skill measurable
    return 1.0 - brier(np.asarray(p, float), o) / bs_ref


def _equal_mass_bins(p: np.ndarray, n_bins: int) -> list[np.ndarray]:
    order = np.argsort(p, kind="stable")
    return [idx for idx in np.array_split(order, n_bins) if idx.size > 0]


def murphy_decomposition(p: np.ndarray, o: np.ndarray) -> dict:
    """BS = reliability - resolution + uncertainty (Murphy 1973).

    Report all three: good Brier can be luck (regime); the decomposition says
    whether the agent is calibrated (low reliability), discriminating (high
    resolution), or neither.

    Binning: by unique forecast value when there are few (identity then holds
    EXACTLY — signals at 5-point confidence steps land here); equal-mass bins
    otherwise (identity approximate: within-bin variance terms remain — the
    known bias Siegert 2017 corrects; fine for a scoreboard).
    """
    p, o = np.asarray(p, float), np.asarray(o, float)
    n = p.size
    if n < MIN_PER_BIN:
        return {"reliability": float("nan"), "resolution": float("nan"),
                "uncertainty": float("nan"), "ece": float("nan"), "n_bins": 0}
    n_bins = max(1, min(MAX_BINS, n // MIN_PER_BIN))
    obar = o.mean()
    rel = res = ece = 0.0
    uniq = np.unique(p)
    if uniq.size <= n_bins:
        bins = [np.flatnonzero(p == v) for v in uniq]      # exact decomposition
    else:
        bins = _equal_mass_bins(p, n_bins)
    for idx in bins:
        pk, ok = p[idx].mean(), o[idx].mean()
        w = idx.size / n
        rel += w * (pk - ok) ** 2
        res += w * (ok - obar) ** 2
        ece += w * abs(pk - ok)
    return {"reliability": float(rel), "resolution": float(res),
            "uncertainty": float(obar * (1 - obar)), "ece": float(ece),
            "n_bins": len(bins)}


def batting_slugging(alpha: np.ndarray, direction_sign: np.ndarray) -> dict:
    """Payoff asymmetry — Brier ignores magnitude. alpha = realized return vs SPY
    at horizon; direction_sign = +1 long / -1 short / 0 neutral (excluded).
    Batting = P(directional call profitable); slugging = avg win / avg loss.
    A 40% batting with 2x slugging adds alpha; the scoreboard shows both."""
    alpha, sign = np.asarray(alpha, float), np.asarray(direction_sign, float)
    mask = sign != 0
    if mask.sum() == 0:
        return {"batting": float("nan"), "slugging": float("nan"), "n_directional": 0}
    pnl = alpha[mask] * sign[mask]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    batting = float(wins.size / pnl.size)
    slugging = float(wins.mean() / abs(losses.mean())) if wins.size and losses.size \
        else float("inf") if wins.size else 0.0
    return {"batting": batting, "slugging": slugging, "n_directional": int(pnl.size)}


def shrunk_skill(observed_bss: float, n_graded: int,
                 pool_bss: float = 0.0, k: int = SHRINK_PSEUDO_COUNTS) -> float:
    """Empirical-Bayes shrink toward the pool: w = n/(n+k). A lucky 20-call
    streak moves the weight a little; 300 honest calls move it a lot."""
    if not np.isfinite(observed_bss) or n_graded <= 0:
        return pool_bss
    w = n_graded / (n_graded + k)
    return float(w * observed_bss + (1 - w) * pool_bss)


def recency_weights(n: int, half_life: int = RECENCY_HALF_LIFE) -> np.ndarray:
    """Exponential decay over graded calls, oldest first. GJP-style recency."""
    if n <= 0:
        return np.array([])
    age = np.arange(n - 1, -1, -1, dtype=float)   # last call has age 0
    return 0.5 ** (age / half_life)


def weighted_brier(p: np.ndarray, o: np.ndarray, w: np.ndarray) -> float:
    p, o, w = np.asarray(p, float), np.asarray(o, float), np.asarray(w, float)
    if p.size == 0 or w.sum() <= 0:
        return float("nan")
    return float(np.sum(w * (p - o) ** 2) / w.sum())


@dataclass
class AgentScore:
    seat: str
    n_graded: int
    n_abstain: int
    brier: float
    bss: float                  # recency-weighted, vs pool base rate
    bss_shrunk: float           # feeds the PM weight
    total_skill: float          # bss_shrunk * n_graded — rewards volume, not hiding
    reliability: float
    resolution: float
    ece: float
    batting: float
    slugging: float


def pm_weights(scores: list[AgentScore],
               floor_fraction: float = WEIGHT_FLOOR_FRACTION,
               min_graded: int = MIN_GRADED_FOR_WEIGHT) -> dict[str, float]:
    """Signal weights for the PM. weight ~ max(bss_shrunk, 0), floored at
    floor_fraction x mean so no agent collapses to zero on a cold streak
    (equal weights are hard to beat; weights drift from equality only as
    evidence accumulates). Agents under min_graded calls get exactly the mean
    weight — a track record too short to trust is treated as no track record.
    Weights sum to 1. Empty input -> {} (caller treats as equal-weight)."""
    if not scores:
        return {}
    raw = {}
    for s in scores:
        if s.n_graded < min_graded or not np.isfinite(s.bss_shrunk):
            raw[s.seat] = None          # placeholder: gets mean of the rest
        else:
            raw[s.seat] = max(s.bss_shrunk, 0.0)
    known = [v for v in raw.values() if v is not None]
    mean_known = float(np.mean(known)) if known else 1.0
    filled = {k: (mean_known if v is None else v) for k, v in raw.items()}
    floor = floor_fraction * float(np.mean(list(filled.values()))) if filled else 0.0
    floored = {k: max(v, floor) for k, v in filled.items()}
    total = sum(floored.values())
    if total <= 0:
        return {k: 1.0 / len(floored) for k in floored}
    return {k: v / total for k, v in floored.items()}
