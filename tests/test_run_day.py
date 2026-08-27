"""Offline tests for the live composition root's decision seams (MVF T16).

scripts/run_day.py is the only place real clocks/Slack/Alpaca/LLM sessions are
constructed, so most of it can only be exercised live. What CAN be tested
offline is every place it decides something — the paper guard, env fail-fast,
the market-closed guard, the channel remap, and the committed watchlist — and
those are exactly the places a wrong answer trades against a shut market, an
unfunded account, or the wrong Slack channel.

Never calls main(): that builds real clients.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from orchestrator.clock import SimClock
from slackkit.fake import FakeSlack
from state.db import connect

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_day.py"
START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("run_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_day_script = _load()


# --- invariant 1: paper only -------------------------------------------------

@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_paper_guard_accepts_only_true(value):
    run_day_script.paper_guard({"ALPACA_PAPER_TRADE": value})


@pytest.mark.parametrize("value", ["false", "", "1", "yes", "paper"])
def test_paper_guard_refuses_anything_else(value):
    with pytest.raises(SystemExit) as e:
        run_day_script.paper_guard({"ALPACA_PAPER_TRADE": value})
    assert "invariant 1" in str(e.value)


def test_paper_guard_refuses_when_unset():
    """An unset var must stop the day, never default to trading."""
    with pytest.raises(SystemExit):
        run_day_script.paper_guard({})


# --- fail fast on env --------------------------------------------------------

def test_require_env_names_every_missing_var_at_once():
    with pytest.raises(SystemExit) as e:
        run_day_script.require_env(("FUND_DB", "SLACK_BOT_TOKEN", "ALPACA_API_KEY"),
                                   {"ALPACA_API_KEY": "PK1"})
    message = str(e.value)
    assert "FUND_DB" in message and "SLACK_BOT_TOKEN" in message
    assert "ALPACA_API_KEY" not in message
    assert ".env" in message


def test_require_env_treats_blank_as_missing_and_strips():
    with pytest.raises(SystemExit):
        run_day_script.require_env(("FUND_DB",), {"FUND_DB": "   "})
    assert run_day_script.require_env(
        ("FUND_DB",), {"FUND_DB": " state/fund.sqlite "}) == {
        "FUND_DB": "state/fund.sqlite"}


def test_required_env_covers_every_live_dependency():
    """A var the script reads but does not require would fail obscurely deep
    in a client constructor instead of on line one."""
    assert set(run_day_script.REQUIRED_ENV) == {
        "ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "FUND_DB", "SLACK_BOT_TOKEN"}


# --- market-closed guard (invariant 4) --------------------------------------

class _Source:
    def __init__(self, payload=None, raises=None):
        self._payload = payload
        self._raises = raises

    def market_clock(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


def test_market_is_open_when_the_broker_says_so():
    assert run_day_script.market_is_open(_Source({"is_open": True})) is True


def test_market_is_closed_when_the_broker_says_so():
    assert run_day_script.market_is_open(_Source({"is_open": False})) is False


def test_unreachable_broker_clock_reads_as_closed():
    """A day we could not verify is a day we do not trade."""
    assert run_day_script.market_is_open(
        _Source(raises=ConnectionError("boom"))) is False


def test_unparseable_clock_payload_reads_as_closed():
    assert run_day_script.market_is_open(_Source({})) is False
    assert run_day_script.market_is_open(_Source(None)) is False


# --- Slack channel remap -----------------------------------------------------

def test_parse_channel_overrides_empty_is_no_remap():
    assert run_day_script.parse_channel_overrides(None) == {}
    assert run_day_script.parse_channel_overrides("") == {}
    assert run_day_script.parse_channel_overrides("  , ") == {}


def test_parse_channel_overrides_pairs():
    assert run_day_script.parse_channel_overrides(
        "#pnl=#test-pnl, #risk=#test-risk") == {
        "#pnl": "#test-pnl", "#risk": "#test-risk"}


@pytest.mark.parametrize("raw", ["#pnl", "#pnl=", "=#test-pnl"])
def test_malformed_override_is_a_hard_stop(raw):
    """Half-parsed remaps must not silently post a rehearsal to the real
    channel."""
    with pytest.raises(SystemExit):
        run_day_script.parse_channel_overrides(raw)


class _RecordingSlack:
    def __init__(self):
        self.posts = []
        self.personas = []

    def post(self, channel, text, thread_ts=None, blocks=None,
             username=None, icon_emoji=None):
        self.posts.append((channel, text, thread_ts))
        self.personas.append((username, icon_emoji))
        return "ts-1"


def test_remapped_slack_rewrites_listed_channels_only():
    inner = _RecordingSlack()
    slack = run_day_script.RemappedSlack(inner, {"#pnl": "#test-pnl"})
    assert slack.post("#pnl", "digest") == "ts-1"
    slack.post("#trade-log", "fill", "ts-0")
    assert inner.posts == [("#test-pnl", "digest", None),
                           ("#trade-log", "fill", "ts-0")]


def test_remapped_slack_carries_the_persona_through():
    """The staging path wraps every post. If this decorator drops username
    and icon, a rehearsal day silently loses seat identity while production
    keeps it — the one case a rehearsal exists to catch, broken in the
    rehearsal itself."""
    inner = _RecordingSlack()
    slack = run_day_script.RemappedSlack(inner, {"#research": "#fund-staging"})
    slack.post("#research", "signal", username="Nora (Analyst)",
               icon_emoji="🔎")
    assert inner.posts == [("#fund-staging", "signal", None)]
    assert inner.personas == [("Nora (Analyst)", "🔎")]


# --- committed config --------------------------------------------------------

def test_watchlist_loads_from_yaml_not_from_code():
    tickers = run_day_script.load_watchlist(run_day_script.WATCHLIST_YAML)
    assert tickers and all(t == t.upper() for t in tickers)


def test_empty_watchlist_is_a_hard_stop(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers: []\n")
    with pytest.raises(SystemExit):
        run_day_script.load_watchlist(path)


def test_every_watchlist_ticker_has_a_sector():
    """A ticker with no sector is rejected fail-closed by the gate, so it
    would burn a seat turn for a guaranteed HOLD."""
    sectors = yaml.safe_load((ROOT / "config" / "sectors.yaml").read_text())
    missing = [t for t in run_day_script.load_watchlist(
        run_day_script.WATCHLIST_YAML) if t not in sectors]
    assert missing == [], f"no sector for {missing} in config/sectors.yaml"


def test_watchlist_stays_within_the_cost_bound():
    """Spec P1: watchlist <= 3 tickers is the fund's per-day cost bound."""
    assert len(run_day_script.load_watchlist(run_day_script.WATCHLIST_YAML)) <= 3


def test_every_stage_seat_has_a_committed_config():
    for stage_seats in run_day_script.SEATS.values():
        for seat in stage_seats:
            assert (run_day_script.SEAT_CONFIG / f"{seat}.yaml").exists(), seat


# --- nothing after connect() may die silently (Fixes 2 & 3) -----------------

@pytest.fixture
def wired(tmp_path):
    """The live root's post-connect() world, minus the real clients: a real DB,
    a real outbox, a Slack we can read back."""
    conn = connect(tmp_path / "fund.sqlite")
    return conn, FakeSlack(), SimClock(START)


def _risk_texts(slack) -> list[str]:
    return [p["text"] for p in slack.posts.get("#risk", [])]


def test_a_raise_inside_the_day_alerts_slack_and_exits_nonzero(wired):
    """Fix 2 — the silent death. run_day(...) then report_audit(...) with no
    try/finally means any raise inside a stage body (a DB error in
    run_research's insert, a journals permission failure in run_close) skips
    the audit entirely: no alert appended, nothing drained, Slack never told.
    That is reachable AFTER the gate minted a ticket and the seat placed an
    order, so #risk shows a ticket and #trade-log shows nothing."""
    conn, slack, clock = wired

    def body():
        raise sqlite3_error()

    assert run_day_script.guarded(conn, slack, clock, body) == 1
    texts = _risk_texts(slack)
    assert len(texts) == 1
    assert "run_day_failed" in texts[0] and "OperationalError" in texts[0]
    assert conn.execute("SELECT COUNT(*) c FROM events WHERE kind = 'alert'"
                        " AND posted_at IS NOT NULL").fetchone()["c"] == 1
    import json
    payload = json.loads(conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert'").fetchone()["payload"])
    assert payload["code"] == "run_day_failed"


def sqlite3_error():
    import sqlite3
    return sqlite3.OperationalError("database is locked")


def test_a_systemexit_inside_the_day_is_not_silent(wired):
    """Fix 3 — load_watchlist raises SystemExit, which `except Exception` would
    walk straight past. A scheduled job that stops on a config error must still
    say so in Slack."""
    conn, slack, clock = wired

    def body():
        raise SystemExit("run_day: config/watchlist.yaml lists no tickers")

    assert run_day_script.guarded(conn, slack, clock, body) == 1
    assert "lists no tickers" in _risk_texts(slack)[0]


def test_the_guard_returns_the_bodys_own_exit_code(wired):
    conn, slack, clock = wired
    assert run_day_script.guarded(conn, slack, clock, lambda: 0) == 0
    assert run_day_script.guarded(conn, slack, clock, lambda: 1) == 1
    assert slack.posts == {}


class _DeadDataSource:
    """The market-data half of Fix 3: account_state answers, close_frame does
    not. Both run after connect() and before the first DB write."""

    def account_state(self) -> dict:
        return {"equity": 100000.0, "cash": 30000.0, "positions": {},
                "position_count": 0, "daily_pnl_pct": 0.0,
                # Explicitly flat, not merely silent. Without this key
                # orchestrator/ingest_guard.py reads the field as UNREADABLE
                # and falls through to the fund's own order records, which
                # this fixture's DB happens to have none of — so the day
                # would run by accident, and would start halting the moment
                # anyone gave the `wired` fixture an `orders` row. This
                # fixture must fail at close_frame, not before it.
                "long_market_value": 0.0}

    def close_frame(self, universe, end=None):
        raise ConnectionError("alpaca data api unreachable")


def test_a_market_data_failure_reaches_slack(wired, tmp_path):
    """Fix 3, through the real composition path — not a stubbed body."""
    conn, slack, clock = wired
    code = run_day_script.guarded(
        conn, slack, clock,
        lambda: run_day_script._trading_day(
            conn, slack, clock, _DeadDataSource(), "2026-07-06",
            str(tmp_path / "fund.sqlite"),
            {"FUND_JOURNALS": str(tmp_path / "journals")}))
    assert code == 1
    assert "ConnectionError" in _risk_texts(slack)[0]
    assert conn.execute("SELECT COUNT(*) c FROM checkpoints").fetchone()["c"] == 0


# --- market-data gaps must be named, never silent ---------------------------

# MIN_HISTORY_RETURNS-clearing fixtures for the price-history alerts: a ticker
# with a handful of bars is now itself unpriceable, so a 3-bar "healthy" column
# would make these tests assert the opposite of what they mean.
_PRICED = [100.0, 101.0] * 22 + [100.0]        # 45 closes = 44 returns
_DARK = [float("nan")] * 45


def _alert_payloads(conn) -> list[dict]:
    import json
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def test_a_held_ticker_with_no_price_history_is_named_in_an_alert(wired):
    """market/features.py drops an unpriceable holding from the
    book-correlation basket so ONE data gap cannot reject the whole universe
    — but a shrunken basket understates correlation and sizes UP, so the
    exclusion must never be silent. features.py is pure compute with no event
    access, so the composition root that assembles the market snapshot owns
    the alert."""
    import pandas as pd
    conn, _, clock = wired
    close_df = pd.DataFrame({"NVDA": _PRICED, "AAPL": _DARK})
    run_day_script.alert_missing_price_history(
        conn, clock, close_df, {"AAPL": 40, "NVDA": 5})
    payloads = _alert_payloads(conn)
    assert len(payloads) == 1
    assert payloads[0]["code"] == "missing_price_history"
    assert payloads[0]["tickers"] == ["AAPL"]
    assert "AAPL" in payloads[0]["text"] and "NVDA" not in payloads[0]["text"]
    assert "sizing is looser" in payloads[0]["text"]


def test_a_book_with_no_priceable_member_alerts_that_buys_fail_closed(wired):
    """When EVERY holding is dark there is no shrunken basket — correlation
    is unmeasurable, features returns NaN and the gate rejects. The alert
    must say that, because the operator response is opposite: a partial gap
    means watch the loose sizing, a blackout means the day will not trade."""
    import pandas as pd
    conn, _, clock = wired
    close_df = pd.DataFrame({"NVDA": _PRICED, "AAPL": _DARK})
    run_day_script.alert_missing_price_history(conn, clock, close_df, {"AAPL": 40})
    payloads = _alert_payloads(conn)
    assert len(payloads) == 1
    assert payloads[0]["tickers"] == ["AAPL"]
    assert "fails the gate closed" in payloads[0]["text"]
    assert "sizing is looser" not in payloads[0]["text"]


def test_a_fully_priced_book_raises_no_price_history_alert(wired):
    import pandas as pd
    conn, _, clock = wired
    close_df = pd.DataFrame({"NVDA": _PRICED, "AAPL": _PRICED})
    run_day_script.alert_missing_price_history(
        conn, clock, close_df, {"AAPL": 40, "NVDA": 5})
    assert _alert_payloads(conn) == []


def test_a_held_ticker_missing_from_sectors_yaml_is_named_in_an_alert(wired):
    """sector_book_value fails closed on an unmapped holding, so every buy
    of the day becomes gate_error. The alert must name the ticker — the fix
    is a one-line commit to config/sectors.yaml."""
    conn, _, clock = wired
    run_day_script.alert_unmapped_sectors(
        conn, clock, {"AAPL": 40, "NVDA": 5}, {"NVDA": "tech"})
    payloads = _alert_payloads(conn)
    assert len(payloads) == 1
    assert payloads[0]["code"] == "unmapped_sector"
    assert payloads[0]["tickers"] == ["AAPL"]
    assert "AAPL" in payloads[0]["text"] and "sectors.yaml" in payloads[0]["text"]


def test_a_fully_mapped_book_raises_no_sector_alert(wired):
    conn, _, clock = wired
    run_day_script.alert_unmapped_sectors(
        conn, clock, {"AAPL": 40, "NVDA": 5}, {"NVDA": "tech", "AAPL": "tech"})
    assert _alert_payloads(conn) == []


def test_the_audit_rollup_keeps_its_self_alert_marker(wired, monkeypatch):
    """audit_day.SELF_ALERT_KEY is how BOTH audit_day and the filer avoid
    double-counting the rollup. Migrating must not drop it."""
    conn, slack, clock = wired
    monkeypatch.setattr(run_day_script.audit_day, "audit",
                        lambda *a, **k: ["forced failure"])

    code = run_day_script.report_audit(conn, slack, "db", "2026-07-06", clock)

    assert code == 1
    payload = _alert_payloads(conn)[-1]
    assert payload["code"] == "audit_failed"
    assert payload[run_day_script.audit_day.SELF_ALERT_KEY] is True


# --- single-instance guard (Fix 5) ------------------------------------------

def test_a_second_instance_backs_off_instead_of_racing(tmp_path):
    """Two overlapping processes both re-run a stage whose checkpoint is
    'running' — correct crash-resume semantics, but overlapping it means two
    concurrent seat turns (double LLM spend) and two concurrent drains (double
    Slack posts)."""
    path = tmp_path / "run_day.lock"
    held = run_day_script.acquire_lock(path)
    assert held is not None
    assert run_day_script.acquire_lock(path) is None


def test_a_dead_process_leaves_no_lock_behind(tmp_path):
    """Tomorrow's run must not be blocked by yesterday's crash: flock is held
    by the open file description, so the kernel drops it when the process
    dies — no staleness check, no pid liveness guessing."""
    path = tmp_path / "run_day.lock"
    held = run_day_script.acquire_lock(path)
    held.close()                                   # == the process going away
    assert run_day_script.acquire_lock(path) is not None


# --- cost accounting must never take the day down (Fix 6) -------------------

class _Result:
    def __init__(self, cost=0.01, turns=7):
        self.total_cost_usd = cost
        self.session_id = "sess-1"
        self.num_turns = turns


def test_a_cost_recording_failure_does_not_take_the_trading_day_down(wired):
    """Fix 6: record_turn_result's own DB writes can raise, and run_day calls
    it outside make_turn's try. A cost-accounting failure must not kill a day
    that has already traded — the audit's cost check is the backstop."""
    conn, _, clock = wired
    conn.close()
    run_day_script.record_cost_guarded(conn, clock, "2026-07-06", "analyst",
                                       _Result())


def test_the_cost_seam_still_records_when_the_db_is_healthy(wired):
    conn, _, clock = wired
    run_day_script.record_cost_guarded(conn, clock, "2026-07-06", "analyst",
                                       _Result())
    assert conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"] == 1


def test_num_turns_is_logged_for_every_seat_turn(capsys):
    """HANDOFF-LIVE §7 right-sizes max_turns from the first live day's
    num_turns; the SDK's own stdout formatting must not be the only capture
    path for it."""
    run_day_script.log_turn_result("analyst", _Result(turns=11))
    out = capsys.readouterr().out
    assert "analyst" in out and "num_turns=11" in out


def test_a_result_without_num_turns_still_logs(capsys):
    run_day_script.log_turn_result("pm", object())
    assert "num_turns=" in capsys.readouterr().out


def test_the_tool_calls_a_turn_made_are_logged(capsys):
    """First live day (2026-08-17): an exec turn billed 4 turns, placed no
    order, and left NO record of what it had called — the diagnosis needed
    introspecting the broker's MCP schema to infer it. One line here makes the
    next one a grep."""
    run_day_script.log_turn_result(
        "exec", _Result(turns=4),
        ["mcp__fund__list_open_tickets", "mcp__alpaca__place_stock_order"])
    out = capsys.readouterr().out
    assert "mcp__fund__list_open_tickets" in out
    assert "mcp__alpaca__place_stock_order" in out


def test_a_turn_whose_tools_were_never_captured_says_so(capsys):
    """'n/a' rather than an empty list: a turn we failed to observe must not
    read as a turn that called nothing — that is the exact distinction the
    exec guard's (a) check turns into a hard failure."""
    run_day_script.log_turn_result("analyst", _Result(turns=3))
    out = capsys.readouterr().out
    assert "tools=n/a" in out and "tools=[]" not in out


# --- _seat_session: the id-binding leg above make_turn --------------------

def test_seat_session_threads_the_bound_id_to_build_seat_options(monkeypatch):
    """The middle leg of the id-binding chain that scripts/reflect_day.py's
    submit_reflection fix depends on end-to-end: _seat_session must forward
    expected_decision_id into build_seat_options, not just accept it as a
    parameter and drop it. ClaudeSDKClient and run_seat_turn are faked — a
    live SDK session is exactly what this offline suite must never open."""
    import asyncio

    import claude_agent_sdk

    seen = {}

    def _fake_build_seat_options(cfg, db_path, clock, *, snapshot=None,
                                 journals_root=None,
                                 expected_decision_id=None):
        seen["expected_decision_id"] = expected_decision_id
        return object()          # opaque; only ClaudeSDKClient below sees it

    class _FakeClient:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _fake_run_seat_turn(client, prompt, required):
        return ([], None)

    monkeypatch.setattr(run_day_script, "build_seat_options",
                        _fake_build_seat_options)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(run_day_script, "run_seat_turn", _fake_run_seat_turn)

    asyncio.run(run_day_script._seat_session(
        {"seat": "reflect"}, ":memory:", SimClock(START), "p", None, None,
        expected_decision_id=42))

    assert seen["expected_decision_id"] == 42


def test_make_turn_threads_the_bound_id_to_seat_session(wired, monkeypatch):
    """The fourth leg of the id-binding chain, above _seat_session:
    make_turn's own run() must forward expected_decision_id into the
    _seat_session call. test_seat_session_threads_the_bound_id_to_
    build_seat_options (above) calls _seat_session directly and so never
    exercises this call site — deleting expected_decision_id from make_turn's
    _seat_session call left that test, and all its siblings, green."""
    conn, _, clock = wired
    seen = {}

    async def _fake_seat_session(cfg, db_path, clk, prompt, snapshot,
                                 journals_root, expected_decision_id=None):
        seen["expected_decision_id"] = expected_decision_id
        return ([], _Result(turns=1))

    monkeypatch.setattr(run_day_script, "_seat_session", _fake_seat_session)

    _turn(conn, clock, seat="reflect", expected_decision_id=99)()

    assert seen["expected_decision_id"] == 99


# --- make_turn: a seat failure degrades to HOLD, it does not abort the day --

def _turn(conn, clock, seat="analyst", **over):
    """make_turn with everything but the seat session already wired. The
    session itself is monkeypatched per-test — it is the only piece that needs
    a real SDK client."""
    kwargs = dict(cfg={}, db_path=":memory:", clock=clock, conn=conn,
                  run_date="2026-07-06", prompt="p")
    kwargs.update(over)
    return run_day_script.make_turn(seat, **kwargs)


def _alert_texts(conn) -> list[str]:
    return [p["text"] for p in _alert_payloads(conn)]


def test_one_seat_failing_leaves_the_other_analysts_turn_intact(
        wired, monkeypatch):
    """The COMPOSITION, not the part. Research now runs two seats behind one
    stage key, so a dead analyst must not swallow the news seat's turn —
    otherwise one seat's outage silently shrinks the OTHER seat's calibration
    sample too, which is the survivorship bias the per-seat default exists to
    prevent. Every pre-existing failure test is single-seat."""
    conn, _, clock = wired
    ran = []

    async def _seat_aware(cfg, *a, **k):
        if cfg.get("seat") == "analyst":
            raise TimeoutError("session never connected")
        ran.append(cfg.get("seat"))
        return ([], _Result(turns=3))

    monkeypatch.setattr(run_day_script, "_seat_session", _seat_aware)
    for seat in run_day_script.SEATS["research"]:
        _turn(conn, clock, seat=seat, cfg={"seat": seat})()   # neither raises

    assert ran == ["news"]                     # the healthy seat still ran
    texts = _alert_texts(conn)
    assert len(texts) == 1                     # exactly one seat was alerted
    assert "analyst_turn_failed" in texts[0] and "TimeoutError" in texts[0]
    assert "default is HOLD" in texts[0]


def test_a_seat_turn_that_raises_alerts_and_lets_the_stage_default_land(
        wired, monkeypatch):
    """make_turn's `except Exception` IS the degrade-to-HOLD path (invariant
    4). Delete it and one seat failure — a uvx cold start, an SDK timeout, a
    blown budget — propagates out of run_stage, past run_day, and aborts the
    whole trading day: no gate pass, no digest, nothing drained. Untested
    until now, so nothing noticed."""
    conn, _, clock = wired

    async def _boom(*a, **k):
        raise TimeoutError("session never connected")

    monkeypatch.setattr(run_day_script, "_seat_session", _boom)
    run = _turn(conn, clock, seat="analyst")
    run()                                    # must NOT raise

    texts = _alert_texts(conn)
    assert len(texts) == 1
    assert "analyst_turn_failed" in texts[0] and "TimeoutError" in texts[0]
    assert "default is HOLD" in texts[0]
    assert _alert_payloads(conn)[0]["code"] == "seat_turn_failed"

    # ...and the stage that owns this turn still lands its own default: the
    # ticker gets neutral/0 "no report" instead of the day stopping.
    from orchestrator.daily import StageCtx, run_research
    run_research(StageCtx(conn=conn, run_date="2026-07-06", clock=clock,
                          slack=None, research_seats=("analyst",),
                          run_turn={"research": run}), ["NVDA"])
    row = conn.execute("SELECT direction, confidence, summary FROM signals"
                       " WHERE run_date = '2026-07-06' AND ticker = 'NVDA'"
                       ).fetchone()
    assert (row["direction"], row["confidence"], row["summary"]) == (
        "neutral", 0, "no report")


def test_seat_turn_failure_uses_a_literal_code_not_the_seat_name(
        wired, monkeypatch):
    """The seat belongs in the TEXT. A code of f"{seat}_turn_failed" mints a
    new code — and therefore a new issue — for every seat that ever fails."""
    conn, _, clock = wired

    async def _boom(*a, **k):
        raise TimeoutError("session never connected")

    monkeypatch.setattr(run_day_script, "_seat_session", _boom)
    _turn(conn, clock, seat="analyst")()

    payload = _alert_payloads(conn)[-1]
    assert payload["code"] == "seat_turn_failed"
    assert payload["text"].startswith("analyst_turn_failed —")


# --- a seat turn that HANGS is a different failure from one that raises -----

def test_a_seat_turn_that_never_returns_is_abandoned_at_its_wall_clock_bound(
        wired, monkeypatch):
    """Issue #44. max_turns and max_budget_usd bound turns and dollars; a
    stalled MCP tool call or model stream spends neither, so before this the
    turn simply never came back. The stage default is triggered by an
    EXCEPTION and by nothing else (orchestrator/daily.py's run_stage calls
    body() with no time budget), so a hang reached no default at all: the day
    hung forever holding the flock, and the next day's timer found the lock
    and exited 0 — indistinguishable from a market-closed day.

    The 30s sleep stands in for "never returns": it is 600x the bound this
    test configures, so any pass is the bound firing and not the sleep
    finishing — while an unbounded regression still ENDS the suite (with a
    red assertion) instead of hanging it."""
    conn, _, clock = wired

    async def _hang(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(run_day_script, "_seat_session", _hang)
    run = _turn(conn, clock, seat="pm", cfg={"max_wall_s": 0.05})
    started = time.monotonic()
    run()                                    # must NOT raise, must NOT hang
    assert time.monotonic() - started < 5    # abandoned at the bound

    payload = _alert_payloads(conn)[-1]
    assert payload["code"] == "seat_turn_timeout"
    assert payload["text"].startswith("pm_turn_timeout —")
    assert "0.05s" in payload["text"] and "max_wall_s" in payload["text"]
    assert "default is HOLD" in payload["text"]

    # ...and because it now RAISES, the stage's own default finally lands:
    # the ticker gets hold/0 instead of the day stopping dead.
    from orchestrator.daily import StageCtx, run_decision
    run_decision(StageCtx(conn=conn, run_date="2026-07-06", clock=clock,
                          slack=None, research_seats=(),
                          run_turn={"decision": run}), ["NVDA"])
    row = conn.execute("SELECT action, qty FROM decisions"
                       " WHERE run_date = '2026-07-06' AND ticker = 'NVDA'"
                       ).fetchone()
    assert (row["action"], row["qty"]) == ("hold", 0)


def test_a_seat_with_no_max_wall_s_still_gets_the_default_ceiling(
        wired, monkeypatch):
    """max_wall_s is optional and resolved from ONE named constant, so a seat
    yaml that never mentions it is still bounded. A `cfg["max_wall_s"]`
    subscript would instead leave every one of today's six configs unbounded —
    the exact defect #44 reports."""
    conn, _, clock = wired
    seen = {}

    async def _capture(*a, **k):
        return [], _Result()

    def _spy(coro, wall_s):
        seen["wall_s"] = wall_s
        return coro

    monkeypatch.setattr(run_day_script, "_seat_session", _capture)
    monkeypatch.setattr(run_day_script, "_bounded", _spy)
    _turn(conn, clock, seat="analyst", cfg={})()

    assert seen["wall_s"] == run_day_script.SEAT_MAX_WALL_S


def test_the_seat_wall_clock_ceiling_fires_before_systemd_sigterms_the_unit():
    """The whole point of a per-seat bound is that it beats
    ops/fund-daily.service's TimeoutStartSec=30min — that SIGTERM can land
    between the broker accepting a place_stock_order and the PostToolUse
    recorder committing the row. A day runs at most four seat turns (the two
    research seats, the PM, the trader), so the ceiling must leave real room
    for the non-turn work underneath: the broker snapshot, the market-data
    fetch, reconcile's 90s wait, the audit and the drains."""
    turns_per_day = sum(len(seats) for seats in run_day_script.SEATS.values())
    assert turns_per_day == 4
    worst_case_s = turns_per_day * run_day_script.SEAT_MAX_WALL_S
    assert worst_case_s <= 0.6 * 30 * 60          # >= 40% of the unit's budget
    # ...and still far above any turn ever observed (exec's measured live turn
    # was 3 tool-calling turns), so a slow real turn is not culled as a hang.
    assert run_day_script.SEAT_MAX_WALL_S >= 120


def test_an_exec_tool_call_violation_is_alerted_after_the_cost_is_recorded(
        wired, monkeypatch):
    """check_tool_calls fires only for the exec seat, and only AFTER the cost
    is recorded — a violating turn still spent real money. A silent no-op exec
    turn (zero tool calls) is the shape this catches: open tickets, no orders,
    nobody told."""
    conn, _, clock = wired

    async def _silent(*a, **k):
        return [], _Result(cost=0.02)

    monkeypatch.setattr(run_day_script, "_seat_session", _silent)
    _turn(conn, clock, seat="exec")()

    texts = _alert_texts(conn)
    assert len(texts) == 1 and "exec_turn_violation" in texts[0]
    assert "zero tool calls" in texts[0]
    assert conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"] == 1
    assert _alert_payloads(conn)[0]["code"] == "exec_turn_violation"


def test_an_off_mandate_tool_call_is_alerted(wired, monkeypatch):
    conn, _, clock = wired

    async def _off_mandate(*a, **k):
        return ["Bash"], _Result()

    monkeypatch.setattr(run_day_script, "_seat_session", _off_mandate)
    _turn(conn, clock, seat="exec")()
    assert "Bash" in _alert_texts(conn)[0]


def test_a_clean_exec_turn_raises_no_alert(wired, monkeypatch):
    """The inverse: an alert on every turn would be as useless as an alert on
    none."""
    conn, _, clock = wired

    async def _clean(*a, **k):
        return ["mcp__fund__list_open_tickets",
                "mcp__alpaca__place_stock_order"], _Result()

    monkeypatch.setattr(run_day_script, "_seat_session", _clean)
    _turn(conn, clock, seat="exec")()
    assert _alert_texts(conn) == []
    assert conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"] == 1


# --- _trading_day: the whole composition, offline ---------------------------

class _QuietSource:
    """A funded-but-uninvestable account: zero cash and no positions, so every
    ticker's buy shape is no_headroom and every sell shape is nothing_held.
    That is the ZERO-ACTIVE-TICKER day — every stage still runs, checkpoints
    and posts the digest (invariant 4), at zero LLM cost."""

    def account_state(self) -> dict:
        return {"equity": 100000.0, "cash": 0.0, "positions": {},
                "prices": {}, "daily_pnl_pct": 0.0,
                # Explicitly flat, not merely silent. Without this key
                # orchestrator/ingest_guard.py reads the field as UNREADABLE
                # and falls through to the fund's own order records, which
                # this fixture's DB happens to have none of — so the day
                # would run by accident, and would start halting the moment
                # anyone gave the `wired` fixture an `orders` row. Zero cash
                # and zero long market value is the state this docstring
                # already claims.
                "long_market_value": 0.0}

    def close_frame(self, universe, end=None):
        import pandas as pd
        # 45 closes = 44 returns, comfortably over MIN_HISTORY_RETURNS: this
        # day must be uninvestable through CASH, not through thin data, or
        # it would stop exercising the no_headroom/nothing_held shapes it
        # exists for and become a gate_error day instead.
        closes = [100.0, 101.0] * 22 + [100.0]
        return pd.DataFrame({t: closes for t in universe},
                            index=pd.bdate_range("2026-06-29", periods=45))

    def get_order(self, client_order_id):        # reconcile_orders' broker
        raise AssertionError("no orders were placed on a zero-ticker day")

    # The account holds nothing, so it must be able to SAY it holds nothing.
    # orchestrator/protection.py fails closed on a broker it cannot read, so
    # a source that stays silent here would alert and red the audit.
    def open_positions(self) -> list[dict]:
        return []

    def open_orders(self) -> list[dict]:
        return []

    # orchestrator/preconditions.py fails closed on a broker it cannot read,
    # same as open_positions above — quiet means matching the baseline
    # exactly, not staying silent. This reads the same file the production
    # code compares against, rather than returning DEFAULT_ACCOUNT_CONFIG,
    # so this stays quiet once the placeholder baseline is replaced with
    # values captured from the real account. Tying the fake to its own
    # defaults would pass today only by coincidence, and once a real capture
    # made it red, the tempting "fix" would be to edit the captured baseline
    # to match this fixture instead of the other way around.
    def account_config(self) -> dict:
        return yaml.safe_load(
            run_day_script.ACCOUNT_BASELINE_YAML.read_text()) or {}


def test_a_zero_ticker_day_runs_the_whole_composition_and_audits_clean(
        wired, tmp_path, monkeypatch, capsys):
    """_trading_day's only other caller is a fake source that raises inside
    close_frame, so build_market_inputs, StageCtx, run_pre_gate, make_turn,
    run_day and report_audit were never reached by any test. This drives all
    of them with a real DB, a real outbox and a FakeSlack.

    Zero active tickers is the shape that needs no LLM at all — asserted, not
    assumed: _seat_session raises if anything tries to open a session."""
    conn, slack, clock = wired
    db_path = str(tmp_path / "fund.sqlite")

    async def _no_llm(*a, **k):
        raise AssertionError("a zero-ticker day must open no seat session")

    monkeypatch.setattr(run_day_script, "_seat_session", _no_llm)

    code = run_day_script._trading_day(
        conn, slack, clock, _QuietSource(), "2026-07-06", db_path,
        {"FUND_JOURNALS": str(tmp_path / "journals")})

    assert code == 0
    out = capsys.readouterr().out
    assert "no active tickers" in out           # run_pre_gate ran and found none
    assert "AUDIT CLEAN 2026-07-06" in out      # report_audit ran, not skipped

    # every stage of run_day reached 'done' — the composition ran end to end
    assert {r["stage"]: r["status"] for r in conn.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date = '2026-07-06'")
    } == {s: "done" for s in ("pre_gate", "research", "decision", "gate",
                              "execution", "reconciliation", "close")}

    # ...and the day still spoke: a digest AND a scorecard in #pnl, no alerts,
    # nothing left undrained (which is what audit_day's own clean verdict above
    # rests on — the scorecard is appended BEFORE the audit and drained with
    # it, so an unposted one would have reddened this very assertion).
    texts = [p["text"] for p in slack.posts["#pnl"]]
    assert len(texts) == 2
    assert any("2026-07-06 close" in t for t in texts)
    assert any("2026-07-06 scorecard" in t for t in texts)
    assert _alert_payloads(conn) == []
    assert conn.execute("SELECT COUNT(*) c FROM events"
                        " WHERE posted_at IS NULL").fetchone()["c"] == 0


def test_a_zero_ticker_day_writes_no_decisions_and_costs_nothing(
        wired, tmp_path, monkeypatch):
    """The other half of invariant 4's "nothing is possible today": no ticker
    reaches an LLM, so no signal, no decision and no cost row exist — and the
    audit still calls that clean (a day that ran no turns owes no cost row)."""
    conn, slack, clock = wired
    monkeypatch.setattr(run_day_script, "_seat_session", None)

    run_day_script._trading_day(
        conn, slack, clock, _QuietSource(), "2026-07-06",
        str(tmp_path / "fund.sqlite"),
        {"FUND_JOURNALS": str(tmp_path / "journals")})

    for table in ("signals", "decisions", "tickets", "costs", "orders"):
        assert conn.execute(f"SELECT COUNT(*) c FROM {table}"
                            ).fetchone()["c"] == 0, table


# --- the positions payload the whole day is sized from (issue #39) ----------

class _LostPayloadSource(_QuietSource):
    """The broker answers, plausibly, and is wrong: it reports long market
    value and lists no positions. Everything else about the day is fine, which
    is the point — nothing downstream would notice.

    Keeps _QuietSource's zero cash on purpose. It is not what this test is
    about, and a funded account would make every ticker active, open three
    seat turns, and bury the assertion under seat_turn_failed noise."""

    def account_state(self) -> dict:
        return {"equity": 100000.0, "cash": 0.0, "last_equity": 100000.0,
                "long_market_value": 8594.0, "positions": {}, "prices": {},
                "daily_pnl_pct": 0.0}


class _FlatSource(_QuietSource):
    """The fund's genuinely-empty first day, said explicitly: no positions AND
    the broker's own long market value is zero. Identical to _QuietSource
    post-change; kept as its own name so the test that reads it says what it
    is testing."""

    def account_state(self) -> dict:
        return {"equity": 100000.0, "cash": 0.0, "last_equity": 100000.0,
                "long_market_value": 0.0, "positions": {}, "prices": {},
                "daily_pnl_pct": 0.0}


def _filled_buy_row(conn, symbol="NVDA", qty=80,
                    tid="a3f90000-0000-4000-8000-000000000001",
                    submitted_at="2026-07-02T19:59:00+00:00"):
    """A filled buy and the ticket behind it — the fund's own record that it
    holds something. Returns the ticket id, so a caller can tell the row it
    seeded apart from one the day minted."""
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " (?, ?, 'buy', ?, 't', 'i', NULL, 'executed', ?)",
        (submitted_at[:10], symbol, qty, submitted_at))
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " stop_price, expires_at, status, created_at)"
        " VALUES (?, ?, ?, 'buy', ?, NULL, ?, 'consumed', ?)",
        (tid, cur.lastrowid, symbol, qty, submitted_at, submitted_at))
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " filled_qty, submitted_at) VALUES (?, ?, 'buy', ?, 'filled', ?, ?)",
        (tid, symbol, qty, qty, submitted_at))
    conn.commit()
    return tid


def test_a_lost_positions_payload_holds_the_whole_day(wired, tmp_path,
                                                      monkeypatch):
    """Issue #39 case (a). The fund's records hold filled positions, the broker
    returns none, and the account is not flat. Nothing may be sized from that
    payload: no checkpoint, no seat turn, no ticket — and #risk must be told
    under a code the alert filer can key an issue on."""
    conn, slack, clock = wired
    seeded = _filled_buy_row(conn)

    async def _no_llm(*a, **k):
        raise AssertionError("a held day must open no seat session")

    monkeypatch.setattr(run_day_script, "_seat_session", _no_llm)

    code = run_day_script._trading_day(
        conn, slack, clock, _LostPayloadSource(), "2026-07-06",
        str(tmp_path / "fund.sqlite"),
        {"FUND_JOURNALS": str(tmp_path / "journals")})

    assert code == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM checkpoints").fetchone()["c"] == 0
    # Not a count of zero: orders.client_order_id REFERENCES tickets(id), so
    # the seeded fill above CANNOT exist without a ticket row. Naming the
    # seeded id is the stronger claim anyway — the day minted nothing of its
    # own, and a ticket from any other source would fail this too.
    assert [r["id"] for r in conn.execute("SELECT id FROM tickets")] == [seeded]
    payloads = _alert_payloads(conn)
    assert [p["code"] for p in payloads] == ["positions_payload_lost"]
    assert any("long market value" in t for t in _risk_texts(slack))


def test_a_genuinely_empty_first_day_still_trades(wired, tmp_path, monkeypatch):
    """Issue #39 case (b), and the constraint the whole design bends around.
    A flat broker and an empty book are the fund's normal first day: every
    stage must still run, and market/features.py's 1.10x empty-book tier
    (tests/test_features.py:335) must still be reachable."""
    conn, slack, clock = wired

    async def _no_llm(*a, **k):
        raise AssertionError("a zero-ticker day must open no seat session")

    monkeypatch.setattr(run_day_script, "_seat_session", _no_llm)

    code = run_day_script._trading_day(
        conn, slack, clock, _FlatSource(), "2026-07-06",
        str(tmp_path / "fund.sqlite"),
        {"FUND_JOURNALS": str(tmp_path / "journals")})

    assert code == 0
    assert [p["code"] for p in _alert_payloads(conn)
            if p["code"] == "positions_payload_lost"] == []
    assert {r["stage"]: r["status"] for r in conn.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date = '2026-07-06'")
    } == {s: "done" for s in ("pre_gate", "research", "decision", "gate",
                              "execution", "reconciliation", "close")}


def test_the_open_ticket_count_reaches_check_tool_calls(wired, monkeypatch):
    """D3's WIRING, not its logic. The guard itself is unit-tested in
    test_exec_turn_guard, but nothing exercised run_day's call site: replacing
    the live count with a literal 0 — which fully reinstates the 2026-08-17
    failure, an exec turn that reads its tickets and places nothing — left the
    entire suite green. The count is what makes the guard load-bearing, so the
    count is what this pins."""
    from gate.tickets import create_ticket
    from orchestrator.clock import iso
    conn, _, clock = wired
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " ('2026-07-06','NVDA','buy',58,'t','i',NULL,'approved',?)",
        (iso(clock.now()),))
    conn.commit()
    tid = "cad36b5a-e04d-4d20-8262-71c71d30c283"
    create_ticket(conn, id=tid, decision_id=cur.lastrowid, ticker="NVDA",
                  side="buy", max_qty=58, stop_price=None,
                  expires_at_iso="2026-07-07T00:00:00+00:00",
                  now_iso=iso(clock.now()))

    async def _read_only(*a, **k):        # exactly yesterday's shape
        return ["mcp__fund__list_open_tickets"], _Result(cost=0.02, turns=4)

    monkeypatch.setattr(run_day_script, "_seat_session", _read_only)
    _turn(conn, clock, seat="exec")()

    texts = _alert_texts(conn)
    assert len(texts) == 1
    assert "exec_turn_violation" in texts[0]
    assert "1 open ticket" in texts[0]
    # the cost is still recorded — a violating turn spent real money
    assert conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"] == 1


def test_a_consumed_ticket_does_not_accuse_the_seat_that_executed_it(
        wired, monkeypatch):
    """The count is taken AFTER the turn deliberately. A seat that places an
    order consumes its ticket, so nothing is left open to accuse it of
    inaction — counting BEFORE the turn would fire on every successful day."""
    conn, _, clock = wired

    async def _placed(*a, **k):
        return ["mcp__fund__list_open_tickets",
                "mcp__alpaca__place_stock_order"], _Result(cost=0.02)

    monkeypatch.setattr(run_day_script, "_seat_session", _placed)
    _turn(conn, clock, seat="exec")()
    assert _alert_texts(conn) == []


# --- trace emission ---------------------------------------------------------

class _Res:
    num_turns = 2
    total_cost_usd = 0.01
    duration_ms = 55
    is_error = False


_PM_CFG = {"seat": "pm", "model": "claude-sonnet-5"}


def test_emit_trace_guarded_sends_one_trace_per_turn():
    """The sink is injected, so the wiring is asserted with no filesystem and
    no SDK: production passes evals.live.file_sink, tests pass list.append."""
    import itertools

    run_day = _load()
    got = []
    seq = itertools.count()
    brief = {"cash": 100.0, "positions": {},
             "allowed_actions": {"NVDA": {"buy": 3, "sell": 0}}}

    run_day.emit_trace_guarded("pm", _PM_CFG, "2026-08-18", seq,
                               lambda: brief, ["mcp__fund__submit_decision"],
                               _Res(), got.append)

    assert len(got) == 1
    assert got[0].case == "live-2026-08-18"
    assert got[0].seat == "pm"
    assert got[0].trial == 0
    assert got[0].tool_names == ["mcp__fund__submit_decision"]


def test_brief_tickers_come_from_the_allowed_actions_key_set():
    """allowed_actions' keys ARE the active set (a ticker with both shapes 0 is
    absent entirely), so they are what the seat was actually shown — not the
    positions it happens to hold."""
    import itertools

    run_day = _load()
    got = []
    brief = {"cash": 1.0, "positions": {"AMD": 4},
             "allowed_actions": {"NVDA": {"buy": 3, "sell": 0}}}

    run_day.emit_trace_guarded("pm", _PM_CFG, "2026-08-18", itertools.count(),
                               lambda: brief, [], _Res(), got.append)

    assert got[0].brief_tickers == ["NVDA"]


def test_the_turn_sequence_does_not_repeat_across_seats():
    """The sequence is the trace filename — two seats sharing a number would
    silently overwrite each other's turn."""
    import itertools

    run_day = _load()
    got = []
    seq = itertools.count()
    for seat in ("analyst", "pm", "exec"):
        run_day.emit_trace_guarded(seat, {"seat": seat, "model": "m"},
                                   "2026-08-18", seq, None, [], _Res(),
                                   got.append)

    assert [t.trial for t in got] == [0, 1, 2]


def test_a_failing_sink_never_costs_the_day():
    """A trace is evidence, not control flow, and this runs after the seat may
    already have placed a real order. Same posture as record_cost_guarded."""
    import itertools

    run_day = _load()

    def _boom(_trace):
        raise OSError("disk full")

    run_day.emit_trace_guarded("pm", _PM_CFG, "2026-08-18", itertools.count(),
                               None, [], _Res(), _boom)   # must not raise


def test_no_sink_configured_is_a_silent_no_op():
    """FUND_TRACES unset runs the day exactly as before — recording is not a
    precondition for trading."""
    import itertools

    run_day = _load()
    run_day.emit_trace_guarded("pm", _PM_CFG, "2026-08-18", itertools.count(),
                               None, [], _Res(), None)


def test_account_baseline_yaml_parses_and_is_not_empty():
    """An empty or unparseable baseline makes the drift check unable to fail,
    so it is checked here rather than discovered at 09:00."""
    baseline = yaml.safe_load(
        run_day_script.ACCOUNT_BASELINE_YAML.read_text())
    assert isinstance(baseline, dict) and baseline


def test_a_drifted_account_setting_alerts_but_does_not_abort_the_day(
        wired, tmp_path, monkeypatch):
    """WIRING, not the assertion's own logic (that is test_preconditions.py's
    job). `_trading_day`'s call to `assert_account_config_unchanged` is not
    pinned by any existing test: deleting the `if
    assert_account_config_unchanged(...)` block at scripts/run_day.py changes
    no test result, because the three tests that look like they cover it do
    not — one reads only the yaml file, one calls the assertion directly with
    no import from run_day.py, and `make sim-day` never reaches `_trading_day`
    at all. This drives the same entry point
    test_a_zero_ticker_day_runs_the_whole_composition_and_audits_clean uses,
    with a broker whose account_config() differs from the baseline in one
    field, and proves the design's promised claim: the day still completes
    when drift is present — alert-only, not blocking.

    "Not blocking" is NOT the same as a clean exit code: audit_day.py counts
    "no alert events TODAY" as part of a clean day by design (drift means the
    fund traded under preconditions nobody verified, and that is meant to
    surface), so a day with real drift genuinely returns 1 — that IS the
    audit doing its job, not the day aborting. What "alert-only, not
    blocking" actually promises, and what this asserts, is that every stage
    still ran to completion and the day's digest still posted, rather than
    the drift check stopping the day before a single stage runs."""
    conn, slack, clock = wired
    db_path = str(tmp_path / "fund.sqlite")

    class _DriftingSource(_QuietSource):
        def account_config(self) -> dict:
            baseline = super().account_config()
            return dict(baseline, suspend_trade=not baseline["suspend_trade"])

    async def _no_llm(*a, **k):
        raise AssertionError("a zero-ticker day must open no seat session")

    monkeypatch.setattr(run_day_script, "_seat_session", _no_llm)

    code = run_day_script._trading_day(
        conn, slack, clock, _DriftingSource(), "2026-07-06", db_path,
        {"FUND_JOURNALS": str(tmp_path / "journals")})

    # The audit correctly reddens on a real drift alert (see docstring) — this
    # is the load-bearing half of the proof: delete the call site and no
    # alert fires at all, so this becomes 0 instead.
    assert code == 1
    assert any("suspend_trade" in t for t in _risk_texts(slack))

    # ...but every stage still ran to completion — the check did not stop the
    # day, which is what "alert-only, not blocking" actually means.
    assert {r["stage"]: r["status"] for r in conn.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date = '2026-07-06'")
    } == {s: "done" for s in ("pre_gate", "research", "decision", "gate",
                              "execution", "reconciliation", "close")}
