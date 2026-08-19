"""Assertion: a promised stop still exists at the broker (2026-08-19).

Not a stage — no checkpoint, no CAS, no resumability. It re-checks on a
resumed day rather than being skipped as 'done', and a duplicate alert is the
safe direction.

The BROKER owns what protection exists; the fund's own record owns what
protection was PROMISED. Comparing those two is exactly the comparison nobody
performed on 2026-08-17, when the database said 'stop at 215' for two sessions
while the broker held no order at all. Note the direction: the database is
never allowed to assert that a stop EXISTS — only what was intended.

So a promised stop that is gone is a fault and alerts. A position the PM
deliberately opened without one (charters/pm.md:25 — stop_price is passed only
for a hard price invalidation) is standing exposure, not a fault: alerting on
it would red the audit (scripts/audit_day.py:148) every day on a correct day,
and a channel that cries wolf daily protects nothing. That exposure is
reported in the EOD digest instead (follow-up branch).

Every ambiguity alerts. Broker unreachable, a number that will not parse, a
position with no provenance in our own records — none of them may pass
quietly, because a check that can pass while lying is worse than no check at
all (invariant 4)."""
from __future__ import annotations

import sqlite3
from typing import Callable

from slackkit.outbox import append_event

# One short wait before calling a position naked. Matches reconcile_orders'
# poll_s default. Deliberately NOT max_wait_s (90s): this sits on the critical
# path of a live trading day, just before the digest posts.
_RETRY_S = 3.0

# Order types that actually cap a loss. A sell LIMIT is a take-profit: it caps
# the upside and leaves the downside fully exposed, so it is not protection.
_STOP_TYPES = ("stop", "stop_limit", "trailing_stop")

# The closing side for a position. A short is absent on purpose: this fund is
# long-only (state/models.py — stops guard new or added longs), so a short at
# the broker is unclassifiable and must fail closed.
_CLOSING_SIDE = {"long": "sell"}

# "The fund has no record of opening this position" — distinct from "it was
# opened with no stop on purpose". The first fails closed, the second is fine.
_UNKNOWN = object()


def _qty(value) -> int | None:
    """Whole-share count from a string or int; None if unreadable. Broker
    numerics arrive as strings. Fractional, negative, bool and unparseable all
    return None and therefore alert.

    Twin of gate/tickets.py:_as_share_count (which coerces adversarial AGENT
    input) and a cousin of reconcile.py:_parse_fill. Kept separate on purpose:
    this one coerces BROKER output and the two may legitimately diverge.
    Unifying all three into a shared helper is a follow-up — it would mean
    editing reconcile's fill parsing, which does not belong in this diff."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != int(n) or n <= 0:
        return None
    return int(n)


def _covering_qty(orders: list, symbol: str, closing_side: str) -> int | None:
    """Shares of `symbol` protected by live stop orders. None if ANY order is
    unreadable — an order that will not parse might be the protective one, so
    the answer is 'unknown', never a smaller number."""
    total = 0
    for o in orders:
        if not isinstance(o, dict):
            return None
        if str(o.get("symbol") or "") != symbol:
            continue
        if str(o.get("side") or "").lower() != closing_side:
            continue
        if str(o.get("type") or "").lower() not in _STOP_TYPES:
            continue
        n = _qty(o.get("qty"))
        if n is None:
            return None
        total += n
    return total


def _promised_stop(conn: sqlite3.Connection, symbol: str):
    """The stop the fund promised when it LAST opened this symbol: the price,
    None if that buy deliberately carried no stop, or _UNKNOWN if there is no
    filled buy order for it at all.

    Most-recent rather than any: a symbol sold and re-bought without a stop is
    read on its current terms instead of inheriting an old promise forever.

    `filled_qty > 0` as well as `status = 'filled'`, because reconcile.py
    records a timed-out partial as CANCELED with shares held and calls that a
    real position."""
    row = conn.execute(
        "SELECT t.stop_price AS stop_price FROM orders o"
        " JOIN tickets t ON t.id = o.client_order_id"
        " WHERE o.symbol = ? AND o.side = 'buy'"
        "   AND (o.status = 'filled' OR o.filled_qty > 0)"
        " ORDER BY o.submitted_at DESC LIMIT 1",
        (symbol,)).fetchone()
    if row is None:
        return _UNKNOWN
    return row["stop_price"]


def _evaluate(conn: sqlite3.Connection, positions: list,
              orders: list) -> list[str]:
    """Alert texts for ONE snapshot of the account. Reads only; appends
    nothing, so the caller can evaluate a snapshot, wait, and evaluate a
    fresher one without having written anything it must retract."""
    out: list[str] = []
    for raw in positions:
        p = raw if isinstance(raw, dict) else {}
        symbol = str(p.get("symbol") or "?")
        side = str(p.get("side") or "").lower()
        held = _qty(p.get("qty"))
        closing_side = _CLOSING_SIDE.get(side)
        if held is None or closing_side is None:
            out.append(f"{symbol} position UNVERIFIED — cannot read"
                       f" side={p.get('side')!r} qty={p.get('qty')!r}, so"
                       " whether it is protected is unknown")
            continue
        covered = _covering_qty(orders, symbol, closing_side)
        if covered is None:
            out.append(f"{symbol} {held} UNVERIFIED — a live order for it"
                       " could not be read, so its cover cannot be confirmed")
            continue
        if covered >= held:
            continue
        promised = _promised_stop(conn, symbol)
        if promised is _UNKNOWN:
            out.append(f"{symbol} {held} is held with NO live protective order"
                       " and no fund record of opening it — provenance"
                       " unknown, so whether it should be protected cannot be"
                       " established")
        elif promised is not None:
            # "NO live protective order (30 of 80 stopped)" contradicts
            # itself. Say which of the two situations this actually is.
            shortfall = ("the broker has NO live protective order" if not covered
                         else f"the broker covers only {covered} of {held}"
                              " shares")
            out.append(f"{symbol} {held} was ticketed with a stop at"
                       f" {promised} but {shortfall} — the position is exposed"
                       " and no code path will protect it; place or restore a"
                       " stop manually")
        # promised is None: the PM opened this without a stop on purpose
        # (charters/pm.md:25). Standing exposure, not a fault — reported in
        # the EOD digest, not as an alert.
    return out


def assert_positions_protected(conn: sqlite3.Connection, *, broker,
                               now_iso: str,
                               sleep: Callable[[float], None] | None = None
                               ) -> int:
    """Alert on every open position whose promised stop is not live at the
    broker. Returns the number of alerts appended. Never raises."""
    nap = sleep or (lambda _s: None)

    def alert(text: str) -> None:
        append_event(conn, "alert", {"text": text}, now_iso)

    def why(e: Exception) -> str:
        # The type alone ("ConnectionError") is not actionable at 16:05 on a
        # day nobody is reading logs; "401 unauthorized" is.
        return f"{type(e).__name__}: {str(e)[:120]}"

    def read_orders() -> list:
        return list(broker.open_orders())

    if broker is None:
        alert("position protection UNVERIFIED — no broker wired into the run;"
              " a held position could be unprotected and nothing would say so")
        return 1
    try:
        positions = list(broker.open_positions())
    except Exception as e:
        alert("position protection UNVERIFIED — could not read positions"
              f" ({why(e)}); a held position could be unprotected and nothing"
              " would say so")
        return 1
    if not positions:
        return 0

    def unread(how: str, e: Exception) -> str:
        return (f"position protection UNVERIFIED — holding {len(positions)}"
                f" position(s) but could not {how} live orders ({why(e)});"
                " cover is unknown, not confirmed")

    try:
        problems = _evaluate(conn, positions, read_orders())
    except Exception as e:
        alert(unread("read", e))
        return 1
    if problems:
        # An OTO stop leg is created 'held' and can lag its parent in the API
        # by moments — and this runs immediately after reconciliation, which
        # is exactly when a fill just happened. Without one short wait and a
        # re-read, the fund would alert on every position it correctly
        # protects, on every day it actually trades. Once per run, never once
        # per position.
        nap(_RETRY_S)
        try:
            problems = _evaluate(conn, positions, read_orders())
        except Exception as e:
            alert(unread("re-read", e))
            return 1
    for text in problems:
        alert(text)
    return len(problems)
