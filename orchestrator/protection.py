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

import json
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
        # `side` and `type` are what decide whether this order PROTECTS the
        # position, so a value we cannot READ makes cover unknown — never
        # silently smaller. Skipping an unreadable one would quietly downgrade
        # cover and then report "NO live protective order", which is a false
        # statement about an order we simply could not classify. Only a
        # readable, genuinely non-matching value is skipped.
        side, order_type = o.get("side"), o.get("type")
        if not isinstance(side, str) or not side.strip():
            return None
        if not isinstance(order_type, str) or not order_type.strip():
            return None
        if side.strip().lower() != closing_side:
            continue
        if order_type.strip().lower() not in _STOP_TYPES:
            continue
        n = _qty(o.get("qty"))
        if n is None:
            return None
        total += n
    return total


def _promised_stop(conn: sqlite3.Connection, symbol: str) -> float | None | object:
    """The stop the fund promised when it LAST opened this symbol.

    Three distinct answers, which is why the return type is widened rather
    than Optional: a float (a stop was promised at that price), None (that buy
    deliberately carried no stop — charter-sanctioned), or the _UNKNOWN
    sentinel (no filled buy order at all, so the promise cannot be read).
    Collapsing "no stop promised" into "cannot tell" would either silence a
    real fault or alert on every sanctioned stopless buy.

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
    broker. Returns the number of alerts appended.

    Never raises on a BROKER or DATA problem — unreachable, unparseable,
    unclassifiable all become alerts, because the day must finish and the
    finding must be recorded. A SQLite write failure DOES propagate, and
    deliberately: SQLite is the source of truth (invariant 6), every stage
    writes to it, and reconcile.py's own fail-closed path appends outside any
    try for the same reason. Swallowing a failed write here would mean the
    alert this module exists to raise was silently never recorded — the exact
    failure it is built to prevent."""
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
            # BOTH sides are re-read, not just the orders. A stop can FILL
            # during the wait, which closes the position and cancels nothing
            # — re-reading orders alone would then alert about a position that
            # no longer exists, using a stale list from before the nap.
            positions = list(broker.open_positions())
            if not positions:
                return 0
            problems = _evaluate(conn, positions, read_orders())
        except Exception as e:
            alert(unread("re-read", e))
            return 1
    for text in problems:
        alert(text)
    return len(problems)


# --- the mirror: shares the records account for that the broker does not hold -


def _recorded_holdings(conn: sqlite3.Connection) -> dict[str, int]:
    """Net shares per symbol the fund's OWN order records account for.

    THE SEAM. The fund stores no position — `state/schema.sql` has no positions
    table — so what it holds is *implied* by its filled orders. If a later
    design stores that fact directly, swap this one read; nothing above it
    changes.

    `filled_qty > 0` rather than `status = 'filled'`, for the same reason
    _promised_stop does it: reconcile.py records a timed-out partial as
    CANCELED with shares genuinely held."""
    net: dict[str, int] = {}
    for r in conn.execute("SELECT symbol, side, filled_qty FROM orders"
                          " WHERE filled_qty > 0"):
        signed = r["filled_qty"] if r["side"] == "buy" else -r["filled_qty"]
        net[r["symbol"]] = net.get(r["symbol"], 0) + signed
    return net


def _held_by_symbol(positions: list) -> dict[str, int] | None:
    """Whole shares per symbol at the broker. None if ANY line is unreadable —
    the unreadable one could be exactly the position that explains a gap, so a
    partial answer here would license a false accusation."""
    out: dict[str, int] = {}
    for raw in positions:
        p = raw if isinstance(raw, dict) else {}
        symbol, qty = p.get("symbol"), _qty(p.get("qty"))
        if not symbol or qty is None:
            return None
        out[str(symbol)] = out.get(str(symbol), 0) + qty
    return out


def _shortfall_text(symbol: str, recorded: int, held: int) -> str:
    return (f"{symbol}: the fund's own orders account for {recorded} shares"
            f" but the broker holds {held} — no recorded order explains the"
            " difference, so something the fund never recorded closed it (a"
            " stop leg, or an exit placed by hand). Record the exit or"
            " reconcile by hand; until then the fund's trade history is"
            " incomplete")


def _last_accounting(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """The most recent thing this guard said about `symbol`, or None.

    A READ of past findings, not a write, so the module's append-nothing
    discipline and its retraction-safety both survive: a read cannot create
    anything to retract.

    Most-recent rather than ever-said, and this is the whole design. Matching
    "have I alerted about NVDA before" would suppress a discrepancy that
    cleared and then RECURRED — the old event is still in the table, so the
    second occurrence would stay silent forever. That trades a noise problem
    for a silence problem, which is the worse of the two and the one this repo
    keeps being bitten by. A cleared finding is recorded as cleared, which
    re-arms the alert."""
    row = conn.execute(
        "SELECT json_extract(payload, '$.accounting') AS a FROM events"
        " WHERE kind = 'alert' AND json_extract(payload, '$.accounting.symbol')"
        " = ? ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    return json.loads(row["a"]) if row and row["a"] else None


def assert_positions_accounted(conn: sqlite3.Connection, *, broker,
                               now_iso: str,
                               sleep: Callable[[float], None] | None = None
                               ) -> int:
    """Alert when the fund's records account for shares the broker does not
    hold. Returns the number of alerts appended.

    assert_positions_protected iterates BROKER positions, so a position that
    has CLOSED produces no iteration and no alert. That is right for
    protection — a closed position needs none — and it is exactly why nothing
    notices when a stop fires. An OTO stop leg has no `orders` row by
    construction (agents/runtime.py:151 writes one row per place_* response,
    keyed on the parent), so the exit that closed the position is recorded
    nowhere and the fund's account of its own trading is silently wrong.

    Direction matters here as it does above, and it is the opposite one. The
    BROKER is the authority on what is held; the fund's records are the
    authority on what the fund DID. This asks only whether the fund's own
    trades can account for what the broker shows, never the converse — that
    would alert on every hand-placed intervention these alerts ask for, and
    assert_positions_protected already covers that direction via _UNKNOWN.

    Reported once per distinct discrepancy, and again if it clears and
    recurs. The condition is permanent until a human reconciles it, and
    repeating it every run would redden the audit forever — a permanently red
    audit is what masks the next new failure. That is a narrower argument than
    crying wolf: the sanctioned stopless position above is a correct state,
    while this is a fault that is still a fault on day two. Worth saying once,
    not worth saying daily."""
    nap = sleep or (lambda _s: None)

    def alert(text: str, accounting: dict) -> None:
        append_event(conn, "alert",
                     {"text": text, "accounting": accounting}, now_iso)

    def why(e: Exception) -> str:
        return f"{type(e).__name__}: {str(e)[:120]}"

    def unverified(text: str) -> int:
        append_event(conn, "alert", {"text": text}, now_iso)
        return 1

    recorded = {s: q for s, q in _recorded_holdings(conn).items() if q > 0}
    if not recorded:
        return 0

    if broker is None:
        return unverified(
            "position accounting UNVERIFIED — no broker wired into the run;"
            f" the fund's records account for {len(recorded)} holding(s) and"
            " nothing can confirm the broker still has them")

    def read() -> dict[str, int] | None:
        return _held_by_symbol(list(broker.open_positions()))

    def unread(how: str, e: Exception) -> str:
        return (f"position accounting UNVERIFIED — could not {how} positions"
                f" ({why(e)}); a holding the fund believes it opened may"
                " already be gone and nothing would say so")

    try:
        held = read()
    except Exception as e:
        return unverified(unread("read", e))

    if held is None or any(held.get(s, 0) < q for s, q in recorded.items()):
        # A buy that just filled is recorded before the broker lists the
        # position, and this runs right after reconciliation — exactly when a
        # fill has just happened. Without one short wait the fund would alert
        # on every day it actually trades. Once per run, never once per symbol.
        nap(_RETRY_S)
        try:
            held = read()
        except Exception as e:
            return unverified(unread("re-read", e))
    if held is None:
        return unverified(
            "position accounting UNVERIFIED — a position line could not be"
            " read, so whether the fund's records are accounted for is unknown")

    appended = 0
    for symbol in sorted(recorded):
        have, want = held.get(symbol, 0), recorded[symbol]
        last = _last_accounting(conn, symbol) or {}
        if have >= want:
            # Clearing is recorded, not merely observed: without this the next
            # occurrence would match the stale open finding and stay silent.
            if not last.get("cleared", True):
                alert(f"{symbol}: the fund's orders and the broker agree again"
                      f" at {have} shares — the earlier accounting gap is"
                      " closed",
                      {"symbol": symbol, "cleared": True})
                appended += 1
            continue
        if (last.get("recorded"), last.get("held")) == (want, have):
            continue                      # same finding, already standing
        alert(_shortfall_text(symbol, want, have),
              {"symbol": symbol, "recorded": want, "held": have,
               "cleared": False})
        appended += 1
    return appended
