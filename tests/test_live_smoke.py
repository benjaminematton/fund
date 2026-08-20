"""Acceptance P1 @live smoke (manual, never CI):
    set -a; source .env; set +a
    .venv/bin/pytest -m live tests/test_live_smoke.py -v

Core round-trip needs ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY.
The Execution Trader runs a REAL turn through run_exec_turn (the guarded path:
waits for the broker MCP server, then asserts the tool calls), a 1-share paper
order round-trips, and the PostToolUse recorder writes the order row.

Slack is OPTIONAL and decoupled: the projection is asserted only when a real
bot token (xoxb-) is present. An app-level token (xapp-) cannot chat.postMessage
— the core round-trip must not fail for lack of a Slack bot.
"""

import asyncio
import json
import os
import time
import urllib.request
import uuid

import pytest

pytestmark = pytest.mark.live

PAPER = "https://paper-api.alpaca.markets"


def _alpaca_get(path):
    req = urllib.request.Request(PAPER + path, headers={
        "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _latest_trade_price(symbol: str) -> float:
    """Last trade from the market-data host (a different host from PAPER, so
    it does not go through _alpaca_get)."""
    req = urllib.request.Request(
        f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
        headers={"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                 "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]})
    with urllib.request.urlopen(req) as r:
        return float(json.loads(r.read())["trade"]["p"])


def _alpaca_delete(path):
    req = urllib.request.Request(PAPER + path, method="DELETE", headers={
        "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]})
    urllib.request.urlopen(req)


def test_one_share_paper_round_trip(tmp_path):
    # Core requirements only — Slack is optional (asserted separately below).
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set — load .env first")

    from datetime import timedelta

    from agents.exec_turn import run_exec_turn
    from agents.trader import build_trader_options, load_seat_config
    from agents.wallclock import WallClock
    from gate.tickets import create_ticket
    from orchestrator.clock import iso
    from state.db import connect

    clock = WallClock()
    db_path = tmp_path / "live-smoke.sqlite"
    conn = connect(db_path)
    now = iso(clock.now())
    ticket_id = str(uuid.uuid4())
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " (?, 'AAPL', 'buy', 1, 'live smoke', 'n/a', 'approved', ?)",
        (now[:10], now))
    conn.commit()
    create_ticket(conn, id=ticket_id, decision_id=cur.lastrowid,
                  ticker="AAPL", side="buy", max_qty=1, stop_price=None,
                  expires_at_iso=iso(clock.now() + timedelta(minutes=45)),
                  now_iso=now)

    async def run_turn():
        from claude_agent_sdk import ClaudeSDKClient

        opts = build_trader_options(
            load_seat_config("agents/config/exec.yaml"), db_path, clock)
        async with ClaudeSDKClient(options=opts) as client:
            # guarded path: (c) wait for alpaca+fund before querying, then
            # (a)/(b) assert the calls the seat actually made.
            return await run_exec_turn(
                client,
                "Execution stage: execute all open tickets per your charter.",
                {"alpaca", "fund"})

    tool_calls = asyncio.run(run_turn())
    # the seat must have exercised its tools within the two globs
    assert any(t.startswith("mcp__alpaca__place_") for t in tool_calls), tool_calls

    # round-trip: poll until terminal; cancel if the market is closed
    status = None
    for _ in range(30):
        o = _alpaca_get(
            f"/v2/orders:by_client_order_id?client_order_id={ticket_id}")
        status = o["status"]
        if status in ("filled", "canceled", "rejected", "expired"):
            break
        time.sleep(3)
    if status not in ("filled", "canceled"):
        _alpaca_delete(f"/v2/orders/{o['id']}")
        status = "canceled"
    assert status in ("filled", "canceled")

    # primary success signal (market-hours-independent): the PostToolUse
    # recorder mirrored the order into SQLite. A fill event only exists if the
    # order actually filled, so we assert the row, not the fill.
    row = conn.execute("SELECT * FROM orders WHERE client_order_id = ?",
                       (ticket_id,)).fetchone()
    assert row is not None, "recorder did not write the order row"

    # Slack projection: only when a real BOT token is configured. xapp- app
    # tokens can't post — skip that leg rather than fail the round-trip.
    slack_token = os.environ.get("SLACK_BOT_TOKEN_EXEC", "")
    if not slack_token.startswith("xoxb-"):
        pytest.skip("SLACK_BOT_TOKEN_EXEC is not a bot token (xoxb-) —"
                    " core round-trip passed; Slack projection not exercised")

    from slackkit.outbox import drain
    from slackkit.real import RealSlack

    slack = RealSlack(slack_token)
    posted = drain(conn, slack, iso(clock.now()))
    if posted == 0:  # not filled (market closed) — still prove Slack works
        assert slack.post("#trade-log",
                          f"live-smoke: order {ticket_id[:8]} status {status}")


def test_alpaca_source_account_state_and_close_frame():
    # AlpacaSource is the ONLY module that imports alpaca-py (review A1).
    # This proves its two read paths against the real paper API: account
    # reads (equity/cash finite) and market data (close_frame has enough
    # history with no NaNs at the tail).
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set — load .env first")
    os.environ.setdefault("ALPACA_PAPER_TRADE", "true")

    import math

    import pandas as pd

    from market.source_alpaca import AlpacaSource

    src = AlpacaSource()

    acct = src.account_state()
    assert math.isfinite(acct["equity"])
    assert math.isfinite(acct["cash"])

    frame = src.close_frame(["NVDA", "SPY"], end=pd.Timestamp.now(tz="UTC"))
    assert len(frame) >= 60
    assert not frame.tail(5).isna().any().any()


# --- schema pin: the real place_stock_order parameter surface ---------------
#
# 2026-08-17, first live day: the gate validated a NESTED stop leg
# (`stop_loss: {stop_price: ...}`) that the real MCP tool has never exposed —
# it takes FLAT `stop_loss_stop_price` / `stop_loss_limit_price`. Every
# offline test agreed with the gate because FakeAlpaca and the recordings
# encoded the same wrong assumption, so a ticket carrying a stop_price was
# undeliverable in production and nothing could catch it. This pins the real
# surface: schema drift now fails loudly here instead of silently at 09:35.

STOP_LEG_FIELDS = ("stop_loss_stop_price", "stop_loss_limit_price",
                   "take_profit_limit_price")


def _tools_list() -> list[dict]:
    """tools/list the real alpaca-mcp-server under the exec seat's own
    toolsets. Read-only: initialize + list, no tool is ever called."""
    import subprocess

    from agents.seats import ALPACA_MCP_SPEC

    def send(proc, msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    env = {**os.environ, "ALPACA_TOOLSETS": "account,trading,stock-data"}
    proc = subprocess.Popen(
        ["uvx", ALPACA_MCP_SPEC], env=env, text=True, bufsize=1,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "schema-pin", "version": "1"}}})
        proc.stdout.readline()
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized",
                    "params": {}})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = json.loads(proc.stdout.readline())["result"]["tools"]
    finally:
        proc.kill()
    return tools


def _place_stock_order_schema() -> dict:
    for t in _tools_list():
        if t["name"] == "place_stock_order":
            return t["inputSchema"]
    raise AssertionError(
        "alpaca-mcp-server exposes no place_stock_order; got "
        f"{[t['name'] for t in _tools_list()]}")


def test_surface_pin_no_unpinned_broker_verb_has_appeared():
    """Checks `config/broker_tool_surface.yaml` against the real server.
    Live-only because `make test` is offline by contract — no network, no keys.

    This is DETECTION, not protection. `_broker_verb_policy` is
    deny-by-default, so an unpinned verb is ALREADY denied and nothing is
    exposed while this is red. What it buys is knowing WHEN the surface moved,
    so a new mutating verb is a decision someone makes rather than a fact
    someone discovers — which is how `close_all_positions` went unnoticed.

    On failure, classify each new name as `gated`, `mutating` or `read`. There
    is deliberately no 'harmless' bucket: `update_account_config` would have
    qualified for one, and it is the verb that can clear `no_shorting` and
    flip `suspend_trade`."""
    from pathlib import Path

    import yaml

    from agents.seats import ALPACA_MCP_SPEC

    pin = yaml.safe_load(
        (Path(__file__).resolve().parents[1]
         / "config/broker_tool_surface.yaml").read_text())
    assert pin["spec"] == ALPACA_MCP_SPEC, (
        f"pin records {pin['spec']}, agents/seats.py pins {ALPACA_MCP_SPEC};"
        " the surface is a function of the spec string, so these cannot drift")

    live = {t["name"] for t in _tools_list()}
    pinned = set(pin["gated"]) | set(pin["mutating"]) | set(pin["read"])

    appeared = sorted(live - pinned)
    assert not appeared, (
        f"unpinned broker tools: {appeared}. Each is currently DENIED by"
        " _broker_verb_policy, so nothing is exposed — classify them in"
        " config/broker_tool_surface.yaml.")

    vanished = sorted(pinned - live)
    assert not vanished, (
        f"pinned tools no longer exposed: {vanished}. A verb disappearing is"
        " not automatically good news: if a GATED one vanished, the exec seat"
        " can no longer place that order at all.")


def test_schema_pin_place_stock_order_takes_a_flat_stop_leg():
    """gate/tickets.py validate_order reads these exact names. If Alpaca ever
    renames them, or reintroduces a nested object, the gate silently stops
    matching real orders — which is precisely the 2026-08-17 failure."""
    props = _place_stock_order_schema().get("properties", {})

    missing = [f for f in STOP_LEG_FIELDS if f not in props]
    assert not missing, (
        f"place_stock_order no longer exposes {missing} — gate/tickets.py "
        f"validates against these names. Present: {sorted(props)}")

    # the shape the gate used to assume, and must never assume again
    assert "stop_loss" not in props, (
        "place_stock_order now exposes a nested 'stop_loss' object; "
        "validate_order was rewritten to the flat fields on 2026-08-17")
    assert "take_profit" not in props

    # flat legs arrive as strings, like every other numeric in this API
    for field in STOP_LEG_FIELDS:
        types = props[field].get("anyOf") or [props[field]]
        assert any(t.get("type") == "string" for t in types), (
            f"{field} is no longer a string: {props[field]}")

    # the fields the gate also reads on every order
    for field in ("client_order_id", "symbol", "side", "qty", "order_class"):
        assert field in props, f"place_stock_order lost {field!r}"

    # The 2026-08-19 rule (gate/tickets.py): a stop-carrying order must be
    # gtc, because a DAY stop leg expires at the close of the session it was
    # placed in and leaves the position naked overnight. That rule is only
    # satisfiable if the tool actually EXPOSES time_in_force — the captured
    # output omits it (tests/fixtures/alpaca/place_stock_order.json), and a
    # gate rule the seat cannot satisfy is an unplaceable order, not a guard.
    assert "time_in_force" in props, (
        "place_stock_order does not expose time_in_force — validate_order's "
        "gtc rule would deny every stopped order with no way for the seat to "
        f"comply. Present: {sorted(props)}")
    tif_types = props["time_in_force"].get("anyOf") or [props["time_in_force"]]
    assert any(t.get("type") == "string" for t in tif_types), (
        f"time_in_force is not a string: {props['time_in_force']}")

    # The DEFAULT is the mechanism of the 2026-08-17 incident: the seat did
    # not pass time_in_force, the tool supplied 'day', and the OTO stop leg
    # inherited it and expired at the bell. gate/tickets.py cites this default
    # as its reason for requiring gtc explicitly, so the citation is pinned
    # here rather than asserted from memory. If Alpaca ever changes it, the
    # gate's reasoning changes with it.
    assert props["time_in_force"].get("default") == "day", (
        "place_stock_order's time_in_force default is no longer 'day' — "
        "gate/tickets.py's docstring cites it as the reason a stopped order "
        f"must name gtc explicitly. Got: {props['time_in_force']}")


def _flatten_aapl_test_artifacts() -> list[str]:
    """Unconditional teardown for the stopped-ticket smoke: cancel every open
    AAPL order, then flatten any AAPL position.

    AAPL-ONLY BY CONSTRUCTION — it filters on symbol before every destructive
    call, so a protective stop on any other symbol (the hand-placed NVDA stop,
    say) can never be caught by it.

    Idempotent: safe to call twice, safe when nothing was ever placed.

    Swallows its own errors and RETURNS them rather than raising, because it
    runs in a `finally` — an exception here would mask the assertion failure
    that triggered it, which is the opposite of what teardown is for.

    This exists because on 2026-08-19 the cleanup lived at the END of the test,
    after every assertion. An assertion failed, cleanup never ran, and a live
    GTC market buy was left on the paper account that would have filled at the
    next open as a position with no gate ticket. A test that places real orders
    needs teardown that runs when it FAILS — that is the only time it matters.
    """
    problems: list[str] = []
    try:
        for o in _alpaca_get("/v2/orders?status=open&nested=false"):
            if o.get("symbol") != "AAPL":
                continue
            try:
                _alpaca_delete(f"/v2/orders/{o['id']}")
            except Exception as e:            # already terminal, or a 404
                problems.append(f"cancel {o['id']}: {type(e).__name__}: {e}")
    except Exception as e:
        problems.append(f"list open orders: {type(e).__name__}: {e}")
    try:
        if any(p.get("symbol") == "AAPL" for p in _alpaca_get("/v2/positions")):
            _alpaca_delete("/v2/positions/AAPL")
    except Exception as e:
        problems.append(f"flatten AAPL: {type(e).__name__}: {e}")
    return problems


def test_a_stopped_ticket_places_with_a_flat_stop_leg(tmp_path):
    """The path the 2026-08-17 outage lived on, end to end against the real
    broker.

    Everything about a STOPPED ticket was undeliverable that day: the gate
    validated a nested `stop_loss: {stop_price}` the tool has never exposed,
    the seat could not satisfy both the gate and the broker, and no order was
    ever placed. The other smoke seeds stop_price NULL, so it exercises the
    plain path only — this class of failure could recur under a fully green
    `pytest -m live` run.

    This is the ONLY test that proves charter -> seat -> flat
    stop_loss_stop_price -> order gate -> broker actually connects. It places
    a REAL 1-share paper order with a resting stop and cleans up after
    itself."""
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set — load .env first")

    from datetime import timedelta

    from agents.exec_turn import run_exec_turn
    from agents.trader import build_trader_options, load_seat_config
    from agents.wallclock import WallClock
    from market.source_alpaca import AlpacaSource

    # MARKET MUST BE OPEN, and this SKIPS rather than passes when it is not.
    # With the market shut the parent market order cannot fill, so its OTO
    # child stays `held` forever and the leg-visibility assertion below is
    # unreachable — the run would prove only the half that needs no fill.
    # That is exactly the 2026-08-18 defect: the timer rehearsal ran against a
    # closed market, exited early, and passed WHILE CONCEALING the thing it
    # existed to check. A skip is loud; a green tick that proves nothing is not.
    if not AlpacaSource().market_clock()["is_open"]:
        pytest.skip("market is closed — the parent cannot fill, so the stop "
                    "leg stays 'held' and leg visibility cannot be checked. "
                    "Re-run during regular hours; a pass here would be a lie.")
    from gate.tickets import create_ticket
    from orchestrator.clock import iso
    from state.db import connect

    # A stop far BELOW the market: it must rest unfilled, never turn this into
    # an accidental exit. Priced off the live quote so it cannot go stale.
    price = _latest_trade_price("AAPL")
    stop_price = round(price * 0.70, 2)          # 30% below: rests, never fires

    clock = WallClock()
    db_path = tmp_path / "live-smoke-stop.sqlite"
    conn = connect(db_path)
    now = iso(clock.now())
    ticket_id = str(uuid.uuid4())
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " (?, 'AAPL', 'buy', 1, 'live smoke: stopped ticket', 'n/a', ?,"
        " 'approved', ?)", (now[:10], stop_price, now))
    conn.commit()
    create_ticket(conn, id=ticket_id, decision_id=cur.lastrowid,
                  ticker="AAPL", side="buy", max_qty=1, stop_price=stop_price,
                  expires_at_iso=iso(clock.now() + timedelta(minutes=45)),
                  now_iso=now)

    async def run_turn():
        from claude_agent_sdk import ClaudeSDKClient
        opts = build_trader_options(
            load_seat_config("agents/config/exec.yaml"), db_path, clock)
        async with ClaudeSDKClient(options=opts) as client:
            return await run_exec_turn(
                client,
                "Execution stage: execute all open tickets per your charter.",
                {"alpaca", "fund"}, open_ticket_count=1)

    # EVERYTHING from the placement onward is inside try/finally. The seat is
    # about to put a REAL order on the account, and the assertions below are
    # the ones under test — which makes them the ones that can fail. Teardown
    # that only runs on success is teardown that never runs when it matters.
    try:
        tool_calls = asyncio.run(run_turn())
        assert any(t.startswith("mcp__alpaca__place_") for t in tool_calls), (
            f"seat never attempted a placement: {tool_calls}")

        # THE assertion: the order reached the broker at all. On 2026-08-17
        # this is where it died — the gate denied every shape the seat could
        # send.
        order = _alpaca_get(
            f"/v2/orders:by_client_order_id?client_order_id={ticket_id}")
        assert order.get("client_order_id") == ticket_id, order

        # ...carrying the ticket's stop, as a real oto leg the broker accepted.
        # Polled: on an oto the child is created held and can lag the parent in
        # the API by a moment, so an immediate read is a false negative.
        assert order.get("order_class") == "oto", order.get("order_class")
        leg_stops = []
        for _ in range(10):
            order = _alpaca_get(
                f"/v2/orders:by_client_order_id?client_order_id={ticket_id}")
            leg_stops = [float(l["stop_price"])
                         for l in (order.get("legs") or [])
                         if l.get("stop_price")]
            if leg_stops:
                break
            time.sleep(2)
        assert stop_price in leg_stops, (
            f"ticket stop {stop_price} not on the order's legs: {leg_stops}")

        # ...with a lifetime that OUTLIVES the session. This is the 2026-08-19
        # class: on 08-17 the parent and its leg both went in tif DAY, the leg
        # expired at 20:00:06Z the same day, and NVDA 80 sat unprotected for
        # two sessions while the DB asserted a live stop at 215.
        # gate/tickets.py now denies a stop-carrying order that is not gtc —
        # this is the other half of that rule, and the only place it can be
        # checked: that Alpaca ACCEPTS gtc on an OTO market parent and hands
        # the lifetime down to the leg. If the leg comes back 'day', the stop
        # dies at the bell and the gate rule bought nothing.
        assert str(order.get("time_in_force")).lower() == "gtc", (
            f"parent time_in_force is {order.get('time_in_force')!r}, not gtc")
        legs = order.get("legs") or []
        assert legs, f"no stop leg on the placed order: {order}"
        for leg in legs:
            assert str(leg.get("time_in_force")).lower() == "gtc", (
                f"stop leg time_in_force is {leg.get('time_in_force')!r}, not"
                " gtc — it will expire at the close and leave the position"
                " naked")

        # ...and once the parent FILLS, that leg is visible to the protection
        # assertion. Measured 2026-08-19: a `held` OTO child is NOT returned by
        # QueryOrderStatus.OPEN, so this must be checked after the fill
        # activates it — which is also the only moment that matters, because
        # orchestrator/protection.py runs after reconciliation, when a position
        # exists precisely because its parent filled. If the activated leg were
        # invisible here, protection.py would report every correctly-stopped
        # position as naked, every day, and the alert channel would be dead in
        # a week. tests/fake_alpaca.py cannot settle it — the fake picks the
        # leg's status itself, a fixture agreeing with our code while both may
        # disagree with Alpaca, which is the 2026-08-17 defect exactly.
        source = AlpacaSource()
        for _ in range(20):
            parent = _alpaca_get(
                f"/v2/orders:by_client_order_id?client_order_id={ticket_id}")
            if parent["status"] == "filled":
                break
            time.sleep(3)
        assert parent["status"] == "filled", (
            f"parent never filled ({parent['status']}) — leg visibility is "
            "unproven, not proven")
        live = []
        for _ in range(10):                 # the leg activates a beat later
            live = [o for o in source.open_orders() if o["symbol"] == "AAPL"
                    and o["side"] == "sell" and o["type"].startswith("stop")]
            if live:
                break
            time.sleep(2)
        assert live, (
            "the activated stop leg is invisible to open_orders() — "
            "protection.py would call this position naked every day. Saw: "
            f"{[o for o in source.open_orders() if o['symbol'] == 'AAPL']}")

        # ...and the recorder mirrored it, so the DB tells the truth about it
        row = conn.execute("SELECT * FROM orders WHERE client_order_id = ?",
                           (ticket_id,)).fetchone()
        assert row is not None, "recorder did not write the order row"
    finally:
        leftovers = _flatten_aapl_test_artifacts()
        if leftovers:
            # Printed, never raised: raising here would replace the real
            # failure with a teardown error and hide what actually broke.
            print("\n!!! LIVE SMOKE TEARDOWN INCOMPLETE — CHECK THE ACCOUNT:")
            for problem in leftovers:
                print("   ", problem)
