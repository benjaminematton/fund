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


# --- (c) open tickets demand an ATTEMPT (first live day, 2026-08-17) --------

def test_reading_the_tickets_and_stopping_is_not_execution():
    """THE first-live-day failure. The exec seat called list_open_tickets,
    never reached a place_* call, and (a) passed it because it HAD called a
    tool. The turn billed four turns, the stage checkpointed done, and the
    only signal was the end-of-day audit noticing a stranded decision."""
    with pytest.raises(ExecTurnViolation) as e:
        check_tool_calls(["mcp__fund__list_open_tickets"], open_ticket_count=1)
    assert "no mcp__alpaca__place_* call" in str(e.value)
    assert "1 open ticket" in str(e.value)
    # the names are named, so the log says what it DID do
    assert "mcp__fund__list_open_tickets" in str(e.value)


def test_an_attempted_placement_satisfies_the_check_even_if_it_was_denied():
    """(c) asserts on the ATTEMPT, not the outcome. A placement the order gate
    denied, or the broker rejected, is a DIFFERENT failure with its own alert
    — conflating the two would make a correctly-blocked order look like a lazy
    seat, and would fire twice for one incident."""
    check_tool_calls(["mcp__fund__list_open_tickets",
                      "mcp__alpaca__place_stock_order"], open_ticket_count=1)


def test_no_open_tickets_means_no_placement_is_required():
    """A hold day: the gate approved nothing, so a turn that only reads is
    correct and must stay silent."""
    check_tool_calls(["mcp__fund__list_open_tickets"], open_ticket_count=0)
    check_tool_calls(["mcp__fund__list_open_tickets"])          # default


def test_the_older_two_checks_still_bite_with_tickets_open():
    """(c) is additive — it must not shadow (a) or (b)."""
    with pytest.raises(ExecTurnViolation) as zero:
        check_tool_calls([], open_ticket_count=1)
    assert "zero tool calls" in str(zero.value)
    with pytest.raises(ExecTurnViolation) as off:
        check_tool_calls(["Bash"], open_ticket_count=1)
    assert "outside" in str(off.value)
