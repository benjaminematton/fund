"""The factual half of a reflection is computed, never narrated.

orchestrator/resolve.py writes the outcome and leaves `reflection` NULL on
purpose — "it is an agent write, and this job holds no LLM." This is that
follow-on, and it inverts the usual shape: the seat receives the facts rather
than recalling them, and writes only interpretation on top.

Why bother: a re-analysis of Reflexion's own logs found 32% of environments
developed frozen reflective memory in which zero of 121 stored reflections
named the correct target. The mechanism is an information vacuum — given a
coarse outcome and no detail, a model emits a plausible, causally wrong
diagnosis that then persists BECAUSE it reads as credible. Removing the vacuum
is cheaper than asking the model to be careful.

Numbers are fixtures/golden-day.md's T+5 vector, which orchestrator/resolve.py
already passes against: NVDA $180.14 -> $191.20 against SPY +1.1% is +6.14%
realized and +5.04pp alpha.
"""

from __future__ import annotations

import pytest

from orchestrator.reflect import reflection_frame, store_reflection
from state.db import connect

RUN = "2026-07-06"
REALIZED = (191.20 - 180.14) / 180.14      # +6.14%
ALPHA = REALIZED - 0.011                   # +5.04pp


@pytest.fixture
def resolved(tmp_path):
    """One decided, executed, resolved NVDA call with two analyst signals."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " (?,'NVDA','buy',66,'license overhang is priced in',"
        " 'close below 170','executed',?)", (RUN, f"{RUN}T13:05:00Z"))
    decision_id = conn.execute(
        "SELECT id FROM decisions WHERE ticker='NVDA'").fetchone()["id"]
    for agent, direction, confidence in (("analyst", "bullish", 72),
                                         ("news", "bullish", 60)):
        conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at) VALUES (?,?,?,?,?,?,?)",
            (RUN, agent, "NVDA", direction, confidence, "s",
             f"{RUN}T12:00:00Z"))
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?,5,?,?,0,?)",
        (decision_id, REALIZED, ALPHA, "2026-07-13T20:35:00Z"))
    conn.commit()
    return conn, decision_id


def test_frame_states_the_call_the_confidence_and_the_outcome(resolved):
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)

    assert "NVDA" in frame
    assert "buy" in frame
    assert "bullish" in frame            # what was predicted
    assert "72" in frame                 # at what confidence
    assert "+6.14%" in frame             # what happened
    assert "+5.04pp" in frame            # versus the benchmark


def test_frame_names_every_seat_that_signalled(resolved):
    """Per-seat, because the PM's call rests on more than one analyst and a
    reflection that hides which seat was right teaches nobody anything."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    assert "analyst" in frame
    assert "news" in frame
    assert "60" in frame


def test_frame_makes_no_invalidation_claim(resolved):
    """resolve.py writes `invalidated` as a constant 0 because neither signal
    the fund has — the broker's stop leg, Ops' watch on the free-text condition
    — is readable from that job. Rendering it would assert "not invalidated" as
    fact on every row, which is the confident-but-wrong input this frame exists
    to eliminate. The field re-enters when something actually writes it."""
    conn, decision_id = resolved
    assert "invalidat" not in reflection_frame(conn, decision_id).lower()


def test_a_losing_call_is_stated_as_plainly_as_a_winning_one(resolved):
    conn, decision_id = resolved
    conn.execute("UPDATE resolutions SET realized_return = ?,"
                 " alpha_vs_spy = ?", (-0.0231, -0.0341))
    conn.commit()
    frame = reflection_frame(conn, decision_id)
    assert "-2.31%" in frame
    assert "-3.41pp" in frame


def test_an_unresolved_decision_has_no_frame(resolved):
    """No row, never a zero (invariant 4). An unmeasured call and a call that
    exactly matched SPY mean opposite things."""
    conn, decision_id = resolved
    conn.execute("DELETE FROM resolutions")
    conn.commit()
    assert reflection_frame(conn, decision_id) is None


def test_a_hold_with_no_signals_still_frames_its_outcome(tmp_path):
    """A defaulted hold is still a graded call — the scoring event is the
    ticker's move, whether or not the fund took the position."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " (?,'AMD','hold',0,'no decision by the deadline (pm_timeout)','n/a',"
        " 'held',?)", (RUN, f"{RUN}T13:05:00Z"))
    decision_id = conn.execute(
        "SELECT id FROM decisions WHERE ticker='AMD'").fetchone()["id"]
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?,5,0.01,0.002,0,?)",
        (decision_id, "2026-07-13T20:35:00Z"))
    conn.commit()

    frame = reflection_frame(conn, decision_id)
    assert "AMD" in frame
    assert "hold" in frame
    assert "no signals" in frame          # stated, not silently omitted


def test_store_reflection_keeps_the_frame_when_the_seat_writes_nothing(
        resolved):
    """A silent seat leaves the facts behind rather than a blank. The seat
    cannot write the factual half — that is a property of how the row is
    assembled, not an instruction the model is asked to follow."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    store_reflection(conn, decision_id, frame, prose="")

    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored == frame


def test_store_reflection_puts_the_facts_before_the_interpretation(resolved):
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    store_reflection(conn, decision_id, frame,
                     prose="Half-size cost ~1pp but was correct process.")

    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored.startswith(frame)
    assert stored.endswith("Half-size cost ~1pp but was correct process.")


def test_a_second_store_leaves_the_first_reflection_untouched(resolved):
    """The stage body is re-run from the top on crash-resume, so a decision
    already reflected on is reached again. Writing unconditionally would
    replace a real reflection with whatever the re-run produced; first write
    wins instead. The guard saves the text, not the turn — the seat has already
    answered by the time `prose` exists."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    store_reflection(conn, decision_id, frame, prose="Correct process.")
    store_reflection(conn, decision_id, frame, prose="Different second take.")

    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored.endswith("Correct process.")
    assert "Different second take." not in stored


def test_a_frame_only_first_write_locks_out_the_seats_prose(resolved):
    """Pinning the shape the guard demands of its callers: frame and prose go
    in together, one call per decision. Storing the frame ahead of the turn and
    the prose after it would leave the row permanently factual-only, so no
    caller may split the two halves across separate writes."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    store_reflection(conn, decision_id, frame)
    store_reflection(conn, decision_id, frame, prose="Arrives too late.")

    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored == frame


def test_a_reflection_committed_on_another_connection_is_not_clobbered(
        resolved, tmp_path):
    """The guard is on the column, not on a memory of what this process did.
    Many sessions work this DB at once, so the write that has to be stopped is
    one this connection never saw — hence a genuinely separate connect() to the
    same file rather than a second UPDATE on the fixture's own."""
    conn, decision_id = resolved
    other = connect(tmp_path / "fund.sqlite")
    other.execute("UPDATE resolutions SET reflection = 'from another session'"
                  " WHERE decision_id = ?", (decision_id,))
    other.commit()

    assert not store_reflection(conn, decision_id,
                                reflection_frame(conn, decision_id),
                                prose="Would have overwritten it.")
    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored == "from another session"


def test_store_reflection_reports_whether_this_call_wrote_the_row(resolved):
    """The caller cannot see the guard fire otherwise: both outcomes leave a
    populated row, and only the return value separates "I reflected on this"
    from "someone already had"."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)

    assert store_reflection(conn, decision_id, frame, prose="First.") is True
    assert store_reflection(conn, decision_id, frame, prose="Second.") is False


def test_a_decision_with_no_resolution_row_is_reported_as_unwritten(resolved):
    """Zero rows updated used to mean only this. Never a silent success: a
    decision resolve.py has not graded yet gets no reflection and must not be
    logged as though it did, or it leaves the audit trail without ever
    appearing to fail (CLAUDE.md: never swallow)."""
    conn, decision_id = resolved
    frame = reflection_frame(conn, decision_id)
    conn.execute("DELETE FROM resolutions")
    conn.commit()

    assert store_reflection(conn, decision_id, frame, prose="Nowhere to go.") \
        is False
