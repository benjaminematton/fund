"""Composition root for all seats (design Appendix A). One yaml-driven
factory serves exec, analyst, and pm. Everything per-run (db path, clock,
tokens) is injected — never in prompts."""

from __future__ import annotations

from pathlib import Path

import yaml
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from agents.runtime import make_order_gate, make_order_recorder
from agents.tools.fund_server import build_fund_server
from orchestrator.clock import Clock
from state.db import connect

CHARTERS_DIR = Path(__file__).resolve().parents[1] / "charters"

# PIN. Bare `uvx alpaca-mcp-server` resolves LATEST at run time — unattended, at
# 09:35, on a host that trades. An upstream release moving a tool-schema field
# name overnight is exactly the 2026-08-17 outage class, and `make schema-pin`
# only defends when someone remembers to run it. Pinned to what the droplet's
# warm uvx cache already resolves, so this makes today's behaviour explicit
# rather than changing it — no cold fetch lands in the launch path.
# tests/test_live_smoke.py's schema pin and ops/README.md's cache pre-warm both
# derive THIS spec: the guard, the warm cache and the thing guarded cannot drift
# apart. An upgrade is a deliberate commit with a green `make schema-pin`.
ALPACA_MCP_SPEC = "alpaca-mcp-server@2.2.1"


def load_seat_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def build_seat_options(cfg: dict, db_path: str | Path, clock: Clock, *,
                       snapshot=None, journals_root=None) -> ClaudeAgentOptions:
    """`snapshot` (zero-arg -> {cash, positions, allowed_actions}) and
    `journals_root` are this day's stage-brief providers, injected the same
    way the DB and the clock are. Unbound (the default) is legal and safe:
    get_stage_brief then reports the section as unavailable instead of
    inventing one. Both are ignored by seats without the tool."""
    conn_factory = lambda: connect(db_path)
    charter = CHARTERS_DIR / f"{cfg['seat']}.md"
    options = dict(
        system_prompt=charter.read_text(),
        model=cfg["model"],
        fallback_model=cfg["fallback_model"],
        max_budget_usd=cfg["max_budget_usd"],
        max_turns=cfg["max_turns"],
        permission_mode="dontAsk",
        # PREVENTER: `tools` restricts AVAILABILITY (unlike allowed_tools, which
        # only pre-approves). Unset => the claude_code preset (Bash/Write/Task/…)
        # leaks onto a seat that can reach the broker and can route around the
        # gate. Driven from cfg so each seat declares its own surface.
        tools=cfg["tools"],
        # No settings source: no CLAUDE.md, no project/local settings.json feed
        # this seat's context or permission surface. Invariants live in the
        # charter. Per-seat via cfg; default [] never loads a dev file.
        setting_sources=cfg.get("setting_sources", []),
        mcp_servers={
            "alpaca": {"command": "uvx", "args": [ALPACA_MCP_SPEC],
                       "env": {"ALPACA_PAPER_TRADE": "true",     # invariant 1
                               "ALPACA_TOOLSETS": cfg["alpaca_toolsets"]}},
            "fund": build_fund_server(conn_factory, clock, cfg["seat"],
                                      snapshot=snapshot,
                                      journals_root=journals_root),
        },
        allowed_tools=["mcp__alpaca__*", "mcp__fund__*"],
        # Belt over the toolset brace (invariant 2): every non-trading seat's
        # yaml sets this to deny mcp__alpaca__place_* even if ALPACA_TOOLSETS
        # is ever misconfigured server-side. Absent for the trading seat.
        disallowed_tools=cfg.get("disallowed_tools"),
    )
    # Order gate + recorder hooks belong ONLY to the seat that carries the
    # `trading` toolset (invariant 2; CLAUDE.md: hooks live in agents/runtime.py
    # only, attached only to a seat that trades).
    if "trading" in [t.strip() for t in cfg["alpaca_toolsets"].split(",")]:
        options["hooks"] = {
            # matcher=None => the hook fires for EVERY tool call. The CLI matches
            # `matcher` against the full tool name (anchored/full match), so a
            # prefix like "mcp__alpaca__place_" NEVER matches
            # "mcp__alpaca__place_stock_order" and the hook silently never runs —
            # verified live: with a prefix matcher the order gate did NOT fire and
            # orders reached the broker un-gated. make_order_gate /
            # make_order_recorder already self-filter by PLACE_PREFIX internally,
            # so fire-for-all is correct and robust.
            "PreToolUse": [HookMatcher(
                matcher=None,
                hooks=[make_order_gate(conn_factory, clock)])],
            "PostToolUse": [HookMatcher(
                matcher=None,
                hooks=[make_order_recorder(conn_factory, clock)])],
        }
    return ClaudeAgentOptions(**options)
