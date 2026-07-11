"""Composition root for the Execution Trader seat (design Appendix A).
Everything per-run (db path, clock, tokens) is injected — never in prompts."""

from __future__ import annotations

from pathlib import Path

import yaml
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from agents.runtime import make_order_gate, make_order_recorder
from agents.tools.fund_server import build_fund_server
from orchestrator.clock import Clock
from state.db import connect

CHARTER = Path(__file__).resolve().parents[1] / "charters" / "exec.md"


def load_seat_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def build_trader_options(cfg: dict, db_path: str | Path,
                         clock: Clock) -> ClaudeAgentOptions:
    conn_factory = lambda: connect(db_path)
    return ClaudeAgentOptions(
        system_prompt=CHARTER.read_text(),
        model=cfg["model"],
        fallback_model=cfg["fallback_model"],
        max_budget_usd=cfg["max_budget_usd"],
        max_turns=cfg["max_turns"],
        permission_mode="dontAsk",
        setting_sources=["project"],          # CLAUDE.md for every seat
        mcp_servers={
            "alpaca": {"command": "uvx", "args": ["alpaca-mcp-server"],
                       "env": {"ALPACA_PAPER_TRADE": "true",     # invariant 1
                               "ALPACA_TOOLSETS": cfg["alpaca_toolsets"]}},
            "fund": build_fund_server(conn_factory, clock, cfg["seat"]),
        },
        allowed_tools=["mcp__alpaca__*", "mcp__fund__*"],
        hooks={
            "PreToolUse": [HookMatcher(
                matcher="mcp__alpaca__place_",
                hooks=[make_order_gate(conn_factory, clock)])],
            "PostToolUse": [HookMatcher(
                matcher="mcp__alpaca__place_",
                hooks=[make_order_recorder(conn_factory, clock)])],
        },
    )
