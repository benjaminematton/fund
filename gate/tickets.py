"""Ticket store + deterministic order validation (Phase 1 slice of the gate).
Pure Python + SQLite — purity-linted (invariant 3). The risk math that MINTS
tickets is Phase 2; here: storage, expiry, and the trader-hook validation.
Deny-by-default: any malformed or mismatched input -> (False, reason)."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from state.models import Ticket
from state.transition import try_transition


def create_ticket(conn: sqlite3.Connection, *, id: str, decision_id: int,
                  ticker: str, side: str, max_qty: int,
                  stop_price: float | None, expires_at_iso: str,
                  now_iso: str) -> None:
    t = Ticket(id=id, decision_id=decision_id, ticker=ticker, side=side,
               max_qty=max_qty, stop_price=stop_price,
               expires_at=expires_at_iso)
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " stop_price, expires_at, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (t.id, t.decision_id, t.ticker, t.side, t.max_qty, t.stop_price,
         expires_at_iso, now_iso))
    conn.commit()


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tickets WHERE id = ?",
                        (ticket_id,)).fetchone()


def _expired(expires_at_iso: str, now_iso: str) -> bool:
    return datetime.fromisoformat(now_iso) >= datetime.fromisoformat(expires_at_iso)


def open_tickets(conn: sqlite3.Connection, now_iso: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ticker, side, max_qty, stop_price, expires_at"
        " FROM tickets WHERE status = 'open' ORDER BY created_at").fetchall()
    return [dict(r) for r in rows if not _expired(r["expires_at"], now_iso)]


def expire_open_tickets(conn: sqlite3.Connection, now_iso: str) -> list[str]:
    """Gate expiry, clock-injected (acceptance §0). Ticket open->expired and
    its decision approved->expired (contracts §1)."""
    expired: list[str] = []
    rows = conn.execute(
        "SELECT id, decision_id, expires_at FROM tickets"
        " WHERE status = 'open'").fetchall()
    for r in rows:
        if not _expired(r["expires_at"], now_iso):
            continue
        if try_transition(conn, "tickets", {"id": r["id"]},
                          "open", "expired", now_iso):
            expired.append(r["id"])
            try_transition(conn, "decisions", {"id": r["decision_id"]},
                           "approved", "expired", now_iso)
    return expired


def _as_share_count(qty):
    """Whole-share count from an int or a digit-string; None otherwise. The
    Alpaca MCP place tool sends qty as a STRING ("1"); tickets store max_qty as
    an int. Accept the string form of a whole number; reject bool, float, and
    non-digit strings — no fractional shares, no guessing (invariant 4)."""
    if isinstance(qty, bool):
        return None
    if isinstance(qty, int):
        return qty
    if isinstance(qty, str) and qty.isdigit():
        return int(qty)
    return None


# Every exit-leg parameter the real place_stock_order exposes. A ticket with
# no stop_price must carry NONE of them — naming them all means a take-profit
# smuggled onto an unstopped ticket is denied too, not just a stop.
_STOP_LEG_KEYS = ("stop_loss_stop_price", "stop_loss_limit_price",
                  "take_profit_limit_price")


def _as_price(value):
    """Price from a float/int or a numeric STRING; None otherwise. The Alpaca
    MCP place tool sends every numeric as a string ("210.0"), so the string
    form is the normal case here, not the edge case. Rejects bool and
    unparseable input — a stop we cannot read is a stop we cannot verify."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def validate_order(conn: sqlite3.Connection, tool_input,
                   now_iso: str) -> tuple[bool, str]:
    """The five acceptance checks + malformed-input denial (invariant 4)."""
    if not isinstance(tool_input, dict):
        return False, "malformed tool input: not an object"
    coid = tool_input.get("client_order_id")
    if not isinstance(coid, str) or not coid:
        return False, "missing client_order_id (must equal the gate ticket id)"
    t = get_ticket(conn, coid)
    if t is None:
        return False, f"no gate ticket with id {coid!r}"
    if t["status"] != "open":
        return False, f"ticket {coid[:8]} is {t['status']}, not open"
    if _expired(t["expires_at"], now_iso):
        return False, f"ticket {coid[:8]} expired at {t['expires_at']}"
    if tool_input.get("symbol") != t["ticker"]:
        return False, (f"symbol {tool_input.get('symbol')!r} != ticket "
                       f"symbol {t['ticker']!r}")
    if tool_input.get("side") != t["side"]:
        return False, f"side {tool_input.get('side')!r} != ticket side {t['side']!r}"
    qty = _as_share_count(tool_input.get("qty"))
    if qty is None or qty < 1:
        return False, ("qty must be a positive whole number, got "
                       f"{tool_input.get('qty')!r}")
    if qty > t["max_qty"]:
        return False, f"qty {qty} exceeds ticket max_qty {t['max_qty']}"
    # The stop leg is FLAT — `stop_loss_stop_price`, a string, exactly as the
    # real place_stock_order exposes it. It is NOT a nested
    # `stop_loss: {stop_price: ...}` object: that shape never existed at the
    # broker, so before 2026-08-17 every ticket carrying a stop_price was
    # undeliverable (the gate denied the real shape and the MCP server
    # rejected the assumed one). tests/test_live_smoke.py's schema pin holds
    # these names to the server's actual surface.
    # A nested object is not a parameter the broker has ever accepted, on any
    # ticket. Denying it by name means the pre-2026-08-17 shape can never be
    # quietly approved on a key Alpaca would drop on the floor.
    nested = [k for k in ("stop_loss", "take_profit")
              if tool_input.get(k) is not None]
    if nested:
        return False, (f"{nested} is not a parameter place_stock_order accepts"
                       " — exit legs are flat (stop_loss_stop_price)")
    legs = {k: tool_input.get(k) for k in _STOP_LEG_KEYS
            if tool_input.get(k) is not None}
    if t["stop_price"] is None:
        if legs:
            return False, ("ticket has no stop_price; order must not carry a "
                           f"stop leg, got {sorted(legs)}")
    else:
        # The ticket authorizes ONE exit: a stop-MARKET at its stop_price.
        # stop_loss_limit_price would make that a stop-LIMIT, which can go
        # unfilled through a gap-down — the exact move the stop exists for —
        # and take_profit_limit_price is an exit no ticket field authorizes.
        extra = sorted(k for k in legs if k != "stop_loss_stop_price")
        if extra:
            return False, (f"ticket authorizes a stop-market exit only; {extra}"
                           " changes the exit the gate approved")
        leg_price = _as_price(tool_input.get("stop_loss_stop_price"))
        if leg_price is None or leg_price != float(t["stop_price"]):
            return False, (
                f"stop_loss_stop_price {tool_input.get('stop_loss_stop_price')!r}"
                f" != ticket stop_price {t['stop_price']} — the order must"
                " carry the ticket's stop")
        # A stop exit places at Alpaca as order_class 'oto' carrying the single
        # stop leg — NOT 'bracket' (bracket 422s: it requires a take_profit leg
        # the ticket has no field for). Fail-fast on the unplaceable class
        # rather than let the broker reject it (invariant 4). The plain path
        # (stop_price NULL) stays order_class-agnostic — no false-deny on a
        # legitimate simple order.
        if tool_input.get("order_class") != "oto":
            return False, (f"order_class {tool_input.get('order_class')!r} must "
                           "be 'oto' for a stop exit — bracket is unplaceable")
    return True, "ok"
