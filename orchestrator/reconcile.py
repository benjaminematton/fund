"""Fill-poll: drive submitted orders to a terminal state (MVF review A3/T2).
Deterministic; broker + sleep are injected. Bounded: poll_s cadence, max_wait_s
cap, then the timeout path (order canceled*, decision failed, alert). Errors
and unparseable broker payloads fail closed — the order stays 'submitted' and
the next run retries; both are surfaced as loud alerts, never silent.
*cancel is issued agent-side next cycle if needed; DB reflects intent."""
from __future__ import annotations
import sqlite3
from typing import Callable
from orchestrator.clock import Clock, iso
from slackkit.outbox import append_event
from state.transition import try_transition

def _statuses(conn):
    return conn.execute("SELECT client_order_id, symbol, side FROM orders"
                        " WHERE status IN ('submitted','partially_filled')").fetchall()

def _parse_fill(o) -> tuple[int, float]:
    """Coerce filled_qty/filled_avg_price into locals. Raises ValueError (or
    lets a TypeError/KeyError through) on anything malformed: missing, null,
    non-positive, or a fractional qty (this fund is whole-share only —
    orders.filled_qty is INTEGER, so a fractional fill is an anomaly to
    reject, not floor silently). Callers must call this BEFORE any
    transition, so a malformed payload raises before anything commits."""
    raw_qty = float(o["filled_qty"])
    filled_avg_price = float(o["filled_avg_price"])
    if raw_qty != int(raw_qty):
        raise ValueError(f"fractional filled_qty: {o['filled_qty']!r}")
    filled_qty = int(raw_qty)
    if filled_qty <= 0 or filled_avg_price <= 0:
        raise ValueError(
            f"non-positive fill: qty={filled_qty} price={filled_avg_price}")
    return filled_qty, filled_avg_price

def _apply(conn, row, o, now) -> bool:
    """Mirror one broker order dict into the DB. Returns True iff THIS call's
    CAS actually moved the order to 'filled' (False on a no-op re-run, so the
    caller's fill counter can't over-count). Fill numbers are parsed into
    locals before any transition — a malformed payload must raise before
    anything commits, never after. The numbers are written before the status
    CAS: while the row is still 'submitted'/'partially_filled' that write is
    inert (nothing reads filled_qty/filled_avg_price off a non-terminal
    order), and the CAS still gates the fill event + decision transition —
    so a crash between the two leaves the row pending for the next poll to
    repair, instead of stuck 'filled' with garbage numbers."""
    coid = row["client_order_id"]
    st = o.get("status")
    if st == "filled":
        filled_qty, filled_avg_price = _parse_fill(o)
        conn.execute("UPDATE orders SET filled_qty=?, filled_avg_price=?,"
                     " closed_at=? WHERE client_order_id=?",
                     (filled_qty, filled_avg_price, now, coid))
        conn.commit()
        moved = (try_transition(conn, "orders", {"client_order_id": coid},
                                "submitted", "filled", now)
                 or try_transition(conn, "orders", {"client_order_id": coid},
                                   "partially_filled", "filled", now))
        if moved:
            append_event(conn, "fill", {
                "ticker": row["symbol"], "side": row["side"],
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
                "ticket_id": coid}, now)
            t = conn.execute("SELECT decision_id FROM tickets WHERE id=?",
                             (coid,)).fetchone()
            if t is not None:
                decision_moved = try_transition(
                    conn, "decisions", {"id": t["decision_id"]},
                    "approved", "executed", now)
                if not decision_moved:
                    dec = conn.execute("SELECT status FROM decisions WHERE id=?",
                                       (t["decision_id"],)).fetchone()
                    dec_status = dec["status"] if dec is not None else "missing"
                    append_event(conn, "alert", {"text":
                        f"order {coid[:8]} filled but decision "
                        f"{t['decision_id']} was '{dec_status}', not "
                        "'approved' — left as-is, manual review"}, now)
        return moved
    if st == "partially_filled":
        filled_qty, filled_avg_price = _parse_fill(o)
        conn.execute("UPDATE orders SET filled_qty=?, filled_avg_price=?"
                     " WHERE client_order_id=?",
                     (filled_qty, filled_avg_price, coid))
        conn.commit()
        if try_transition(conn, "orders", {"client_order_id": coid},
                          "submitted", "partially_filled", now):
            append_event(conn, "alert", {"text":
                f"partial fill {row['symbol']} {coid[:8]} — manual review"}, now)
        return False
    return False

def reconcile_orders(conn: sqlite3.Connection, *, clock: Clock, broker,
                     sleep: Callable[[float], None],
                     poll_s: float = 3.0, max_wait_s: float = 90.0) -> int:
    if poll_s <= 0:
        raise ValueError(f"poll_s must be positive, got {poll_s!r}")
    filled, waited = 0, 0.0
    # Positive framing (MVF review Fix 4): what we KNOW from the most recent
    # poll of each still-pending order, not what we failed to rule out.
    confirmed_open: set[str] = set()   # broker confirmed non-terminal, non-partial
    problem: dict[str, str] = {}       # broker unreachable or payload unparseable -> exception type
    while True:
        pending = _statuses(conn)
        if not pending:
            return filled
        now = iso(clock.now())
        for row in pending:
            coid = row["client_order_id"]
            try:
                o = broker.get_order_by_client_order_id(coid)
                if o and _apply(conn, row, o, now):
                    filled += 1
            except Exception as e:
                # Fail closed: broker unreachable, or a payload we couldn't
                # parse (e.g. _apply's coercion raised). Either way leave the
                # order 'submitted' for the next poll and surface it loudly.
                problem[coid] = type(e).__name__
                confirmed_open.discard(coid)
                continue
            problem.pop(coid, None)
            status = o.get("status") if o else None
            if status in ("filled", "partially_filled"):
                confirmed_open.discard(coid)   # terminal or partial — never cap-canceled
            else:
                confirmed_open.add(coid)
        if waited >= max_wait_s:
            break
        sleep(poll_s)
        waited += poll_s
    now = iso(clock.now())
    for coid in confirmed_open:                   # timeout path: broker confirmed still open
        if try_transition(conn, "orders", {"client_order_id": coid},
                          "submitted", "canceled", now):
            t = conn.execute("SELECT decision_id FROM tickets WHERE id=?",
                             (coid,)).fetchone()
            decision_failed = False
            if t is not None:
                decision_failed = try_transition(conn, "decisions",
                                                 {"id": t["decision_id"]},
                                                 "approved", "failed", now)
            suffix = "decision failed" if decision_failed else "decision unchanged"
            append_event(conn, "alert", {"text":
                f"order {coid[:8]} unfilled after {int(max_wait_s)}s — "
                f"canceled, {suffix}"}, now)
    for coid, reason in problem.items():           # fail-closed path: loud, not silent
        cur = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                           (coid,)).fetchone()
        cur_status = cur["status"] if cur is not None else "submitted"
        append_event(conn, "alert", {"text":
            f"order {coid[:8]} unresolved — broker unreachable or response "
            f"unparseable ({reason}) at cap, left {cur_status} for retry"}, now)
    return filled
