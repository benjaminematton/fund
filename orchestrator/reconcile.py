"""Fill-poll: drive submitted orders to a terminal state (MVF review A3/T2).
Deterministic; broker + sleep are injected. Bounded: poll_s cadence, max_wait_s
cap, then the timeout path (order canceled*, decision failed, alert). Errors
fail closed — the order stays 'submitted' and the next run retries.
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

def _apply(conn, row, o, now) -> bool:
    """Mirror one broker order dict into the DB. True iff terminal fill landed."""
    coid = row["client_order_id"]
    st = o.get("status")
    if st == "filled":
        moved = (try_transition(conn, "orders", {"client_order_id": coid},
                                "submitted", "filled", now)
                 or try_transition(conn, "orders", {"client_order_id": coid},
                                   "partially_filled", "filled", now))
        if moved:
            conn.execute("UPDATE orders SET filled_qty=?, filled_avg_price=?,"
                         " closed_at=? WHERE client_order_id=?",
                         (int(float(o["filled_qty"])),
                          float(o["filled_avg_price"]), now, coid))
            conn.commit()
            append_event(conn, "fill", {
                "ticker": row["symbol"], "side": row["side"],
                "filled_qty": int(float(o["filled_qty"])),
                "filled_avg_price": float(o["filled_avg_price"]),
                "ticket_id": coid}, now)
            t = conn.execute("SELECT decision_id FROM tickets WHERE id=?",
                             (coid,)).fetchone()
            if t is not None:
                try_transition(conn, "decisions", {"id": t["decision_id"]},
                               "approved", "executed", now)
        return True
    if st == "partially_filled":
        if try_transition(conn, "orders", {"client_order_id": coid},
                          "submitted", "partially_filled", now):
            append_event(conn, "alert", {"text":
                f"partial fill {row['symbol']} {coid[:8]} — manual review"}, now)
        return False
    return False

def reconcile_orders(conn: sqlite3.Connection, *, clock: Clock, broker,
                     sleep: Callable[[float], None],
                     poll_s: float = 3.0, max_wait_s: float = 90.0) -> int:
    filled, waited = 0, 0.0
    unreachable: set[str] = set()   # coids whose most recent poll errored
    while True:
        pending = _statuses(conn)
        if not pending:
            return filled
        now = iso(clock.now())
        for row in pending:
            coid = row["client_order_id"]
            try:
                o = broker.get_order_by_client_order_id(coid)
                unreachable.discard(coid)
            except Exception:
                o = None                          # fail closed, retry next poll
                unreachable.add(coid)
            if o and _apply(conn, row, o, now):
                filled += 1
        if waited >= max_wait_s:
            break
        sleep(poll_s)
        waited += poll_s
    now = iso(clock.now())
    for row in _statuses(conn):                   # timeout path
        coid = row["client_order_id"]
        if coid in unreachable:
            continue    # broker never confirmed this order — leave submitted, next run retries
        if try_transition(conn, "orders", {"client_order_id": coid},
                          "submitted", "canceled", now):
            t = conn.execute("SELECT decision_id FROM tickets WHERE id=?",
                             (coid,)).fetchone()
            if t is not None:
                try_transition(conn, "decisions", {"id": t["decision_id"]},
                               "approved", "failed", now)
            append_event(conn, "alert", {"text":
                f"order {coid[:8]} unfilled after {int(max_wait_s)}s — "
                "canceled, decision failed"}, now)
    return filled
