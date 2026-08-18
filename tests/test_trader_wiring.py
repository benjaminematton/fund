"""Offline checks on the live-trader composition (no network, no keys):
config loads, options carry the charter + hooks + paper-only env."""

from datetime import datetime, timezone

from orchestrator.clock import SimClock


def test_seat_config_loads_and_pins_models():
    from agents.trader import load_seat_config

    cfg = load_seat_config("agents/config/exec.yaml")
    assert cfg["seat"] == "exec"
    assert cfg["model"].startswith("claude-")
    assert cfg["max_budget_usd"] > 0
    assert "trading" in cfg["alpaca_toolsets"]


def test_alpaca_mcp_server_is_version_pinned(tmp_path):
    """Bare `uvx alpaca-mcp-server` resolves LATEST at run time, unattended, at
    09:35 on a host that trades — an upstream release moving a tool-schema field
    is the 2026-08-17 outage class. Nothing else offline notices a revert to
    bare, so this is the guard: the spec must name an exact version, and the
    seat must actually launch that spec."""
    import re

    from agents.seats import ALPACA_MCP_SPEC
    from agents.trader import build_trader_options, load_seat_config

    assert re.fullmatch(r"alpaca-mcp-server@\d+\.\d+\.\d+", ALPACA_MCP_SPEC), (
        f"ALPACA_MCP_SPEC must pin an exact version, got {ALPACA_MCP_SPEC!r}. "
        "A bare or range spec resolves latest at run time.")

    cfg = load_seat_config("agents/config/exec.yaml")
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    opts = build_trader_options(cfg, tmp_path / "fund.sqlite", clock)
    assert opts.mcp_servers["alpaca"]["args"] == [ALPACA_MCP_SPEC]


def test_build_trader_options_is_paper_only_with_hooks(tmp_path):
    from agents.trader import build_trader_options, load_seat_config

    cfg = load_seat_config("agents/config/exec.yaml")
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    opts = build_trader_options(cfg, tmp_path / "fund.sqlite", clock)
    alpaca = opts.mcp_servers["alpaca"]
    assert alpaca["env"]["ALPACA_PAPER_TRADE"] == "true"   # invariant 1
    # Contract change (not test-fixing): the exec seat loads NO settings source.
    # CLAUDE.md is a coding-agent file; the seat's invariants live in its charter.
    # See tests/test_exec_seat_tool_surface.py for the full surface guard.
    assert opts.setting_sources == []
    assert opts.tools == ["mcp__fund__*", "mcp__alpaca__*"]  # locked surface
    assert "Execution Trader" in opts.system_prompt        # charter is the prompt
    assert opts.hooks and "PreToolUse" in opts.hooks       # order gate attached
    # matcher MUST be None (fire-for-all; hooks self-filter by PLACE_PREFIX).
    # A prefix/tool-name matcher does NOT match mcp__alpaca__place_stock_order
    # under the CLI's full-match semantics -> the gate would never fire live.
    assert opts.hooks["PreToolUse"][0].matcher is None
    assert opts.hooks["PostToolUse"][0].matcher is None
