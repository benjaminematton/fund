"""Composition root for all seats (design Appendix A). One yaml-driven
factory serves exec, analyst, and pm. Everything per-run (db path, clock,
tokens) is injected — never in prompts."""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from agents.runtime import make_order_gate, make_order_recorder
from agents.tools.fund_server import SEAT_CAPS, build_fund_server
from orchestrator.clock import Clock
from state.db import connect

CHARTERS_DIR = Path(__file__).resolve().parents[1] / "charters"

# PIN. Bare `uvx alpaca-mcp-server` resolves LATEST at run time — unattended, at
# 09:35, on a host that trades. An upstream release moving a tool-schema field
# name overnight is exactly the 2026-08-17 outage class, and `make schema-pin`
# only defends when someone remembers to run it. Pinned to what the droplet's
# warm uvx cache already resolves, so this makes today's behavior explicit
# rather than changing it — no cold fetch lands in the launch path.
# tests/test_live_smoke.py's schema pin and ops/README.md's cache pre-warm both
# derive THIS spec: the guard, the warm cache, and the thing guarded cannot
# drift apart. An upgrade is a deliberate commit with a green
# `make schema-pin`.
ALPACA_MCP_SPEC = "alpaca-mcp-server@2.2.1"


def load_seat_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _parse_charter_version(text: str) -> str:
    """'# Portfolio Manager — v6' -> 'v6'. 'unknown' when the header does not
    carry one.

    The header is the source of truth because `charters/_template.md` already
    requires bumping it on any change; a second field in the yaml would be one
    more thing to forget. An unparseable header returns 'unknown' rather than
    raising: a charter's formatting must never take a trading day down
    (invariant 4), and 'unknown' is a value the scoreboard already knows to
    exclude."""
    first = text.split("\n", 1)[0]
    match = re.search(r"\bv(\d+)\b", first)
    return f"v{match.group(1)}" if match else "unknown"


def charter_version_for(cfg: dict) -> str:
    return _parse_charter_version(charter_text_for(cfg))


def charter_text_for(cfg: dict) -> str:
    """The charter this seat runs under, read the same way build_seat_options
    reads it. Exposed so a trace can record the charter AS IT WAS at run time
    (evals/trace.py's reason: a sha alone makes every historical trace
    ungradeable the moment a charter is edited) without a second copy of where
    charters live."""
    return (CHARTERS_DIR / f"{cfg['seat']}.md").read_text()


def _turn_tools(cfg: dict, override: list[str] | None) -> list[str]:
    """The tool surface for ONE turn: the seat's standing `tools`, unless the
    caller narrows it for this turn only.

    WHY A PARAMETER AND NOT A SECOND YAML KEY. The Critic legitimately carries
    `stock-data` for its trade turns (specs/design.md's seat table), and at G1
    its charter forbids using it — "never at G1, a spec is judged on its
    internal coherence". That is a property of the TURN, not of the seat, so it
    belongs at the call site that knows which turn it is. Issue #170's Phase 6
    per-kind surfaces are the same shape and reuse this parameter.

    NARROWING ONLY — CHECKED AGAINST WHAT THE SEAT IS SERVED, NOT AGAINST ITS
    YAML GLOBS. A check against cfg["tools"] would be decorative: critic.yaml
    (and analyst, news, pm, exec) carry ["mcp__fund__*", "mcp__alpaca__*"], so
    a glob check ACCEPTS mcp__alpaca__place_stock_order for the Critic. Only
    reflect, whose tools are ["mcp__fund__*"], would ever have rejected
    anything. So:

      a glob          REFUSED FIRST, before any of the below. See the next
                      paragraph — this is what makes the rest of the checks
                      mean anything.
      mcp__fund__*    exact membership in SEAT_CAPS[seat], which IS the fund
                      server's registration for this seat (build_fund_server
                      serves nothing else). A name outside it does not exist
                      for this seat, so refusing it is both the invariant-2
                      check and the typo check.
      mcp__alpaca__*  the external alpaca server's tool names are not
                      enumerable in this repo — only the toolset STRING is
                      ours. So: the standing glob must admit it, AND a
                      place_* name is refused unless cfg["alpaca_toolsets"]
                      contains `trading`. That is the same field the order
                      hooks key off (see below), which is what makes invariant
                      2 a property of this function rather than a claim about
                      it.
      anything else   refused. `tools` exists so the claude_code preset never
                      reaches a seat; an override is not a second door in.

    AN OVERRIDE MUST NAME CONCRETE TOOLS. `tools` entries are PATTERNS here —
    agents/seats.py:92 passes cfg["tools"] straight to the SDK and every seat
    yaml uses wildcards — so an override that is itself a glob re-WIDENS rather
    than narrows. Without this rule all of ['mcp__alpaca__*'],
    ['mcp__alpaca__*place*'], ['mcp__alpaca__pla?e_stock_order'] and
    ['mcp__alpaca__[pq]lace_stock_order'] were ACCEPTED for the Critic, because
    the invariant-2 rule below is a literal fnmatchcase(name, "...place_*")
    test and a wildcard walks past a literal test. Refusing the metacharacter
    is the fix rather than trying to prove a glob is a subset: subset-of-a-glob
    is undecidable against a server whose names this repo never enumerates, and
    a per-TURN narrowing has no legitimate use for a pattern.

    HONEST LIMIT, stated rather than oversold: on the alpaca half this is NOT a
    proof of subset. A read-only alpaca name the seat's toolsets do not
    actually serve would pass this check — and be inert, because the SDK never
    surfaces it.

    AND THE TWO HALVES ARE NOT EQUALLY STRONG, which survives the glob fix and
    is worth naming. The fund half is exact set membership, so it is
    case-sensitive by construction: `mcp__fund__SUBMIT_DECISION` is refused.
    The alpaca half is a glob match plus a case-sensitive literal test, so
    `mcp__alpaca__PLACE_stock_order` is ACCEPTED — it matches the standing
    `mcp__alpaca__*` and not `mcp__alpaca__place_*`. It names no tool any
    server registers, so it is inert for the same reason as any other unserved
    alpaca name; it is not an escalation, and it is the price of a surface this
    repo cannot enumerate. fnmatchcase, not fnmatch, is what keeps this a
    documented asymmetry instead of a platform-dependent one.

    WHAT IS ACTUALLY GUARANTEED, and it is what #170's Phase 6 per-kind
    surfaces inherit — no more: (i) every entry is a concrete tool name, never
    a pattern; (ii) no fund tool the seat is not served, exactly; (iii) no
    order tool for a seat without `trading` in alpaca_toolsets; (iv) no
    builtin. NOT guaranteed: that every alpaca name passed is one the seat's
    toolsets serve.

    fnmatchcase, not fnmatch: fnmatch normalises case on some platforms, and a
    tool surface that means something different on macOS than on the droplet is
    not a lock.

    [] is refused rather than read as "no narrowing". [] and None are different
    intents; collapsing them would buy a turn the seat cannot possibly
    complete, which costs money and writes nothing."""
    standing = cfg["tools"]
    if override is None:
        return standing
    seat = cfg["seat"]
    if not override:
        raise ValueError(
            f"build_seat_options: empty per-turn surface for seat"
            f" {seat!r} — a seat with no tools cannot complete a turn."
            " Pass tools=None to keep the seat's standing surface.")

    trades = "trading" in [t.strip()
                           for t in cfg["alpaca_toolsets"].split(",")]
    served_fund = {f"mcp__fund__{cap}" for cap in SEAT_CAPS.get(seat, ())}
    patterned, ungranted, escalating = [], [], []
    for name in override:
        if any(ch in name for ch in "*?["):
            patterned.append(name)
        elif name.startswith("mcp__fund__"):
            if name not in served_fund:
                ungranted.append(name)
        elif name.startswith("mcp__alpaca__"):
            if not any(fnmatchcase(name, glob) for glob in standing):
                ungranted.append(name)
            elif fnmatchcase(name, "mcp__alpaca__place_*") and not trades:
                escalating.append(name)
        else:
            ungranted.append(name)

    if patterned:
        raise ValueError(
            f"build_seat_options: a per-turn override must name CONCRETE"
            f" tools — {patterned} contain glob metacharacters. `tools`"
            " entries are PATTERNS in this repo (agents/seats.py:92 passes"
            " cfg['tools'] straight to the SDK), so a wildcard override"
            " re-widens the surface instead of narrowing it, and the"
            " invariant-2 place_* rule below is a literal name test a"
            " wildcard walks past. A per-turn narrowing names the tools the"
            " turn uses; widen the seat's yaml, where it is reviewed.")
    if escalating:
        raise ValueError(
            f"build_seat_options: a per-turn override may not grant"
            f" {escalating} to seat {seat!r}, whose alpaca_toolsets"
            f" ({cfg['alpaca_toolsets']!r}) do not include `trading` —"
            " invariant 2: only the Execution Trader places orders.")
    if ungranted:
        raise ValueError(
            f"build_seat_options: a per-turn override may only NARROW —"
            f" {ungranted} are not served to seat {seat!r} (fund tools:"
            f" {sorted(served_fund)}; alpaca globs: {standing}). Widen the"
            " seat's yaml, or SEAT_CAPS, where the change is reviewed.")
    return list(override)


def build_seat_options(cfg: dict, db_path: str | Path, clock: Clock, *,
                       snapshot=None, journals_root=None,
                       expected_decision_id: int | None = None,
                       tools: list[str] | None = None
                       ) -> ClaudeAgentOptions:
    """Build one seat's ClaudeAgentOptions from its yaml config.
    `snapshot` (zero-arg -> {cash, positions, allowed_actions}) and
    `journals_root` are this day's stage-brief providers, injected the same
    way the DB and the clock are. Unbound (the default) is legal and safe:
    get_stage_brief then reports the section as unavailable instead of
    inventing one. Both are ignored by seats without the tool.

    `expected_decision_id` binds the reflect seat's submit_reflection call to
    the decision this turn was launched for (see handle_submit_reflection).
    None (the default) means no binding — every other seat, unaffected.

    `tools` narrows this ONE turn's surface below the seat's standing
    `cfg['tools']`. None (the default) means no narrowing — every existing
    call site is unaffected. It may only narrow, never widen, and "widen" is
    measured against what the seat is SERVED (SEAT_CAPS plus its
    alpaca_toolsets), not against its yaml globs; see _turn_tools."""
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
        tools=_turn_tools(cfg, tools),
        # No settings source: no CLAUDE.md, no project/local settings.json feed
        # this seat's context or permission surface. Invariants live in the
        # charter. Per-seat from cfg; default [] never loads a dev file.
        setting_sources=cfg.get("setting_sources", []),
        mcp_servers={
            "alpaca": {"command": "uvx", "args": [ALPACA_MCP_SPEC],
                       "env": {"ALPACA_PAPER_TRADE": "true",     # invariant 1
                               "ALPACA_TOOLSETS": cfg["alpaca_toolsets"]}},
            "fund": build_fund_server(conn_factory, clock, cfg["seat"],
                                      snapshot=snapshot,
                                      journals_root=journals_root,
                                      charter_version=charter_version_for(cfg),
                                      model_id=cfg.get("model", "unknown"),
                                      expected_decision_id=expected_decision_id),
        },
        allowed_tools=["mcp__alpaca__*", "mcp__fund__*"],
        # A second guard over the toolset restriction (invariant 2): every
        # non-trading seat's yaml sets this to deny mcp__alpaca__place_* even
        # if ALPACA_TOOLSETS is ever misconfigured server-side. Absent for the
        # trading seat.
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
