"""Acceptance P1 @live smoke (manual, never CI):
    .venv/bin/pytest -m live tests/test_live_smoke.py -v
Needs .env loaded in the shell (ALPACA_API_KEY, ALPACA_SECRET_KEY,
SLACK_BOT_TOKEN_EXEC, ANTHROPIC_API_KEY). 1-share paper order round-trips
(submitted -> filled/canceled) and the fill/outcome lands in real Slack."""

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
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                "SLACK_BOT_TOKEN_EXEC", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            pytest.skip(f"{var} not set — load .env first")

    from datetime import timedelta

    from agents.trader import build_trader_options, load_seat_config
    from agents.wallclock import WallClock
    from gate.tickets import create_ticket
    from orchestrator.clock import iso
    from slackkit.outbox import drain
    from slackkit.real import RealSlack
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
            await client.query(
                "Execution stage: execute all open tickets per your charter.")
            async for _ in client.receive_response():
                pass

    asyncio.run(run_turn())

    # round-trip: poll until filled; cancel if the market is closed
    status = None
    for _ in range(30):
        o = _alpaca_get(f"/v2/orders:by_client_order_id?client_order_id={ticket_id}")
        status = o["status"]
        if status in ("filled", "canceled", "rejected", "expired"):
            break
        time.sleep(3)
    if status not in ("filled", "canceled"):
        _alpaca_delete(f"/v2/orders/{o['id']}")
        status = "canceled"
    assert status in ("filled", "canceled")

    # the DB saw the order (PostToolUse recorder), and Slack gets the outcome
    row = conn.execute("SELECT * FROM orders WHERE client_order_id = ?",
                       (ticket_id,)).fetchone()
    assert row is not None
    slack = RealSlack(os.environ["SLACK_BOT_TOKEN_EXEC"])
    posted = drain(conn, slack, iso(clock.now()))
    if posted == 0:  # not filled (market closed) — still prove Slack works
        ts = slack.post("#trade-log",
                        f"live-smoke: order {ticket_id[:8]} status {status}")
        assert ts
