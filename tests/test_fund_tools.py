import json

import pytest

from agents.tools.fund_server import (SEAT_CAPS, _can,
                                      handle_get_stage_brief,
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

def test_decision_event_names_the_seat_that_submitted_it(fund_db, sim_clock):
    """The Slack projection attributes the post from this field. It used to
    assume the PM, which mis-attributes silently the moment a second seat can
    submit."""
    insert_default_critiques(fund_db, RUN, ["NVDA"], "no_critic_seat",
                             iso(sim_clock.now()))
    assert _dec(fund_db, sim_clock)["ok"]
    payload = json.loads(fund_db.execute(
        "SELECT payload FROM events WHERE kind='decision'").fetchone()["payload"])
    assert payload["seat"] == "pm"

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
def test_brief_is_refused_to_seats_without_the_capability(fund_db, seat):
    """The exec seat is the only one that can trade; it acts on gate tickets
    alone and must not gain a read channel into the day's thinking. `critic`
    stays here deliberately — a Critic seat wants get_spec_brief, not this."""
    result = handle_get_stage_brief(fund_db, seat=seat, run_date=RUN,
                                    snapshot=lambda: SNAPSHOT)
    assert not result["ok"]
    assert result["error"] == f"get_stage_brief is not granted to seat {seat!r}"
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


def _handlers(server):
    """(list_tools, call_tool) as plain awaitables, across mcp 1.x and 2.x.

    mcp 2.0 replaced the `request_handlers` dict — keyed by request type, handler
    taking a whole request and returning a ServerResult wrapper — with
    `get_request_handler(method)`, whose handler takes (ctx, params) and returns
    the result directly. These tests pin the registered MCP surface, so they have
    to reach it whichever way the installed mcp exposes it. Production never
    touches either: it hands the instance to the SDK.
    """
    import mcp.types as mcp

    if hasattr(server, "get_request_handler"):              # mcp >= 2.0
        listing = server.get_request_handler("tools/list")
        calling = server.get_request_handler("tools/call")

        async def list_tools():
            return await listing.handler(None, None)

        async def call_tool(name, args):
            return await calling.handler(
                None, calling.params_type(name=name, arguments=args))
    else:                                                    # mcp 1.x
        listing = server.request_handlers[mcp.ListToolsRequest]
        calling = server.request_handlers[mcp.CallToolRequest]

        async def list_tools():
            req = mcp.ListToolsRequest(method="tools/list")
            return (await listing(req)).root

        async def call_tool(name, args):
            req = mcp.CallToolRequest(
                method="tools/call",
                params=mcp.CallToolRequestParams(name=name, arguments=args))
            return (await calling(req)).root

    return list_tools, call_tool


def _is_error(result) -> bool:
    """mcp 2.0 renamed CallToolResult.isError to is_error."""
    return result.is_error if hasattr(result, "is_error") else result.isError


def _tool_names(conn, clock, seat) -> set[str]:
    import asyncio

    list_tools, _ = _handlers(_server(conn, clock, seat))
    return {t.name for t in asyncio.run(list_tools()).tools}


def _call(conn, clock, seat, name, args):
    """One tool call through the registered MCP surface — the same path a live
    seat's call takes, wrappers included."""
    import asyncio

    _, call_tool = _handlers(_server(conn, clock, seat))
    return asyncio.run(call_tool(name, args))


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
    assert _tool_names(fund_db, sim_clock, "critic") == {
        "get_spec_brief", "submit_spec_critique"}


def test_an_unrecognized_seat_is_a_hard_stop_not_a_toolless_seat(fund_db,
                                                                 sim_clock):
    """A silently toolless seat is an analyst that never records a signal all
    day — a full-HOLD day nobody ordered. `quant` is the live near-miss: a
    real charter (charters/quant.md) with no entry in SEAT_CAPS. This used to
    use `critic`, which stopped testing anything the day the Critic seat was
    added."""
    with pytest.raises(ValueError, match="unrecognized seat"):
        _server(fund_db, sim_clock, "quant")


def test_a_refused_call_comes_back_as_is_error_through_the_wrapper(
        fund_db, sim_clock, monkeypatch):
    """The refusal envelope. Flipping `if not result["ok"]` would hand the
    model "signal recorded: NVDA" for a call that wrote nothing — the seat
    would believe its whole turn landed and stop retrying.

    Reached by revoking the capability under a live analyst server rather than
    by building a mis-seated one: build_fund_server refuses unknown seats, and
    SEAT_CAPS (pinned above) is what normally keeps this branch out of reach.
    This is the shape it takes the moment either of those regresses.
    """
    import asyncio

    from agents.tools import fund_server

    # Build the server while the analyst still HOLDS submit_signal so the tool
    # is registered, and only then revoke it. Tool registration is derived from
    # SEAT_CAPS (ADR-0002), so revoking first unregisters the tool and the call
    # dies at the MCP layer ("Tool 'submit_signal' not found") without ever
    # reaching the handler guard this test exists to pin.
    _, call_tool = _handlers(_server(fund_db, sim_clock, "analyst"))
    monkeypatch.setitem(fund_server.SEAT_CAPS, "analyst",
                        frozenset({"get_stage_brief", "read_account"}))
    result = asyncio.run(call_tool("submit_signal", SIGNAL_ARGS))

    assert _is_error(result) is True
    assert "submit_signal is not granted to seat 'analyst'" in result.content[0].text
    assert result.content[0].text.startswith("error: ")
    assert fund_db.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0


def test_a_refused_decision_is_never_reported_as_recorded(fund_db, sim_clock):
    """The same envelope on the path where it actually bites in production:
    submit_decision before any critique row exists."""
    result = _call(fund_db, sim_clock, "pm", "submit_decision",
                   {"ticker": "NVDA", "action": "buy", "qty": 80,
                    "thesis": "t", "invalidation": "i"})
    assert _is_error(result) is True
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
    assert _is_error(result) is False
    assert result.content[0].text == "signal recorded: NVDA"
    assert fund_db.execute("SELECT run_date FROM signals").fetchone()[
        "run_date"] == RUN


def _reflectable_decision(conn, sim_clock, ticker="NVDA"):
    now = iso(sim_clock.now())
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06',?,'buy',96,'t','i','executed',?)", (ticker, now))
    did = cur.lastrowid
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at)"
        " VALUES (?, 5, 0.0614, 0.0504, 0, ?)", (did, now))
    conn.commit()
    return did


def test_submit_reflection_wrapper_refuses_an_unbound_turn(fund_db,
                                                            sim_clock):
    """build_fund_server's submit_reflection wrapper must forward
    expected_decision_id to the handler. The schema carries no decision_id
    argument any more (change A: the seat passes only prose), so an unbound
    turn — the default — is the only way a wrapper-level call can be refused
    for the wrong row: there is no id left in `args` to get wrong."""
    import asyncio

    from agents.tools.fund_server import build_fund_server

    did = _reflectable_decision(fund_db, sim_clock)

    server = build_fund_server(lambda: fund_db, sim_clock,
                               "reflect")["instance"]
    _, call_tool = _handlers(server)
    result = asyncio.run(call_tool("submit_reflection", {"prose": "noted"}))

    assert _is_error(result) is True
    assert fund_db.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (did,)).fetchone()["reflection"] is None


def test_submit_reflection_wrapper_writes_the_row_it_is_bound_to(fund_db,
                                                                  sim_clock):
    """The write half of the same seam: a server built bound to one decision
    writes a prose-only call to exactly that row."""
    import asyncio

    from agents.tools.fund_server import build_fund_server

    did = _reflectable_decision(fund_db, sim_clock)

    server = build_fund_server(lambda: fund_db, sim_clock, "reflect",
                               expected_decision_id=did)["instance"]
    _, call_tool = _handlers(server)
    result = asyncio.run(call_tool("submit_reflection", {"prose": "noted"}))

    assert _is_error(result) is False
    # N3: charters/reflect.md tells the seat it is never told a decision id —
    # the success message must not hand the surrogate id back, or a per-run
    # identifier re-enters the seat's transcript (replay determinism).
    assert result.content[0].text == "reflection recorded"
    assert str(did) not in result.content[0].text
    assert fund_db.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (did,)).fetchone()["reflection"].endswith("noted")


def test_build_seat_options_threads_the_bound_id_to_the_constructed_server(
        tmp_path, sim_clock):
    """The leg above the wrapper: build_seat_options must forward
    expected_decision_id into build_fund_server's constructed server, not
    just accept it and drop it. Built through the real composition root
    (agents.seats.build_seat_options) against a real on-disk db — this is
    the leg the wrapper-level tests above cannot see, since they call
    build_fund_server directly."""
    import asyncio

    from agents.seats import build_seat_options, load_seat_config
    from state.db import connect

    db_path = tmp_path / "fund.sqlite"
    conn = connect(db_path)
    did = _reflectable_decision(conn, sim_clock)
    conn.close()

    cfg = load_seat_config("agents/config/reflect.yaml")
    options = build_seat_options(cfg, db_path, sim_clock,
                                 expected_decision_id=did)
    server = options.mcp_servers["fund"]["instance"]
    _, call_tool = _handlers(server)
    result = asyncio.run(call_tool("submit_reflection", {"prose": "noted"}))

    assert _is_error(result) is False
    conn = connect(db_path)
    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (did,)).fetchone()["reflection"]
    conn.close()
    assert stored.endswith("noted")


# --- seat capability table (ADR-0002) ---------------------------------------

def test_news_seat_can_signal_and_brief_but_not_see_the_book():
    """design.md §2 grants News/Sentiment `news,stock-data` -- no `account`.
    Its capabilities must match the toolset the seat table grants it."""
    assert _can("news", "submit_signal") and _can("news", "get_stage_brief")
    assert not _can("news", "read_account")
    assert not _can("news", "submit_decision")


def test_every_registered_seat_has_at_least_one_capability():
    """A seat with no caps gets no tools -- the silent failure the
    unrecognized-seat ValueError exists to prevent."""
    assert all(caps for caps in SEAT_CAPS.values())


def test_tool_caps_are_real_registered_tool_names(fund_db, sim_clock):
    """The naming rule, asserted rather than intended: every cap not starting
    with read_ IS a registered tool name. Catches a typo'd cap at test time
    instead of at seat-build time on a live host."""
    for seat, caps in SEAT_CAPS.items():
        expected = {c for c in caps if not c.startswith("read_")}
        assert _tool_names(fund_db, sim_clock, seat) == expected, seat


def test_seat_caps_covers_every_config_file():
    """A yaml seat missing from SEAT_CAPS raises only when that seat is BUILT
    -- which may be 09:00 on a live host. Subset, not equality: caps without a
    config is a dead entry nothing can build, and equality would couple this
    file's commit boundaries to unrelated branches for no safety gain."""
    import pathlib as _pl

    import yaml
    root = _pl.Path(__file__).resolve().parents[1] / "agents" / "config"
    configs = {yaml.safe_load(p.read_text())["seat"] for p in root.glob("*.yaml")}
    assert configs <= set(SEAT_CAPS), f"config seats with no caps: {configs - set(SEAT_CAPS)}"


def test_every_seat_config_declares_a_model():
    """Pins the unwritten invariant that keeps a live footgun shut.

    `evals/runner.py` passes `configured_model=cfg.get("model", "")`, and
    `_unmatched_models` returns [] on an empty configured model (deliberately
    -- a caller that cannot name the seat's model must not manufacture a
    divergence against the empty string, which every key would 'mismatch').

    Those two are safe together only because every file in agents/config/
    happens to declare `model`. Nothing pinned that. Add a sixth seat whose
    yaml omits it -- entirely plausible for a seat meant to inherit a default
    -- and fallback detection is silently off for that seat IN THE RIG THAT
    JUDGES IT: a Haiku turn would score as though Sonnet produced it, with no
    error, no empty result, and nothing in the trace to notice.

    Pinned here rather than fixed at the call site, because the `""` default
    is correct and the missing declaration is the actual defect. Enumerates
    the directory so the seat that would break it reddens on the commit that
    adds it, not on the eval run that silently mis-scores it."""
    import pathlib as _pl

    import yaml
    root = _pl.Path(__file__).resolve().parents[1] / "agents" / "config"
    missing = [p.name for p in root.glob("*.yaml")
               if not (yaml.safe_load(p.read_text()) or {}).get("model")]
    assert missing == [], f"seat configs with no model: {missing}"


def test_news_brief_omits_the_book_while_the_analyst_keeps_it(fund_db, tmp_path):
    """Behavior, not the lookup table: the capability-gating rewrite is what
    could get this wrong, and asserting _can() against itself would not."""
    snap = lambda: SNAPSHOT
    news = handle_get_stage_brief(fund_db, seat="news", run_date=RUN,
                                  snapshot=snap, journals_root=tmp_path)["brief"]
    analyst = handle_get_stage_brief(fund_db, seat="analyst", run_date=RUN,
                                     snapshot=snap, journals_root=tmp_path)["brief"]
    assert "cash" not in news and "positions" not in news
    assert "journal" in news                      # calibration loop still reaches it
    assert analyst["cash"] == 30000.0 and analyst["positions"] == {"MSFT": 40}
