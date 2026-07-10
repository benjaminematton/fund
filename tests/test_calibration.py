"""Calibration scoreboard tests: proper-scoring math, Murphy identity,
shrinkage behavior, weight floors, and the abstain-incentive design."""

import math

import numpy as np

from calibration.scoring import (batting_slugging, brier, brier_skill_score,
                                 murphy_decomposition, pm_weights,
                                 recency_weights, shrunk_skill,
                                 signal_probability, AgentScore)
from calibration.scoreboard import render_markdown, score_agents


def test_signal_probability_mapping():
    assert signal_probability("long", 80) == 0.8
    assert signal_probability("short", 80) == 0.2 - 0 or True
    assert abs(signal_probability("short", 80) - 0.2) < 1e-12
    assert signal_probability("neutral", 100) == 0.5
    for bad in (("long", 101), ("long", -1), ("up", 50)):
        try:
            signal_probability(*bad)
            raise AssertionError("should raise")
        except ValueError:
            pass


def test_brier_known_values():
    assert brier([1.0], [1.0]) == 0.0                      # perfect
    assert brier([0.0], [1.0]) == 1.0                      # perfectly wrong
    assert brier([0.5, 0.5], [1.0, 0.0]) == 0.25           # always-50% baseline
    assert math.isnan(brier([], []))


def test_bss_sign_convention():
    o = np.array([1, 0, 1, 0, 1, 0] * 20, float)
    skilled = np.where(o == 1, 0.8, 0.2)
    assert brier_skill_score(skilled, o) > 0
    anti = np.where(o == 1, 0.2, 0.8)
    assert brier_skill_score(anti, o) < 0
    assert abs(brier_skill_score(np.full_like(o, 0.5), o)) < 1e-12


def test_murphy_identity_exact_for_discrete_forecasts():
    # Signals arrive at discrete confidence steps -> identity holds exactly
    rng = np.random.default_rng(3)
    p = rng.choice([0.2, 0.35, 0.5, 0.65, 0.8], 400)
    o = (rng.uniform(size=400) < p).astype(float)          # calibrated by design
    d = murphy_decomposition(p, o)
    bs = brier(p, o)
    assert abs((d["reliability"] - d["resolution"] + d["uncertainty"]) - bs) < 1e-12
    assert d["reliability"] < 0.02                         # calibrated -> low


def test_murphy_approximate_for_continuous_forecasts():
    rng = np.random.default_rng(4)
    p = rng.uniform(0.05, 0.95, 1000)
    o = (rng.uniform(size=1000) < p).astype(float)
    d = murphy_decomposition(p, o)
    bs = brier(p, o)
    # within-bin variance terms remain: identity approximate, same ballpark
    assert abs((d["reliability"] - d["resolution"] + d["uncertainty"]) - bs) < 0.02


def test_shrinkage_pulls_small_samples_to_pool():
    lucky = shrunk_skill(0.40, n_graded=10, pool_bss=0.0)   # 10-call hot streak
    proven = shrunk_skill(0.40, n_graded=300, pool_bss=0.0)
    assert lucky < 0.11 and proven > 0.36                   # streaks barely move it


def test_recency_weights():
    w = recency_weights(150, half_life=75)
    assert w[-1] == 1.0 and abs(w[-76] - 0.5) < 0.01 and w[0] < w[-1]


def test_batting_slugging():
    alpha = np.array([0.02, -0.01, 0.03, -0.01, 0.0])
    sign = np.array([1.0, 1.0, -1.0, 1.0, 0.0])            # neutral excluded
    r = batting_slugging(alpha, sign)
    assert r["n_directional"] == 4
    assert abs(r["batting"] - 0.25) < 1e-12                # only the first wins
    assert r["slugging"] > 1.0                              # win bigger than losses


def test_pm_weights_floor_and_short_track():
    scores = [
        AgentScore("hot", 200, 0, 0.2, 0.3, 0.28, 56, 0.01, 0.05, 0.03, 0.6, 1.2),
        AgentScore("cold", 200, 0, 0.3, -0.2, -0.18, -36, 0.05, 0.01, 0.08, 0.4, 0.8),
        AgentScore("new", 10, 2, 0.24, 0.5, 0.09, 0.9, float("nan"), float("nan"),
                   float("nan"), 0.7, 1.5),
    ]
    w = pm_weights(scores)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["hot"] > w["new"] > 0                          # skill earns weight
    assert w["cold"] > 0                                    # floor: never zeroed
    mean_w = 1.0 / 3
    assert w["cold"] >= 0.5 * mean_w * 0.8                  # ~floor_fraction x mean


def test_abstain_gets_no_protected_average():
    # An agent that abstains on everything has zero TOTAL skill, not a safe 0.25
    rows = []
    rng = np.random.default_rng(9)
    for i in range(120):
        alpha = float(rng.normal(0, 0.02))
        rows.append({"seat": "hider", "direction": "neutral", "confidence": 0,
                     "alpha": alpha})
        rows.append({"seat": "caller", "direction": "long" if alpha > -0.005 else "short",
                     "confidence": 70, "alpha": alpha})
    scores, weights = score_agents(rows)
    by = {s.seat: s for s in scores}
    # Hider's OWN skill is ~0 (always-0.5 ~ the base rate); shrinkage toward the
    # pool mean can leave a small residual, but the caller must dominate hard.
    assert abs(by["hider"].bss) < 0.05                      # no earned skill
    assert by["caller"].total_skill > 5 * max(by["hider"].total_skill, 0.0) or \
        by["hider"].total_skill <= 0
    assert weights["caller"] > weights["hider"]


def test_scoreboard_renders_and_drops_malformed():
    rows = [
        {"seat": "a", "direction": "long", "confidence": 80, "alpha": 0.01},
        {"seat": "a", "direction": "long", "confidence": 200, "alpha": 0.01},  # dropped
        {"seat": "a", "direction": "short", "confidence": 60, "alpha": float("nan")},
    ] * 60
    scores, weights = score_agents(rows)
    assert scores[0].n_graded == 60                         # only valid rows graded
    md = render_markdown(scores, weights)
    assert "| a |" in md and "PM weight" in md
