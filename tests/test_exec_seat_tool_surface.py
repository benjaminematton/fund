"""Regression guard for every seat's tool surface + settings isolation.

The exec seat places real (paper) orders; analyst and pm are read-only. Every
seat's entire capability surface must be a diff: exactly the two MCP tool
globs, nothing from the claude_code preset (no Bash/Write/Edit/Task/Agent/
Workflow/Web/Cron), and no on-disk settings or CLAUDE.md feeding its context
or permission surface. Read-only seats additionally must never be able to
reach `mcp__alpaca__place_*` (invariant 2).

`tools` governs AVAILABILITY; `allowed_tools`/`disallowed_tools` only govern
APPROVAL. Leaving `tools` unset inherits the full coding-agent surface — which
lets a seat route around the gate/recorder/default-HOLD entirely (Read .env ->
curl broker). This test pins the levers that close that hole so a harness
change or a reverted config cannot silently regrow shell access, or silently
grant a read-only seat the `trading` toolset.

These assertions intentionally FAIL against the pre-fix config (tools=None,
setting_sources=["project"]). That failure is the point: it is the instrument
that measures the fix.
"""

from datetime import datetime, timezone

import pytest

from agents.seats import build_seat_options, load_seat_config
from orchestrator.clock import SimClock

BANNED_BUILTINS = (
    "Bash", "Write", "Edit", "NotebookEdit", "Task", "Agent", "Workflow",
    "WebFetch", "WebSearch", "ScheduleWakeup", "CronCreate", "CronDelete",
    "Read", "Skill", "ToolSearch",
)

SEATS = ("exec", "analyst", "news", "pm", "critic")


def _cfg(seat: str) -> dict:
    return load_seat_config(f"agents/config/{seat}.yaml")


def _opts(seat: str, tmp_path):
    cfg = _cfg(seat)
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    return build_seat_options(cfg, tmp_path / "fund.sqlite", clock)


@pytest.mark.parametrize("seat", SEATS)
def test_tools_is_explicit_not_the_full_preset(seat, tmp_path):
    # None => the CLI applies the claude_code preset (Bash/Write/Task/...).
    # The seat MUST pass an explicit allow-array.
    assert _opts(seat, tmp_path).tools is not None


@pytest.mark.parametrize("seat", SEATS)
def test_tools_are_exactly_the_two_mcp_globs(seat, tmp_path):
    assert _opts(seat, tmp_path).tools == ["mcp__fund__*", "mcp__alpaca__*"]


def test_the_alpaca_glob_is_safe_only_because_the_gate_denies_what_it_admits():
    """The assertion above pins a WILDCARD, and for a while that was the whole
    check — which is how a seat holding `alpaca_toolsets: trading` passed a
    test named for its tool surface while `cancel_all_orders` and
    `close_all_positions` were reachable with no ticket and no `orders` row.

    `mcp__alpaca__*` is the right shape: enumerating every read verb would
    break the exec turn the first time the toolset grew a getter. What makes it
    SAFE is that the PreToolUse gate allowlists what the glob admits. This
    pins the two together, so loosening either one alone reddens here.

    Deliberately asserted at the policy level rather than by driving the hook:
    this file is about what the SEAT can reach, and tests/test_runtime_hooks.py
    owns the hook's behaviour. All this claims is that the two agree."""
    from agents.runtime import _broker_verb_policy

    for verb in ("cancel_order_by_id", "cancel_all_orders", "close_position",
                 "close_all_positions"):
        assert _broker_verb_policy(f"mcp__alpaca__{verb}") == "deny", verb
    # ...and the glob must still admit the surface the seat genuinely needs
    assert _broker_verb_policy("mcp__alpaca__place_stock_order") == "gated"
    assert _broker_verb_policy("mcp__alpaca__get_account_info") == "allow"


@pytest.mark.parametrize("seat", SEATS)
def test_no_builtin_tool_is_available_to_the_seat(seat, tmp_path):
    tools = _opts(seat, tmp_path).tools or []
    leaked = [t for t in tools if t in BANNED_BUILTINS]
    assert leaked == [], f"seat can call built-in tools: {leaked}"


@pytest.mark.parametrize("seat", SEATS)
def test_setting_sources_empty_no_claude_md_or_project_settings(seat, tmp_path):
    # setting_sources=[] => --setting-sources= (nothing). No CLAUDE.md, no
    # project/local settings.json feeding context or the permission allow-list.
    assert _opts(seat, tmp_path).setting_sources == []


@pytest.mark.parametrize("seat", SEATS)
def test_permission_mode_is_dont_ask(seat, tmp_path):
    assert _opts(seat, tmp_path).permission_mode == "dontAsk"


@pytest.mark.parametrize("seat", ["analyst", "news", "pm", "critic"])
def test_read_only_seats_cannot_trade(seat, tmp_path):
    opts = _opts(seat, tmp_path)
    assert "trading" not in _cfg(seat)["alpaca_toolsets"]
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    # The yaml value is inert unless it's actually threaded into the built
    # options — this is what the alpaca-mcp-server subprocess reads to decide
    # which tools to REGISTER at all (the only load-bearing lock for this
    # seat's `mcp__alpaca__*` glob).
    env = opts.mcp_servers["alpaca"]["env"]
    assert env["ALPACA_TOOLSETS"] == _cfg(seat)["alpaca_toolsets"]
    assert "trading" not in env["ALPACA_TOOLSETS"]


def test_only_exec_has_trading_toolset(tmp_path):
    assert "trading" in _cfg("exec")["alpaca_toolsets"]
    env = _opts("exec", tmp_path).mcp_servers["alpaca"]["env"]
    assert env["ALPACA_TOOLSETS"] == _cfg("exec")["alpaca_toolsets"]
    assert "trading" in env["ALPACA_TOOLSETS"]


@pytest.mark.parametrize("seat", ["analyst", "news", "pm", "critic"])
def test_read_only_seats_carry_no_order_hooks(seat, tmp_path):
    # Only the trading seat may carry the PreToolUse order gate / PostToolUse
    # recorder (CLAUDE.md: hooks attach only to a seat that trades).
    assert _opts(seat, tmp_path).hooks in (None, {})


def test_exec_carries_both_order_hooks(tmp_path):
    hooks = _opts("exec", tmp_path).hooks
    assert hooks and "PreToolUse" in hooks and "PostToolUse" in hooks


@pytest.mark.parametrize("seat", SEATS)
def test_the_seat_yaml_budget_cap_is_threaded_into_the_options(seat, tmp_path):
    """max_budget_usd is the only hard stop on a runaway turn. A yaml value
    that is not actually threaded into the built options is inert — the SDK
    would apply no cap at all, and the first evidence would be the bill.

    The caps are BACKSTOPS, not the expectation: they sum to $2.25 worst case
    against an expected spend under $0.50/day (README "Cost"). What bounds the
    expectation is the watchlist size and the per-seat max_turns, not these."""
    cap = _cfg(seat)["max_budget_usd"]
    assert isinstance(cap, (int, float)) and cap > 0
    assert _opts(seat, tmp_path).max_budget_usd == cap
