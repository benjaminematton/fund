from datetime import datetime, timezone

import pytest

from orchestrator.clock import SimClock, iso
from state.db import connect


@pytest.fixture
def fund_db(tmp_path):
    """Temp SQLite with the full contracts.md §2 DDL applied (acceptance §0)."""
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def sim_clock():
    """11:30 ET on the golden day (15:30 UTC)."""
    return SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))


def make_executor(conn_factory, clock, broker, seat=None):
    """Real tool execution for replay mode (acceptance §0): Alpaca tools hit
    the in-memory broker; fund tools hit the real temp DB. `seat` binds the
    submit_signal/submit_decision handlers' seat guard — the same seat every
    line in a Task-10-style recording carries, so callers replaying a single
    seat's recording pass it once here rather than per line."""
    from gate.tickets import open_tickets

    from agents.tools.fund_server import (handle_submit_decision,
                                          handle_submit_signal,
                                          run_date_from_clock)
    from tests.fake_alpaca import mcp_envelope

    def execute(tool: str, args: dict):
        if tool.startswith("mcp__alpaca__place_"):
            # Wrap the broker response the way the real alpaca-mcp-server does,
            # so the recorder sees the true wire shape (JSON string + `data`).
            return mcp_envelope(broker.place_order(args))
        if tool == "mcp__fund__list_open_tickets":
            return open_tickets(conn_factory(), iso(clock.now()))
        if tool == "mcp__fund__submit_signal":
            return handle_submit_signal(
                conn_factory(), seat=seat, args=args,
                run_date=run_date_from_clock(clock), now_iso=iso(clock.now()))
        if tool == "mcp__fund__submit_decision":
            return handle_submit_decision(
                conn_factory(), seat=seat, args=args,
                run_date=run_date_from_clock(clock), now_iso=iso(clock.now()))
        raise ValueError(f"no executor for tool {tool!r}")

    return execute
