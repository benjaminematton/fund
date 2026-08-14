import pytest

from agents.tools.fund_server import (handle_get_stage_brief,
                                      handle_submit_decision,
                                      handle_submit_signal,
                                      insert_default_critiques)
from orchestrator.clock import iso
from state.journal import append_entry
from state.transition import transition

RUN = "2026-07-06"

SNAPSHOT = {"cash": 30000.0, "positions": {"MSFT": 40},
            "allowed_actions": {"NVDA": {"buy": 66, "sell": 0},
                                "MSFT": {"buy": 0, "sell": 40}}}

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


# --- get_stage_brief: the read half of the seam ------------------------------

def _brief(fund_db, seat="pm", snapshot=SNAPSHOT, journals_root=None):
    result = handle_get_stage_brief(
        fund_db, seat=seat, run_date=RUN,
        snapshot=None if snapshot is None else (lambda: snapshot),
        journals_root=journals_root)
    assert result["ok"], result.get("error")
    return result["brief"]


@pytest.mark.parametrize("seat", ["exec", "critic", ""])
def test_brief_is_analyst_and_pm_only(fund_db, seat):
    """The exec seat is the only one that can trade; it acts on gate tickets
    alone and must not gain a read channel into the day's thinking."""
    result = handle_get_stage_brief(fund_db, seat=seat, run_date=RUN,
                                    snapshot=lambda: SNAPSHOT)
    assert not result["ok"] and "analyst/pm-only" in result["error"]
    assert "brief" not in result


def test_analyst_brief_is_the_book_and_its_own_journal(tmp_path, fund_db):
    """Seat-scoped by construction: the analyst gets account context, never
    the PM's signal table or the gate's sizing budget."""
    append_entry(tmp_path, "analyst", "2026-07-02", "signals: NVDA bullish (61/100)")
    brief = _brief(fund_db, seat="analyst", journals_root=tmp_path)
    assert (brief["run_date"], brief["seat"]) == (RUN, "analyst")
    assert (brief["cash"], brief["positions"]) == (30000.0, {"MSFT": 40})
    assert "NVDA bullish (61/100)" in brief["journal"]
    assert "signals" not in brief and "allowed_actions" not in brief
    assert brief["unavailable"] == []


def test_pm_brief_adds_todays_signals_and_the_gate_budget(tmp_path, fund_db,
                                                          sim_clock):
    _sig(fund_db, sim_clock, summary="capex re-accelerating")
    brief = _brief(fund_db, journals_root=tmp_path)
    assert brief["signals"] == [{"agent": "analyst", "ticker": "NVDA",
                                 "direction": "bullish", "confidence": 72,
                                 "summary": "capex re-accelerating"}]
    assert brief["allowed_actions"] == SNAPSHOT["allowed_actions"]
    assert brief["unavailable"] == []


def test_brief_journal_is_scoped_to_the_calling_seat(tmp_path, fund_db):
    append_entry(tmp_path, "analyst", "2026-07-02", "analyst-only line")
    append_entry(tmp_path, "pm", "2026-07-02", "pm-only line")
    assert "pm-only" not in _brief(fund_db, seat="analyst",
                                   journals_root=tmp_path)["journal"]
    assert "analyst-only" not in _brief(fund_db, journals_root=tmp_path)["journal"]


def test_brief_only_shows_todays_signals(fund_db, sim_clock):
    _sig(fund_db, sim_clock)
    fund_db.execute(
        "INSERT INTO signals (run_date,agent,ticker,direction,confidence,"
        "summary,created_at) VALUES ('2026-07-03','analyst','NVDA','bearish',"
        "10,'stale',?)", (iso(sim_clock.now()),))
    assert [s["direction"] for s in _brief(fund_db)["signals"]] == ["bullish"]


def test_a_broken_snapshot_provider_degrades_to_hold_not_a_crash(fund_db):
    """Invariant 4. A provider that raises must not take the day down and must
    not silently look like a normal day: every affected section is NAMED in
    `unavailable`, and allowed_actions comes back empty — "nothing is possible
    today", which the PM's charter resolves to HOLD."""
    def boom():
        raise ConnectionError("alpaca account read failed")

    result = handle_get_stage_brief(fund_db, seat="pm", run_date=RUN,
                                    snapshot=boom)
    assert result["ok"]                          # never an exception, never a hole
    brief = result["brief"]
    assert brief["allowed_actions"] == {}
    assert (brief["cash"], brief["positions"]) == (None, {})
    assert any("account snapshot" in m and "ConnectionError" in m
               for m in brief["unavailable"])


def test_unbound_providers_are_named_not_faked(fund_db):
    """A wiring bug (no snapshot, no journals root) reads as missing evidence,
    not as an empty book and an empty journal."""
    brief = _brief(fund_db, snapshot=None)
    assert brief["allowed_actions"] == {} and brief["journal"] == ""
    assert [m.split(" (")[0] for m in brief["unavailable"]] == [
        "account snapshot", "journal", "allowed actions"]


def test_brief_writes_nothing(fund_db, sim_clock):
    """It is a READ tool: no rows, no projection events, no state change."""
    _sig(fund_db, sim_clock)
    before = [fund_db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
              for t in ("signals", "decisions", "events", "tickets")]
    _brief(fund_db)
    _brief(fund_db, seat="analyst")
    after = [fund_db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
             for t in ("signals", "decisions", "events", "tickets")]
    assert before == after


def test_default_critiques_idempotent(fund_db, sim_clock):
    now = iso(sim_clock.now())
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat", now)
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat", now)
    assert fund_db.execute("SELECT COUNT(*) c FROM critiques").fetchone()["c"] == 1


# --- the @tool wrappers themselves ------------------------------------------
#
# Everything above (and tests/conftest.py's make_executor, which the whole
# replay suite runs on) calls the handle_* functions DIRECTLY. That bypasses
# build_fund_server's @tool wrappers entirely, leaving three things untested:
# the is_error refusal envelope, the run_date_from_clock(clock) wiring, and
# tools_by_seat. Inverting `if not result["ok"]` would report a REFUSED
# decision to the model as recorded; adding submit_decision to the analyst's
# list broke no test. These drive the registered MCP surface instead.

def _server(conn, clock, seat):
    from agents.tools.fund_server import build_fund_server
    return build_fund_server(lambda: conn, clock, seat)["instance"]


def _tool_names(conn, clock, seat) -> set[str]:
    import asyncio

    import mcp.types as mcp

    handler = _server(conn, clock, seat).request_handlers[mcp.ListToolsRequest]
    result = asyncio.run(handler(mcp.ListToolsRequest(method="tools/list")))
    return {t.name for t in result.root.tools}


def _call(conn, clock, seat, name, args):
    """One tool call through the registered MCP surface — the same path a live
    seat's call takes, wrappers included."""
    import asyncio

    import mcp.types as mcp

    handler = _server(conn, clock, seat).request_handlers[mcp.CallToolRequest]
    request = mcp.CallToolRequest(
        method="tools/call",
        params=mcp.CallToolRequestParams(name=name, arguments=args))
    return asyncio.run(handler(request)).root


SIGNAL_ARGS = {"ticker": "NVDA", "direction": "bullish", "confidence": 72,
               "summary": "s"}


def test_tools_by_seat_is_exactly_what_each_seat_owns(fund_db, sim_clock):
    """The registered tool list IS the seat's write surface. get_stage_brief on
    the exec seat would widen the read surface of the only seat that can trade
    (invariant 2); submit_decision on the analyst would let the analyst decide."""
    assert _tool_names(fund_db, sim_clock, "analyst") == {
        "get_stage_brief", "submit_signal"}
    assert _tool_names(fund_db, sim_clock, "pm") == {
        "get_stage_brief", "submit_decision"}
    assert _tool_names(fund_db, sim_clock, "exec") == {"list_open_tickets"}


def test_an_unrecognized_seat_is_a_hard_stop_not_a_toolless_seat(fund_db,
                                                                 sim_clock):
    """A silently toolless seat is an analyst that never records a signal all
    day — a full-HOLD day nobody ordered."""
    with pytest.raises(ValueError, match="unrecognized seat"):
        _server(fund_db, sim_clock, "critic")


def test_a_refused_call_comes_back_as_is_error_through_the_wrapper(
        fund_db, sim_clock, monkeypatch):
    """The refusal envelope. Flipping `if not result["ok"]` would hand the
    model "signal recorded: NVDA" for a call that wrote nothing — the seat
    would believe its whole turn landed and stop retrying.

    Reached by moving the seat table under a live analyst server rather than
    by building a mis-seated one: build_fund_server refuses unknown seats, and
    tools_by_seat (pinned above) is what normally keeps this branch out of
    reach. This is the shape it takes the moment either of those regresses.
    """
    from agents.tools import fund_server

    monkeypatch.setattr(fund_server, "SIGNAL_SEATS", ("pm",))
    result = _call(fund_db, sim_clock, "analyst", "submit_signal", SIGNAL_ARGS)

    assert result.isError is True
    assert "analyst-seat-only" in result.content[0].text
    assert result.content[0].text.startswith("error: ")
    assert fund_db.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0


def test_a_refused_decision_is_never_reported_as_recorded(fund_db, sim_clock):
    """The same envelope on the path where it actually bites in production:
    submit_decision before any critique row exists."""
    result = _call(fund_db, sim_clock, "pm", "submit_decision",
                   {"ticker": "NVDA", "action": "buy", "qty": 80,
                    "thesis": "t", "invalidation": "i"})
    assert result.isError is True
    assert "no critique row" in result.content[0].text
    assert "decision recorded" not in result.content[0].text
    assert fund_db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 0


def test_the_wrapper_stamps_the_run_date_from_the_injected_clock(fund_db,
                                                                 sim_clock):
    """run_date is the DB key every stage joins on. The wrapper takes it from
    the bound clock (11:30 ET on the golden day), never from the agent — a
    wrapper reading the wall clock would key rows to the wrong day and break
    replay."""
    result = _call(fund_db, sim_clock, "analyst", "submit_signal", SIGNAL_ARGS)
    assert result.isError is False
    assert result.content[0].text == "signal recorded: NVDA"
    assert fund_db.execute("SELECT run_date FROM signals").fetchone()[
        "run_date"] == RUN
