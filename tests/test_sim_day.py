"""Day-shape simulations (MVF T3). Four full trading days driven end-to-end
through the REAL gate, tools, hooks, DB, outbox and fill-poll, against
RECORDED LLM decisions — the LLM is the only thing replaced (acceptance §0).
No network, no API keys, no LLM cost.

Everything downstream of the recording is production code: the PreToolUse
order gate, the PostToolUse order recorder, the fund MCP tool handlers, the
risk math, the ticket store, orchestrator/daily.py's stage machine and
orchestrator/reconcile.py's fill-poll. Market state and the fill price are
fixtures/golden-day.md's own numbers.

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


def sim_day(tmp_path, *, market: dict,
            analyst_recs=("mvf_analyst.jsonl",),
            news_recs=("mvf_news.jsonl",),
            pm_recs=("mvf_pm.jsonl",),
            exec_recs=("mvf_exec.jsonl",),
            feed_break: dict | None = None,
            slack=None,
            id_factory=None) -> SimResult:
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
    broker = FakeAlpaca(PRICES, FILL_PRICES, mode="fill")
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
    """The golden-day sim, shared with tests/test_audit_day.py."""
    return sim_day(tmp_path, market={"NVDA": _nvda()})


# --- helpers ----------------------------------------------------------------

def _checkpoints(sim: SimResult) -> dict:
    return dict(sim.conn.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date = ?",
        (sim.run_date,)).fetchall())


def _count(sim: SimResult, table: str) -> int:
    return sim.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def _decision(sim: SimResult, ticker: str) -> sqlite3.Row:
    return sim.conn.execute(
        "SELECT * FROM decisions WHERE run_date = ? AND ticker = ?",
        (sim.run_date, ticker)).fetchone()


def _event_payloads(sim: SimResult, kind: str) -> list[dict]:
    return [json.loads(r["payload"]) for r in sim.conn.execute(
        "SELECT payload FROM events WHERE kind = ? ORDER BY id", (kind,))]


def _brief(sim: SimResult, stage: str) -> dict:
    """The stage brief the seat actually received that turn, straight off the
    replayed tool result — not re-derived, or the assertion would be circular."""
    briefs = [o["result"]["brief"] for o in sim.outcomes.get(stage, [])
              if o.get("tool") == "mcp__fund__get_stage_brief"]
    assert len(briefs) == 1, f"{stage}: expected one brief, got {len(briefs)}"
    return briefs[0]


def _id_sequence(ids):
    """Deterministic multi-ticket id_factory (review Fix 3): yields `ids` in
    call order. A day that mints more tickets than provided is a bug the
    sim should crash on, not paper over with a repeated id — StopIteration
    is the loud failure that gives us."""
    it = iter(ids)
    return lambda: next(it)


def _assert_day_completed(sim: SimResult) -> None:
    """Every stage done, the outbox fully drained, the digest posted — true on
    EVERY day shape, including the ones that trade nothing (invariant 4)."""
    assert _checkpoints(sim) == {s: "done" for s in STAGES}
    assert _count(sim, "events WHERE posted_at IS NULL") == 0
    assert len(sim.slack.posts["#pnl"]) == 1


# --- 1. golden day ----------------------------------------------------------

def test_golden_day(tmp_path):
    """NVDA active, analyst bullish/72, PM buys 80, gate caps at the golden 66,
    order fills at 180.14 through the real fill-poll."""
    sim = golden_day(tmp_path)

    # each seat's OWN signal, not run_research's neutral/0 default. Both
    # lenses land under distinct agents — the whole point of a second seat.
    rows = {r["agent"]: (r["ticker"], r["direction"], r["confidence"])
            for r in sim.conn.execute("SELECT * FROM signals").fetchall()}
    assert rows == {"analyst": ("NVDA", "bullish", 72),
                    "news": ("NVDA", "neutral", 45)}

    # the PM's ask survives on the decision; the gate's cap lives on the ticket
    d = _decision(sim, "NVDA")
    assert (d["action"], d["qty"], d["status"]) == ("buy", 80, "executed")
    ticket = sim.conn.execute("SELECT * FROM tickets").fetchone()
    assert ticket["id"] == TID                       # id == client_order_id
    assert ticket["max_qty"] == 66                   # min(80, gate 66)
    assert ticket["status"] == "consumed"
    assert _count(sim, "tickets") == 1

    # the order really went to the broker, sized to the ticket, once
    assert len(sim.broker.place_attempts) == 1
    attempt = sim.broker.place_attempts[0]
    assert (attempt["client_order_id"], attempt["symbol"], attempt["side"],
            attempt["qty"]) == (TID, "NVDA", "buy", "66")

    o = sim.conn.execute("SELECT * FROM orders").fetchone()
    assert _count(sim, "orders") == 1
    assert (o["client_order_id"], o["status"]) == (TID, "filled")
    assert (o["filled_qty"], o["filled_avg_price"]) == (66, 180.14)

    # The whole projected message, text and Block Kit both: the golden day's
    # observable output at the Slack boundary. text is asserted alongside
    # blocks because Slack renders text — not blocks — in push notifications
    # and to screen readers. A fill is the broker reporting, not the trader
    # speaking, so it carries the seat as a label but posts with no persona.
    assert sim.slack.posts["#trade-log"] == [
        {"ts": sim.slack.posts["#trade-log"][0]["ts"],
         "text": "*Dash (Execution)* · 🧾 bought *66 NVDA* at *$180.14*"
                 " — $11,889.24\nTicket `a3f90000`",
         "thread_ts": None,
         "username": None,
         "icon_emoji": None,
         "blocks": [
             {"type": "section",
              "text": {"type": "mrkdwn", "text": "🧾 bought *66 NVDA*"}},
             {"type": "section",
              "fields": [{"type": "mrkdwn", "text": "*Price*\n$180.14"},
                         {"type": "mrkdwn", "text": "*Notional*\n$11,889.24"}]},
             {"type": "context",
              "elements": [{"type": "mrkdwn",
                            "text": "Dash (Execution) · Ticket `a3f90000`"}]},
         ]}]
    assert [p["max_qty"] for p in _event_payloads(sim, "gate_approved")] == [66]

    # every turn that ran recorded its cost, and the digest reports the sum
    assert sim.turns == {"research": 2, "decision": 1, "execution": 1}
    assert _count(sim, "costs") == 4   # analyst + news + pm + exec
    digest = _event_payloads(sim, "digest")[0]["text"]
    assert "decisions: NVDA buy 80 (executed)" in digest
    assert "fills: NVDA buy 66@180.14" in digest
    assert "est. inference cost $0.04" in digest   # 4 turns: +news seat

    assert (sim.journals / "exec.md").read_text().count("NVDA buy 66@180.14") == 1
    _assert_day_completed(sim)


# --- 2. all-hold day --------------------------------------------------------

def test_all_hold_day(tmp_path):
    """PM holds: nothing is ticketed, the exec seat is never woken (no LLM
    spend on a hold day), and the day still completes and still posts."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  pm_recs=("mvf_pm_hold.jsonl",))

    d = _decision(sim, "NVDA")
    assert (d["action"], d["qty"], d["status"]) == ("hold", 0, "held")
    assert _count(sim, "tickets") == 0
    assert _count(sim, "orders") == 0
    assert sim.broker.place_attempts == []           # zero broker attempts
    assert sim.turns["execution"] == 0               # counted, not inferred
    assert sim.turns == {"research": 2, "decision": 1, "execution": 0}
    assert _count(sim, "costs") == 3        # analyst + news + pm, no exec
    assert "#trade-log" not in sim.slack.posts
    assert _event_payloads(sim, "digest")[0]["text"].endswith(
        "decisions: NVDA hold 0 (held)\nfills: none\nest. inference cost $0.03")

    # Fix 5: pin the risk channel too — a spurious gate_rejected/gate_approved/
    # alert on a hold-only day would otherwise pass unnoticed.
    assert _event_payloads(sim, "gate_approved") == []
    assert _event_payloads(sim, "gate_rejected") == []
    assert _event_payloads(sim, "alert") == []
    assert "#risk" not in sim.slack.posts

    _assert_day_completed(sim)


# --- 3. mixed day -----------------------------------------------------------

def test_mixed_day(tmp_path):
    """Two tickers, one traded and one held, in the same day."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda(), "MSFT": _msft()},
                  pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_hold.jsonl"))

    nvda, msft = _decision(sim, "NVDA"), _decision(sim, "MSFT")
    assert (nvda["action"], nvda["status"]) == ("buy", "executed")
    assert (msft["action"], msft["qty"], msft["status"]) == ("hold", 0, "held")

    # MSFT had no analyst coverage -> run_research's neutral/0 default
    msft_sig = sim.conn.execute(
        "SELECT * FROM signals WHERE ticker = 'MSFT'").fetchone()
    assert (msft_sig["direction"], msft_sig["confidence"], msft_sig["summary"]) \
        == ("neutral", 0, "no report")

    assert _count(sim, "tickets") == 1
    assert sim.conn.execute("SELECT ticker FROM tickets").fetchone()["ticker"] == "NVDA"
    assert _count(sim, "orders") == 1
    assert [a["symbol"] for a in sim.broker.place_attempts] == ["NVDA"]
    o = sim.conn.execute("SELECT * FROM orders").fetchone()
    assert (o["symbol"], o["status"], o["filled_qty"]) == ("NVDA", "filled", 66)
    assert len(sim.slack.posts["#trade-log"]) == 1
    assert sim.turns["execution"] == 1
    _assert_day_completed(sim)


# --- 4. gate-reject day -----------------------------------------------------

def test_gate_reject_day(tmp_path):
    """The vol feed goes NaN between the PM's submit and the gate's enforce
    pass. The decision is rejected, nothing is ticketed, nothing is placed,
    the reject is announced, and the day still completes (invariant 4)."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  feed_break={"NVDA": {"vol_60d": float("nan")}})

    d = _decision(sim, "NVDA")
    assert (d["action"], d["qty"], d["status"]) == ("buy", 80, "rejected")
    assert _count(sim, "tickets") == 0
    assert _count(sim, "orders") == 0
    assert sim.broker.place_attempts == []
    assert sim.turns["execution"] == 0                # no ticket -> no exec turn

    assert _event_payloads(sim, "gate_rejected") == [
        {"ticker": "NVDA", "side": "buy", "reason": "gate_error"}]
    assert any("*buy NVDA* blocked" in p["text"] and "`gate_error`" in p["text"]
               for p in sim.slack.posts["#risk"])
    assert _event_payloads(sim, "gate_approved") == []
    _assert_day_completed(sim)


def test_gate_reject_day_circuit_breaker(tmp_path):
    """Fix 4: a blanket `except Exception: reject('gate_error')` can fake the
    NaN case above by simply blowing up on anything. It cannot fake a
    circuit-breaker rejection, which is a value the gate computes correctly
    and returns as data, never an exception. The daily P&L feed breaches
    CIRCUIT_BREAKER (-0.03) between the PM's submit and the gate's enforce
    pass."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  feed_break={"NVDA": {"daily_pnl_pct": -0.05}})

    d = _decision(sim, "NVDA")
    assert (d["action"], d["qty"], d["status"]) == ("buy", 80, "rejected")
    assert _count(sim, "tickets") == 0
    assert _count(sim, "orders") == 0
    assert sim.broker.place_attempts == []
    assert sim.turns["execution"] == 0

    assert _event_payloads(sim, "gate_rejected") == [
        {"ticker": "NVDA", "side": "buy", "reason": "circuit_breaker"}]
    assert any("*buy NVDA* blocked" in p["text"] and "`circuit_breaker`" in p["text"]
               for p in sim.slack.posts["#risk"])
    assert _event_payloads(sim, "gate_approved") == []
    _assert_day_completed(sim)


# --- 5. two orders in one day ------------------------------------------------

def test_two_orders_same_day(tmp_path):
    """Fix 3: NVDA buys, MSFT sells, in the same day — two tickets, two
    orders, both live in reconcile_orders' multi-pending poll loop and both
    rendered in the same fill-line/trade-log pass at once, for the first
    time at any level."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda(), "MSFT": _msft()},
                  pm_recs=("mvf_pm.jsonl", "mvf_pm_msft_sell.jsonl"),
                  exec_recs=("mvf_exec_two.jsonl",),
                  id_factory=_id_sequence([TID, TID2]))

    nvda, msft = _decision(sim, "NVDA"), _decision(sim, "MSFT")
    assert (nvda["action"], nvda["qty"], nvda["status"]) == ("buy", 80, "executed")
    assert (msft["action"], msft["qty"], msft["status"]) == ("sell", 40, "executed")

    tickets = {r["ticker"]: r for r in sim.conn.execute(
        "SELECT * FROM tickets ORDER BY ticker")}
    assert _count(sim, "tickets") == 2
    assert tickets["NVDA"]["id"] == TID
    assert tickets["MSFT"]["id"] == TID2
    assert tickets["NVDA"]["id"] != tickets["MSFT"]["id"]
    assert {t["status"] for t in tickets.values()} == {"consumed"}

    assert {a["symbol"] for a in sim.broker.place_attempts} == {"NVDA", "MSFT"}
    assert len(sim.broker.place_attempts) == 2

    orders = {r["symbol"]: r for r in sim.conn.execute(
        "SELECT * FROM orders ORDER BY symbol")}
    assert _count(sim, "orders") == 2
    assert (orders["NVDA"]["status"], orders["NVDA"]["filled_qty"]) == ("filled", 66)
    assert (orders["MSFT"]["status"], orders["MSFT"]["filled_qty"]) == ("filled", 40)

    trade_log = sim.slack.posts["#trade-log"]
    assert len(trade_log) == 2
    texts = {p["text"] for p in trade_log}
    assert any("bought *66 NVDA* at *$180.14*" in t for t in texts)
    assert any("sold *40 MSFT* at *$505.00*" in t for t in texts)

    assert sim.turns == {"research": 2, "decision": 1, "execution": 1}
    _assert_day_completed(sim)


# --- 6. the PM can actually see the analyst's work ---------------------------

def test_pm_brief_carries_the_signal_and_the_budget_the_gate_enforces(tmp_path):
    """README's headline claim, as a test: "what the PM was shown is what the
    gate enforces".

    Both seats open their turn with `get_stage_brief` — the only path by which
    the analyst's signal and the gate's allowed-actions snapshot reach the PM
    (the stage prompt carries nothing but the ticker list, by design: per-run
    values never go into prompts). The day then runs the REAL gate against the
    same fixture, so the number the PM was shown and the number the broker was
    sent are compared, not assumed."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  analyst_recs=("mvf_analyst_brief.jsonl",),
                  pm_recs=("mvf_pm_brief.jsonl",))

    # the analyst's brief: account context + its own journal, nothing PM-only
    analyst = _brief(sim, "research")
    assert (analyst["seat"], analyst["run_date"]) == ("analyst", sim.run_date)
    assert (analyst["cash"], analyst["positions"]) == (30000.0, {})
    assert analyst["unavailable"] == []
    assert "signals" not in analyst and "allowed_actions" not in analyst

    # the PM's brief: BOTH analysts' ACTUAL signal rows, submitted this same
    # day. This is the branch's payoff — one missing seat here would silently
    # halve the evidence the decision is made on.
    pm = _brief(sim, "decision")
    assert pm["seat"] == "pm"
    assert pm["unavailable"] == []
    assert {s["agent"] for s in pm["signals"]} == {"analyst", "news"}
    assert pm["signals"] == [{
        "agent": "analyst", "ticker": "NVDA", "direction": "bullish",
        "confidence": 72,
        "summary": "DC capex guides re-accelerating; fwd P/E below 3y median;"
                   " reclaimed 50d on volume."},
        {"agent": "news", "ticker": "NVDA", "direction": "neutral",
         "confidence": 45,
         "summary": "Capex headline is 3 sessions old and already faded; no"
                    " fresh primary reporting today."}]
    # not re-derived from the same dict — the row the brief carried IS the row
    # the seat wrote
    assert [s["summary"] for s in pm["signals"]] == [r["summary"] for r in
        sim.conn.execute("SELECT summary FROM signals WHERE ticker = 'NVDA'"
                         " ORDER BY agent").fetchall()]

    # THE claim: the snapshot the PM was shown IS the cap the gate enforced and
    # the size the broker was sent. The PM asked for 80 over a 66 budget, so
    # the number below is load-bearing, not a coincidence of a small ask.
    shown = pm["allowed_actions"]["NVDA"]
    assert shown == {"buy": 66, "sell": 0}
    ticket = sim.conn.execute("SELECT * FROM tickets").fetchone()
    assert _decision(sim, "NVDA")["qty"] == 80          # the PM's over-ask
    assert ticket["max_qty"] == shown["buy"]            # gate cap == shown budget
    assert sim.broker.place_attempts[0]["qty"] == str(shown["buy"])
    assert [p["max_qty"] for p in _event_payloads(sim, "gate_approved")] \
        == [shown["buy"]]

    _assert_day_completed(sim)
