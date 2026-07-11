import asyncio
from pathlib import Path

import pytest

from agents.replay import load_recording, replay_turn
from agents.runtime import make_order_gate, make_order_recorder
from orchestrator.stages import run_execution_stage
from slackkit.fake import FakeSlack
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import TID, _seed

RECORDING = Path(__file__).with_name("recordings") / "happy_market.jsonl"


def _make_turn(fund_db, sim_clock, broker, *, post_extra=()):
    decisions = load_recording(RECORDING)

    def run_turn():
        asyncio.run(replay_turn(
            decisions,
            pre_hooks=[make_order_gate(lambda: fund_db, sim_clock)],
            executor=make_executor(lambda: fund_db, sim_clock, broker),
            post_hooks=[make_order_recorder(lambda: fund_db, sim_clock),
                        *post_extra]))

    return run_turn


def _fire(fund_db, sim_clock, broker, slack, turn=None):
    return run_execution_stage(
        fund_db, run_date="2026-07-06", clock=sim_clock,
        run_trader_turn=turn or _make_turn(fund_db, sim_clock, broker),
        slack=slack)


def test_sim_ticket_to_order_to_fill_message(fund_db, sim_clock):
    """Acceptance P1: seed open ticket -> fire stage -> exactly one orders
    row, client_order_id == ticket.id, one #trade-log fill message."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14})
    slack = FakeSlack()
    assert _fire(fund_db, sim_clock, broker, slack) == "done"
    rows = fund_db.execute("SELECT * FROM orders").fetchall()
    assert len(rows) == 1 and rows[0]["client_order_id"] == TID
    msgs = slack.posts["#trade-log"]
    assert len(msgs) == 1
    assert msgs[0]["text"] == "🧾 NVDA buy 67@180.14 (ticket a3f90000)"
    cp = fund_db.execute("SELECT status FROM checkpoints WHERE stage='execution'").fetchone()
    assert cp["status"] == "done"
    # invariant 6: a 'done' checkpoint must never coexist with an unposted
    # (undrained) outbox event
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"] == 0


def test_idempotency_fire_twice_same_ticket(fund_db, sim_clock):
    """Acceptance P1: fire the execution stage twice with the same ticket ->
    still exactly one order row, one Slack message."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14})
    slack = FakeSlack()
    _fire(fund_db, sim_clock, broker, slack)
    _fire(fund_db, sim_clock, broker, slack)
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
    assert len(slack.posts["#trade-log"]) == 1
    assert len(broker.place_attempts) == 1


def test_crash_after_consumption_then_restart(fund_db, sim_clock):
    """Acceptance P1: kill the stage after ticket consumption, restart ->
    checkpoint + consumed ticket prevent re-execution (one attempt total)."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14})
    slack = FakeSlack()

    class Kill(Exception):
        pass

    async def killer(input_data, tool_use_id, context):
        if str(input_data.get("tool_name", "")).startswith("mcp__alpaca__place_"):
            raise Kill()  # dies right after the order recorder consumed the ticket
        return {}

    with pytest.raises(Kill):
        _fire(fund_db, sim_clock, broker, slack,
              turn=_make_turn(fund_db, sim_clock, broker, post_extra=(killer,)))
    # killed mid-stage: order + consumption landed, checkpoint stuck 'running',
    # fill event not yet projected
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
    cp = fund_db.execute("SELECT status FROM checkpoints WHERE stage='execution'").fetchone()
    assert cp["status"] == "running"
    assert "#trade-log" not in slack.posts  # nothing projected before restart

    # restart: resume re-runs the idempotent body — no open tickets remain,
    # so no new placement; the pending fill event drains exactly once
    assert _fire(fund_db, sim_clock, broker, slack) == "done"
    assert len(broker.place_attempts) == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
    assert len(slack.posts["#trade-log"]) == 1


def test_expiry_simclock_past_expires_at(fund_db, sim_clock):
    """Acceptance P1: SimClock past expires_at -> ticket expired, order
    attempt denied, zero orders."""
    _seed(fund_db)  # expires 16:00 UTC
    sim_clock.advance(minutes=31)  # 16:01 UTC
    broker = FakeAlpaca({"NVDA": 180.00})
    slack = FakeSlack()
    assert _fire(fund_db, sim_clock, broker, slack) == "done"
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID,)).fetchone()["status"] == "expired"
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0
    assert broker.place_attempts == []
    assert "#trade-log" not in slack.posts
