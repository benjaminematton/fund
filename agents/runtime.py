"""Seat runtime plumbing. ALL hooks are defined here (CLAUDE.md): the
PreToolUse order gate, the PostToolUse order recorder, the decision recorder
(record mode), and cost recording. Hook factories bind (conn_factory, clock)
so the same functions serve live SDK sessions and offline replay."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Callable

from gate.tickets import validate_order
from orchestrator.clock import Clock, iso
from slackkit.outbox import append_event
from state.transition import try_transition

log = logging.getLogger(__name__)

PLACE_PREFIX = "mcp__alpaca__place_"

# Every tool the broker MCP server exposes. The exec seat's tool surface is
# `mcp__alpaca__*` — a wildcard over this whole namespace — so the gate, not
# the allow-list, is what decides which of them may reach the broker
# (CLAUDE.md: hooks run before allow rules, and `allowed_tools` fails open).
BROKER_PREFIX = "mcp__alpaca__"

# Broker verbs that MUTATE state and are authorized by a gate ticket.
# THE SINGLE EXTENSION POINT. A verb added here is routed to validate_order —
# the same ticket check place_* gets — instead of being denied. An amend path
# needs `mcp__alpaca__replace_` here; that is one line, deliberately.
#
# If you add one: the PostToolUse recorder below still matches PLACE_PREFIX
# alone, so a newly-gated verb would execute and leave no `orders` row. Extend
# both or the broker and SQLite disagree about what happened.
GATED_PREFIXES = (PLACE_PREFIX,)

# Broker verbs that only READ. Matched by naming convention rather than an
# enumerated list so a new getter does not take the exec turn down for a quote;
# every alpaca verb in the entire recorded corpus (test recordings and live
# traces alike) is a get_* or place_stock_order.
READ_PREFIX = "mcp__alpaca__get_"


def _broker_verb_policy(tool: str) -> str:
    """`allow` | `gated` | `deny` for one tool name.

    Anything under the broker namespace that is neither a known read nor a
    gated mutation is DENIED, not waved through. This is an allowlist on
    purpose: a denylist of the verbs we happen to know about fails open the
    first time the toolset grows one nobody enumerated — which is exactly how
    cancel_all_orders and close_all_positions came to be reachable with no
    ticket, no max_qty and no `orders` row (invariant 4: ambiguity resolves to
    no action, never to a guess)."""
    if not tool.startswith(BROKER_PREFIX):
        return "allow"                      # not the gate's business
    if tool.startswith(GATED_PREFIXES):
        return "gated"
    if tool.startswith(READ_PREFIX):
        return "allow"
    return "deny"


def _cached(conn_factory):
    """Lazily open conn_factory() once and reuse it. Scope = the hook
    binding (one seat's turn set) — live composition roots create fresh
    factories per day, so this never outlives a day's connections."""
    box = {}
    def get():
        if "c" not in box:
            box["c"] = conn_factory()
        return box["c"]
    return get


def _deny_alert_text(tool_input, reason) -> str:
    """Name the ticker, the reason, and the 8-char ticket id. The input of a
    DENIED order is untrusted by construction — a malformed-input deny is the
    normal case — so every field degrades to '?' and this never raises."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    symbol, coid = ti.get("symbol"), ti.get("client_order_id")
    ticker = symbol if isinstance(symbol, str) and symbol else "?"
    ticket = coid[:8] if isinstance(coid, str) and coid else "?"
    return f"⛔ ORDER DENIED {ticker} (ticket {ticket}) — {reason}"


def _deny(conn, clock, reason: str, alert_text: str) -> dict:
    """The denial, plus the alert that makes it visible.

    A deny is runbook abort criterion #1, so it must be OBSERVABLE: the alert
    projects to #risk and reddens scripts/audit_day.py. The denial is built
    BEFORE the append and the append's failure is swallowed — an UNRECORDABLE
    deny is still a deny, and observability must never open the gate
    (invariant 4)."""
    denial = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}
    try:
        append_event(conn(), "alert", {"text": alert_text}, iso(clock.now()))
    # Logged, never silent: if this path is broken, denies go dark again.
    except Exception as exc:
        log.error("order gate: DENIED %s but could not record the alert —"
                  " %s: %s; the denial still stands",
                  alert_text, type(exc).__name__, exc)
    return denial


def make_order_gate(conn_factory: Callable[[], sqlite3.Connection],
                    clock: Clock):
    """PreToolUse: deny any order lacking a valid gate ticket (invariant 5;
    design Appendix A). Hooks run before allow rules — nothing bypasses."""
    conn = _cached(conn_factory)

    async def order_gate(input_data, tool_use_id, context) -> dict:
        tool = str(input_data.get("tool_name", ""))
        policy = _broker_verb_policy(tool)
        if policy == "allow":
            return {}
        if policy == "deny":
            # A broker mutation with no gated route. charters/exec.md rule 7
            # already forbids these ("You never modify, cancel, or work an
            # order beyond the ticket's terms") — but a charter is a prompt,
            # and this gate is the deterministic layer between the model and
            # the broker. Denied here so the rule survives a model that
            # ignores it.
            verb = tool.removeprefix(BROKER_PREFIX)
            reason = (f"{verb} is not gated: no ticket authorizes it, and a"
                      " ticket authorizes only the order it names")
            return _deny(conn, clock, reason,
                         f"⛔ BROKER VERB DENIED {verb} — {reason}")
        try:
            ok, reason = validate_order(conn(),
                                        input_data.get("tool_input"),
                                        iso(clock.now()))
        # Fail closed (invariant 4): never let an internal error allow the
        # order.
        except Exception as exc:
            ok, reason = False, f"gate error: {exc}"
        if ok:
            return {}
        return _deny(conn, clock, reason,
                     _deny_alert_text(input_data.get("tool_input"), reason))

    return order_gate


def _extract_order(tool_response):
    """Normalize a place_* tool_response to the order dict, or None if nothing
    landed. The real alpaca-mcp-server returns a JSON STRING wrapping the order
    under ``data`` with an ``_alpaca_mcp_security`` envelope; FakeAlpaca and the
    offline fixtures return the order dict directly. A rejection (``error`` key,
    or Alpaca ``code``/``message``) -> None: invariant 4, a rejection is never
    recorded. Numeric fields can be strings ("67") and prices can be null."""
    obj = tool_response
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            return None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("data"), dict):
        obj = obj["data"]                      # unwrap the MCP security envelope
    if "error" in obj or "code" in obj:
        return None                            # error/rejection payload
    if not obj.get("client_order_id"):
        return None
    return obj


def _parse_fill(order: dict) -> tuple[int | None, float | None]:
    """Coerce filled_qty/filled_avg_price into locals, never raising: returns
    (None, None) for anything malformed — missing, null, non-positive, or a
    fractional qty. This fund is whole-share only and orders.filled_qty is
    INTEGER, so a fractional fill is an anomaly, not something to floor
    silently. Callers must check for None before transitioning anything."""
    try:
        raw_qty = float(order["filled_qty"])
        avg_price = float(order["filled_avg_price"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if raw_qty != int(raw_qty) or raw_qty <= 0 or avg_price <= 0:
        return None, None
    return int(raw_qty), avg_price


def make_order_recorder(conn_factory: Callable[[], sqlite3.Connection],
                        clock: Clock):
    """PostToolUse on place_*: mirror the broker's answer into SQLite.
    Idempotent under retry: INSERT OR IGNORE + CAS transitions; the fill
    event is appended only when the order CAS submitted->filled wins.
    Parses the real MCP envelope (JSON string + `data`) with _extract_order."""
    conn_get = _cached(conn_factory)

    async def record_order(input_data, tool_use_id, context) -> dict:
        if not str(input_data.get("tool_name", "")).startswith(PLACE_PREFIX):
            return {}
        order = _extract_order(input_data.get("tool_response"))
        if order is None:
            # Nothing landed; retry/reconcile is the turn's job
            # (contracts §5.1).
            return {}
        conn = conn_get()
        now = iso(clock.now())
        coid = order["client_order_id"]
        conn.execute(
            "INSERT OR IGNORE INTO orders (client_order_id, alpaca_order_id,"
            " symbol, side, qty, status, submitted_at)"
            " VALUES (?, ?, ?, ?, ?, 'submitted', ?)",
            (coid, order.get("id"), order["symbol"], order["side"],
             int(order["qty"]), now))
        conn.commit()
        try_transition(conn, "tickets", {"id": coid}, "open", "consumed", now)
        # A market order acks 'accepted' first; the fill (and thus the fill
        # event + decision->executed) only lands after status is 'filled' —
        # async fills reconcile on a later turn (Phase 2). Recording the order
        # row + consuming the ticket above is unconditional and idempotent.
        if order.get("status") == "filled":
            filled_qty, filled_avg_price = _parse_fill(order)
            if filled_qty is None:
                # Malformed fill payload (missing/null/non-positive/fractional
                # qty): take NO action at all — no transition, no event. Leave
                # the row 'submitted' so orchestrator/reconcile.py's next poll
                # can repair it (invariant 4: default is HOLD, never a guess).
                # This hook must never raise into the SDK.
                return {}
            if try_transition(conn, "orders", {"client_order_id": coid},
                              "submitted", "filled", now):
                conn.execute(
                    "UPDATE orders SET filled_qty = ?, filled_avg_price = ?,"
                    " closed_at = ? WHERE client_order_id = ?",
                    (filled_qty, filled_avg_price, now, coid))
                conn.commit()
                append_event(conn, "fill", {
                    "ticker": order["symbol"], "side": order["side"],
                    "filled_qty": filled_qty,
                    "filled_avg_price": filled_avg_price,
                    "ticket_id": coid}, now)
                t = conn.execute("SELECT decision_id FROM tickets WHERE id = ?",
                                 (coid,)).fetchone()
                if t is not None:
                    try_transition(conn, "decisions", {"id": t["decision_id"]},
                                   "approved", "executed", now)
        return {}

    return record_order


def make_decision_recorder(path: str | Path, seat: str):
    """PreToolUse in record mode (acceptance §0): append each MCP tool-call
    decision as one JSONL line {"seat","tool","args"}. Never blocks."""

    async def record_decision(input_data, tool_use_id, context) -> dict:
        tool = str(input_data.get("tool_name", ""))
        if tool.startswith("mcp__"):
            line = json.dumps({"seat": seat, "tool": tool,
                               "args": input_data.get("tool_input") or {}})
            with open(path, "a") as f:
                f.write(line + "\n")
        return {}

    return record_decision


def record_cost(conn: sqlite3.Connection, run_date: str, agent: str,
                session_id: str, usd_estimate: float, now_iso: str) -> None:
    """Insert one cost row for a seat turn. ResultMessage.total_cost_usd is a
    client-side ESTIMATE — label 'est.' wherever surfaced (CLAUDE.md)."""
    conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, ?, ?, ?, ?)",
        (run_date, agent, session_id, usd_estimate, now_iso))
    conn.commit()


def _served_matches(key: str, configured: str) -> bool:
    """True when `key` and `configured` name the same model.

    model_usage's keys come from the CLI unchanged, so they may be the alias
    the yaml pins ('claude-sonnet-5') or a resolved dated id
    ('claude-sonnet-5-<date>'). Which one is not determinable from the SDK
    source, and no recorded trace carries the field, so the comparison has to
    be right under either. Prefix in EITHER direction covers both, and plain
    equality covers the already-dated configs (analyst/exec pin
    'claude-haiku-4-5-20251001').

    Deliberately not `==`: under resolved keys that would fire on every clean
    turn, and an event that fires daily is one you stop reading.

    Deliberately not exact either — two genuinely different models in a prefix
    relationship (a hypothetical 'claude-opus-5' and 'claude-opus-5-mini')
    would match and stay silent. No such pair exists in these configs; a
    suffix allowlist would cost more than the risk, so this is written down
    rather than defended against."""
    return key == configured or key.startswith(configured) \
        or configured.startswith(key)


def _unmatched_models(model_usage, configured: str) -> list[str]:
    """The served model ids that are NOT the configured model.

    THE QUANTIFIER IS THE POINT. model_usage is a dict because one turn can
    run more than one model — that mid-turn fallback is what this exists to
    catch. `any(_served_matches(...))` would match on the haiku key of a
    haiku-then-sonnet turn and stay silent on exactly the case that motivated
    reading model_usage at all. Flag when ANY key fails, not when none match.

    An empty `configured` returns nothing: a caller that cannot state the
    seat's model must not manufacture a divergence against the empty string,
    which every key would 'mismatch'."""
    if not model_usage or not configured:
        return []
    return sorted(k for k in model_usage
                  if not _served_matches(k, configured))


def record_turn_result(conn: sqlite3.Connection, run_date: str, seat: str,
                       result, now_iso: str,
                       configured_model: str = "") -> bool:
    """The cost seam: pull the estimate off ONE seat turn's ResultMessage and
    record it. scripts/run_day.py calls this after EVERY seat turn — this is
    the only production caller of record_cost, so it is deliberately a plain
    tested function rather than inline script code.

    Read by attribute, never isinstance: the SDK type is not importable from
    the purity-lint-adjacent test path, and an offline stub must exercise the
    same code the live day runs.

    total_cost_usd is Optional[float] in the SDK and is NOT always populated.
    When it is missing/None/non-finite this writes NO cost row and appends one
    `alert` instead. Deliberate: costs.usd_estimate is REAL NOT NULL, so the
    only alternatives are a fabricated 0.0 (which would make real spend look
    free in the digest and in the ≤$0.50/day acceptance box) or silence
    (which would hide a broken cost pillar). An alert is the honest third
    option — it costs the day an audit violation, which is exactly the human
    review a fund that cannot account for its spend deserves.

    A MISSING estimate therefore never raises. A broken DB still does: both
    branches below write and commit, and swallowing a failed write here would
    hide the very gap this function exists to surface. Keeping the trading day
    alive through that is the CALLER's job, and scripts/run_day.py does it in
    record_cost_guarded() — cost accounting must never take down trading
    (invariant 4), but it must not lie about itself either.

    It is also the model-divergence seam, for the same reason: this is the one
    place that sees a finished ResultMessage for every seat turn. The rows the
    turn wrote name the seat's CONFIGURED model, because the MCP handler that
    wrote them never saw this message — it does not exist until the turn ends.
    `model_usage`'s keys name what actually ran, so a key that is not the
    configured model means those rows are attributed to a model that did not
    serve them. That is recorded as its own `model_fallback_used` event and
    NOT as an `alert`: scripts/audit_day.py fails the day on any alert, and a
    fallback is not a failed day — the fund traded correctly and only
    model_id is stale. The daily scorecard ranks it at severity 3.

    Rows are never retro-edited. The point is that model_id is trustworthy
    precisely when this event is absent, with the exception enumerated rather
    than hidden.

    Returns True if a cost row was written; False otherwise."""
    unmatched = _unmatched_models(getattr(result, "model_usage", None),
                                  configured_model)
    if unmatched:
        append_event(conn, "model_fallback_used",
                     {"seat": seat, "configured": configured_model,
                      "served": unmatched}, now_iso)

    usd = getattr(result, "total_cost_usd", None)
    session_id = str(getattr(result, "session_id", None) or "unknown")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) \
            or usd != usd or usd in (float("inf"), float("-inf")):
        append_event(conn, "alert", {
            "text": f"cost_unavailable {seat} — turn completed with no"
                    f" total_cost_usd estimate (session {session_id}); the"
                    " day's est. inference cost understates spend"}, now_iso)
        return False
    record_cost(conn, run_date, seat, session_id, float(usd), now_iso)
    return True
