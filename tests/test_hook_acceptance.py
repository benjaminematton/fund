"""Acceptance P1 'Hook' and 'Bracket orders': replayed trader turns through
the REAL PreToolUse gate -> deny in all five violation cases, zero order
rows; bracket leg follows the ticket's stop_price exactly."""

import asyncio
from pathlib import Path

import pytest

from agents.replay import load_recording, replay_turn
from agents.runtime import make_order_gate, make_order_recorder
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import _seed

REC = Path(__file__).with_name("recordings")


def _replay(fund_db, sim_clock, broker, name):
    return asyncio.run(replay_turn(
        load_recording(REC / name),
        pre_hooks=[make_order_gate(lambda: fund_db, sim_clock)],
        executor=make_executor(lambda: fund_db, sim_clock, broker),
        post_hooks=[make_order_recorder(lambda: fund_db, sim_clock)]))


DENY_CASES = [
    # (recording, seed kwargs or None, clock advance minutes, reason fragment)
    ("deny_no_ticket.jsonl", None, 0, "no gate ticket"),
    ("deny_expired.jsonl", {}, 31, "expired"),
    ("deny_over_qty.jsonl", {}, 0, "max_qty"),
    ("deny_wrong_symbol.jsonl", {}, 0, "symbol"),
    ("deny_wrong_stop.jsonl", {"stop_price": 168.0}, 0, "stop"),
]


@pytest.mark.parametrize("recording,seed_kwargs,advance,fragment", DENY_CASES)
def test_replayed_place_order_denied(fund_db, sim_clock, recording,
                                     seed_kwargs, advance, fragment):
    if seed_kwargs is not None:
        _seed(fund_db, **seed_kwargs)
    if advance:
        sim_clock.advance(minutes=advance)
    broker = FakeAlpaca({"NVDA": 180.00, "AAPL": 232.00})
    outcomes = _replay(fund_db, sim_clock, broker, recording)
    assert fragment in outcomes[-1]["denied"]
    assert broker.place_attempts == []
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0


def test_bracket_ticket_yields_bracket_order(fund_db, sim_clock):
    _seed(fund_db, stop_price=168.0)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14})
    outcomes = _replay(fund_db, sim_clock, broker, "bracket.jsonl")
    assert outcomes[-1]["result"]["status"] == "filled"
    placed = broker.place_attempts[0]
    assert placed["order_class"] == "bracket"
    assert placed["stop_loss"] == {"stop_price": 168.0}


def test_stopless_ticket_yields_plain_order(fund_db, sim_clock):
    _seed(fund_db)  # stop_price NULL
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14})
    outcomes = _replay(fund_db, sim_clock, broker, "happy_market.jsonl")
    assert outcomes[-1]["result"]["status"] == "filled"
    placed = broker.place_attempts[0]
    assert "stop_loss" not in placed and placed.get("order_class") is None
