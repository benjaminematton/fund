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
from gate.tickets import open_tickets_without_orders
from orchestrator.clock import Clock, iso
from slackkit.outbox import append_alert, append_event
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
                    append_alert(conn, "fill_on_unapproved_decision",
                        f"order {coid[:8]} filled but decision "
                        f"{t['decision_id']} was '{dec_status}', not "
                        "'approved' — left as-is, manual review",
                        now_iso=now)
        return moved
    if st == "partially_filled":
        filled_qty, filled_avg_price = _parse_fill(o)
        conn.execute("UPDATE orders SET filled_qty=?, filled_avg_price=?"
                     " WHERE client_order_id=?",
                     (filled_qty, filled_avg_price, coid))
        conn.commit()
        if try_transition(conn, "orders", {"client_order_id": coid},
                          "submitted", "partially_filled", now):
            append_alert(conn, "partial_fill_manual_review",
                f"partial fill {row['symbol']} {coid[:8]} — manual review",
                now_iso=now)
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
    null, or 0, and filled_avg_price is null. Anything else goes through
    _parse_fill and raises if malformed: a dead order that claims shares must
    say how many and at what price, or we record nothing at all."""
    raw_qty = o.get("filled_qty")
    if raw_qty in (None, "") or float(raw_qty) == 0:
        return None
    return _parse_fill(o)


def _timeout_close(conn, row, broker, now, max_wait_s) -> bool:
    """Close out ONE order the broker last confirmed still working, past the
    cap: request the cancel, re-query ONCE, record the TRUE terminal state.
    Returns True if the order filled in the race; False otherwise, which
    keeps the caller's fill counter honest.

    The order can be 'submitted' OR 'partially_filled' in the DB (accepted ->
    partially_filled -> canceled happens inside one run), so the CAS starts
    from whatever the row actually holds. The re-queried fill numbers decide
    the decision's fate, because the DB must tell the truth about what's held:
    filled_qty > 0 is a REAL position, so the decision is 'executed' with a
    fill event for the partial qty; only a zero fill is 'failed'.

    Fail closed everywhere the broker's answer is ambiguous — a cancel that
    errored while the order still works, a re-query that errored or returned
    nothing, a status that is not confirmed-dead, fill numbers that do not
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
        append_alert(conn, "order_unreconciled", stale.format(
            why=f"re-query raised {type(e).__name__}{cancel_err}"), now_iso=now)
        return False
    if status in ("filled", "partially_filled"):
        try:
            return _apply(conn, row, o, now)      # the race: record the fill
        except Exception as e:
            append_alert(conn, "order_unreconciled", stale.format(
                why=f"broker reports '{status}' but the payload is "
                    f"unparseable ({type(e).__name__})"), now_iso=now)
            return False
    if status not in _CONFIRMED_DEAD:
        append_alert(conn, "order_unreconciled", stale.format(
            why=f"broker still reports '{status}'{cancel_err}"), now_iso=now)
        return False
    dead = _CONFIRMED_DEAD[status]
    cur = conn.execute("SELECT status, qty FROM orders WHERE client_order_id=?",
                       (coid,)).fetchone()
    from_status = cur["status"] if cur is not None else "submitted"
    if (from_status, dead) not in EDGES["orders"]:
        append_alert(conn, "order_unreconciled", stale.format(
            why=f"broker reports '{status}' but the order is '{from_status}'"
                " — no legal transition"), now_iso=now)
        return False
    try:
        fill = _dead_fill(o)
    except Exception as e:
        append_alert(conn, "order_unreconciled", stale.format(
            why=f"broker reports '{status}' but the fill numbers are "
                f"unparseable ({type(e).__name__})"), now_iso=now)
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
            append_alert(conn, "order_unfilled_at_cap",
                f"order {coid[:8]} unfilled after {int(max_wait_s)}s — "
                f"{dead} at the broker, "
                f"{'decision failed' if moved else 'decision unchanged'}",
                now_iso=now)
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
            append_alert(conn, "order_partial_then_dead",
                f"order {coid[:8]} partial {filled_qty} of {cur['qty']} then "
                f"{dead} at the broker after {int(max_wait_s)}s, "
                f"{'decision executed' if moved else 'decision unchanged'}",
                now_iso=now)
    return False


def reconcile_orders(conn: sqlite3.Connection, *, clock: Clock, broker,
                     sleep: Callable[[float], None],
                     poll_s: float = 3.0, max_wait_s: float = 90.0) -> int:
    """Poll every submitted order to a terminal state, then close out at the
    cap whatever the broker still confirms as working. Returns the number of
    orders this call moved to 'filled'. poll_s is the polling cadence and
    max_wait_s the total budget before the timeout path runs; raises
    ValueError on a non-positive poll_s."""
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
                # parse (for example, _apply's coercion raised). Either way
                # leave the order 'submitted' for the next poll and surface it
                # loudly.
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
        append_alert(conn, "order_unresolved_at_cap",
            f"order {coid[:8]} unresolved — broker unreachable or response "
            f"unparseable ({reason}) at cap, left {cur_status} for retry",
            now_iso=now)
    return filled


def _recoverable_qty(o: dict, ticket: dict) -> int | None:
    """The whole-share qty on a broker order that MATCHES the ticket which
    authorized it; None if anything does not line up.

    Fail closed (invariant 4): an order that is not what the gate approved is
    not this ticket's order, and recording it would put an unauthorized trade
    in the books. So a mismatched client_order_id, symbol or side, and a qty
    that is not a whole number, is below 1, or is above the ticket's max_qty
    all deny.

    client_order_id IS the ticket id (invariant 5) and is the key this whole
    lookup was made on, so it is CHECKED, never assumed: an adapter that
    answers a lookup with some other order — a stop leg, a fuzzy match — must
    not get that order recorded under this ticket, under a foreign
    alpaca_order_id. An order that does not echo the key at all denies too:
    open_tickets_without_orders always supplies the ticket's id, so an absent
    key compares unequal, and an answer that cannot prove which order it is is
    exactly the ambiguity invariant 4 resolves to no action.

    Numbers arrive as STRINGS on the real wire (alpaca-py 0.44 Order fields
    are Optional[str]), so coerce and reject rather than floor — this fund is
    whole-share only and orders.qty is INTEGER. The coercion accepts an int or
    an ASCII digit-string and nothing else, which is at least as strict as
    both sibling coercers (gate/tickets.py:_as_share_count, which this
    mirrors, and orchestrator/protection.py:_qty). They stay separate
    functions on purpose — see _qty's docstring. Matching on SHAPE rather than
    running float() is also what keeps this function total: 'nan' and 'inf'
    parse as floats and then raise inside int(), which would make this
    function raise where it promises None, and the caller would file
    "could not be checked against the broker" for a payload it checked fine."""
    if o.get("symbol") != ticket["ticker"] or o.get("side") != ticket["side"]:
        return None
    if o.get("client_order_id") != ticket.get("id"):
        return None
    qty = o.get("qty")
    if isinstance(qty, bool):              # bool is an int; 1 share nobody placed
        return None
    if isinstance(qty, str) and qty.isascii() and qty.isdigit():
        qty = int(qty)
    if not isinstance(qty, int):
        return None
    if qty < 1 or qty > ticket["max_qty"]:
        return None
    return qty


def recover_lost_orders(conn: sqlite3.Connection, *, clock: Clock,
                        broker) -> int:
    """Repair pass for issue #40: an order that LANDED at the broker but whose
    `orders` row was never written. Returns the number of tickets whose
    open->consumed CAS THIS call won, so a re-run cannot over-count.

    The row is written submit-then-write, by a PostToolUse hook on the place_*
    response. A response lost to a gateway 504, or returned as the
    duplicate-client_order_id 422, makes agents.runtime._extract_order return
    None and nothing at all is written. reconcile_orders selects `FROM orders`
    and so is structurally blind to it: the broker filled and SQLite
    permanently records that nothing traded.

    **The recovered order is recorded at status 'submitted' and nothing else.**
    No fill parsing, no order transition, no fill event, no decision touched —
    reconcile_orders runs immediately after, in the same stage body, and drives
    all of that through its already-tested _apply. This holds even when the
    broker's order is already 'rejected' or 'canceled': it is still recorded at
    'submitted', and reconcile_orders then polls, sees the terminal status at
    the broker, and CASes it through the state machine. A recovered order must
    behave IDENTICALLY to a normally-recorded one — same broker state, same
    outcome, regardless of whether the recorder happened to work that day —
    because two classes of order row would force anyone defining retry
    semantics later to handle both. state/schema.sql declares
    `status TEXT NOT NULL DEFAULT 'submitted'`: rows are born submitted and
    move by CAS, and that is the canonical DDL's stated intent.

    Fail closed everywhere (invariant 4). No broker or a port without the
    lookup: return 0, no writes, no alert. Broker never heard of the ticket:
    SILENT skip — that is the ordinary "the turn placed nothing" day, which
    the execution stage's ticket_open_after_exec alert already reports. Broker
    raises: alert, write nothing, leave the ticket open for the next run.
    Order does not match the ticket: alert, write nothing. Row could not be
    written: alert, leave the ticket open — see the INSERT below. The
    try/except is INSIDE the loop, so one bad ticket does not stop the rest.

    Every alert here passes the ticket's `ticker`. scripts/file_alert_issues.py
    groups on ("alert:{code}", "ticker:{ticker}") and skips a group that
    already has an open issue, so a bare code would file the first bad ticket
    and silently drop every later one — one issue for the whole class, on the
    path that exists to catch an order the fund cannot account for.

    A successful recovery alerts deliberately: the PostToolUse recorder
    failing is a fault a human should see, and #40 exists because such days
    read as clean. Idempotent (invariant 5): INSERT OR IGNORE whose rowcount
    is CHECKED, then the ticket moves by CAS, and the count and the alert fire
    only when that CAS wins."""
    lookup = getattr(broker, "get_order_by_client_order_id", None)
    if lookup is None:
        return 0
    now = iso(clock.now())
    recovered = 0
    for ticket in open_tickets_without_orders(conn):
        tid = ticket["id"]
        try:
            o = lookup(tid)
            if not o:
                continue                  # the turn placed nothing: normal
            qty = _recoverable_qty(o, ticket)
            if qty is None:
                append_alert(conn, "order_recovery_mismatch",
                    f"ticket {tid[:8]} has no order row and the broker's order"
                    f" does not match it ({o.get('symbol')!r}"
                    f" {o.get('side')!r} {o.get('qty')!r} vs ticket"
                    f" {ticket['ticker']!r} {ticket['side']!r} max"
                    f" {ticket['max_qty']}) — nothing recorded", now_iso=now,
                    ticker=ticket["ticker"])
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO orders (client_order_id,"
                " alpaca_order_id, symbol, side, qty, status, submitted_at)"
                " VALUES (?, ?, ?, ?, ?, 'submitted', ?)",
                (tid, o.get("id"), ticket["ticker"], ticket["side"], qty, now))
            conn.commit()
            if cur.rowcount != 1:
                # The INSERT was DROPPED and OR IGNORE swallowed it — most
                # likely orders.alpaca_order_id (TEXT UNIQUE) already holds
                # this broker id. Consuming the ticket now would take it out
                # of open_tickets_without_orders forever and leave a live
                # broker order with no row anywhere, while the alert claimed
                # success: precisely the hole this pass exists to close. So
                # do not CAS, do not claim recovery — alert and leave it open
                # for the next run (invariant 4).
                append_alert(conn, "order_recovery_unwritable",
                    f"ticket {tid[:8]} has no order row and the broker's order"
                    f" {o.get('id')!r} could NOT be written — the INSERT was"
                    " dropped (alpaca_order_id is UNIQUE and this id is"
                    " already in the books). Nothing recorded, ticket left"
                    " open; the broker order is unaccounted for until a human"
                    " resolves the id collision", now_iso=now,
                    ticker=ticket["ticker"])
                continue
            if try_transition(conn, "tickets", {"id": tid},
                              "open", "consumed", now):
                recovered += 1
                append_alert(conn, "order_recovered",
                    f"ticket {tid[:8]} had no order row but the broker holds"
                    f" {qty} — recorded submitted; the place_* recorder did"
                    " not run", now_iso=now, ticker=ticket["ticker"])
        except Exception as e:            # per-ticket isolation, fail closed
            append_alert(conn, "order_recovery_failed",
                f"ticket {tid[:8]} could not be checked against the broker"
                f" ({type(e).__name__}) — nothing recorded, left open for the"
                " next run", now_iso=now, ticker=ticket["ticker"])
    return recovered


def reconcile_stage(conn: sqlite3.Connection, *, clock: Clock, broker,
                    sleep: Callable[[float], None],
                    poll_s: float = 3.0, max_wait_s: float = 90.0) -> int:
    """The reconciliation stage body: recover the orders that never got a row,
    then poll every submitted order to a terminal state. Returns
    reconcile_orders' fill count, so the stage's return value keeps its
    existing meaning."""
    recover_lost_orders(conn, clock=clock, broker=broker)
    return reconcile_orders(conn, clock=clock, broker=broker, sleep=sleep,
                            poll_s=poll_s, max_wait_s=max_wait_s)
