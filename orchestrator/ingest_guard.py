"""Guard on the positions payload the whole trading day is sized from
(issue #39, 2026-08-25).

scripts/run_day.py reads the account ONCE, at the top of the day, and every
gate input for every ticker is computed from that one payload. A positions
list that comes back EMPTY therefore does not fail — it sizes. held_qty 0, so
no sell is possible; position_count 0; an empty correlation book, which
market/features.py:83 correctly scores at the most permissive 1.10x tier;
sector book value 0. The day trades UP, on a book it cannot see, and the
holdings it does have cannot be exited.

The 1.85x mis-sizing issue #39 reports is from a WHAT-IF run against a
recorded account, not an observed production incident. The defect is real and
reachable; the number is a simulation. Do not cite it as a measured event.

orchestrator/protection.py already compares the broker against the fund's own
records — but at the CLOSE stage, hours after the gate sized the day. This is
the same seam read at INGESTION, and with the opposite consequence:
protection alerts and lets the day finish, this refuses the day (invariant 4).

WHAT THIS CATCHES, AND WHAT IT DOES NOT
---------------------------------------
market/source_alpaca.py:161-162 makes TWO independent API calls against ONE
broker backend, and the account's long_market_value is derived from the same
position store the positions list is read from. So this catches a fault
BETWEEN the two reads — transport, serialization, a dropped or truncated
response — because that is the class where they disagree.

It does NOT catch a fault at or below the position store, where both reads
agree and are both wrong: a wrong ALPACA_API_KEY pointing at a different or
fresh paper account, an account reset, a backend that lost the positions.
Those return a self-consistent positions=[] + long_market_value=0, this reads
"genuinely flat", and the day trades. That class is issue #64 ("The fund never
verifies it is talking to the account it thinks it is") and belongs in the
daily preconditions pass, not here — identity needs one field against one
pinned value and no tolerance. orchestrator/preconditions.py:46 pins account
SETTINGS, not identity, so a fresh paper account passes it today.

The fund's own records WOULD catch that class, and they are still only the
tie-breaker below. That is deliberate, and the reason is one line of SQL:
recorded_holdings (protection.py:284-289) selects every order ever placed with
NO DATE FILTER. A stop that fires on day 5 leaves those rows forever, so a
records-first guard refuses day 6 and every day after until a human edits the
table. Buying the #64 class costs a fund that can never trade again.

Not caught here either: a PARTIAL payload (some positions listed, some
dropped) — issue #63. Detecting one needs a tolerance, a tolerance is a
threshold, and invariant 3 makes thresholds human-commit decisions.

Deliberately NOT here: any per-symbol comparison. protection.py:352-357
settled that direction — the broker is the authority on what is held, the
fund's records on what the fund DID — and reversing it would halt the fund on
every hand-placed intervention its alerts ask for.

WHY A REFUSED DAY IS ABSENT FROM THE AUDIT, AND WHY THAT IS NOT #39 AGAIN
-------------------------------------------------------------------------
scripts/run_day.py returns before StageCtx is built, so report_audit never
runs — the same shape as every other guarded() exit. The irony is worth
naming, because the next reader will notice it: this lane exists because of a
defect that produced an AUDIT CLEAN day (#39's body — "audit_day.py has no
positions concept, so the day is AUDIT CLEAN"), so a fix whose own end state
is "absent from the audit" looks like it repeats the mistake.

It does not. append_alert (slackkit/outbox.py:33) writes through _insert to
INSERT INTO events (kind, payload, created_at) (outbox.py:21-22), so the alert
is durably in SQLite, not only in Slack — the day is recorded in the source of
truth, satisfying invariant 6 rather than skirting it. And append_alert's own
docstring makes `code` "what scripts/file_alert_issues.py keys a GitHub issue
on", identical across runs, so the day carries a stable machine identity that
can become a tracked issue on its own — a stronger trail than an audit row,
not a weaker one.

So a payload-lost day is absent from the audit, present in the source of
truth, and carrying a machine-readable code. That is a different thing from
#39's original defect, where the day was present in the audit and marked
CLEAN: the audit affirmatively said nothing was wrong. Not-asserted and
wrongly-asserted are not the same failure.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Callable

from orchestrator.protection import recorded_holdings
from slackkit.outbox import append_alert

# One short wait before refusing a day, matching orchestrator/protection.py:35
# and for the same reason — see account_snapshot. Deliberately a local
# constant: this sits at a different point in the day and may diverge.
_RETRY_S = 3.0


def _as_dollars(value) -> float | None:
    """The broker's dollar figure, or None if it is missing, NaN, or will not
    parse. Cousin of protection.py:_qty, which coerces SHARE counts and so
    rejects fractions and zero; a market value is legitimately fractional and
    legitimately zero, and zero is the answer that matters most here."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) else n


def payload_fault(account: dict, recorded: dict[str, int]) -> str | None:
    """Why today's positions payload cannot be trusted, or None if it can.

    Fires ONLY on an empty positions payload. Empty is ambiguous, and telling
    the two cases apart is the whole of this function:

      - genuinely empty — the fund has never traded, or it sold out. The
        permissive empty-book tier is the correct answer and must be reached.
      - lost — the account holds something and the list did not say so.

    The broker's own long_market_value settles it for the class this module
    catches — see the module docstring for the class it does not. The fund's
    records cannot lead here: an OTO stop leg has no `orders` row by
    construction (protection.py:347), so a fund stopped out overnight has
    non-empty records and an honestly empty book — the state
    tests/test_protection.py:530 pins as alert-once-and-keep-trading. And
    recorded_holdings has no date filter (protection.py:284-289), so halting
    on records alone would halt that day and every day after it, until a human
    edits the orders table. A fund that never trades again is not the safe
    direction, it is a different outage.

    So a readable long_market_value of exactly 0 means flat, whatever the
    records say. A readable non-zero means the list dropped positions the
    account still carries. An UNREADABLE one is ambiguity, and there the
    records are the tie-breaker: no records means nothing suggests a book
    exists and the fund's first day must not be blocked by a broker that will
    not report a market value; records mean HOLD.

    `recorded` is recorded_holdings()' raw output, signed and unfiltered.
    Netting to zero or below is not a holding — a fully sold symbol nets to 0
    — so it is filtered here rather than being trusted to arrive filtered.
    """
    # Bare truthiness, deliberately: a missing key, None, and {} all mean
    # "the list said nothing", which is the case this guards. (No `or {}` —
    # it would be dead code, since {} is already falsy.)
    if account.get("positions"):
        return None

    held_value = _as_dollars(account.get("long_market_value"))
    if held_value == 0:
        return None
    if held_value is not None:
        return (f"the broker returned NO positions while reporting"
                f" {held_value} of long market value — the positions payload"
                " was lost, not empty. Sizing the day on it would compute"
                " every gate input against a book that is not there and make"
                " every sell impossible, so no stage ran and nothing traded."
                " Re-run once the broker lists positions again")

    book = {s: q for s, q in recorded.items() if q > 0}
    if not book:
        return None
    holdings = ", ".join(f"{s} {q}" for s, q in sorted(book.items()))
    return ("the broker returned NO positions and no readable long market"
            f" value, while the fund's own filled orders account for"
            f" {holdings} — whether the book is empty or the payload was lost"
            " cannot be established, so no stage ran and nothing traded"
            " (invariant 4)")


def account_snapshot(conn: sqlite3.Connection, *, source, now_iso: str,
                     sleep: Callable[[float], None] | None = None
                     ) -> dict | None:
    """The account the day may trade on, or None — the positions payload could
    not be trusted, an alert is appended, and no stage may run.

    None rather than a raise. The caller has to drain the outbox before it
    exits, and a raise would land in scripts/run_day.py's guarded(), which
    records every failure under the single code `run_day_failed` — the code
    scripts/file_alert_issues.py keys its GitHub issue on. A lost positions
    payload has its own operator response and deserves its own code.

    A broker read that RAISES is deliberately not caught: guarded() already
    turns it into an alert, a drain and exit 1, which is the same HOLD. Only
    the case where the broker answers, plausibly, and is wrong needs this
    module.

    One nap and one re-read before refusing, for the reason protection.py:405
    naps: a buy that just filled is recorded before the broker lists the
    position. A FIRST run of the day has placed no orders and cannot hit that
    race; a RESUMED run reads the account again after the execution stage
    already filled something, and it can. Three seconds is a cheap price for
    not killing a trading day over a settle lag, and it is only ever paid on
    the path that is about to refuse.

    The re-read replaces the whole account, and the FRESH one is what the day
    is sized from. Validating a fresh payload and sizing on the stale one
    would be this same bug wearing a guard.
    """
    nap = sleep or (lambda _s: None)
    recorded = recorded_holdings(conn)

    account = source.account_state()
    fault = payload_fault(account, recorded)
    if fault is None:
        return account

    nap(_RETRY_S)
    account = source.account_state()
    fault = payload_fault(account, recorded)
    if fault is None:
        return account

    append_alert(conn, "positions_payload_lost", fault, now_iso=now_iso)
    return None
