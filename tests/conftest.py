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


def make_executor(conn_factory, clock, broker):
    """Real tool execution for replay mode (acceptance §0): Alpaca tools hit
    the in-memory broker; fund tools hit the real temp DB."""
    from gate.tickets import open_tickets

    def execute(tool: str, args: dict):
        if tool.startswith("mcp__alpaca__place_"):
            return broker.place_order(args)
        if tool == "mcp__fund__list_open_tickets":
            return open_tickets(conn_factory(), iso(clock.now()))
        raise ValueError(f"no executor for tool {tool!r}")

    return execute
