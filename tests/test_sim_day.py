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

import json
import sqlite3

from state.db import connect
from tests.simday import (STAGES, TID, TID2, SimResult, _nvda, golden_day,
                          run_scenario, sim_day)


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


def _participating_seats(sim: SimResult) -> set[str]:
    """The seats run_close owes a journal to (orchestrator/daily.py:460-467):
    every seat that signalled today, plus the PM, plus the trader IFF the day
    had a fill. Derived rather than named literally so a new seat is covered
    without editing the test.

    The exec arm must ask the question run_close asks, which is _fill_rows'
    (daily.py:392-397) — scoped to THIS run_date and to the two statuses that
    mean shares changed hands. `filled_qty > 0` alone is a different question:
    a partially-filled order later canceled or expired carries a non-zero
    filled_qty, and run_close writes no exec journal for a day whose fills
    line reads `none`."""
    seats = {r["agent"] for r in sim.conn.execute(
        "SELECT DISTINCT agent FROM signals WHERE run_date = ?",
        (sim.run_date,))} | {"pm"}
    if sim.conn.execute(
            "SELECT COUNT(*) c FROM orders o"
            " JOIN tickets t ON t.id = o.client_order_id"
            " JOIN decisions d ON d.id = t.decision_id"
            " WHERE d.run_date = ? AND o.status IN ('filled', 'partially_filled')",
            (sim.run_date,)).fetchone()["c"]:
        seats.add("exec")
    return seats


def _brief(sim: SimResult, stage: str) -> dict:
    """The stage brief the seat actually received that turn, straight off the
    replayed tool result — not re-derived, or the assertion would be circular."""
    briefs = [o["result"]["brief"] for o in sim.outcomes.get(stage, [])
              if o.get("tool") == "mcp__fund__get_stage_brief"]
    assert len(briefs) == 1, f"{stage}: expected one brief, got {len(briefs)}"
    return briefs[0]


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
    # One row per turn. NOT the "per seat per session" criterion: the sim
    # harness supplies both agent and session_id itself (`sim-{seat}`, :144),
    # so any assertion on those values here would be checking the harness's
    # own f-string. That criterion is pinned on the production seam instead,
    # in tests/test_runtime_hooks.py.
    assert _count(sim, "costs") == 4   # analyst + news + pm + exec
    digest = _event_payloads(sim, "digest")[0]["text"]
    assert "decisions: NVDA buy 80 (executed)" in digest
    assert "fills: NVDA buy 66@180.14" in digest
    assert "est. inference cost $0.04" in digest   # 4 turns: +news seat

    assert (sim.journals / "exec.md").read_text().count("NVDA buy 66@180.14") == 1
    # EVERY participating seat, not just the two named by hand: the news seat
    # signalled today and owes a journal exactly as the analyst does (#158).
    # The criterion is "has an ENTRY", so each file must carry today's entry,
    # not merely exist -- _append_entry_once stamps the run_date as its header.
    seats = _participating_seats(sim)
    assert {p.stem for p in sim.journals.glob("*.md")} == seats
    for seat in seats:
        assert sim.run_date in (sim.journals / f"{seat}.md").read_text(), seat
    _assert_day_completed(sim)


def test_golden_day_records_its_offered_set(tmp_path):
    """specs/acceptance.md Phase 2b: under sim, the pre-gate stage writes one
    `offered` row per surviving ticker. The golden day offers NVDA alone."""
    sim = golden_day(tmp_path)
    rows = sim.conn.execute(
        "SELECT run_date, ticker FROM offered").fetchall()
    assert [(r["run_date"], r["ticker"]) for r in rows] == [(sim.run_date, "NVDA")]


# --- 2. all-hold day --------------------------------------------------------

def test_all_hold_day(tmp_path):
    """PM holds: nothing is ticketed, the exec seat is never woken (no LLM
    spend on a hold day), and the day still completes and still posts."""
    sim = run_scenario("all_hold", tmp_path)

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

    # No fill, so no exec journal — the negative arm of the rule test_golden_day
    # asserts positively. Without a no-fill day here, _participating_seats'
    # exec branch is never exercised and an unconditional add("exec") would
    # pass every test in this file.
    assert _participating_seats(sim) == {"analyst", "news", "pm"}
    assert {p.stem for p in sim.journals.glob("*.md")} == _participating_seats(sim)

    _assert_day_completed(sim)


# --- 3. mixed day -----------------------------------------------------------

def test_mixed_day(tmp_path):
    """Two tickers, one traded and one held, in the same day."""
    sim = run_scenario("mixed", tmp_path)

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
    sim = run_scenario("gate_reject", tmp_path)

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
    sim = run_scenario("circuit_breaker", tmp_path)

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
    sim = run_scenario("two_orders", tmp_path)

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
    sim = run_scenario("pm_brief", tmp_path)

    # the analyst's brief: account context + its own journal, nothing PM-only
    analyst = _brief(sim, "research")
    assert (analyst["seat"], analyst["run_date"]) == ("analyst", sim.run_date)
    assert (analyst["cash"], analyst["positions"]) == (30000.0, {})
    # No scoring night has run before the golden day, so the weights table is
    # empty and improvement.md §2.1 (ii) NAMES it — never an empty section that
    # reads as "no seats". Everything else built.
    assert [m.split(" (")[0] for m in analyst["unavailable"]] == ["weights"]
    assert "signals" not in analyst and "allowed_actions" not in analyst

    # the PM's brief: BOTH analysts' ACTUAL signal rows, submitted this same
    # day. This is the branch's payoff — one missing seat here would silently
    # halve the evidence the decision is made on.
    pm = _brief(sim, "decision")
    assert pm["seat"] == "pm"
    assert [m.split(" (")[0] for m in pm["unavailable"]] == ["weights"]
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


def test_the_pm_brief_renders_the_row_the_scoring_job_wrote(tmp_path):
    """specs/acceptance.md Phase 2b item 2, under sim: the PM's brief
    `weights` section equals the latest row for every analyst seat, each
    with its as_of_date — read off the replayed tool result, never
    re-derived. The scoring job ran on an earlier night against the same
    on-disk database the sim then opens."""
    from datetime import datetime, timezone

    from orchestrator.clock import SimClock
    from orchestrator.improve import (WeightsConfig, latest_weights,
                                      write_weights)
    from tests.test_improve import _two_seat_history

    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)
    write_weights(conn, SimClock(datetime(2026, 7, 2, 20, 35, tzinfo=timezone.utc)),
                  WeightsConfig(window_days=20, horizon_days=5))
    expected = latest_weights(conn)
    conn.close()

    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  pm_recs=("mvf_pm_brief.jsonl",))

    pm = _brief(sim, "decision")
    assert pm["weights"] == expected
    assert [(w["agent"], w["as_of_date"]) for w in pm["weights"]] == [
        ("a", "2026-07-02"), ("b", "2026-07-02")]
    assert [m.split(" (")[0] for m in pm["unavailable"]] == []


# --- 5. the stop that expired at the bell (2026-08-19) ----------------------

def test_the_stop_that_expired_at_the_bell_is_caught(tmp_path):
    """2026-08-19, reproduced end to end. A stopped NVDA entry fills, its OTO
    stop leg then EXPIRES at the close exactly as the real one did on 08-17,
    and the day must not end quietly.

    Before this assertion existed the run was clean — holds, AUDIT CLEAN, a
    digest posted — while the position sat naked for two sessions. A green
    suite proved nothing then. This is what proving looks like: the real gate,
    the real hooks, the real fill-poll, and a broker whose protection dies on
    schedule."""
    sim = run_scenario("stop_expires", tmp_path)

    # the entry really happened, carrying the ticket's stop
    placed = sim.broker.place_attempts[0]
    assert (placed["order_class"], placed["time_in_force"]) == ("oto", "gtc")
    assert placed["stop_loss_stop_price"] == "168.0"
    assert sim.conn.execute(
        "SELECT stop_price FROM tickets").fetchone()["stop_price"] == 168.0

    # ...and the broker is now holding the position with nothing guarding it
    assert sim.broker.open_positions() == [
        {"symbol": "NVDA", "qty": "66", "side": "long"}]
    assert sim.broker.open_orders() == []

    texts = [p["text"] for p in _event_payloads(sim, "alert")]
    assert any("NVDA" in t and "stop at 168" in t for t in texts), (
        f"the naked position was not reported; alerts were {texts}")
    assert any("NO live protective order" in p["text"]
               for p in sim.slack.posts["#risk"])


# --- 7. the HOLD-only ticker never reaches a seat ---------------------------

def test_a_hold_only_ticker_never_reaches_the_pipeline(tmp_path):
    """acceptance.md Phase 2, criterion 7: a ticker the pre-gate resolves to
    {buy:0, sell:0} "never reaches the LLM pipeline (assert zero agent turns
    for it)" (#157). The chain was pinned link by link in test_daily_stages.py
    and joined nowhere -- and the joins are what a refactor breaks, since
    run_research and run_decision take `active` as a parameter.

    Turn counts are per STAGE, not per ticker (`turns` above), so the
    per-ticker form of "zero agent turns" is: the ticker is absent from the
    snapshot the PM decides on, and NEITHER stage default fired for it. The
    defaults are what make the negative observable -- an ACTIVE ticker nobody
    covers still gets a row (run_research writes neutral/0 and run_decision
    writes hold/0, exactly as test_mixed_day's MSFT does), so no row at all is
    reachable only by never having been active.

    AAPL carries no cash and no shares: nothing to buy, nothing to sell. NVDA
    trades its normal golden day beside it, so this is a drop and not an empty
    day."""
    sim = run_scenario("hold_only_ticker", tmp_path)

    # the snapshot the PM was actually shown, off the replayed tool result
    assert list(_brief(sim, "decision")["allowed_actions"]) == ["NVDA"]

    # neither stage default fired for it: no row, not a zero row
    assert [r["ticker"] for r in sim.conn.execute(
        "SELECT DISTINCT ticker FROM signals ORDER BY ticker")] == ["NVDA"]
    assert [r["ticker"] for r in sim.conn.execute(
        "SELECT DISTINCT ticker FROM decisions ORDER BY ticker")] == ["NVDA"]

    # a drop, not an empty day -- NVDA still ran the whole golden pipeline
    assert _decision(sim, "NVDA")["action"] == "buy"
    assert _count(sim, "tickets") == 1
    _assert_day_completed(sim)


# --- the scenario table --------------------------------------------------------

def test_every_scenario_runs_and_completes_or_alerts(tmp_path):
    """Every name in SCENARIOS is a day the sim can run. Each test above
    builds its day from this table, so a scenario sim-report can render is a
    scenario a test pins — the table cannot drift from the assertions."""
    from tests.simday import SCENARIOS, run_scenario
    assert set(SCENARIOS) == {
        "golden", "all_hold", "mixed", "gate_reject", "circuit_breaker",
        "two_orders", "pm_brief", "stop_expires", "hold_only_ticker"}
    for name in SCENARIOS:
        sim = run_scenario(name, tmp_path / name)
        assert sim.run_date, name
        assert _checkpoints(sim).get("close") == "done", name
        _assert_day_completed(sim)
