"""Stage bodies in isolation (MVF P4). Full-day sims are Task 15."""

import json
from datetime import timedelta

import pytest

from orchestrator.clock import iso
from orchestrator.daily import (StageCtx, allowed_actions, run_close, run_day,
                                run_decision, run_execution, run_gate,
                                run_pre_gate, run_research)
from slackkit.fake import FakeSlack
from slackkit.outbox import append_event

RUN = "2026-07-06"
TID = "a3f90000-0000-0000-0000-000000000000"


def _ctx(fund_db, sim_clock, market, turns=None, journals_root=None,
         research_seats=("analyst",)):
    """market: {ticker: gate-input dict (pre-validated by risk later)}."""
    return StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock,
                    slack=FakeSlack(), market_inputs=market,
                    research_seats=research_seats,
                    run_turn=turns or {}, id_factory=lambda: TID,
                    journals_root=journals_root)


def _nvda_inputs(**over):
    d = dict(ticker="NVDA", side="buy", equity=100000.0, cash=30000.0,
             price=180.0, vol_60d=0.42, avg_corr=0.55, held_qty=0,
             position_count=2, sector="tech", sector_value=48040.0,
             daily_pnl_pct=-0.004)
    d.update(over)
    return d


def _seed_decision(fund_db, sim_clock, ticker, action, qty):
    fund_db.execute(
        "INSERT INTO decisions (run_date,ticker,action,qty,thesis,"
        "invalidation,status,created_at) VALUES (?,?,?,?,?,?,'submitted',?)",
        (RUN, ticker, action, qty, "t", "i", iso(sim_clock.now())))
    fund_db.commit()


# --- pre-gate ---------------------------------------------------------------

def test_pre_gate_drops_no_action_tickers(fund_db, sim_clock):
    market = {"NVDA": _nvda_inputs(),
              "AAPL": _nvda_inputs(ticker="AAPL", cash=0.0, held_qty=0)}
    ctx = _ctx(fund_db, sim_clock, market)
    active = run_pre_gate(ctx)
    assert active == ["NVDA"]                    # AAPL: {buy:0, sell:0} dropped


def test_pre_gate_keeps_sell_only_ticker(fund_db, sim_clock):
    """No cash to buy but shares held -> sell is possible, ticker stays."""
    market = {"AAPL": _nvda_inputs(ticker="AAPL", cash=0.0, held_qty=40)}
    assert run_pre_gate(_ctx(fund_db, sim_clock, market)) == ["AAPL"]


def test_pre_gate_drops_garbage_inputs(fund_db, sim_clock):
    """NaN vol -> gate_error on both shapes -> dropped, never a crash."""
    market = {"NVDA": _nvda_inputs(vol_60d=float("nan"))}
    assert run_pre_gate(_ctx(fund_db, sim_clock, market)) == []


# --- allowed-actions snapshot (the PM's sizing budget) ----------------------

def test_allowed_actions_is_the_golden_days_budget():
    """fixtures/golden-day.md: the advisory pass sizes NVDA to 66 — the same
    number the enforcement pass caps the PM's 80-share ask at. Nothing held,
    so the sell shape is 0."""
    assert allowed_actions({"NVDA": _nvda_inputs()}) == {
        "NVDA": {"buy": 66, "sell": 0}}


def test_allowed_actions_reports_a_sell_only_ticker():
    assert allowed_actions({"AAPL": _nvda_inputs(ticker="AAPL", cash=0.0,
                                                 held_qty=40)}) == {
        "AAPL": {"buy": 0, "sell": 40}}


@pytest.mark.parametrize("over", [dict(cash=0.0, held_qty=0),
                                  dict(vol_60d=float("nan"))])
def test_allowed_actions_omits_tickers_where_nothing_is_possible(over):
    """{buy:0, sell:0} and garbage feeds are ABSENT, not present-and-zero: an
    empty snapshot is what the PM reads as "HOLD everything"."""
    assert allowed_actions({"NVDA": _nvda_inputs(**over)}) == {}


def test_allowed_actions_key_set_is_the_active_set(fund_db, sim_clock):
    """The snapshot the PM is shown and the tickers it is asked to decide on
    are the same list, by construction — they cannot drift."""
    market = {"NVDA": _nvda_inputs(),
              "AAPL": _nvda_inputs(ticker="AAPL", cash=0.0, held_qty=0),
              "MSFT": _nvda_inputs(ticker="MSFT", cash=0.0, held_qty=40)}
    ctx = _ctx(fund_db, sim_clock, market)
    assert list(allowed_actions(market)) == run_pre_gate(ctx) == ["NVDA", "MSFT"]


# --- research ---------------------------------------------------------------

def test_research_missing_signal_defaults_neutral(fund_db, sim_clock):
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    run_research(ctx, active=["NVDA"])           # no analyst turn wired
    row = fund_db.execute("SELECT * FROM signals").fetchone()
    assert (row["direction"], row["confidence"], row["summary"]) == ("neutral", 0, "no report")


def test_research_keeps_the_analysts_own_signal(fund_db, sim_clock):
    def turn():
        fund_db.execute(
            "INSERT INTO signals (run_date,agent,ticker,direction,confidence,"
            "summary,created_at) VALUES (?,'analyst','NVDA','bullish',72,'capex',?)",
            (RUN, iso(sim_clock.now())))
        fund_db.commit()

    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
               turns={"research": turn})
    run_research(ctx, active=["NVDA"])
    rows = fund_db.execute("SELECT * FROM signals").fetchall()
    assert len(rows) == 1
    assert (rows[0]["direction"], rows[0]["confidence"]) == ("bullish", 72)


def test_research_rerun_writes_no_duplicate(fund_db, sim_clock):
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    run_research(ctx, active=["NVDA"])
    run_research(ctx, active=["NVDA"])
    assert fund_db.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 1


def test_research_defaults_are_per_seat_not_per_ticker(fund_db, sim_clock):
    """Seat A reports, seat B is silent -> B still gets its own neutral row.
    The old guard asked only whether the TICKER was covered, so B's silence
    was invisible and calibration/rows.py (which groups by s.agent) would
    grade B only on the days it chose to speak."""
    def turn():
        fund_db.execute(
            "INSERT INTO signals (run_date,agent,ticker,direction,confidence,"
            "summary,created_at) VALUES (?,'analyst','NVDA','bullish',72,'capex',?)",
            (RUN, iso(sim_clock.now())))
        fund_db.commit()

    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
               turns={"research": turn}, research_seats=("analyst", "news"))
    run_research(ctx, active=["NVDA"])

    rows = {r["agent"]: r for r in
            fund_db.execute("SELECT * FROM signals ORDER BY agent").fetchall()}
    assert set(rows) == {"analyst", "news"}
    assert (rows["analyst"]["direction"], rows["analyst"]["confidence"]) == ("bullish", 72)
    assert (rows["news"]["direction"], rows["news"]["confidence"],
            rows["news"]["summary"]) == ("neutral", 0, "no report")


def test_research_with_no_seats_configured_raises(fund_db, sim_clock):
    """An empty seat tuple must never silently skip the defaults — that would
    turn invariant 4's neutral/0 guarantee into a no-op."""
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}, research_seats=())
    with pytest.raises(ValueError, match="research_seats is empty"):
        run_research(ctx, active=["NVDA"])


def test_research_skips_the_turn_when_every_seat_is_covered(fund_db, sim_clock):
    """Crash-resume: run_stage re-runs a 'running' stage body. Without the
    skip, every seat's LLM turn is paid for a second time — a money leak with
    no visible symptom."""
    calls = []

    def turn():
        calls.append(1)
        fund_db.execute(
            "INSERT INTO signals (run_date,agent,ticker,direction,confidence,"
            "summary,created_at) VALUES (?,'analyst','NVDA','bullish',72,'x',?)",
            (RUN, iso(sim_clock.now())))
        fund_db.commit()

    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
               turns={"research": turn}, research_seats=("analyst",))
    run_research(ctx, active=["NVDA"])
    run_research(ctx, active=["NVDA"])      # the resume path
    assert calls == [1]


# --- decision ---------------------------------------------------------------

def test_decision_timeout_defaults_hold_with_event(fund_db, sim_clock):
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    run_decision(ctx, active=["NVDA"])           # no PM turn wired
    d = fund_db.execute("SELECT * FROM decisions").fetchone()
    assert d["action"] == "hold" and d["qty"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM critiques").fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='alert'"
                           " AND payload LIKE '%pm_timeout%'").fetchone()["c"] == 1


def test_decision_critique_row_lands_before_the_pm_turn(fund_db, sim_clock):
    """submit_decision refuses without a critique row: the default critique
    must already be committed when the PM turn runs (review decision 2.3)."""
    seen = {}

    def turn():
        seen["critiques"] = fund_db.execute(
            "SELECT COUNT(*) c FROM critiques").fetchone()["c"]

    run_decision(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
                      turns={"decision": turn}), active=["NVDA"])
    assert seen["critiques"] == 1


def test_decision_rerun_writes_no_duplicate(fund_db, sim_clock):
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    run_decision(ctx, active=["NVDA"])
    run_decision(ctx, active=["NVDA"])
    assert fund_db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM critiques").fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='alert'"
                           " AND payload LIKE '%pm_timeout%'").fetchone()["c"] == 1


# --- gate -------------------------------------------------------------------

def test_gate_stage_hold_goes_held_buy_mints_ticket(fund_db, sim_clock):
    _seed_decision(fund_db, sim_clock, "MSFT", "hold", 0)
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(),
                                    "MSFT": _nvda_inputs(ticker="MSFT")})
    run_gate(ctx)
    assert fund_db.execute("SELECT status FROM decisions WHERE ticker='MSFT'"
                           ).fetchone()["status"] == "held"
    t = fund_db.execute("SELECT * FROM tickets").fetchone()
    assert t["ticker"] == "NVDA" and t["max_qty"] > 0
    assert fund_db.execute("SELECT status FROM decisions WHERE ticker='NVDA'"
                           ).fetchone()["status"] == "approved"
    # expiry = clock + 45 min
    assert t["expires_at"] == iso(sim_clock.now() + timedelta(minutes=45))
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='gate_approved'"
                           ).fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 1


def test_gate_caps_the_pms_ask(fund_db, sim_clock):
    """Golden day: PM asks 80, gate computes 66 -> ticket carries 66."""
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}))
    assert fund_db.execute("SELECT max_qty FROM tickets").fetchone()["max_qty"] == 66


def test_gate_never_sizes_up_a_smaller_ask(fund_db, sim_clock):
    """PM asks 10 while the gate allows 66 -> ticket carries 10, not 66."""
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 10)
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}))
    assert fund_db.execute("SELECT max_qty FROM tickets").fetchone()["max_qty"] == 10


def test_gate_stage_reject_flows(fund_db, sim_clock):
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(vol_60d=float("nan"))})
    run_gate(ctx)
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "rejected"
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='gate_rejected'"
                           ).fetchone()["c"] == 1


def test_gate_rejects_ticker_with_no_market_inputs(fund_db, sim_clock):
    """No snapshot for the ticker -> gate_error, never a KeyError crash."""
    _seed_decision(fund_db, sim_clock, "TSLA", "buy", 5)
    run_gate(_ctx(fund_db, sim_clock, {}))
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "rejected"
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='gate_rejected'"
                           ).fetchone()["c"] == 1


def test_gate_rerun_mints_no_second_ticket(fund_db, sim_clock):
    _seed_decision(fund_db, sim_clock, "MSFT", "hold", 0)
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(),
                                    "MSFT": _nvda_inputs(ticker="MSFT")})
    run_gate(ctx)
    run_gate(ctx)
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE"
                           " kind='gate_approved'").fetchone()["c"] == 1
    assert fund_db.execute("SELECT status FROM decisions WHERE ticker='MSFT'"
                           ).fetchone()["status"] == "held"


def test_gate_resumes_after_a_crash_between_ticket_and_cas(fund_db, sim_clock):
    """Ticket minted, then the process died before the decision CAS. The
    re-run must reuse that ticket (tickets.UNIQUE(decision_id)) rather than
    try to mint a second one."""
    from gate.tickets import create_ticket
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    did = fund_db.execute("SELECT id FROM decisions").fetchone()["id"]
    create_ticket(fund_db, id="b7c00000-0000-0000-0000-000000000000",
                  decision_id=did, ticker="NVDA", side="buy", max_qty=66,
                  stop_price=None,
                  expires_at_iso=iso(sim_clock.now() + timedelta(minutes=45)),
                  now_iso=iso(sim_clock.now()))
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}))
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 1
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE"
                           " kind='gate_approved'").fetchone()["c"] == 1


def test_gate_resume_rejects_and_closes_the_stale_open_ticket(fund_db, sim_clock):
    """Crash between create_ticket and the decision CAS; on resume the
    rebuilt snapshot now REJECTS (NaN feed / circuit breaker / dropped
    ticker). The stale open ticket must not survive a decision the gate now
    rejects (review Critical 1) — otherwise validate_order still authorizes
    an order against it."""
    from gate.tickets import create_ticket, open_tickets
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    did = fund_db.execute("SELECT id FROM decisions").fetchone()["id"]
    create_ticket(fund_db, id="b7c00000-0000-0000-0000-000000000000",
                  decision_id=did, ticker="NVDA", side="buy", max_qty=66,
                  stop_price=None,
                  expires_at_iso=iso(sim_clock.now() + timedelta(minutes=45)),
                  now_iso=iso(sim_clock.now()))
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(vol_60d=float("nan"))}))
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "rejected"
    assert open_tickets(fund_db, iso(sim_clock.now())) == []


def test_gate_resume_rejects_when_ticker_dropped_from_snapshot(fund_db, sim_clock):
    """Same crash window, but the resumed snapshot no longer has the ticker
    at all (contract change / delisting mid-crash). Must reject and close
    the stale ticket, never raise a KeyError."""
    from gate.tickets import create_ticket, open_tickets
    _seed_decision(fund_db, sim_clock, "TSLA", "buy", 5)
    did = fund_db.execute("SELECT id FROM decisions").fetchone()["id"]
    create_ticket(fund_db, id="c8d00000-0000-0000-0000-000000000000",
                  decision_id=did, ticker="TSLA", side="buy", max_qty=5,
                  stop_price=None,
                  expires_at_iso=iso(sim_clock.now() + timedelta(minutes=45)),
                  now_iso=iso(sim_clock.now()))
    run_gate(_ctx(fund_db, sim_clock, {}))
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "rejected"
    assert open_tickets(fund_db, iso(sim_clock.now())) == []


def test_gate_resume_reconciles_existing_ticket_to_a_smaller_cap(fund_db, sim_clock):
    """Same crash window, tightened risk instead of a reject: the rebuilt
    snapshot now approves a SMALLER cap. The existing ticket (same id —
    invariant 5, it's the client_order_id) must be updated IN PLACE to the
    new cap, and the gate_approved event must match what's actually
    enforced (review Critical 2)."""
    from gate.tickets import create_ticket
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    did = fund_db.execute("SELECT id FROM decisions").fetchone()["id"]
    create_ticket(fund_db, id="b7c00000-0000-0000-0000-000000000000",
                  decision_id=did, ticker="NVDA", side="buy", max_qty=66,
                  stop_price=None,
                  expires_at_iso=iso(sim_clock.now() + timedelta(minutes=45)),
                  now_iso=iso(sim_clock.now()))
    # sector now much closer to cap -> headroom shrinks the recomputed max_qty to 11
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(sector_value=58020.0)}))
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 1
    t = fund_db.execute("SELECT * FROM tickets").fetchone()
    assert t["id"] == "b7c00000-0000-0000-0000-000000000000"   # unchanged: idempotency
    assert t["max_qty"] == 11
    ev = fund_db.execute(
        "SELECT payload FROM events WHERE kind='gate_approved'").fetchone()
    assert json.loads(ev["payload"])["max_qty"] == 11


def test_gate_isolates_a_per_decision_failure(fund_db, sim_clock, monkeypatch):
    """A raise handling one decision must not abort the whole stage (review
    Important 4) — matches the fail-closed posture size() already has."""
    import orchestrator.daily as daily
    real_create_ticket = daily.create_ticket

    def boom(*a, **k):
        if k.get("ticker") == "MSFT":
            raise RuntimeError("boom")
        return real_create_ticket(*a, **k)

    monkeypatch.setattr(daily, "create_ticket", boom)
    _seed_decision(fund_db, sim_clock, "MSFT", "buy", 10)
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    ctx = _ctx(fund_db, sim_clock, {"MSFT": _nvda_inputs(ticker="MSFT"),
                                    "NVDA": _nvda_inputs()})
    run_gate(ctx)
    assert fund_db.execute(
        "SELECT status FROM decisions WHERE ticker='MSFT'").fetchone()["status"] == "rejected"
    assert fund_db.execute(
        "SELECT status FROM decisions WHERE ticker='NVDA'").fetchone()["status"] == "approved"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='gate_rejected'"
        " AND payload LIKE '%gate_error%'").fetchone()["c"] == 1


def test_gate_approved_expiry_is_et_no_z_suffix(fund_db, sim_clock):
    """#risk reads every other time in ET (review Important 3); a bare
    UTC HH:MM with a 'Z' suffix reads as a much longer TTL than it is."""
    _seed_decision(fund_db, sim_clock, "NVDA", "buy", 80)
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}))
    ev = fund_db.execute(
        "SELECT payload FROM events WHERE kind='gate_approved'").fetchone()
    payload = json.loads(ev["payload"])
    assert "Z" not in payload["expires_hhmm"]
    from orchestrator.clock import et_hhmm
    expected = et_hhmm(sim_clock.now() + timedelta(minutes=45))
    assert payload["expires_hhmm"] == expected


def test_pre_gate_alerts_on_gate_error_but_stays_silent_on_no_headroom(fund_db, sim_clock):
    """A malformed feed must not be a silent no-trade day (review Important
    5): alert on gate_error, but the legitimate no_headroom/nothing_held
    skip must stay silent — that's the normal cost optimization."""
    market = {"NVDA": _nvda_inputs(vol_60d=float("nan")),          # gate_error both shapes
              "AAPL": _nvda_inputs(ticker="AAPL", cash=0.0, held_qty=0)}  # legit skip
    ctx = _ctx(fund_db, sim_clock, market)
    run_day(ctx, execution_turn=None, broker=None, sleep=lambda s: None)
    alerts = fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'").fetchall()
    texts = [json.loads(r["payload"])["text"] for r in alerts]
    assert any("gate_error" in t and "NVDA" in t for t in texts)
    assert not any("AAPL" in t for t in texts)


def test_close_journals_survive_a_crash_after_the_digest_commits(fund_db, sim_clock, tmp_path):
    """Digest-exists guard used to short-circuit the whole body, so a kill
    between the digest commit and the journal writes lost the journals
    forever (review Minor 6)."""
    _seed_decision(fund_db, sim_clock, "NVDA", "hold", 0)
    root = tmp_path / "journals"
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}, journals_root=root)
    run_research(ctx, active=["NVDA"])
    now = iso(sim_clock.now())
    # Simulate the crash: digest event already committed, journals never written.
    append_event(fund_db, "digest", {"text": "stub", "run_date": RUN}, now)
    run_close(ctx)
    assert (root / "pm.md").exists() and (root / "analyst.md").exists()
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='digest'").fetchone()["c"] == 1


def test_decision_pm_timeout_alert_ordered_before_the_row_commit(
        fund_db, sim_clock, monkeypatch):
    """review Minor 7: the row commit used to precede append_event, so a
    kill in between plus the SELECT 1 resume-guard silently dropped the
    pm_timeout alert forever. If append_event raises/crashes, the decision
    row must not already be committed — otherwise the resume guard skips
    the ticker forever and the alert is lost for good."""
    import orchestrator.daily as daily

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(daily, "append_event", boom)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    with pytest.raises(RuntimeError):
        run_decision(ctx, active=["NVDA"])
    assert fund_db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 0


def test_gate_sell_is_capped_by_held_qty(fund_db, sim_clock):
    _seed_decision(fund_db, sim_clock, "NVDA", "sell", 100)
    run_gate(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs(held_qty=40)}))
    t = fund_db.execute("SELECT * FROM tickets").fetchone()
    assert (t["side"], t["max_qty"]) == ("sell", 40)


# --- close ------------------------------------------------------------------

def test_close_posts_digest_and_journals(fund_db, sim_clock, tmp_path):
    _seed_decision(fund_db, sim_clock, "NVDA", "hold", 0)
    root = tmp_path / "journals"
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()}, journals_root=root)
    run_research(ctx, active=["NVDA"])
    run_close(ctx)
    row = fund_db.execute("SELECT payload FROM events WHERE kind='digest'").fetchone()
    assert "est." in row["payload"] and RUN in row["payload"]
    assert (root / "pm.md").exists() and (root / "analyst.md").exists()


def _seed_order(fund_db, sim_clock, ticker, status, filled_qty, price):
    """A decision + its ticket + the broker's answer, the way a day that
    actually traded leaves them."""
    _seed_decision(fund_db, sim_clock, ticker, "buy", 80)
    decision_id = fund_db.execute(
        "SELECT id FROM decisions WHERE ticker = ?", (ticker,)).fetchone()["id"]
    now = iso(sim_clock.now())
    fund_db.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " expires_at, status, created_at) VALUES (?,?,?,'buy',66,?,'open',?)",
        (TID, decision_id, ticker, now, now))
    fund_db.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " filled_qty, filled_avg_price, submitted_at)"
        " VALUES (?, ?, 'buy', 66, ?, ?, ?, ?)",
        (TID, ticker, status, filled_qty, price, now))
    fund_db.commit()


def _digest_text(fund_db) -> str:
    return _digest_payload(fund_db)["text"]


def _digest_payload(fund_db) -> dict:
    return json.loads(fund_db.execute(
        "SELECT payload FROM events WHERE kind='digest'"
        ).fetchone()["payload"])


def test_the_digest_event_carries_structured_decisions_and_fills(
        fund_db, sim_clock, tmp_path):
    """render.py cannot lay out a digest it has to parse back out of prose
    (and must not query the DB — invariant 6), so run_close emits the rows
    alongside the text it already composed."""
    _seed_order(fund_db, sim_clock, "NVDA", "filled", 66, 180.14)
    run_close(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
                   journals_root=tmp_path / "journals"))
    payload = _digest_payload(fund_db)
    assert payload["decisions"] == [
        {"ticker": "NVDA", "action": "buy", "qty": 80, "status": "submitted"}]
    assert payload["fills"] == [
        {"symbol": "NVDA", "side": "buy", "filled_qty": 66,
         "filled_avg_price": 180.14, "partial": False}]
    assert payload["cost_usd"] == 0
    # the flat text is still there: it is the Slack notification fallback and
    # what pre-Block-Kit rows carry
    assert payload["text"] == _digest_text(fund_db)


def test_a_partially_filled_order_is_flagged_in_the_structured_fills(
        fund_db, sim_clock, tmp_path):
    _seed_order(fund_db, sim_clock, "NVDA", "partially_filled", 30, 180.14)
    run_close(_ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()},
                   journals_root=tmp_path / "journals"))
    assert _digest_payload(fund_db)["fills"][0]["partial"] is True


def test_close_digest_marks_a_partial_fill(fund_db, sim_clock, tmp_path):
    """Fix 7: a partially_filled order moved REAL shares, but the fill line
    filtered on status='filled' alone, so the digest read `fills: none` — and
    HANDOFF-LIVE §5 now cites that digest as acceptance evidence, so a digest
    that omits a real fill is a truthfulness problem, not a cosmetic one."""
    _seed_order(fund_db, sim_clock, "NVDA", "partially_filled", 20, 180.14)
    run_close(_ctx(fund_db, sim_clock, {}, journals_root=tmp_path / "journals"))
    text = _digest_text(fund_db)
    assert "fills: NVDA buy 20@180.14 (partial)" in text
    assert "fills: none" not in text


def test_close_digest_leaves_a_complete_fill_unmarked(fund_db, sim_clock, tmp_path):
    """The other half: 'partial' must mean something, so a full fill never
    carries it."""
    _seed_order(fund_db, sim_clock, "NVDA", "filled", 66, 180.14)
    run_close(_ctx(fund_db, sim_clock, {}, journals_root=tmp_path / "journals"))
    text = _digest_text(fund_db)
    assert "fills: NVDA buy 66@180.14" in text
    assert "partial" not in text


def test_close_rerun_posts_one_digest(fund_db, sim_clock, tmp_path):
    root = tmp_path / "journals"
    ctx = _ctx(fund_db, sim_clock, {}, journals_root=root)
    run_close(ctx)
    run_close(ctx)
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='digest'"
                           ).fetchone()["c"] == 1
    assert (root / "pm.md").read_text().count(RUN) == 1


# --- run_day ----------------------------------------------------------------

def test_full_hold_day_completes_every_stage(fund_db, sim_clock, tmp_path):
    """No turns wired at all: every stage reaches 'done', the digest still
    posts, and nothing is placed (invariant 4)."""
    slack = FakeSlack()
    ctx = StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock, slack=slack,
                   research_seats=("analyst",),
                   market_inputs={"NVDA": _nvda_inputs()}, run_turn={},
                   id_factory=lambda: TID, journals_root=tmp_path / "journals")
    run_day(ctx, execution_turn=None, broker=None, sleep=lambda s: None)
    stages = dict(fund_db.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date=?", (RUN,)).fetchall())
    assert set(stages) == {"pre_gate", "research", "decision", "gate",
                           "execution", "reconciliation", "close"}
    assert set(stages.values()) == {"done"}
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "held"
    assert fund_db.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0
    assert len(slack.posts["#pnl"]) == 1                     # digest posted
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE posted_at IS NULL"
                           ).fetchone()["c"] == 0


def test_full_hold_day_is_rerunnable(fund_db, sim_clock, tmp_path):
    """A re-fire of the whole day is a no-op: done stages never re-run."""
    def day():
        ctx = StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock,
                       slack=FakeSlack(), research_seats=("analyst",),
                       market_inputs={"NVDA": _nvda_inputs()},
                       run_turn={}, id_factory=lambda: TID,
                       journals_root=tmp_path / "journals")
        run_day(ctx, execution_turn=None, broker=None, sleep=lambda s: None)
        return ctx

    day()
    second = day()
    assert sum(len(v) for v in second.slack.posts.values()) == 0   # nothing new
    counts = {t: fund_db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
              for t in ("signals", "critiques", "decisions", "tickets", "events")}
    assert counts == {"signals": 1, "critiques": 1, "decisions": 1,
                      "tickets": 0, "events": 2}   # pm_timeout alert + digest


def test_execution_turn_skipped_when_no_open_tickets(fund_db, sim_clock, tmp_path):
    calls = []
    ctx = StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock, slack=FakeSlack(),
                   research_seats=("analyst",),
                   market_inputs={"NVDA": _nvda_inputs()}, run_turn={},
                   id_factory=lambda: TID, journals_root=tmp_path / "journals")
    run_day(ctx, execution_turn=lambda: calls.append(1), broker=None,
            sleep=lambda s: None)
    assert calls == []
    assert fund_db.execute("SELECT status FROM checkpoints WHERE stage='execution'"
                           ).fetchone()["status"] == "done"
    # a hold day has nothing open, so it must not raise the no-order alert
    assert not any("after exec turn" in t for t in _alert_texts(fund_db))


# --- execution: the silent no-order day (D2) ---------------------------------

def _alert_texts(fund_db) -> list[str]:
    return [json.loads(r["payload"])["text"] for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id").fetchall()]


def _open_ticket(fund_db, sim_clock, ticker="NVDA", tid=TID):
    """An approved decision + its live (unexpired) ticket, exactly as run_gate
    leaves them just before the trader turn."""
    _seed_decision(fund_db, sim_clock, ticker, "buy", 80)
    decision_id = fund_db.execute(
        "SELECT id FROM decisions WHERE ticker = ?", (ticker,)).fetchone()["id"]
    now = iso(sim_clock.now())
    fund_db.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " expires_at, status, created_at) VALUES (?,?,?,'buy',66,?,'open',?)",
        (tid, decision_id, ticker, iso(sim_clock.now() + timedelta(minutes=45)),
         now))
    fund_db.commit()
    return tid


def test_execution_alerts_when_the_turn_placed_no_order(fund_db, sim_clock):
    """2026-08-16 live day: the gate approved NVDA, the trader burned a turn,
    NO order was placed, and the stage still checkpointed 'done' — the day
    read as a success. The stage must still complete (default HOLD is a valid
    outcome, not a failed stage); the alert is the signal."""
    _open_ticket(fund_db, sim_clock)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    assert run_execution(ctx, lambda: None) == "done"      # turn does nothing
    assert _alert_texts(fund_db) == [
        f"ticket {TID[:8]} open after exec turn — no order"]
    assert fund_db.execute("SELECT status FROM checkpoints WHERE stage='execution'"
                           ).fetchone()["status"] == "done"
    posts = ctx.slack.posts["#risk"]         # projected to #risk, drained once
    assert len(posts) == 1
    # the alert's wording is owned by test_slackkit; here it only has to carry
    # the alert text the stage wrote
    assert f"ticket {TID[:8]} open after exec turn — no order" in posts[0]["text"]


def test_execution_stays_silent_when_the_ticket_has_an_order(fund_db, sim_clock):
    """The healthy path: an order row keyed by the ticket id (invariant 5)
    means the turn did its job — no alert, even if the ticket is still open."""
    _open_ticket(fund_db, sim_clock)
    fund_db.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " submitted_at) VALUES (?, 'NVDA', 'buy', 66, 'submitted', ?)",
        (TID, iso(sim_clock.now())))
    fund_db.commit()
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    assert run_execution(ctx, lambda: None) == "done"
    assert _alert_texts(fund_db) == []


def test_execution_alert_is_not_reposted_on_a_resume(fund_db, sim_clock):
    """The stage is 'done' after the first pass, so a re-fire must not append
    a second copy of the alert."""
    _open_ticket(fund_db, sim_clock)
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    run_execution(ctx, lambda: None)
    run_execution(ctx, lambda: None)
    assert len(_alert_texts(fund_db)) == 1
