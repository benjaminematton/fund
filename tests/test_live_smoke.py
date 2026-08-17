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


def _place_stock_order_schema() -> dict:
    """tools/list the real alpaca-mcp-server and return place_stock_order's
    JSON schema. Read-only: initialize + list, no tool is ever called."""
    import subprocess

    def send(proc, msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    env = {**os.environ, "ALPACA_TOOLSETS": "account,trading,stock-data"}
    proc = subprocess.Popen(
        ["uvx", "alpaca-mcp-server"], env=env, text=True, bufsize=1,
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
    for t in tools:
        if t["name"] == "place_stock_order":
            return t["inputSchema"]
    raise AssertionError(
        f"alpaca-mcp-server exposes no place_stock_order; got {[t['name'] for t in tools]}")


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
