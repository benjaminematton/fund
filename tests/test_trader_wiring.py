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


def test_build_trader_options_is_paper_only_with_hooks(tmp_path):
    from agents.trader import build_trader_options, load_seat_config

    cfg = load_seat_config("agents/config/exec.yaml")
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    opts = build_trader_options(cfg, tmp_path / "fund.sqlite", clock)
    alpaca = opts.mcp_servers["alpaca"]
    assert alpaca["env"]["ALPACA_PAPER_TRADE"] == "true"   # invariant 1
    assert opts.setting_sources == ["project"]             # CLAUDE.md loads per seat
    assert "Execution Trader" in opts.system_prompt        # charter is the prompt
    assert opts.hooks and "PreToolUse" in opts.hooks       # order gate attached
