import asyncio
import json
from pathlib import Path

from agents.replay import load_recording, replay_turn
from agents.runtime import make_order_gate, make_order_recorder
from state.models import StrategySpec
from state.specs import insert_strategy_spec
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import TID, _seed, order

CRITIC_RECORDING = Path(__file__).with_name("recordings") / "critic_g1_clear.jsonl"

# Same shape tests/test_state_specs.py and tests/test_critic_g1_job.py pin.
# spec_id is content-addressed (fundbt.hashing.spec_id) and does not depend on
# created_at, so this dict's hash is the literal spec_id the recording names.
SPEC = dict(
    family="F1", seat="quant",
    hypothesis="Reversal pays for absorbing forced selling.",
    mechanism_class="liquidity_provision",
    universe={"index": "Russell 1000", "pit_constituents": True, "filters": []},
    liquidity_bucket="mega_large",
    signal_rule={"entry": "5d return below -1.5 sigma"},
    param_ranges={"sigma": [1.0, 2.5, 0.25]},
    search_budget=24, holding_period_d=5, rebalance="daily",
    expected_turnover=42.0, exit_rule="close at 5 trading days",
    invalidation="12m low-turnover spread negative for two quarters.",
    capacity_usd=4000000.0,
    predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
    llm_in_loop=0)


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


def test_replay_critic_g1_turn_writes_the_verdict_through_the_real_handler(
        fund_db, sim_clock):
    """#169 bullet 4: a recorded Critic G1 turn, replayed through the real
    hooks/executor/DB, reaches strategy_critiques ONLY through
    submit_spec_critique — never a raw INSERT the handler would refuse
    (strategy-contracts.md §3.4). No order gate/recorder: the Critic's tools
    never touch an order, so pre_hooks/post_hooks are empty rather than the
    exec-seat hooks _turn() wires above."""
    sid = insert_strategy_spec(fund_db, StrategySpec(**SPEC),
                               "2026-08-25T18:00:00+00:00")
    decisions = load_recording(CRITIC_RECORDING)
    assert decisions[1]["args"]["spec_id"] == sid  # recording targets this spec

    outcomes = asyncio.run(replay_turn(
        decisions, pre_hooks=[],
        executor=make_executor(lambda: fund_db, sim_clock, broker=None,
                               seat="critic", charter_version="critic-v2",
                               model_id="claude-sonnet-5"),
        post_hooks=[]))

    assert outcomes[0]["result"]["ok"] is True
    assert outcomes[1]["result"] == {"ok": True}
    row = fund_db.execute(
        "SELECT spec_id, verdict, seat FROM strategy_critiques").fetchone()
    assert dict(row) == {"spec_id": sid, "verdict": "clear", "seat": "critic"}
