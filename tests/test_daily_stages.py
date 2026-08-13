"""Stage bodies in isolation (MVF P4). Full-day sims are Task 15."""

from datetime import timedelta

from orchestrator.clock import iso
from orchestrator.daily import (StageCtx, run_close, run_day, run_decision,
                                run_gate, run_pre_gate, run_research)
from slackkit.fake import FakeSlack

RUN = "2026-07-06"
TID = "a3f90000-0000-0000-0000-000000000000"


def _ctx(fund_db, sim_clock, market, turns=None, journals_root=None):
    """market: {ticker: gate-input dict (pre-validated by risk later)}."""
    return StageCtx(conn=fund_db, run_date=RUN, clock=sim_clock,
                    slack=FakeSlack(), market_inputs=market,
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
                       slack=FakeSlack(), market_inputs={"NVDA": _nvda_inputs()},
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
                   market_inputs={"NVDA": _nvda_inputs()}, run_turn={},
                   id_factory=lambda: TID, journals_root=tmp_path / "journals")
    run_day(ctx, execution_turn=lambda: calls.append(1), broker=None,
            sleep=lambda s: None)
    assert calls == []
    assert fund_db.execute("SELECT status FROM checkpoints WHERE stage='execution'"
                           ).fetchone()["status"] == "done"
