"""Regression guard for the exec seat's tool surface + settings isolation.

The exec seat places real (paper) orders. Its entire capability surface must be
a diff: exactly the two MCP tool globs, nothing from the claude_code preset
(no Bash/Write/Edit/Task/Agent/Workflow/Web/Cron), and no on-disk settings or
CLAUDE.md feeding its context or permission surface.

`tools` governs AVAILABILITY; `allowed_tools` only governs APPROVAL. Leaving
`tools` unset inherits the full coding-agent surface — which lets the seat route
around the gate/recorder/default-HOLD entirely (Read .env -> curl broker). This
test pins the two levers that close that hole so a harness change or a reverted
config cannot silently regrow shell access on a seat that trades.

These assertions intentionally FAIL against the pre-fix config (tools=None,
setting_sources=["project"]). That failure is the point: it is the instrument
that measures the fix.
"""

from datetime import datetime, timezone

from agents.trader import build_trader_options, load_seat_config
from orchestrator.clock import SimClock

BANNED_BUILTINS = (
    "Bash", "Write", "Edit", "NotebookEdit", "Task", "Agent", "Workflow",
    "WebFetch", "WebSearch", "ScheduleWakeup", "CronCreate", "CronDelete",
    "Read", "Skill", "ToolSearch",
)


def _opts(tmp_path):
    cfg = load_seat_config("agents/config/exec.yaml")
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    return build_trader_options(cfg, tmp_path / "fund.sqlite", clock)


def test_tools_is_explicit_not_the_full_preset(tmp_path):
    # None => the CLI applies the claude_code preset (Bash/Write/Task/...).
    # The seat MUST pass an explicit allow-array.
    assert _opts(tmp_path).tools is not None


def test_tools_are_exactly_the_two_mcp_globs(tmp_path):
    assert _opts(tmp_path).tools == ["mcp__fund__*", "mcp__alpaca__*"]


def test_no_builtin_tool_is_available_to_the_seat(tmp_path):
    tools = _opts(tmp_path).tools or []
    leaked = [t for t in tools if t in BANNED_BUILTINS]
    assert leaked == [], f"seat can call built-in tools: {leaked}"


def test_setting_sources_empty_no_claude_md_or_project_settings(tmp_path):
    # setting_sources=[] => --setting-sources= (nothing). No CLAUDE.md, no
    # project/local settings.json feeding context or the permission allow-list.
    assert _opts(tmp_path).setting_sources == []


def test_permission_mode_is_dont_ask(tmp_path):
    assert _opts(tmp_path).permission_mode == "dontAsk"
