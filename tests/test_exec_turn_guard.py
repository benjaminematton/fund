"""Turn-level safety assertions for the exec seat (the instrument, written
before the fix). These are pure checks the live turn wraps around a real
ClaudeSDKClient run, so the enforcement is offline-testable without the SDK:

  (a) an exec turn that made ZERO tool calls is a hard failure — the first live
      run returned `success` with no tool calls (silent no-op = the bug behind
      the bug); a turn that touched nothing must raise, never look like success.
  (b) ANY tool call outside mcp__fund__* / mcp__alpaca__* is a hard failure.
      This asserts on calls ATTEMPTED and is invariant to the permission layer:
      allow-lists govern approval, this governs the callable boundary.
  (c) a required MCP server not `connected` at turn start means the turn does
      NOT run — closes the uvx cold-start race that caused the Bash improvisation
      (agent had no place tool yet, but did have a shell).

agents.exec_turn does not exist yet — this module is RED until the fix builds it.
"""

import pytest

from agents.exec_turn import (ExecTurnViolation, check_required_servers,
                              check_tool_calls)


# (c) required servers connected at turn start
def test_all_required_servers_connected_passes():
    init = {"mcp_servers": [{"name": "alpaca", "status": "connected"},
                            {"name": "fund", "status": "connected"}]}
    check_required_servers(init, {"alpaca", "fund"})  # no raise


def test_pending_required_server_blocks_turn():
    init = {"mcp_servers": [{"name": "alpaca", "status": "pending"},
                            {"name": "fund", "status": "connected"}]}
    with pytest.raises(ExecTurnViolation):
        check_required_servers(init, {"alpaca", "fund"})


def test_missing_required_server_blocks_turn():
    init = {"mcp_servers": [{"name": "fund", "status": "connected"}]}
    with pytest.raises(ExecTurnViolation):
        check_required_servers(init, {"alpaca", "fund"})


# (a) zero tool calls is a hard failure
def test_zero_tool_calls_is_hard_failure():
    with pytest.raises(ExecTurnViolation):
        check_tool_calls([])


# (b) any call outside the two globs is a hard failure
def test_all_calls_within_globs_pass():
    check_tool_calls(["mcp__fund__list_open_tickets",
                      "mcp__alpaca__place_stock_order"])  # no raise


def test_bash_call_is_hard_failure():
    with pytest.raises(ExecTurnViolation):
        check_tool_calls(["mcp__fund__list_open_tickets", "Bash"])


def test_subagent_spawn_is_hard_failure():
    with pytest.raises(ExecTurnViolation):
        check_tool_calls(["Task"])


def test_toolsearch_is_hard_failure():
    # ToolSearch is a built-in the seat must never reach for.
    with pytest.raises(ExecTurnViolation):
        check_tool_calls(["ToolSearch", "mcp__fund__list_open_tickets"])
