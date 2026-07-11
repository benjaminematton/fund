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


def make_order_gate(conn_factory: Callable[[], sqlite3.Connection],
                    clock: Clock):
    """PreToolUse: deny any order lacking a valid gate ticket (invariant 5;
    design Appendix A). Hooks run before allow rules — nothing bypasses."""

    async def order_gate(input_data, tool_use_id, context) -> dict:
        if not str(input_data.get("tool_name", "")).startswith(PLACE_PREFIX):
            return {}
        try:
            ok, reason = validate_order(conn_factory(),
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


def make_order_recorder(conn_factory: Callable[[], sqlite3.Connection],
                        clock: Clock):
    """PostToolUse on place_*: mirror the broker's answer into SQLite.
    Idempotent under retry: INSERT OR IGNORE + CAS transitions; the fill
    event is appended only when the order CAS submitted->filled wins."""

    async def record_order(input_data, tool_use_id, context) -> dict:
        if not str(input_data.get("tool_name", "")).startswith(PLACE_PREFIX):
            return {}
        resp = input_data.get("tool_response")
        if not isinstance(resp, dict) or "error" in resp \
                or "client_order_id" not in resp:
            return {}  # nothing landed; retry/reconcile is the turn's job (§5.1)
        conn = conn_factory()
        now = iso(clock.now())
        coid = resp["client_order_id"]
        conn.execute(
            "INSERT OR IGNORE INTO orders (client_order_id, alpaca_order_id,"
            " symbol, side, qty, status, submitted_at)"
            " VALUES (?, ?, ?, ?, ?, 'submitted', ?)",
            (coid, resp.get("id"), resp["symbol"], resp["side"],
             int(resp["qty"]), now))
        conn.commit()
        try_transition(conn, "tickets", {"id": coid}, "open", "consumed", now)
        if resp.get("status") == "filled":
            if try_transition(conn, "orders", {"client_order_id": coid},
                              "submitted", "filled", now):
                conn.execute(
                    "UPDATE orders SET filled_qty = ?, filled_avg_price = ?,"
                    " closed_at = ? WHERE client_order_id = ?",
                    (int(resp["filled_qty"]), float(resp["filled_avg_price"]),
                     now, coid))
                conn.commit()
                append_event(conn, "fill", {
                    "ticker": resp["symbol"], "side": resp["side"],
                    "filled_qty": int(resp["filled_qty"]),
                    "filled_avg_price": float(resp["filled_avg_price"]),
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
