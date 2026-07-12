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
        # PREVENTER: `tools` restricts AVAILABILITY (unlike allowed_tools, which
        # only pre-approves). Unset => the claude_code preset (Bash/Write/Task/…)
        # leaks onto a seat that places orders and can route around the gate.
        # Driven from cfg so each seat declares its own surface (default: none).
        tools=cfg["tools"],
        # No settings source: no CLAUDE.md, no project/local settings.json feed
        # this seat's context or permission surface. Invariants live in the
        # charter. Per-seat via cfg; default [] never loads a dev file.
        setting_sources=cfg.get("setting_sources", []),
        mcp_servers={
            "alpaca": {"command": "uvx", "args": ["alpaca-mcp-server"],
                       "env": {"ALPACA_PAPER_TRADE": "true",     # invariant 1
                               "ALPACA_TOOLSETS": cfg["alpaca_toolsets"]}},
            "fund": build_fund_server(conn_factory, clock, cfg["seat"]),
        },
        allowed_tools=["mcp__alpaca__*", "mcp__fund__*"],
        # matcher=None => the hook fires for EVERY tool call. The CLI matches
        # `matcher` against the full tool name (anchored/full match), so a prefix
        # like "mcp__alpaca__place_" NEVER matches "mcp__alpaca__place_stock_order"
        # and the hook silently never runs — verified live: with a prefix matcher
        # the order gate did NOT fire and orders reached the broker un-gated.
        # make_order_gate / make_order_recorder already self-filter by
        # PLACE_PREFIX internally, so fire-for-all is correct and robust.
        hooks={
            "PreToolUse": [HookMatcher(
                matcher=None,
                hooks=[make_order_gate(conn_factory, clock)])],
            "PostToolUse": [HookMatcher(
                matcher=None,
                hooks=[make_order_recorder(conn_factory, clock)])],
        },
    )
