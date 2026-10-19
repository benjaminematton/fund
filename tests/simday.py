"""The sim-day composition: one simulated trading day through the REAL gate,
tools, hooks, DB, outbox and fill-poll, against RECORDED LLM decisions — the
LLM is the only thing replaced (acceptance §0). No network, no API keys.

Shared by tests/test_sim_day.py (the assertions), tests/test_audit_day.py,
tests/test_score_day.py, tests/test_dispatch.py, scripts/replay_day.py and
scripts/sim_report.py. SCENARIOS is the one list of day shapes: every entry
is pinned by a test and renderable by `make sim-report SCENARIO=<name>`.

Time is injected (SimClock) and so is the poll sleep: `_sleep` advances the
clock and ticks the broker, which is how an order placed as 'accepted'
becomes 'filled' during the reconciliation stage rather than by fiat."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agents.replay import load_recording, replay_turn
from agents.runtime import make_order_gate, make_order_recorder, record_cost
from orchestrator.clock import SimClock, et_run_date, iso
from orchestrator.daily import StageCtx, allowed_actions, run_day
from slackkit.fake import FakeSlack
from state.db import connect
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca

RECORDINGS = Path(__file__).with_name("recordings")

# 11:30 ET on the golden day. run_date is the ET calendar date (schema.sql),
# derived here via et_run_date — never hardcoded into a stage.
START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)

# The exec recording's client_order_id; invariant 5 makes it the ticket id too,
# so the sim's id_factory must mint exactly this.
TID = "a3f90000-0000-4000-8000-000000000001"
# Second ticket id for the two-order day (mvf_exec_two.jsonl's MSFT leg).
TID2 = "b4f90000-0000-4000-8000-000000000002"

PRICES = {"NVDA": 180.00, "MSFT": 505.00}      # fixtures/golden-day.md
FILL_PRICES = {"NVDA": 180.14}                 # fixture fill

# Stands in for ResultMessage.total_cost_usd, which replay has no source for.
# One row per turn, exactly as the live runtime records it (agents/runtime.py).
SIM_TURN_COST_USD = 0.01

STAGES = {"pre_gate", "research", "decision", "gate", "execution",
          "reconciliation", "close"}


def _nvda(**over) -> dict:
    """fixtures/golden-day.md's market state: equity 100k, cash 30k, NVDA 180,
    vol 42%, corr 0.55, tech book 27,840 (AAPL) + 20,200 (MSFT) = 48,040,
    2 positions, day −0.4%. Sizes to the golden 66."""
    d = dict(ticker="NVDA", side="buy", equity=100000.0, cash=30000.0,
             price=180.0, vol_60d=0.42, avg_corr=0.55, held_qty=0,
             position_count=2, sector="tech", sector_value=48040.0,
             daily_pnl_pct=-0.004)
    d.update(over)
    return d


def _msft(**over) -> dict:
    """Same book, the held MSFT line (40 sh @ 505) — sell-able, so it survives
    pre-gate and reaches the PM."""
    return _nvda(ticker="MSFT", price=505.0, held_qty=40, **over)


@dataclass
class SimResult:
    conn: sqlite3.Connection
    slack: FakeSlack
    broker: FakeAlpaca
    clock: SimClock
    run_date: str
    turns: dict[str, int]
    journals: Path
    outcomes: dict[str, list]      # stage -> the turn's replayed tool results

    def close(self) -> None:
        self.conn.close()


def sim_day(tmp_path, *, market: dict,
            analyst_recs=("mvf_analyst.jsonl",),
            news_recs=("mvf_news.jsonl",),
            pm_recs=("mvf_pm.jsonl",),
            exec_recs=("mvf_exec.jsonl",),
            feed_break: dict | None = None,
            slack=None,
            id_factory=None,
            broker_mode: str = "fill") -> SimResult:
    """One simulated trading day. `feed_break` applies market-input overrides
    AFTER the decision turn — a feed that goes bad between the PM's submit and
    the gate's enforcement pass, which is the only way a ticker can be live at
    pre-gate and garbage at the gate. `slack` defaults to a fresh FakeSlack;
    pass a different port (e.g. one whose post() raises) to exercise the
    outbox's dead-letter path. `id_factory` defaults to the fixed single-TID
    factory every existing sim relies on; pass a multi-id factory for a day
    with more than one ticket minted."""
    conn = connect(tmp_path / "fund.sqlite")
    clock = SimClock(START)
    run_date = et_run_date(clock.now())
    broker = FakeAlpaca(PRICES, FILL_PRICES, mode=broker_mode)
    slack = slack if slack is not None else FakeSlack()
    turns = {"research": 0, "decision": 0, "execution": 0}
    outcomes: dict[str, list] = {}
    journals = tmp_path / "journals"

    def _sleep(seconds: float) -> None:
        """The fill-poll's injected wait: time passes and the broker works."""
        clock.advance(seconds=int(seconds))
        broker.tick()

    def _snapshot() -> dict:
        """The sim's binding of run_day's injected stage-brief provider, built
        from the SAME `market` fixture the gate later enforces against — and
        read LIVE, so a `feed_break` applied after the decision turn cannot
        retroactively change what the PM was shown."""
        return {"cash": next(iter(market.values()))["cash"],
                "positions": {t: i["held_qty"] for t, i in market.items()
                              if i["held_qty"]},
                "allowed_actions": allowed_actions(market)}

    def _turn(stage: str, seat: str, files, after=None):
        decisions = [d for f in files for d in load_recording(RECORDINGS / f)]

        def run() -> None:
            turns[stage] += 1
            # EXTEND, never assign: a stage can now hold more than one seat's
            # turn (research runs both analysts), and assignment silently drops
            # the earlier seat's tool calls — which is exactly the evidence a
            # two-analyst sim exists to check.
            outcomes.setdefault(stage, []).extend(asyncio.run(replay_turn(
                decisions,
                pre_hooks=[make_order_gate(lambda: conn, clock)],
                executor=make_executor(lambda: conn, clock, broker, seat=seat,
                                       snapshot=_snapshot,
                                       journals_root=journals),
                post_hooks=[make_order_recorder(lambda: conn, clock)])))
            record_cost(conn, run_date, seat, f"sim-{seat}", SIM_TURN_COST_USD,
                        iso(clock.now()))
            if after is not None:
                after()

        return run

    def break_feed() -> None:
        for ticker, over in (feed_break or {}).items():
            market[ticker].update(over)

    analyst_turn = _turn("research", "analyst", analyst_recs)
    news_turn = _turn("research", "news", news_recs)

    def research_turn() -> None:
        """Both analysts, sequentially — design §3 staggers starts for rate
        limits. Each _turn isolates its own failure, so a seat that raises
        leaves the other's signal and its own neutral/0 default."""
        analyst_turn()
        news_turn()

    ctx = StageCtx(
        conn=conn, run_date=run_date, clock=clock, slack=slack,
        research_seats=("analyst", "news"),
        market_inputs=market,
        run_turn={"research": research_turn,
                  "decision": _turn("decision", "pm", pm_recs, after=break_feed)},
        id_factory=id_factory or (lambda: TID), journals_root=journals)
    run_day(ctx, execution_turn=_turn("execution", "exec", exec_recs),
            broker=broker, sleep=_sleep)
    return SimResult(conn=conn, slack=slack, broker=broker, clock=clock,
                     run_date=run_date, turns=turns, journals=journals,
                     outcomes=outcomes)


def golden_day(tmp_path) -> SimResult:
    """The golden-day sim: SCENARIOS["golden"], by its shared name."""
    return run_scenario("golden", tmp_path)


def _id_sequence(ids):
    """Deterministic multi-ticket id_factory (review Fix 3): yields `ids` in
    call order. A day that mints more tickets than provided is a bug the
    sim should crash on, not paper over with a repeated id — StopIteration
    is the loud failure that gives us."""
    it = iter(ids)
    return lambda: next(it)


# --- scenarios ------------------------------------------------------------------

# name -> factory of sim_day keyword arguments. Factories, not dicts: sim_day
# mutates `market` under feed_break, and _id_sequence is a one-shot iterator.
SCENARIOS = {
    "golden": lambda: dict(market={"NVDA": _nvda()}),
    "all_hold": lambda: dict(market={"NVDA": _nvda()},
                             pm_recs=("mvf_pm_hold.jsonl",)),
    "mixed": lambda: dict(market={"NVDA": _nvda(), "MSFT": _msft()},
                          pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_hold.jsonl")),
    "gate_reject": lambda: dict(market={"NVDA": _nvda()},
                                feed_break={"NVDA": {"vol_60d": float("nan")}}),
    "circuit_breaker": lambda: dict(market={"NVDA": _nvda()},
                                    feed_break={"NVDA": {"daily_pnl_pct": -0.05}}),
    "two_orders": lambda: dict(market={"NVDA": _nvda(), "MSFT": _msft()},
                               pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_sell.jsonl"),
                               exec_recs=("mvf_exec_two.jsonl",),
                               id_factory=_id_sequence([TID, TID2])),
    "pm_brief": lambda: dict(market={"NVDA": _nvda()},
                             analyst_recs=("mvf_analyst_brief.jsonl",),
                             pm_recs=("mvf_pm_brief.jsonl",)),
    "stop_expires": lambda: dict(market={"NVDA": _nvda()},
                                 pm_recs=("mvf_pm_stop.jsonl",),
                                 exec_recs=("mvf_exec_stop_gtc.jsonl",),
                                 broker_mode="stop_expires_at_the_bell"),
    "hold_only_ticker": lambda: dict(
        market={"NVDA": _nvda(),
                "AAPL": _nvda(ticker="AAPL", price=232.0, cash=0.0, held_qty=0)},
        analyst_recs=("mvf_analyst_brief.jsonl",),
        pm_recs=("mvf_pm_brief.jsonl",)),
}


def run_scenario(name: str, tmp_path: Path) -> SimResult:
    """One named day. Unknown names raise KeyError, listing the valid ones."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; valid: {', '.join(sorted(SCENARIOS))}")
    tmp_path.mkdir(parents=True, exist_ok=True)
    return sim_day(tmp_path, **SCENARIOS[name]())
