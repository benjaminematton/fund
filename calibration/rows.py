"""Resolutions -> the rows `scoreboard.grade_rows` grades.

Two layers cross here, and getting either wrong is silent.

GRANULARITY. `resolutions.decision_id` is UNIQUE — one row per PM decision.
grade_rows wants one row per ANALYST. The join fans a resolution back out
through its decision's `(run_date, ticker)` to every signal submitted on that
pair, each analyst graded against the same realized alpha. Grade at decision
granularity instead and the board scores the PM beneath a header that says
analysts, with nothing anywhere reporting a problem.

VOCABULARY. `signals.direction` is CHECKed to bullish/bearish/neutral;
`scoring.signal_probability` speaks long/short/neutral and raises on anything
else — which `grade_rows` catches and skips. An untranslated row therefore does
not fail loudly, it disappears, and the board renders near-empty. Both
vocabularies are correct in their own layer (specs/calibration.md §1 is the
scoring one, state/schema.sql the submission one); this module is the crossing.

Pure stdlib over SQLite — no LLM imports, no clock (invariant 3).
"""

from __future__ import annotations

import sqlite3

# The crossing. A KeyError here means schema.sql's CHECK constraint gained a
# direction this module was never taught — a code bug, and one that must stop
# the job rather than quietly shrink the sample.
_SCORING = {"bullish": "long", "bearish": "short", "neutral": "neutral"}

# Chronological, which is what recency weighting assumes. Ordered by agent
# within a day so the sequence is stable across runs — signals submitted on the
# same run_date have no meaningful order between them.
_ROWS = """
SELECT s.agent AS seat, s.direction, s.confidence, r.alpha_vs_spy AS alpha
  FROM resolutions r
  JOIN decisions d ON d.id = r.decision_id
  JOIN signals   s ON s.run_date = d.run_date AND s.ticker = d.ticker
 ORDER BY s.run_date, s.agent
"""


def scoreboard_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every graded analyst signal, chronological, in grade_rows' shape.

    A decision still inside its horizon has no resolution row, so its signals
    are absent rather than present with a zero alpha — the same invariant-4
    posture the producer takes.
    """
    return [{"seat": r["seat"], "direction": _SCORING[r["direction"]],
             "confidence": r["confidence"], "alpha": r["alpha"]}
            for r in conn.execute(_ROWS)]
