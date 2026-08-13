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

SEATS = ("exec", "analyst", "pm")


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


@pytest.mark.parametrize("seat", ["analyst", "pm"])
def test_read_only_seats_cannot_trade(seat, tmp_path):
    opts = _opts(seat, tmp_path)
    assert "trading" not in _cfg(seat)["alpaca_toolsets"]
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])


def test_only_exec_has_trading_toolset(tmp_path):
    assert "trading" in _cfg("exec")["alpaca_toolsets"]
