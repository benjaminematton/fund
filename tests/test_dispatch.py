"""orchestrator/dispatch.py — Lane B dispatcher + sweep (resident-seats R1, #228).
No LLM: the wake is a callable. Alerts are events(kind='alert'); there is no
alerts table."""
import json
from datetime import datetime, timezone

import pytest

from orchestrator import dispatch
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state import worklist

START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return SimClock(START)


def _enqueue(conn, clock, **kw):
    args = dict(kind="spec_review", producer="orchestrator", seat="critic",
                subject="spec_abc", expires_at="2026-07-06T20:00:00+00:00",
                now_iso=iso(clock.now()))
    args.update(kw)
    return worklist.enqueue(conn, **args)


def _status(conn, wid):
    return conn.execute("SELECT status FROM worklist WHERE work_id=?", (wid,)).fetchone()["status"]


def _alerts(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]


def _costs(conn):
    return conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"]


# --- sweep ------------------------------------------------------------------

def test_sweep_reaps_an_over_lease_claim_to_failed_with_one_alert(fund_db, clock):
    wid = _enqueue(fund_db, clock)
    worklist.claim_next(fund_db, now_iso=iso(clock.now()),
                        lease_until_iso="2026-07-06T15:35:00+00:00")
    clock.advance(minutes=4)
    assert dispatch.sweep(fund_db, iso(clock.now())) == dispatch.SweepResult((), ())
    clock.advance(minutes=1)                       # 15:35: lease is up
    res = dispatch.sweep(fund_db, iso(clock.now()))
    assert res.reaped == (wid,) and res.expired == ()
    assert _status(fund_db, wid) == "failed"
    codes = [a["code"] for a in _alerts(fund_db)]
    assert codes == ["work_lease_expired"]
    assert wid in _alerts(fund_db)[0]["text"]


def test_sweep_treats_a_claimed_row_with_no_lease_as_over_lease(fund_db, clock):
    """Defensive: a claim always carries its lease (Task 3's atomic extra=),
    but a NULL lease must reap, never sit claimed forever."""
    wid = _enqueue(fund_db, clock)
    fund_db.execute("UPDATE worklist SET status='claimed' WHERE work_id=?", (wid,))
    fund_db.commit()
    assert dispatch.sweep(fund_db, iso(clock.now())).reaped == (wid,)


def test_sweep_expires_a_past_due_open_row_with_one_alert(fund_db, clock):
    wid = _enqueue(fund_db, clock, expires_at="2026-07-06T15:40:00+00:00")
    clock.advance(minutes=10)
    res = dispatch.sweep(fund_db, iso(clock.now()))
    assert res.expired == (wid,) and res.reaped == ()
    assert _status(fund_db, wid) == "expired"
    assert [a["code"] for a in _alerts(fund_db)] == ["work_expired"]


def test_sweep_is_idempotent(fund_db, clock):
    _enqueue(fund_db, clock, expires_at="2026-07-06T15:40:00+00:00")
    clock.advance(minutes=10)
    dispatch.sweep(fund_db, iso(clock.now()))
    assert dispatch.sweep(fund_db, iso(clock.now())) == dispatch.SweepResult((), ())
    assert len(_alerts(fund_db)) == 1


# --- dispatch_once ----------------------------------------------------------

def test_dispatch_once_runs_the_wake_with_the_row_and_marks_done(fund_db, clock):
    wid = _enqueue(fund_db, clock, payload={"k": "v"})
    seen = []
    res = dispatch.dispatch_once(fund_db, clock, seen.append)
    assert res.handled == wid
    assert seen[0]["work_id"] == wid and seen[0]["kind"] == "spec_review"
    assert seen[0]["payload"] == '{"k": "v"}'
    assert _status(fund_db, wid) == "done"
    assert _alerts(fund_db) == []


def test_dispatch_once_claims_with_the_lease_from_the_clock(fund_db, clock):
    wid = _enqueue(fund_db, clock)
    leases = []
    dispatch.dispatch_once(fund_db, clock, lambda row: leases.append(row["claim_expires_at"]),
                           lease_s=120)
    assert leases == ["2026-07-06T15:32:00+00:00"]


def test_dispatch_once_wake_raise_fails_the_row_alerts_and_does_not_reraise(fund_db, clock):
    wid = _enqueue(fund_db, clock)

    def boom(row):
        raise RuntimeError("no consumer registered")

    res = dispatch.dispatch_once(fund_db, clock, boom)
    assert res.handled == wid
    assert _status(fund_db, wid) == "failed"
    alerts = _alerts(fund_db)
    assert [a["code"] for a in alerts] == ["work_failed"]
    assert "RuntimeError: no consumer registered" in alerts[0]["text"]
    assert wid in alerts[0]["text"]
    # never requeued: the next cycle is idle
    assert dispatch.dispatch_once(fund_db, clock, boom).handled is None


def test_dispatch_once_refuses_a_disallowed_pair_without_running_the_wake(fund_db, clock):
    """The allow-list is enforced at claim too, not only at enqueue: a row
    written by hand (or by a future producer whose table entry was removed)
    never reaches a wake."""
    wid = _enqueue(fund_db, clock)
    fund_db.execute("UPDATE worklist SET producer='slack_listener' WHERE work_id=?", (wid,))
    fund_db.commit()
    called = []
    res = dispatch.dispatch_once(fund_db, clock, called.append)
    assert res.handled == wid and called == []
    assert _status(fund_db, wid) == "failed"
    assert [a["code"] for a in _alerts(fund_db)] == ["work_disallowed"]


def test_dispatch_once_sweeps_before_claiming(fund_db, clock):
    stale = _enqueue(fund_db, clock, subject="stale", expires_at="2026-07-06T15:31:00+00:00")
    clock.advance(minutes=5)
    fresh = _enqueue(fund_db, clock, subject="fresh")
    seen = []
    res = dispatch.dispatch_once(fund_db, clock, seen.append)
    assert res.swept.expired == (stale,)
    assert res.handled == fresh and seen[0]["work_id"] == fresh


def test_dispatch_once_idle_touches_nothing(fund_db, clock):
    res = dispatch.dispatch_once(fund_db, clock, lambda row: pytest.fail("no row to wake"))
    assert res == dispatch.DispatchResult(None, dispatch.SweepResult((), ()))
    assert _alerts(fund_db) == [] and _costs(fund_db) == 0


# --- run_dispatcher ---------------------------------------------------------

class _CountingSlack(FakeSlack):
    """FakeSlack that counts post attempts and can fail transiently once."""

    def __init__(self, fail_first: int = 0):
        super().__init__()
        self.attempts = 0
        self.fail_first = fail_first

    def post(self, channel, text, thread_ts=None, blocks=None, username=None,
             icon_emoji=None):
        self.attempts += 1
        if self.attempts <= self.fail_first:
            raise RuntimeError("slack hiccup")        # transient, not PermanentPostError
        return super().post(channel, text, thread_ts, blocks, username, icon_emoji)


def test_run_dispatcher_sleeps_only_when_idle(fund_db, clock):
    _enqueue(fund_db, clock, subject="a")
    _enqueue(fund_db, clock, subject="b")
    naps = []

    def _sleep(s):
        naps.append(s)
        clock.advance(seconds=int(s))

    def boom(row):
        raise RuntimeError("x")

    cycles = dispatch.run_dispatcher(
        fund_db, FakeSlack(), clock, boom, sleep=_sleep, poll_s=5.0, max_cycles=4)
    assert cycles == 4
    assert naps == [5.0, 5.0]                      # two busy cycles, two idle


def test_run_dispatcher_leaves_a_foreign_unposted_event_alone_while_idle(fund_db, clock):
    """Idle cycles do not drain: a row run_day queued (not ours) is still
    unposted after several idle cycles. Distinguishes "drain only after our
    writes" from "drain every cycle"."""
    from slackkit.outbox import append_event
    append_event(fund_db, "alert", {"code": "not_ours", "text": "run_day's"},
                 iso(clock.now()))
    slack = _CountingSlack()
    dispatch.run_dispatcher(fund_db, slack, clock, lambda row: None,
                            sleep=lambda s: clock.advance(seconds=int(s)),
                            max_cycles=3)
    assert slack.attempts == 0
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"] == 1


def test_run_dispatcher_retries_a_transiently_failed_drain_on_the_next_idle_cycle(fund_db, clock):
    """A wake fails, the alert's first post raises transiently, the queue goes
    idle — the alert must still land on a later cycle, not wait for the next
    write. Pins drain()'s retry contract through the loop."""
    _enqueue(fund_db, clock)
    slack = _CountingSlack(fail_first=1)

    def boom(row):
        raise RuntimeError("x")

    dispatch.run_dispatcher(fund_db, slack, clock, boom,
                            sleep=lambda s: clock.advance(seconds=int(s)),
                            max_cycles=3)
    assert slack.attempts == 2                     # cycle 1 failed, cycle 2 delivered
    assert len(slack.posts["#risk"]) == 1
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"] == 0


def test_idle_queue_costs_nothing(tmp_path):
    """Design R1 acceptance: a completed sim-day plus an empty queue -> zero
    wakes, zero new cost rows, zero new alerts across several cycles."""
    from tests.test_sim_day import golden_day
    sim = golden_day(tmp_path)
    costs_before = _costs(sim.conn)
    alerts_before = len(_alerts(sim.conn))
    clock = SimClock(START)
    wakes = []
    dispatch.run_dispatcher(sim.conn, sim.slack, clock, wakes.append,
                            sleep=lambda s: clock.advance(seconds=int(s)),
                            max_cycles=5)
    assert wakes == []
    assert _costs(sim.conn) == costs_before
    assert len(_alerts(sim.conn)) == alerts_before


def test_dispatcher_killed_mid_claim_is_reaped_on_the_next_start(tmp_path, clock):
    """Kill mid-claim (design R1 acceptance): process 1 claims and dies; process
    2 starts after the lease, sweeps the row to failed with an alert, and the
    row is neither lost nor run twice."""
    from state.db import connect
    path = tmp_path / "fund.sqlite"
    p1 = connect(path)
    wid = _enqueue(p1, clock)
    worklist.claim_next(p1, now_iso=iso(clock.now()),
                        lease_until_iso="2026-07-06T15:35:00+00:00")
    p1.close()                                     # died holding the claim

    clock.advance(minutes=6)
    p2 = connect(path)
    wakes = []
    res = dispatch.dispatch_once(p2, clock, wakes.append)
    assert res.swept.reaped == (wid,) and res.handled is None and wakes == []
    assert _status(p2, wid) == "failed"
    assert [a["code"] for a in _alerts(p2)] == ["work_lease_expired"]
    p2.close()
