import asyncio
import json

from agents.replay import replay_turn
from agents.runtime import make_order_gate, make_order_recorder
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import TID, _seed, order


def _turn(fund_db, sim_clock, broker, decisions):
    return asyncio.run(replay_turn(
        decisions,
        pre_hooks=[make_order_gate(lambda: fund_db, sim_clock)],
        executor=make_executor(lambda: fund_db, sim_clock, broker),
        post_hooks=[make_order_recorder(lambda: fund_db, sim_clock)]))


def test_replay_happy_turn_executes_real_tools(fund_db, sim_clock):
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.00}, {"NVDA": 180.14}, mode="instant")
    outcomes = _turn(fund_db, sim_clock, broker, [
        {"seat": "exec", "tool": "mcp__fund__list_open_tickets", "args": {}},
        {"seat": "exec", "tool": "mcp__alpaca__place_stock_order",
         "args": order()},
    ])
    assert outcomes[0]["result"][0]["id"] == TID       # real DB read
    # place result is the real MCP wire shape: JSON string, order under `data`
    assert json.loads(outcomes[1]["result"])["data"]["status"] == "filled"
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
    assert broker.place_attempts and broker.place_attempts[0]["qty"] == 67


def test_replay_denied_decision_never_reaches_executor(fund_db, sim_clock):
    broker = FakeAlpaca({"NVDA": 180.00})
    outcomes = _turn(fund_db, sim_clock, broker, [
        {"seat": "exec", "tool": "mcp__alpaca__place_stock_order",
         "args": order()},  # no ticket in DB
    ])
    assert "no gate ticket" in outcomes[0]["denied"]
    assert broker.place_attempts == []
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0
