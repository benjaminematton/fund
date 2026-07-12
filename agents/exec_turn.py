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

ALLOWED_TOOL_PREFIXES = ("mcp__fund__", "mcp__alpaca__")


class ExecTurnViolation(Exception):
    """A hard failure in an exec turn: never resolves to a silent success."""


def check_required_servers(init_data: dict, required: set[str]) -> None:
    """(c) Raise if any required MCP server is not 'connected' at turn start.

    Closes the uvx cold-start race: an agent whose broker server is still
    'pending' has no place tool and improvises (the observed Bash detour).
    The turn must not run until every required server is connected."""
    status = {s.get("name"): s.get("status")
              for s in init_data.get("mcp_servers", [])}
    bad = {name: status.get(name) for name in required
           if status.get(name) != "connected"}
    if bad:
        raise ExecTurnViolation(
            f"required MCP server(s) not connected at turn start: {bad}; "
            "turn must not run")


def check_tool_calls(tool_names: list[str]) -> None:
    """(a)/(b) Raise on zero tool calls, or any call outside the two globs.

    (a) A turn that touched nothing is a silent no-op, not a success — the
    seat exists to execute open tickets. (b) Any name outside
    ALLOWED_TOOL_PREFIXES is off-mandate; this asserts on calls ATTEMPTED and
    is invariant to the permission layer."""
    if not tool_names:
        raise ExecTurnViolation(
            "exec turn made zero tool calls — silent no-op is a hard failure, "
            "not a success")
    off = [t for t in tool_names
           if not any(t.startswith(p) for p in ALLOWED_TOOL_PREFIXES)]
    if off:
        raise ExecTurnViolation(
            f"exec turn called tool(s) outside {ALLOWED_TOOL_PREFIXES}: {off}")
