"""run_exec_turn wired against a fake ClaudeSDKClient — the turn logic is
offline-testable without the SDK. Proves the three assertions fire through the
real runner path (not just the pure helpers): (c) waits for the broker and
never queries if it stays down; (a)/(b) inspect the calls actually attempted.
"""

import asyncio

import pytest

from agents.exec_turn import ExecTurnViolation, run_exec_turn

REQUIRED = {"alpaca", "fund"}


class ToolUseBlock:  # name-matched by the runner (type(b).__name__)
    def __init__(self, name):
        self.name = name


class _Msg:
    def __init__(self, blocks):
        self.content = blocks


class FakeClient:
    """Scripts get_mcp_status() from a status sequence (last entry repeats),
    records query() calls, and streams preset tool-use messages."""

    def __init__(self, status_sequence, tool_names):
        self._status_seq = list(status_sequence)
        self._tool_names = tool_names
        self.query_calls = 0

    async def get_mcp_status(self):
        servers = (self._status_seq.pop(0) if len(self._status_seq) > 1
                   else self._status_seq[0])
        return {"mcpServers": servers}

    async def query(self, prompt):
        self.query_calls += 1

    async def receive_response(self):
        for name in self._tool_names:
            yield _Msg([ToolUseBlock(name)])


async def _noop_sleep(_):
    return None


def _connected(*names):
    return [{"name": n, "status": "connected"} for n in names]


def _run(coro):
    return asyncio.run(coro)


def test_runs_once_servers_connect_and_returns_tool_names():
    # alpaca pending on the first poll, connected on the second (uvx warmup).
    client = FakeClient(
        status_sequence=[[{"name": "alpaca", "status": "pending"},
                          {"name": "fund", "status": "connected"}],
                         _connected("alpaca", "fund")],
        tool_names=["mcp__fund__list_open_tickets",
                    "mcp__alpaca__place_stock_order"])
    names = _run(run_exec_turn(client, "execute", REQUIRED,
                               wait_timeout_s=5, poll_s=0.5, sleep=_noop_sleep))
    assert names == ["mcp__fund__list_open_tickets",
                     "mcp__alpaca__place_stock_order"]
    assert client.query_calls == 1


def test_broker_never_connects_blocks_turn_and_never_queries():
    client = FakeClient(
        status_sequence=[[{"name": "alpaca", "status": "pending"},
                          {"name": "fund", "status": "connected"}]],
        tool_names=["mcp__alpaca__place_stock_order"])
    with pytest.raises(ExecTurnViolation):
        _run(run_exec_turn(client, "execute", REQUIRED,
                           wait_timeout_s=1, poll_s=0.5, sleep=_noop_sleep))
    assert client.query_calls == 0  # (c): the turn must not run


def test_zero_tool_calls_is_hard_failure_through_runner():
    client = FakeClient(status_sequence=[_connected("alpaca", "fund")],
                        tool_names=[])
    with pytest.raises(ExecTurnViolation):
        _run(run_exec_turn(client, "execute", REQUIRED, sleep=_noop_sleep))
    assert client.query_calls == 1  # it queried, but the turn did nothing


def test_out_of_glob_call_is_hard_failure_through_runner():
    client = FakeClient(status_sequence=[_connected("alpaca", "fund")],
                        tool_names=["mcp__fund__list_open_tickets", "Bash"])
    with pytest.raises(ExecTurnViolation):
        _run(run_exec_turn(client, "execute", REQUIRED, sleep=_noop_sleep))
