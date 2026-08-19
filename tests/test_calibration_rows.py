"""Offline coverage for calibration/rows.py — resolutions -> grade_rows input.

Two layers meet here and both fail silently when they are wrong.

GRANULARITY. `resolutions.decision_id` is UNIQUE: one row per PM decision.
`calibration.scoreboard.grade_rows` wants one row per ANALYST. The bridge is
resolution -> decisions.id -> (run_date, ticker) -> every signal on that pair,
each analyst graded against the same realized alpha. Join it at decision
granularity instead and the scoreboard scores the PM under a header that says
analysts — and looks entirely correct doing it.

VOCABULARY. `signals.direction` is CHECKed to bullish/bearish/neutral (the
submission vocabulary). `calibration.scoring.signal_probability` accepts
long/short/neutral (the scoring vocabulary) and raises on anything else — and
`grade_rows` catches ValueError and CONTINUES. So an untranslated row does not
blow up; it vanishes, and the scoreboard renders near-empty without erroring.
Both vocabularies are correct in their own layer (specs/calibration.md §1 vs
state/schema.sql); this module is the specified crossing between them.
"""

from __future__ import annotations

import pytest

from calibration.rows import scoreboard_rows
from calibration.scoreboard import grade_rows
from state.db import connect

RUN_DATE = "2026-07-06"

# fixtures/golden-day.md's three research signals on NVDA.
GOLDEN_SIGNALS = [("fundamentals", "bullish", 72), ("technical", "bullish", 61),
                  ("news", "neutral", 40)]


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "fund.sqlite")


def _resolved(conn, *, run_date=RUN_DATE, ticker="NVDA", alpha=0.0504,
              signals=GOLDEN_SIGNALS):
    """A decision with its signals and its resolution — one graded day."""
    did = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES (?,?,?,96,'t','i',"
        "'executed',?)",
        (run_date, ticker, "buy", f"{run_date}T15:00:00+00:00")).lastrowid
    for agent, direction, confidence in signals:
        conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at) VALUES (?,?,?,?,?,'s',?)",
            (run_date, agent, ticker, direction, confidence,
             f"{run_date}T13:00:00+00:00"))
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?,5,?,?,0,?)",
        (did, 0.0614, alpha, f"{run_date}T20:35:00+00:00"))
    conn.commit()
    return did


# --- layer 1: granularity ---------------------------------------------------

def test_one_resolution_fans_out_to_every_analyst_who_called_the_ticker(conn):
    """Three analysts signalled NVDA that morning; the PM made one decision.
    The scoreboard grades three rows, not one, and each carries the same
    realized alpha — the outcome was the same for all of them."""
    _resolved(conn)

    rows = scoreboard_rows(conn)

    assert [r["seat"] for r in rows] == ["fundamentals", "news", "technical"]
    assert {r["alpha"] for r in rows} == {0.0504}
    assert [r["confidence"] for r in rows] == [72, 40, 61]


def test_an_unresolved_decision_contributes_no_rows(conn):
    """A decision still inside its horizon has no realized alpha. Its signals
    are not yet gradeable and must not arrive as zeros."""
    _resolved(conn)
    conn.execute("INSERT INTO decisions (run_date, ticker, action, qty,"
                 " thesis, invalidation, status, created_at) VALUES"
                 " ('2026-07-20','AMD','buy',10,'t','i','executed','x')")
    conn.execute("INSERT INTO signals (run_date, agent, ticker, direction,"
                 " confidence, summary, created_at) VALUES"
                 " ('2026-07-20','fundamentals','AMD','bullish',80,'s','x')")
    conn.commit()

    rows = scoreboard_rows(conn)

    assert all(r["seat"] != "AMD" for r in rows)
    assert len(rows) == 3


# --- layer 2: the vocabularies do not match ---------------------------------

def test_directions_are_translated_into_the_scoring_vocabulary(conn):
    """bullish -> long, bearish -> short, neutral -> neutral. The DDL's
    vocabulary reaches signal_probability as an unknown direction otherwise."""
    _resolved(conn, signals=[("fundamentals", "bullish", 72),
                             ("technical", "bearish", 61),
                             ("news", "neutral", 40)])

    got = {r["seat"]: r["direction"] for r in scoreboard_rows(conn)}

    assert got == {"fundamentals": "long", "technical": "short",
                   "news": "neutral"}


def test_grade_rows_keeps_every_row_this_module_emits(conn):
    """The one that bites. grade_rows wraps signal_probability in
    `except (ValueError, KeyError): continue`, so an untranslated row is
    dropped in silence rather than raised. In == out is the only evidence the
    crossing worked.
    """
    _resolved(conn, signals=[("fundamentals", "bullish", 72),
                             ("technical", "bearish", 61),
                             ("news", "neutral", 40)])

    rows = scoreboard_rows(conn)
    graded = grade_rows(rows)

    assert len(graded) == len(rows) == 3, \
        "a dropped row means the direction vocabulary did not cross"
    assert [g["p"] for g in graded] == [0.72, 0.5, 1 - 0.61]
