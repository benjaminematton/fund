"""The protection log: what the fund has SEEN protecting a position.

Append-only. No status column, nothing ever closed or rewritten — ADR-0004.
A row is an observation stamped `observed_at`, never a claim about now.

**This module must never become the source of what protection EXISTS.** That
stays a broker read in `orchestrator/protection.py:_covering_qty`. A table
that fed the coverage number would recreate the 2026-08-17 failure, where the
database asserted a stop the broker did not hold and nothing noticed for two
sessions.

`STOP_TYPES`, `CLOSING_SIDE` and `qty_of` live here rather than in
`orchestrator/protection.py` so there is one protective-order predicate and
one broker-numeric coercer, in one place each, that cannot drift apart. The
orchestrator imports them back. Nothing in `state/` imports `orchestrator/`,
and this module keeps it that way.
"""

from __future__ import annotations

import sqlite3

# An order type that protects a long position by closing it on the way down.
# trailing_stop carries no stop price, which is why the column is nullable.
STOP_TYPES = ("stop", "stop_limit", "trailing_stop")

# The closing side for a position. A short is absent on purpose: this fund is
# long-only (state/models.py — stops guard new or added longs), so a short at
# the broker is unclassifiable and must fail closed.
CLOSING_SIDE = {"long": "sell"}

_PROTECTIVE_SIDES = frozenset(CLOSING_SIDE.values())


def normalized(value) -> str | None:
    """A broker enum as a comparable lowercase string, or None if unreadable.

    ONE normalisation rule, shared by the coverage number and the log. They
    compared differently at first — `_covering_qty` stripped and lowered while
    the log compared raw — so ' Stop ' counted toward cover and was silently
    absent from the record. Two reviewers found it independently. Moving
    STOP_TYPES into one module was supposed to make exactly that impossible,
    and it did not, because the constant was shared and the comparison was
    not."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def qty_of(value) -> int | None:
    """Whole-share count from a string or int; None if unreadable. Broker
    numerics arrive as strings. Fractional, negative, bool and unparseable all
    return None and therefore skip.

    Twin of gate/tickets.py:_as_share_count (which coerces adversarial AGENT
    input) and a cousin of reconcile.py:_parse_fill. Kept separate on purpose:
    this one coerces BROKER output and the two may legitimately diverge."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != int(n) or n <= 0:
        return None
    return int(n)


def _price_of(value) -> float | None:
    """Stop price as a float, or None. None is a legitimate value — a
    trailing_stop has no stop price — so an unreadable price is NOT a skip.
    It records the order without a price rather than dropping an order that
    `_covering_qty` counts toward cover."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def log_observed(conn: sqlite3.Connection, orders: list[dict], *,
                 now_iso: str) -> list[str]:
    """Append one row per protective order in `orders`. Returns the ids
    ACTUALLY inserted — empty when everything was already recorded for this
    observation, which is the caller's idempotence signal and is not
    recoverable from the ids alone.

    The id is DERIVED — `f"{alpaca_order_id}@{observed_at}"` — never minted.
    No `id_factory` is threaded, so `assert_positions_protected`'s signature
    is untouched and determinism is structural: sim-day and replay are safe
    without sharing `ctx.id_factory` with tickets.

    An order missing a readable qty or broker id is SKIPPED, never guessed
    (invariant 4). Skipping costs exactly one observation in an append-only
    log and can corrupt nothing. A missing stop price is not a skip.
    """
    written: list[str] = []
    for order in orders:
        if normalized(order.get("type")) not in STOP_TYPES:
            continue
        if normalized(order.get("side")) not in _PROTECTIVE_SIDES:
            continue
        alpaca_order_id = order.get("id")
        qty = qty_of(order.get("qty"))
        symbol = order.get("symbol")
        if not alpaca_order_id or qty is None or not symbol:
            continue
        row_id = f"{alpaca_order_id}@{now_iso}"
        cur = conn.execute(
            "INSERT OR IGNORE INTO protection"
            " (id, symbol, qty, stop_price, alpaca_order_id, client_order_id,"
            "  provenance_kind, broker_expires_at, observed_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'observed', ?, ?, ?)",
            (row_id, symbol, qty, _price_of(order.get("stop_price")),
             alpaca_order_id, order.get("client_order_id"),
             order.get("expires_at"), now_iso, now_iso))
        if cur.rowcount:
            written.append(row_id)
    conn.commit()
    return written
