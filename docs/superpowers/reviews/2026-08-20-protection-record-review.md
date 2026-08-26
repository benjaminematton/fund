# Adversarial review — branch one spec and plan

**Date** 2026-08-20 · **Reviewed:** `specs/2026-08-20-protection-record-design.md`,
`plans/2026-08-20-protection-record.md` · **Reviewer:** fresh context, no access to the
authoring conversation.

**Verdict: the plan is NOT executable as written.** The design is sound; the plan is not. Three
of six tasks contain tests that cannot pass, one task's central interface lacks the data to do
its job, and one task's placement would abort a live trading day.

Findings marked **[verified]** were established by running code in a throwaway copy of the tree.

---

## Critical — must resolve before any code

**C1 — Task 6 asserts a property the schema cannot hold. [verified]**
`manual-protective-stop-nvda-2026-08-19` is the **`client_order_id`**; the `alpaca_order_id` is
`5abc139f-4817-4a34-aedd-f2ca28203c5c` (`PROGRESS.md:123-124`). Task 3's widened `open_orders()`
returns `id` and never `client_order_id`, so adoption has no path to that string — and the DDL
has no column for it. ADR-0004 insists the irregular id "carries the meaning" while giving it
nowhere to live. The only way to green this test is to make the fake return the human string as
`id`: *a fixture agreeing with our code while both disagree with Alpaca* — the 2026-08-17 defect
this document exists to prevent.

**C2 — Task 5's test cannot pass. [verified by execution]**
With no `orders`/`tickets` row seeded, `_promised_stop` returns `_UNKNOWN` and `_evaluate` takes
the `_UNKNOWN` branch. Task 5 only edits the `promised is not None` branch. `"40"` can never
appear. The plan even predicts the right failure text for the wrong branch.

**C3 — `record_live` cannot write a correct `provenance`. [verified]**
No resolution path from a broker order to a ticket exists: `open_orders()` carries no
`client_order_id`, and ADR-0004 itself records that a leg's `client_order_id` bears no relation
to the ticket id. So every row gets `'adopted'`, including fund-placed legs, and the
three-origin table collapses to one value. Task 6's provenance assertion becomes vacuous.

**C4 — Task 4 Step 5 takes the trading day down. [verified]**
`daily.py:77` calls `body()` with no try/except; `run_day` and `scripts/run_day.py` don't wrap
it either. A transient broker read failure in a new, non-essential write path aborts the day
**before** `assert_positions_protected` — the naked-position alert this branch descends from
would be the first casualty. The comparison to `protection.py` is backwards: that module catches
the raise itself. Two more placement defects: reconcile is a checkpointed stage, so a resumed
day records nothing; and `reconcile.py`'s `if not pending: return` fires first on any day with
no submitted orders — i.e. most days.

**C5 — nothing ever moves a row off `live`, and that breaks a CLAUDE.md invariant.
[verified/reasoned]**
No code path writes `cancelled`, `triggered` or `expired`. So `live_for()` returns permanently
stale rows, and the aggregate test passes trivially — it pins a property no code can violate.
Worse: CLAUDE.md requires every workflow table be a state machine applied through
`state/transition()`, with transitions in `specs/contracts.md` §1. The plan adds a 7-state
column with no `EDGES`/`KEYS` entry, no §1 machine, and no step touching either file.

## High

**H1 — `protection.py` is the obvious writer and neither document considered it. [verified]**
It already calls `open_orders()`, runs unconditionally every day, is *deliberately not a
checkpointed stage*, already handles `broker is None`, already catches broker raises and
converts them to alerts, already does the 3-second re-read a lagging OTO leg needs, and runs
after reconcile when legs are live. **Every C4 objection evaporates there, and the `reconcile.py`
contention gate disappears with it.**

**H2 — Task 3's "Expected: PASS" is false. [verified by execution]**
`AttributeError: '_Clock' object has no attribute 'id'`. The stub and its exact-dict assertion
must both widen — a contract change, not a weakened assertion, and the plan gives no carve-out
saying so.

**H3 — Task 1's predicted fallout does not exist. [verified by execution]**
Removing `"held"` leaves the suite fully green. `test_protection.py` doesn't import `FakeAlpaca`
at all. The prediction primes an implementer to "fix" a file where nothing is broken.

**H4 — both "watch it fail" steps fail for the wrong reason. [verified]**
`FakeAlpaca(prices)` — `prices` is required. Both snippets call `FakeAlpaca()`. The seams are
real once fixed, but red-for-the-wrong-reason defeats the TDD gate.

**H5 — dependency inversion and an import cycle. [verified]**
No module in `state/` imports `orchestrator/` today. Promoting `STOP_TYPES` in
`orchestrator/protection.py` and importing it from `state/protection.py` inverts the arrow and
creates a real cycle. The DRY instinct is right; put it in `state/`.

## Medium

- **M1** `record_live` parses *broker* output, so it should reuse `protection.py:_qty`, not the
  gate's coercers — whose docstring explicitly says the two may legitimately diverge.
- **M2** bare `uuid4()` departs from the repo's injectable `id_factory`, which exists so sim-day
  and replay stay deterministic.
- **M3** `provenance` holds both an id and an enum token, so it can't carry a CHECK or be
  grouped — while being justified by contracts §2's vocabulary rule. Wants
  `provenance_kind` + nullable `provenance_ref`.
- **M4** "idempotence comes free from UNIQUE" fails once branch two writes `pending` rows with a
  NULL `alpaca_order_id` — SQLite allows unlimited NULLs in a UNIQUE column.
- **M5** Task 2's test builds from the *new* schema and drops a table; it is not the droplet case
  its docstring claims.
- **M6** Task 5 renders table content into an alert at exactly the moment the table and broker
  disagree, unlabelled. The text must mark it as *the fund's belief, contradicted by the broker*.

## Low

L1 leg `stop_price` wiring is ambiguous · L2 no test that unreadable orders are skipped · L3 no
test that widening `open_orders()` leaves `_covering_qty` unchanged · L4 spec Test 6 is already
covered by three existing tests; say so · L5 undefined behavior for zero/multiple `live_for` rows
· L6 no index, and C5 makes the table grow monotonically · L7 one line-ref off by one.

## What the reviewer verified as correct

`daily.py:475`, `source_alpaca.py:114-138`, `fake_alpaca.py:212-220`, `migrations.py`'s docstring
claim, `db.py:connect()`'s `tickets` guard, `open_orders()` flattening via `nested=False`, and the
fake's docstring claiming to match the real one — all as stated.

## Recommended sequence before any code

1. **Resolve C1 and C3 inside the 🔏 contracts ruling** — both are schema questions, and that
   ruling already gates Task 2. Better than discovering them at Task 6.
2. **Decide C5**: either branch one ships status transitions (plus `EDGES`/`KEYS`/§1), or it ships
   a single `live` state and no CHECK list beyond it. Seven states with one writer and no
   transitions is the worst of both.
3. **Re-open H1.** If `protection.py` is the writer, C4 and the reconcile gate both disappear and
   the plan gets shorter.
4. **Fix the snippets**: H4, C2, H2, H3.
