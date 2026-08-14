"""Seat runtime plumbing. ALL hooks are defined here (CLAUDE.md): the
PreToolUse order gate, the PostToolUse order recorder, the decision recorder
(record mode), and cost recording. Hook factories bind (conn_factory, clock)
so the same functions serve live SDK sessions and offline replay."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from gate.tickets import validate_order
from orchestrator.clock import Clock, iso
from slackkit.outbox import append_event
from state.transition import try_transition

PLACE_PREFIX = "mcp__alpaca__place_"


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


def make_order_gate(conn_factory: Callable[[], sqlite3.Connection],
                    clock: Clock):
    """PreToolUse: deny any order lacking a valid gate ticket (invariant 5;
    design Appendix A). Hooks run before allow rules — nothing bypasses."""
    conn = _cached(conn_factory)

    async def order_gate(input_data, tool_use_id, context) -> dict:
        if not str(input_data.get("tool_name", "")).startswith(PLACE_PREFIX):
            return {}
        try:
            ok, reason = validate_order(conn(),
                                        input_data.get("tool_input"),
                                        iso(clock.now()))
        except Exception as exc:  # fail closed (invariant 4): never let an
            ok, reason = False, f"gate error: {exc}"  # internal error allow
        if ok:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    return order_gate


def _extract_order(tool_response):
    """Normalize a place_* tool_response to the order dict, or None if nothing
    landed. The real alpaca-mcp-server returns a JSON STRING wrapping the order
    under ``data`` with an ``_alpaca_mcp_security`` envelope; FakeAlpaca and the
    offline fixtures return the order dict directly. A rejection (``error`` key,
    or Alpaca ``code``/``message``) -> None: invariant 4, a rejection is never
    recorded. Numeric fields may be strings ("67") and prices may be null."""
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
    (None, None) for anything malformed (missing, null, non-positive, or a
    fractional qty — this fund is whole-share only; orders.filled_qty is
    INTEGER, so a fractional fill is an anomaly, not something to floor
    silently). Callers must check for None before transitioning anything."""
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
    Parses the real MCP envelope (JSON string + `data`) via _extract_order."""
    conn_get = _cached(conn_factory)

    async def record_order(input_data, tool_use_id, context) -> dict:
        if not str(input_data.get("tool_name", "")).startswith(PLACE_PREFIX):
            return {}
        order = _extract_order(input_data.get("tool_response"))
        if order is None:
            return {}  # nothing landed; retry/reconcile is the turn's job (§5.1)
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
        # event + decision->executed) only lands once status is 'filled' —
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
    """ResultMessage.total_cost_usd is a client-side ESTIMATE — label 'est.'
    wherever surfaced (CLAUDE.md)."""
    conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, ?, ?, ?, ?)",
        (run_date, agent, session_id, usd_estimate, now_iso))
    conn.commit()


def record_turn_result(conn: sqlite3.Connection, run_date: str, seat: str,
                       result, now_iso: str) -> bool:
    """The cost seam: pull the estimate off ONE seat turn's ResultMessage and
    record it. scripts/run_day.py calls this after EVERY seat turn — this is
    the only production caller of record_cost, so it is deliberately a plain
    tested function rather than inline script code.

    Read by attribute, never isinstance: the SDK type is not importable from
    the purity-lint-adjacent test path, and an offline stub must exercise the
    same code the live day runs.

    total_cost_usd is Optional[float] in the SDK and is NOT always populated.
    When it is missing/None/non-finite we write NO cost row and append one
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

    Returns True iff a cost row was written."""
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
