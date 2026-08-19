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

import importlib.util
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
                "position_count": 0, "daily_pnl_pct": 0.0}

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
    assert payloads[0]["tickers"] == ["AAPL"]
    assert "AAPL" in payloads[0]["text"] and "sectors.yaml" in payloads[0]["text"]


def test_a_fully_mapped_book_raises_no_sector_alert(wired):
    conn, _, clock = wired
    run_day_script.alert_unmapped_sectors(
        conn, clock, {"AAPL": 40, "NVDA": 5}, {"NVDA": "tech", "AAPL": "tech"})
    assert _alert_payloads(conn) == []


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
                "prices": {}, "daily_pnl_pct": 0.0}

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
