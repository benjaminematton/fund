"""Weekly scoreboard: resolutions -> per-agent scores -> PM weights -> markdown.

Input rows come from the resolutions table (contracts §2): each graded signal
with its realized alpha vs SPY at horizon. Ops runs this weekly and posts to
#pnl; the PM's charter reads the weights. No LLM imports.
"""

from __future__ import annotations

import numpy as np

from .scoring import (AgentScore, batting_slugging, brier, brier_skill_score,
                      murphy_decomposition, pm_weights, recency_weights,
                      shrunk_skill, signal_probability, weighted_brier)


def grade_rows(rows: list[dict]) -> list[dict]:
    """rows: [{seat, direction, confidence, alpha}] in chronological order.
    Adds p (probability of 'beats SPY'), o (outcome), sign. Malformed rows are
    dropped and counted — never guessed (invariant 7)."""
    graded = []
    for r in rows:
        try:
            p = signal_probability(r["direction"], r["confidence"])
        except (ValueError, KeyError):
            continue
        alpha = r.get("alpha")
        if alpha is None or not np.isfinite(alpha):
            continue
        graded.append({
            **r,
            "p": p,
            "o": 1.0 if alpha > 0 else 0.0,
            "sign": {"long": 1.0, "short": -1.0, "neutral": 0.0}[r["direction"]],
        })
    return graded


def score_agents(rows: list[dict]) -> tuple[list[AgentScore], dict[str, float]]:
    """Chronological resolution rows -> (scores, pm_weights)."""
    graded = grade_rows(rows)
    seats = sorted({r["seat"] for r in graded})
    pool_o = np.array([r["o"] for r in graded], float)
    base_rate = float(pool_o.mean()) if pool_o.size else 0.5

    # pool BSS as the shrinkage target: the average agent, not zero, so a new
    # seat neither inherits nor dilutes the desk's established skill
    pool_p = np.array([r["p"] for r in graded], float)
    pool_bss = brier_skill_score(pool_p, pool_o, base_rate) if pool_o.size else 0.0
    if not np.isfinite(pool_bss):
        pool_bss = 0.0

    scores = []
    for seat in seats:
        rs = [r for r in graded if r["seat"] == seat]
        p = np.array([r["p"] for r in rs], float)
        o = np.array([r["o"] for r in rs], float)
        alpha = np.array([r["alpha"] for r in rs], float)
        sign = np.array([r["sign"] for r in rs], float)
        w = recency_weights(len(rs))

        bs_ref = brier(np.full_like(o, base_rate), o)
        wb = weighted_brier(p, o, w)
        wb_ref = weighted_brier(np.full_like(o, base_rate), o, w)
        bss = 1.0 - wb / wb_ref if wb_ref and wb_ref > 0 else float("nan")
        dec = murphy_decomposition(p, o)
        bat = batting_slugging(alpha, sign)
        shr = shrunk_skill(bss, len(rs), pool_bss)

        scores.append(AgentScore(
            seat=seat,
            n_graded=len(rs),
            n_abstain=sum(1 for r in rs if r["direction"] == "neutral"),
            brier=brier(p, o),
            bss=float(bss) if np.isfinite(bss) else float("nan"),
            bss_shrunk=shr,
            total_skill=shr * len(rs),
            reliability=dec["reliability"],
            resolution=dec["resolution"],
            ece=dec["ece"],
            batting=bat["batting"],
            slugging=bat["slugging"],
        ))
    return scores, pm_weights(scores)


def render_markdown(scores: list[AgentScore], weights: dict[str, float]) -> str:
    """The #pnl scoreboard post. Sorted by total skill — volume of genuine
    signal, not protected averages."""
    lines = [
        "*Weekly calibration scoreboard* (event: beats SPY at horizon; "
        "Brier is proper — shading confidence is self-defeating)",
        "",
        "| seat | graded | abstain% | brier | BSS | shrunk | total skill | "
        "reliab. | resol. | ECE | batting | slug | PM weight |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    def f(x, d=3):
        return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) \
            else f"{x:.{d}f}"
    for s in sorted(scores, key=lambda s: -s.total_skill):
        ab = 100.0 * s.n_abstain / s.n_graded if s.n_graded else 0.0
        lines.append(
            f"| {s.seat} | {s.n_graded} | {ab:.0f}% | {f(s.brier)} | {f(s.bss)} "
            f"| {f(s.bss_shrunk)} | {f(s.total_skill, 1)} | {f(s.reliability)} "
            f"| {f(s.resolution)} | {f(s.ece)} | {f(s.batting, 2)} "
            f"| {f(s.slugging, 2)} | {f(weights.get(s.seat), 3)} |"
        )
    lines += [
        "",
        "_Reading guide: BSS > 0 beats the base rate; reliability low = "
        "calibrated, resolution high = discriminating; batting < 0.5 is fine "
        "if slugging compensates; weights shrink toward equal until ~150+ "
        "graded calls._",
    ]
    return "\n".join(lines)
