"""Offline tests for submit_reflection — the write seam between a reflection
turn and resolutions.reflection.

The handler computes the frame itself rather than accepting one: the seat is
asked for an interpretation, and a seat that could supply its own facts could
supply convenient ones. It also makes the one-call-per-decision contract that
store_reflection's guard depends on structural rather than a caller promise.
"""

from __future__ import annotations

import pytest

from agents.tools.fund_server import SEAT_CAPS, handle_submit_reflection
from state.db import connect

NOW = "2026-08-25T20:35:00+00:00"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def decision_id(db):
    """One resolved NVDA buy — golden-day T+5 vector."""
    cur = db.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06','NVDA','buy',96,'t','i','executed',?)", (NOW,))
    did = cur.lastrowid
    db.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at)"
        " VALUES (?, 5, 0.0614, 0.0504, 0, ?)", (did, NOW))
    db.commit()
    return did


def _submit(db, **over):
    kwargs = dict(seat="reflect", args={}, now_iso=NOW)
    kwargs.update(over)
    return handle_submit_reflection(db, **kwargs)


def test_the_reflect_seat_carries_the_cap():
    assert "submit_reflection" in SEAT_CAPS["reflect"]


def test_a_reflection_is_stored_with_the_facts_first(db, decision_id):
    r = _submit(db, args={"decision_id": decision_id,
                          "prose": "Sized right, held too long."})
    assert r["ok"] is True
    stored = db.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored.startswith("NVDA")          # the frame
    assert "+6.14%" in stored                 # the computed facts
    assert stored.endswith("Sized right, held too long.")


def test_another_seat_may_not_write_a_reflection(db, decision_id):
    r = _submit(db, seat="pm", args={"decision_id": decision_id,
                                     "prose": "mine now"})
    assert r["ok"] is False
    assert "pm" in r["error"]
    assert db.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"] is None


def test_an_unresolved_decision_is_refused_rather_than_reflected(db):
    """reflection_frame returns None for a decision with no resolution row.
    Reflecting on nothing would store a seat's guess as a record."""
    cur = db.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06','AMD','hold',0,'t','i','held',?)", (NOW,))
    db.commit()
    r = _submit(db, args={"decision_id": cur.lastrowid, "prose": "x"})
    assert r["ok"] is False
    assert "not resolved" in r["error"]


def test_a_second_reflection_is_refused_with_the_first_intact(db, decision_id):
    """store_reflection is first-write-wins; the handler must report that as
    an error rather than a success, or a resumed job logs work it did not do."""
    _submit(db, args={"decision_id": decision_id, "prose": "first"})
    r = _submit(db, args={"decision_id": decision_id, "prose": "second"})
    assert r["ok"] is False
    assert "already" in r["error"]
    stored = db.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored.endswith("first")


def test_a_malformed_call_is_refused_without_writing(db, decision_id):
    assert _submit(db, args={"prose": "no id"})["ok"] is False
    assert _submit(db, args={"decision_id": decision_id})["ok"] is False
