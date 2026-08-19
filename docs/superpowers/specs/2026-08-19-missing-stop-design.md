# Closing the missing-stop class

**Date** 2026-08-19 · **Branch** `worktree-missing-stop-fix` (off `master`)

## The problem

The 2026-08-17 NVDA entry placed as an OTO whose stop leg inherited the parent's
`time_in_force: DAY`. The leg expired at 16:00 ET the same day. The position sat
unprotected for two sessions while `decisions.stop_price` and `PROGRESS.md` both
asserted a live stop at 215. Broker-confirmed; the full write-up is in
`PROGRESS.md` under "2026-08-19 — the stop that expired at the bell".

Three layers each had a reason not to look: `validate_order` never reads
`time_in_force`; `reconcile_orders` polls only non-terminal orders, so a filled
parent's exit leg is invisible to it by construction; and nothing compares open
positions against live protective orders, so `AUDIT CLEAN` is clean by not
asking.

This is the same family as the two incidents before it: **the system asserted
something nobody compared to the source of truth.**

Two changes close it. The first stops a stop from being placed with a lifetime
shorter than the position. The second notices when a position ends up naked
anyway, for any reason, including reasons not yet imagined.

## Change 1 — `gtc` required on stop-carrying orders

In `gate/tickets.py:validate_order`, in the branch where the ticket carries a
`stop_price`, alongside the existing `order_class == 'oto'` check: deny unless
`time_in_force` is `gtc`.

```python
tif = _as_time_in_force(tool_input.get("time_in_force"))
if tif != "gtc":
    return False, (...)
```

`_as_time_in_force` follows the house coercion pattern of `_as_share_count` and
`_as_price`: a `str` is lowercased and returned; missing, `None`, bool, number,
or object returns `None` and therefore denies. Input is case-insensitive, the
comparison is exact. Every numeric the Alpaca MCP tool sends is a string, so
`time_in_force` is assumed to be one too.

The denial message states the constraint, not just the violation, as the
neighbouring branches do: a `day` stop leg dies at the close of the session it
was placed in, leaving the position unprotected overnight, so a stop must
outlive the day that created it.

The stopless path (`stop_price is None`) is untouched and stays
time-in-force-agnostic. Widening it would be a false deny on a legitimate plain
order.

**Nothing outside `validate_order` is touched.** No charter, no seat config, no
spec prose sets `time_in_force` today — the seat relies on the MCP tool's
default, which the captured fixture
(`tests/fixtures/alpaca/place_stock_order.json`) shows is `day`. The denial
message is therefore the whole teaching mechanism: the seat is denied once,
reads why, and retries with `gtc`.

## Change 2 — every open position has a live protective order

### Port

`BrokerPort` grows two read-only methods:

```python
def open_positions(self) -> list[dict]: ...
def open_orders(self) -> list[dict]: ...
```

Both **raise** on failure rather than returning `None` — the caller converts a
raise into an alert. This differs deliberately from
`get_order_by_client_order_id`, which swallows because its caller re-polls.
There is no re-poll here.

`BrokerPort`'s existing rule stands: this port never places an order and must
never grow a method that does (invariant 2).

`AlpacaSource` implements them over `get_all_positions()` and
`get_orders(GetOrdersRequest(status=OPEN))`. Verified against alpaca-py 0.44:
`Position` carries `symbol`, `qty`, `side`; `Order` carries `symbol`, `side`,
`qty`, `type`, `status`. `nested` defaults to false, so an OTO's stop leg comes
back as its own top-level row — which is exactly the row this check needs.

### The check

New module `orchestrator/protection.py`, one entry point:

```python
def assert_positions_protected(conn, *, broker, now_iso) -> int:
    """Alerts appended. Never raises."""
```

**Covered** means: taking every open order whose `symbol` matches the position,
whose `side` is the position's opposite, and whose `type` is in the stop family
(`stop`, `stop_limit`, `trailing_stop`), their quantities sum to at least the
position's quantity.

A sell *limit* is not protection — a take-profit does not cap a loss. A stop
covering fewer shares than are held is not protection for the remainder.

**It fails closed, everywhere.** Each of the following appends an alert; none of
them may pass quietly:

- `broker` is absent
- `open_positions()` raises
- `open_orders()` raises
- a position quantity that will not parse
- a position that is short, or whose side cannot be read
- an order whose quantity or type will not parse

Every one of these is a test, not a comment. A check that can pass while lying
is the exact failure of all three incidents.

**It is an assertion, not a stage.** No checkpoint, no CAS, no resumability. It
re-runs on a resumed day rather than being skipped as `done`, and duplicate
alerts are the safe direction.

### Where it runs

In `orchestrator/daily.py:run_day`, between the reconciliation and close stages,
as a plain call. It runs unconditionally — including on a zero-ticket full-HOLD
day, which is precisely the shape the NVDA incident had.

`run_day` drains explicitly after the call. On a normal day the close stage's
own drain would flush the alert, but on a resumed day where `close` is already
`done`, `run_stage` returns before draining and a clean audit does not drain
either — so the alert would sit in the outbox until the next run. An explicit
drain costs one line and guarantees same-day delivery.

## Decisions recorded

**If a position is unprotected, the day still trades.** Alert only; no ticket is
blocked. Blocking is more faithful to invariant 4, but one stale stop would halt
the whole fund, and halting does not protect the position that is already naked
— it adds a second incident. Ruled by Benjamin, 2026-08-19.

**A position whose originating ticket legitimately had no `stop_price` still
alerts, every day.** The alternative — suppressing the alert when
`tickets.stop_price` is NULL — puts the database in charge of interpreting the
broker's state, which is the precise pattern behind all three incidents. One
rule, hardcoded, reading only broker positions against broker orders, changeable
only by human commit like the gate thresholds.

Concretely: this alerts on the currently-held NVDA 80 on every run until that
position is resolved. That is the intent, not a side effect.

**A missing broker reads as unverifiable and alerts.** It occurs only in tests
today, but a `None` broker in production is a wiring bug that must scream rather
than skip the check. This adds an expected alert to existing `run_day` stage
tests that pass `broker=None`; updating those assertions records new intended
behavior and is not weakening a test.

## Broker confirmation

Change 1 is unverified against Alpaca and must not merge that way. Nothing in
this repo has ever sent `time_in_force` on an OTO market parent. If Alpaca
rejects `gtc` there, the gate denies `day` while the broker rejects `gtc`, and
every stopped entry becomes unplaceable — silently, exactly like 2026-08-17,
where the gate and the fixtures agreed with each other and both disagreed with
the broker.

`tests/test_live_smoke.py::test_a_stopped_ticket_places_with_a_flat_stop_leg` is
extended to assert that the placed order **and** its stop leg both come back
`time_in_force: gtc`. Live-marked, never in CI, run against paper before merge.

## Tests

**Gate (offline).** `day` denied · missing key denied · non-string denied ·
`GTC` accepted · `gtc` accepted · a stopless ticket with `day` still passes.

**Assertion (offline).** Covered → silent · no orders at all → alert · sell-limit
only → alert · stop quantity short of the position → alert · `open_positions`
raises → alert · `open_orders` raises → alert · unparseable quantity → alert ·
short position → alert · absent broker → alert.

**Sim-day.** `FakeAlpaca` grows positions and models the OTO child leg, plus a
mode where the leg expires at the bell — reproducing the incident, so
verification has a genuinely unprotected position to show the assertion firing
against. A green suite is not evidence here: the suite was green through all
three incidents.

## Out of scope

- **Auto-repair.** A missing stop alerts a human. Placing a replacement is order
  placement: it belongs to the gate and the exec seat, and invariant 4 resolves
  ambiguity to no action.
- **A new stage or checkpoint.** It needs no resumability.
- **Generalized reconciliation** of positions vs orders vs DB in all directions.
  That is a subsystem; this solves the stop hole.
- **A configurable protection policy.** One rule, hardcoded, human-commit-only.
- **`time_in_force` anywhere outside `validate_order`.**
- **Deciding the currently-held naked NVDA 80.** There is no code path for
  protecting an already-held position; that call is Benjamin's and is manual. No
  broker order is placed, canceled, or modified by this work.

## Size

Production diff estimate ~75 lines: `gate/tickets.py` 12, `orchestrator/broker.py`
2, `market/source_alpaca.py` 15, `orchestrator/protection.py` 45,
`orchestrator/daily.py` 3. If it grows past ~100, stop and re-read "Out of
scope".
