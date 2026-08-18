"""Exec-seat turn safety assertions.

IMPORTANT — these are DETECTORS, not preventers. They fire AFTER a tool call
has already executed (check_tool_calls) or inspect session state; they cannot
un-place an order. The PREVENTER is `tools=['mcp__fund__*','mcp__alpaca__*']`
in the seat options: it removes Bash/Write/Task from the callable surface so an
out-of-scope call can never be attempted. Never relax `tools` on the theory
that these checks will catch it — by the time check_tool_calls sees a Bash
call, the shell already ran. Defense in depth, downstream of the real gate.

"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

ALLOWED_TOOL_PREFIXES = ("mcp__fund__", "mcp__alpaca__")
# The one call that actually executes a ticket. Kept in step with
# agents/runtime.py's PLACE_PREFIX — the order gate hooks the same names.
PLACE_PREFIX = "mcp__alpaca__place_"


class ExecTurnViolation(Exception):
    """A hard failure in an exec turn: never resolves to a silent success."""


def _server_statuses(source) -> dict:
    """Normalize a server-status container to {name: status}. Accepts the init
    message data (``mcp_servers``), a get_mcp_status response (``mcpServers``),
    or a bare list of {name,status}."""
    if isinstance(source, dict):
        servers = source.get("mcp_servers") or source.get("mcpServers") or []
    else:
        servers = source
    return {s.get("name"): s.get("status") for s in servers}


def unconnected_servers(source, required: set[str]) -> dict:
    """Required servers whose status is not 'connected' (empty => all ready)."""
    status = _server_statuses(source)
    return {name: status.get(name) for name in required
            if status.get(name) != "connected"}


def check_required_servers(source, required: set[str]) -> None:
    """(c) Raise if any required MCP server is not 'connected'.

    Closes the uvx cold-start race: an agent whose broker server is still
    'pending' has no place tool and improvises (the observed Bash detour).
    The turn must not run until every required server is connected."""
    bad = unconnected_servers(source, required)
    if bad:
        raise ExecTurnViolation(
            f"required MCP server(s) not connected: {bad}; turn must not run")


async def await_servers_connected(
        client, required: set[str], *, timeout_s: float = 30.0,
        poll_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """(c) live gate: poll client.get_mcp_status() until every required server
    is 'connected', or raise ExecTurnViolation after timeout_s elapses. `sleep`
    is injected so the wait is deterministic in tests (no wall clock)."""
    elapsed = 0.0
    while True:
        bad = unconnected_servers(await client.get_mcp_status(), required)
        if not bad:
            return
        if elapsed >= timeout_s:
            raise ExecTurnViolation(
                f"required MCP server(s) not connected after {timeout_s}s: "
                f"{bad}; turn does not run")
        await sleep(poll_s)
        elapsed += poll_s


async def run_seat_turn(client, prompt: str, required: set[str], *,
                        wait_timeout_s: float = 30.0, poll_s: float = 0.5,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
                        ) -> tuple[list[str], object | None]:
    """One turn for ANY seat: (c) wait for the required MCP servers, query,
    drain the response. Returns (tool-call names, ResultMessage | None).

    The result is handed back rather than swallowed because the live
    composition root records every turn's cost off it
    (agents.runtime.record_turn_result). Matched by type NAME, not isinstance,
    so this module keeps its zero SDK imports and stays offline-testable.

    Applies NO (a)/(b) tool-call assertions — analyst and PM degrade to the
    orchestrator's neutral/0 and pm_timeout defaults on a quiet turn
    (invariant 4). run_exec_turn adds those assertions for the one seat where
    a silent no-op is a hard failure."""
    await await_servers_connected(client, required, timeout_s=wait_timeout_s,
                                  poll_s=poll_s, sleep=sleep)
    await client.query(prompt)
    tool_names: list[str] = []
    result = None
    async for msg in client.receive_response():
        if type(msg).__name__ == "ResultMessage":
            result = msg
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            for block in content:
                if type(block).__name__ == "ToolUseBlock":
                    name = getattr(block, "name", None)
                    if name:
                        tool_names.append(name)
    return tool_names, result


async def run_exec_turn(client, prompt: str, required: set[str], *,
                        open_ticket_count: int = 0,
                        wait_timeout_s: float = 30.0, poll_s: float = 0.5,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
                        ) -> list[str]:
    """Production exec turn wrapping a connected ClaudeSDKClient with the three
    safety assertions. (c) pre: wait for required servers before querying — a
    broker that never connects fails the turn instead of letting the agent run
    tool-starved. (a)/(b) post: check the tool calls actually attempted.

    Returns the tool-call names made this turn. Raises ExecTurnViolation on any
    violation. NOTE: (a)/(b) are DETECTORS — they see calls after they ran; the
    PREVENTER is the seat's `tools=[...]` allow-array (no Bash to reach for)."""
    tool_names, _ = await run_seat_turn(
        client, prompt, required, wait_timeout_s=wait_timeout_s,
        poll_s=poll_s, sleep=sleep)
    check_tool_calls(tool_names, open_ticket_count)
    return tool_names


def check_tool_calls(tool_names: list[str], open_ticket_count: int = 0) -> None:
    """(a)/(b)/(c) Raise on zero tool calls, any call outside the two globs, or
    a turn that never attempted a placement while tickets were open.

    (a) A turn that touched nothing is a silent no-op, not a success — the
    seat exists to execute open tickets. (b) Any name outside
    ALLOWED_TOOL_PREFIXES is off-mandate; this asserts on calls ATTEMPTED and
    is invariant to the permission layer.

    (c) is the first live day's lesson (2026-08-17). The seat read
    list_open_tickets, never reached a place_* call, and (a) passed it because
    it HAD called a tool. A turn holding an authorized ticket that never even
    attempts to place it is the same silent no-op (a) exists to catch, one
    step further in. This asserts on the ATTEMPT, not the outcome: a denied or
    broker-rejected placement is a different failure, reported elsewhere, and
    must not be conflated with never trying."""
    if not tool_names:
        raise ExecTurnViolation(
            "exec turn made zero tool calls — silent no-op is a hard failure, "
            "not a success")
    off = [t for t in tool_names
           if not any(t.startswith(p) for p in ALLOWED_TOOL_PREFIXES)]
    if off:
        raise ExecTurnViolation(
            f"exec turn called tool(s) outside {ALLOWED_TOOL_PREFIXES}: {off}")
    if open_ticket_count > 0 and not any(
            t.startswith(PLACE_PREFIX) for t in tool_names):
        raise ExecTurnViolation(
            f"exec turn attempted no {PLACE_PREFIX}* call with "
            f"{open_ticket_count} open ticket(s) — reading the tickets and "
            f"stopping is not execution; called {tool_names}")
