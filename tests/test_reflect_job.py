"""Offline tests for the nightly reflection job's decision seams.

scripts/reflect_day.py is a composition root like close_pnl.py and
resolve_day.py, so main() is never called here — it builds real clients. What
is pinned is what it SELECTS and what it does when a turn misbehaves, because
every turn it runs costs real money.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orchestrator.clock import SimClock
from orchestrator.reflect import store_reflection
from slackkit.fake import FakeSlack
from state.db import connect

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reflect_day.py"

# 2026-08-25 16:35 ET == 20:35 UTC (EDT) — the scheduled fire.
NIGHTLY = datetime(2026, 8, 25, 20, 35, tzinfo=timezone.utc)
TODAY = "2026-08-25"


def _load():
    spec = importlib.util.spec_from_file_location("reflect_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reflect_day = _load()


def _resolved(conn, *, ticker="NVDA", resolved_at, run_date="2026-08-18",
              charter_version="v6", reflection=None):
    # created_at must be distinct from, and realistically EARLIER than,
    # resolved_at: a decision is made, then resolved a horizon later. Using
    # resolved_at for both (the earlier version of this fixture) meant a
    # mutation aiming due_reflections' predicate at d.created_at instead of
    # r.resolved_at survived every test in this file — in production that
    # mutation selects nothing, every night, forever, because created_at
    # falls outside the lookback window.
    #
    # The gap is derived from REFLECT_LOOKBACK_DAYS (N6), not hardcoded: a
    # fixed "8 days" gap only exceeds the window while the window is 7 or
    # fewer nights. This lane has already widened the window once (1 -> 7);
    # the next widening (say, to 14) would put a hardcoded 8-day gap INSIDE
    # the new window, and the created_at/resolved_at mutation above would
    # then silently pass every test in this file again.
    created_at = (datetime.fromisoformat(resolved_at)
                 - timedelta(days=reflect_day.REFLECT_LOOKBACK_DAYS + 1)
                 ).isoformat()
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, charter_version, created_at) VALUES"
        " (?,?,'buy',96,'t','i','executed',?,?)",
        (run_date, ticker, charter_version, created_at))
    did = cur.lastrowid
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, reflection, resolved_at)"
        " VALUES (?, 5, 0.0614, 0.0504, 0, ?, ?)",
        (did, reflection, resolved_at))
    conn.commit()
    return did


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


def test_resolutions_within_the_lookback_window_are_due(db):
    """resolve_day resolves at horizon, so a decision resolved tonight was
    MADE about five sessions ago — filtering on the decision's run_date would
    reflect on nothing, forever, so the window is on resolved_at.

    The window now spans REFLECT_LOOKBACK_DAYS nights, not just tonight: a
    missed reflection (a failed turn, a systemd timeout, a missing token) is
    retried on later nights instead of being lost the moment the calendar
    rolls over. Anything older than the lookback still ages out — an
    unbounded backfill would buy a turn for every historical decision on the
    first fire."""
    fresh = _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")
    recent = _resolved(db, ticker="AMD", resolved_at="2026-08-20T20:35:05+00:00")
    stale = _resolved(db, ticker="MSFT", resolved_at="2026-08-17T12:00:00+00:00")

    due = reflect_day.due_reflections(db, TODAY)

    assert {d["decision_id"] for d in due} == {fresh, recent}
    assert stale not in {d["decision_id"] for d in due}


def test_an_unknown_charter_version_is_still_due(db):
    """schema.sql defaults charter_version to 'unknown', and an unparseable
    charter header (_parse_charter_version) returns exactly that string. The
    due-selection excludes only the orchestrator's own 'none' rows — tightening
    it to something like LIKE 'v%' would pass every other test today and
    silently stop reflecting on these decisions."""
    did = _resolved(db, resolved_at="2026-08-25T20:35:05+00:00",
                    charter_version="unknown")

    assert [d["decision_id"] for d in reflect_day.due_reflections(db, TODAY)] \
        == [did]


def test_an_already_reflected_decision_is_not_due_again(db):
    """The paid turn is what the pre-check saves. store_reflection's guard
    only stops the write, after the money is gone."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00",
              reflection="already done")

    assert reflect_day.due_reflections(db, TODAY) == []


def test_a_machine_written_hold_is_not_reflected_on(db):
    """A pm_timeout row (charter_version 'none') was written by the
    orchestrator, not by a seat. There is no reasoning to reflect on, and the
    turn would be paid for nothing."""
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00",
              charter_version="none")

    assert reflect_day.due_reflections(db, TODAY) == []


def test_a_held_decision_is_still_reflected_on(db):
    """resolve_due deliberately resolves held and rejected calls so the
    scoreboard is not a sample selected by the PM's own convictions. Dropping
    them here would reintroduce exactly that bias one stage later."""
    db.execute("UPDATE decisions SET status = 'held'")
    did = _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")

    assert [d["decision_id"] for d in reflect_day.due_reflections(db, TODAY)] \
        == [did]


def test_the_frame_reaches_the_turn(db):
    """The seat is handed computed facts, not a decision id to look up — it
    has no read tools."""
    did = _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")
    seen = []

    reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                lambda job: seen.append(job))

    assert len(seen) == 1
    assert seen[0]["decision_id"] == did
    assert "NVDA" in seen[0]["frame"] and "+6.14%" in seen[0]["frame"]


def test_run_turn_threads_the_bound_id_to_make_turn(db, monkeypatch):
    """The last leg of the id-binding chain: _make_run_turn's `run_turn`
    must thread job['decision_id'] into run_day.make_turn as
    expected_decision_id — without this leg, the binding pinned at the
    handler and tool-wrapper levels would never actually reach a live
    seat's session. Faking make_turn rather than calling it for real: a
    real one opens a live SDK session, which this offline suite must never
    do. Also pins change A: the prompt carries the frame, never the id."""
    seen = {}

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen["kwargs"] = kwargs
        seen["prompt"] = prompt
        return lambda: None

    monkeypatch.setattr(reflect_day.run_day, "make_turn", _fake_make_turn)

    run_turn = reflect_day._make_run_turn(
        "reflect", {}, ":memory:", SimClock(NIGHTLY), db, TODAY)
    run_turn({"decision_id": 99, "ticker": "NVDA", "frame": "the frame"})

    assert seen["kwargs"]["expected_decision_id"] == 99
    assert "the frame" in seen["prompt"]
    assert "99" not in seen["prompt"]      # change A: the id leaves the prompt


def test_a_vanished_resolution_is_logged_not_silently_skipped(
        db, capsys, monkeypatch):
    """A `due` row whose resolution vanishes between selection and the frame
    lookup used to disappear silently: counted in neither bucket, with
    nothing explaining why reflected + failed < due. Log it by decision, and
    count it `failed` — the same treatment as the post-turn vanished-row
    path below, and the only way reflected + failed can reconcile with what
    was actually taken (N5)."""
    did = _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    monkeypatch.setattr(reflect_day, "reflection_frame",
                        lambda conn, decision_id: None)
    ran = []

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         lambda job: ran.append(job))

    assert ran == []
    assert counts == {"reflected": 0, "failed": 1}
    out = capsys.readouterr().out
    assert f"skip decision {did}" in out and "NVDA" in out


def test_reflected_plus_failed_reconciles_with_taken(db, monkeypatch):
    """N5: a row vanishing BEFORE the turn (reflection_frame returns None)
    and one vanishing AFTER the turn (the post-check re-read finds no row)
    must count the same way, or reflected + failed silently undercounts how
    many decisions were actually taken tonight."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")
    monkeypatch.setattr(reflect_day, "reflection_frame",
                        lambda conn, decision_id: None)

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         lambda job: None)

    taken = 2
    assert counts["reflected"] + counts["failed"] == taken


def test_a_turn_that_writes_nothing_is_counted_failed_and_alerted(db):
    """The likeliest real failure, and the one that matters: run_day.make_turn's
    own `run()` already catches every exception and returns normally, so a
    seat that never calls submit_reflection — or calls it, gets
    {"ok": False} back, and gives up — never raises here. Counting on the
    absence of an exception would silently report that turn as reflected.
    One dead turn must also not swallow the rest of the night — otherwise a
    single failure silently shrinks the whole calibration sample."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")
    ran = []

    def _turn(job):
        ran.append(job["ticker"])
        if job["ticker"] == "AMD":
            store_reflection(db, job["decision_id"], job["frame"], "noted")
        # NVDA: returns normally without ever writing a reflection.

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         _turn)

    assert ran == ["NVDA", "AMD"]
    assert counts == {"reflected": 1, "failed": 1}
    texts = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'")]
    assert len(texts) == 1
    assert "reflect_turn_wrote_nothing" in texts[0]
    assert "NVDA" in texts[0]
    assert "tomorrow" not in texts[0]      # a missed turn is retried, not lost


def test_several_turns_that_write_nothing_produce_one_rollup_alert(db,
                                                                    capsys):
    """A fully broken night must not turn into one Slack message per
    resolved decision — the repo's established pattern for a per-entity
    failure is a single rollup naming every affected entity
    (run_day.alert_missing_price_history). Stdout still gets a line per
    decision (journald, not Slack); Slack gets exactly one alert naming
    all of them."""
    tickers = ["NVDA", "AMD", "MSFT"]
    dids = [_resolved(db, ticker=t, resolved_at="2026-08-25T20:35:05+00:00")
            for t in tickers]

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         lambda job: None)   # writes nothing

    assert counts == {"reflected": 0, "failed": 3}
    texts = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'")]
    assert len(texts) == 1
    for did, ticker in zip(dids, tickers):
        assert str(did) in texts[0] and ticker in texts[0]
    out = capsys.readouterr().out
    for ticker in tickers:
        assert f"({ticker}) wrote nothing" in out    # per-decision, stdout only


def test_a_resolution_that_vanishes_after_the_turn_is_counted_failed_not_crashed(
        db):
    """If the resolutions row vanishes between the turn returning and the
    post-check re-reading it, fetchone() returns None — subscripting that
    used to raise TypeError, which escaped reflect_and_log and stopped the
    rest of the night's decisions from ever running. Treat a missing row the
    same as the vanished-frame case: not written, logged, counted failed,
    keep going. Simulated by having the turn itself delete the row — the
    observable effect at the post-check is identical to a concurrent
    process deleting it underneath a slower turn."""
    did1 = _resolved(db, ticker="NVDA",
                     resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")
    ran = []

    def _turn(job):
        ran.append(job["ticker"])
        if job["decision_id"] == did1:
            db.execute("DELETE FROM resolutions WHERE decision_id = ?",
                      (did1,))
            db.commit()
        else:
            store_reflection(db, job["decision_id"], job["frame"], "noted")

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         _turn)

    assert ran == ["NVDA", "AMD"]          # the whole night still ran; no crash
    assert counts == {"reflected": 1, "failed": 1}


def test_a_turn_that_raises_is_counted_failed_and_alerted(db):
    """Defense in depth: run_turn's own try/except is not reachable through
    run_day.make_turn's real `run()` today (it already swallows everything),
    but it costs nothing to keep and a future contract change could make it
    reachable again."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")
    ran = []

    def _turn(job):
        if job["ticker"] == "NVDA":
            raise TimeoutError("session never connected")
        ran.append(job["ticker"])
        store_reflection(db, job["decision_id"], job["frame"], "noted")

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         _turn)

    assert ran == ["AMD"]
    assert counts == {"reflected": 1, "failed": 1}
    texts = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'")]
    assert len(texts) == 1
    assert "reflect_turn_failed" in texts[0]
    assert "tomorrow" not in texts[0]


def test_the_night_is_capped_and_a_silent_cap_is_alerted(db, capsys):
    """MAX_TURNS_PER_NIGHT bounds the night's spend. A silent cap would read
    as 'covered everything' when it did not — the operator must be told both
    how many were due and how many were actually taken, on BOTH channels:
    the alert (Slack) and a log line (stdout/journald). Removing just the
    log line while keeping the alert survives every other assertion here, so
    it is pinned explicitly."""
    n = reflect_day.MAX_TURNS_PER_NIGHT + 3
    for i in range(n):
        _resolved(db, ticker=f"T{i}", resolved_at="2026-08-25T20:35:05+00:00")
    ran = []

    def _turn(job):
        ran.append(job["decision_id"])
        store_reflection(db, job["decision_id"], job["frame"], "noted")

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         _turn)

    assert len(ran) == reflect_day.MAX_TURNS_PER_NIGHT
    assert counts == {"reflected": reflect_day.MAX_TURNS_PER_NIGHT, "failed": 0}
    texts = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'")]
    capped = [t for t in texts if "reflect_backlog_capped" in t]
    assert len(capped) == 1
    assert str(n) in capped[0]
    assert str(reflect_day.MAX_TURNS_PER_NIGHT) in capped[0]

    out = capsys.readouterr().out
    assert "backlog capped" in out
    assert str(n) in out and str(reflect_day.MAX_TURNS_PER_NIGHT) in out


def test_a_night_with_nothing_due_runs_no_turn_and_says_so(db, capsys):
    """A day with no resolutions is normal. Spending nothing is correct."""
    ran = []

    counts = reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                         lambda job: ran.append(job))

    assert ran == []
    assert counts == {"reflected": 0, "failed": 0}
    assert "reflect_day:" in capsys.readouterr().out


def test_the_job_needs_a_slack_token_unlike_its_sibling(db):
    """resolve_day deliberately requires no Slack token. This job does, and
    the difference is not an oversight: a failed turn appends an alert, and
    audit_day's undrained-events check has no date bound — so an alert this
    job cannot drain reddens tomorrow's audit."""
    assert "SLACK_BOT_TOKEN" in reflect_day.REQUIRED_ENV
    assert "ANTHROPIC_API_KEY" in reflect_day.REQUIRED_ENV


def test_an_alert_from_a_failed_turn_is_drained(db):
    _resolved(db, resolved_at="2026-08-25T20:35:05+00:00")
    slack = FakeSlack()

    def _boom(job):
        raise TimeoutError("nope")

    reflect_day.reflect_and_log(db, slack, SimClock(NIGHTLY), _boom)

    assert db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL"
    ).fetchone()["c"] == 0


def test_the_wrote_nothing_rollup_survives_a_later_frame_error(
        db, monkeypatch):
    """N1: the wrote_nothing rollup used to be appended only AFTER the whole
    loop finished. If a later decision's reflection_frame call raised, the
    loop aborted before that append ever ran — not merely left undrained
    (finally's drain already covers that), but never QUEUED at all, so
    Slack never learned the earlier decision wrote nothing. Same failure
    class the finally-drain was added for: one turn's outcome must not
    depend on every later turn succeeding too."""
    did1 = _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")

    real_frame = reflect_day.reflection_frame

    def _flaky_frame(conn, decision_id):
        row = conn.execute("SELECT ticker FROM decisions WHERE id = ?",
                           (decision_id,)).fetchone()
        if row["ticker"] == "AMD":
            raise sqlite3.OperationalError("database is locked")
        return real_frame(conn, decision_id)

    monkeypatch.setattr(reflect_day, "reflection_frame", _flaky_frame)

    with pytest.raises(sqlite3.OperationalError):
        reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                    lambda job: None)   # NVDA writes nothing

    texts = [r["payload"] for r in db.execute(
        "SELECT payload FROM events WHERE kind = 'alert'"
        " AND posted_at IS NOT NULL")]
    assert any("reflect_turn_wrote_nothing" in t and "NVDA" in t
              for t in texts), (
        f"expected a drained reflect_turn_wrote_nothing alert naming NVDA"
        f" (decision {did1}); got: {texts}")


def test_a_db_error_computing_a_later_frame_still_drains_before_propagating(
        db, monkeypatch):
    """reflection_frame sits outside run_turn's own try/except, and so does
    run_day._alert. Before the fix, an exception from either — a
    sqlite3.OperationalError, say — skipped drain() entirely, leaving the
    FIRST decision's alert stuck at posted_at IS NULL. That check has no date
    bound, so it would redden every audit until something else happened to
    drain the outbox."""
    _resolved(db, ticker="NVDA", resolved_at="2026-08-25T20:35:05+00:00")
    _resolved(db, ticker="AMD", resolved_at="2026-08-25T20:35:06+00:00")

    def _turn(job):
        assert job["ticker"] == "NVDA", "AMD's turn must not run — frame blew up first"
        raise TimeoutError("boom")

    real_frame = reflect_day.reflection_frame

    def _flaky_frame(conn, decision_id):
        row = conn.execute("SELECT ticker FROM decisions WHERE id = ?",
                           (decision_id,)).fetchone()
        if row["ticker"] == "AMD":
            raise sqlite3.OperationalError("database is locked")
        return real_frame(conn, decision_id)

    monkeypatch.setattr(reflect_day, "reflection_frame", _flaky_frame)

    with pytest.raises(sqlite3.OperationalError):
        reflect_day.reflect_and_log(db, FakeSlack(), SimClock(NIGHTLY), _turn)

    assert db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL"
    ).fetchone()["c"] == 0
