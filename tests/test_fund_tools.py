from agents.tools.fund_server import handle_submit_signal, handle_submit_decision, insert_default_critiques
from orchestrator.clock import iso
from state.transition import transition

RUN = "2026-07-06"

def _sig(fund_db, sim_clock, seat="analyst", **over):
    args = dict(ticker="NVDA", direction="bullish", confidence=72, summary="s")
    args.update(over)
    return handle_submit_signal(fund_db, seat=seat, args=args,
                                run_date=RUN, now_iso=iso(sim_clock.now()))

def _dec(fund_db, sim_clock, seat="pm", **over):
    args = dict(ticker="NVDA", action="buy", qty=80, thesis="t", invalidation="i")
    args.update(over)
    return handle_submit_decision(fund_db, seat=seat, args=args,
                                  run_date=RUN, now_iso=iso(sim_clock.now()))

def test_signal_upserts_and_projects(fund_db, sim_clock):
    assert _sig(fund_db, sim_clock)["ok"]
    _sig(fund_db, sim_clock, confidence=61)          # re-submit overwrites
    rows = fund_db.execute("SELECT * FROM signals").fetchall()
    assert len(rows) == 1 and rows[0]["confidence"] == 61
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='signal'"
                           ).fetchone()["c"] == 2

def test_signal_seat_restricted_and_schema_enforced(fund_db, sim_clock):
    assert not _sig(fund_db, sim_clock, seat="pm")["ok"]          # wrong seat
    assert not _sig(fund_db, sim_clock, confidence=101)["ok"]     # invalid
    assert fund_db.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='signal'"
                           ).fetchone()["c"] == 0

def test_decision_requires_critique_row(fund_db, sim_clock):
    assert not _dec(fund_db, sim_clock)["ok"]                     # no critique yet
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat",
                             iso(sim_clock.now()))
    assert _dec(fund_db, sim_clock)["ok"]
    d = fund_db.execute("SELECT * FROM decisions").fetchone()
    assert d["action"] == "buy" and d["qty"] == 80 and d["status"] == "submitted"

def test_decision_seat_restricted_hold_zero_enforced(fund_db, sim_clock):
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat",
                             iso(sim_clock.now()))
    assert not _dec(fund_db, sim_clock, seat="analyst")["ok"]        # wrong seat
    assert fund_db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='decision'"
                           ).fetchone()["c"] == 0
    assert not _dec(fund_db, sim_clock, action="hold", qty=5)["ok"]  # hold!=0
    assert fund_db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='decision'"
                           ).fetchone()["c"] == 0

def test_decision_refused_once_left_submitted(fund_db, sim_clock):
    """contracts §4 ruling 2026-08-13: submit_decision is irrevocable once the
    decision has left 'submitted'. A retry with DIFFERENT qty/action/thesis
    must be refused outright (is_error, message names the current status) and
    must not partially update the row — a mutable thesis/qty behind a live
    ticket would rewrite the audit trail the gate approved against."""
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat",
                             iso(sim_clock.now()))
    assert _dec(fund_db, sim_clock)["ok"]
    row = fund_db.execute(
        "SELECT id FROM decisions WHERE run_date = ? AND ticker = ?",
        (RUN, "NVDA")).fetchone()
    transition(fund_db, "decisions", {"id": row["id"]}, "submitted",
              "approved", iso(sim_clock.now()))
    events_before = fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='decision'").fetchone()["c"]
    result = _dec(fund_db, sim_clock, action="sell", qty=5, thesis="different")
    assert not result["ok"]
    assert "approved" in result["error"]
    after = fund_db.execute(
        "SELECT status, action, qty, thesis FROM decisions WHERE id = ?",
        (row["id"],)).fetchone()
    assert after["status"] == "approved"            # must NOT have reverted
    assert after["action"] == "buy"                 # byte-identical: not overwritten
    assert after["qty"] == 80
    assert after["thesis"] == "t"
    events_after = fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='decision'").fetchone()["c"]
    assert events_after == events_before             # no new event appended


def test_default_critiques_idempotent(fund_db, sim_clock):
    now = iso(sim_clock.now())
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat", now)
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat", now)
    assert fund_db.execute("SELECT COUNT(*) c FROM critiques").fetchone()["c"] == 1
