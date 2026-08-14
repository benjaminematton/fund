"""Fill-poll: drive submitted orders to a terminal state (MVF review A3/T2).
Deterministic; broker + sleep are injected. Bounded: poll_s cadence, max_wait_s
cap, then the timeout path — which CANCELS AT THE BROKER, re-queries once, and
records only the state the broker confirms (order canceled, with the decision
failed on a zero fill or executed over shares actually held, or the fill that
won the race). Errors and unparseable broker payloads fail
closed — the order stays 'submitted' and the next run retries; all of it is
surfaced as loud alerts, never silent. The DB never claims a terminal state
the broker has not confirmed (invariants 4 and 6)."""
from __future__ import annotations
import sqlite3
from typing import Callable
from orchestrator.clock import Clock, iso
from slackkit.outbox import append_event
from state.transition import EDGES, try_transition

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

# Broker statuses that mean "this order is dead, no more shares are coming",
# mapped to the orders status we record. Anything else (pending_cancel,
# done_for_day, accepted, new, ...) is NOT a confirmed cancel: the row stays
# 'submitted' and the next run re-polls and re-cancels it.
_CONFIRMED_DEAD = {"canceled": "canceled", "expired": "canceled",
                   "rejected": "rejected"}


def _dead_fill(o) -> tuple[int, float] | None:
    """Fill numbers on a broker-confirmed-dead order. None means "no shares
    changed hands" — the plain no-fill cancel, where filled_qty is absent,
    null or 0 and filled_avg_price is null. Anything else goes through
    _parse_fill and raises if malformed: a dead order that claims shares must
    say how many and at what price, or we record nothing at all."""
    raw_qty = o.get("filled_qty")
    if raw_qty in (None, "") or float(raw_qty) == 0:
        return None
    return _parse_fill(o)


def _timeout_close(conn, row, broker, now, max_wait_s) -> bool:
    """Close out ONE order the broker last confirmed still working, past the
    cap: request the cancel, re-query ONCE, record the TRUE terminal state.
    Returns True iff the order filled in the race (keeps the caller's fill
    counter honest).

    The order may be 'submitted' OR 'partially_filled' in the DB (accepted ->
    partially_filled -> canceled happens inside one run), so the CAS starts
    from whatever the row actually holds. The re-queried fill numbers decide
    the decision's fate, because the DB must tell the truth about what's held:
    filled_qty > 0 is a REAL position, so the decision is 'executed' with a
    fill event for the partial qty; only a zero fill is 'failed'.

    Fail closed everywhere the broker leaves us guessing — a cancel that
    errored while the order still works, a re-query that errored or returned
    nothing, a status that is not confirmed-dead, fill numbers that will not
    parse, a dead status with no legal edge from the row's current status:
    leave the row as-is for the next run and alert. Requesting a cancel twice
    is harmless (idempotent by client_order_id); recording a cancel that did
    not happen is not."""
    coid = row["client_order_id"]
    try:
        broker.cancel_order(coid)
        cancel_err = ""
    except Exception as e:                 # keep going: the re-query decides
        cancel_err = f", cancel_order raised {type(e).__name__}"
    stale = (f"order {coid[:8]} unfilled after {int(max_wait_s)}s — cancel "
             "unconfirmed ({why}), left submitted for the next run")
    try:
        o = broker.get_order_by_client_order_id(coid)
        if not o:
            raise ValueError("broker returned no order for this client_order_id")
        status = o.get("status")
    except Exception as e:
        append_event(conn, "alert", {"text": stale.format(
            why=f"re-query raised {type(e).__name__}{cancel_err}")}, now)
        return False
    if status in ("filled", "partially_filled"):
        try:
            return _apply(conn, row, o, now)      # the race: record the fill
        except Exception as e:
            append_event(conn, "alert", {"text": stale.format(
                why=f"broker reports '{status}' but the payload is "
                    f"unparseable ({type(e).__name__})")}, now)
            return False
    if status not in _CONFIRMED_DEAD:
        append_event(conn, "alert", {"text": stale.format(
            why=f"broker still reports '{status}'{cancel_err}")}, now)
        return False
    dead = _CONFIRMED_DEAD[status]
    cur = conn.execute("SELECT status, qty FROM orders WHERE client_order_id=?",
                       (coid,)).fetchone()
    from_status = cur["status"] if cur is not None else "submitted"
    if (from_status, dead) not in EDGES["orders"]:
        append_event(conn, "alert", {"text": stale.format(
            why=f"broker reports '{status}' but the order is '{from_status}'"
                " — no legal transition")}, now)
        return False
    try:
        fill = _dead_fill(o)
    except Exception as e:
        append_event(conn, "alert", {"text": stale.format(
            why=f"broker reports '{status}' but the fill numbers are "
                f"unparseable ({type(e).__name__})")}, now)
        return False
    if fill is not None:                 # shares really changed hands
        conn.execute("UPDATE orders SET filled_qty=?, filled_avg_price=?,"
                     " closed_at=? WHERE client_order_id=?",
                     (fill[0], fill[1], now, coid))
        conn.commit()
    if try_transition(conn, "orders", {"client_order_id": coid},
                      from_status, dead, now):
        t = conn.execute("SELECT decision_id FROM tickets WHERE id=?",
                         (coid,)).fetchone()
        if fill is None:                 # nothing held: the decision failed
            moved = t is not None and try_transition(
                conn, "decisions", {"id": t["decision_id"]},
                "approved", "failed", now)
            append_event(conn, "alert", {"text":
                f"order {coid[:8]} unfilled after {int(max_wait_s)}s — "
                f"{dead} at the broker, "
                f"{'decision failed' if moved else 'decision unchanged'}"}, now)
        else:                            # a real position exists: executed
            filled_qty, filled_avg_price = fill
            append_event(conn, "fill", {
                "ticker": row["symbol"], "side": row["side"],
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
                "ticket_id": coid}, now)
            moved = t is not None and try_transition(
                conn, "decisions", {"id": t["decision_id"]},
                "approved", "executed", now)
            append_event(conn, "alert", {"text":
                f"order {coid[:8]} partial {filled_qty} of {cur['qty']} then "
                f"{dead} at the broker after {int(max_wait_s)}s, "
                f"{'decision executed' if moved else 'decision unchanged'}"},
                now)
    return False


def reconcile_orders(conn: sqlite3.Connection, *, clock: Clock, broker,
                     sleep: Callable[[float], None],
                     poll_s: float = 3.0, max_wait_s: float = 90.0) -> int:
    if poll_s <= 0:
        raise ValueError(f"poll_s must be positive, got {poll_s!r}")
    filled, waited = 0, 0.0
    # Positive framing (MVF review Fix 4): what we KNOW from the most recent
    # poll of each still-pending order, not what we failed to rule out.
    # coid -> order row; broker confirmed non-terminal, non-partial
    confirmed_open: dict[str, sqlite3.Row] = {}
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
                confirmed_open.pop(coid, None)
                continue
            problem.pop(coid, None)
            status = o.get("status") if o else None
            if status in ("filled", "partially_filled"):
                confirmed_open.pop(coid, None)  # terminal or partial — never cap-canceled
            else:
                confirmed_open[coid] = row
        if waited >= max_wait_s:
            break
        sleep(poll_s)
        waited += poll_s
    now = iso(clock.now())
    for row in confirmed_open.values():   # timeout path: broker confirmed still open
        if _timeout_close(conn, row, broker, now, max_wait_s):
            filled += 1                   # it filled while the cancel was in flight
    for coid, reason in problem.items():           # fail-closed path: loud, not silent
        cur = conn.execute("SELECT status FROM orders WHERE client_order_id=?",
                           (coid,)).fetchone()
        cur_status = cur["status"] if cur is not None else "submitted"
        append_event(conn, "alert", {"text":
            f"order {coid[:8]} unresolved — broker unreachable or response "
            f"unparseable ({reason}) at cap, left {cur_status} for retry"}, now)
    return filled
