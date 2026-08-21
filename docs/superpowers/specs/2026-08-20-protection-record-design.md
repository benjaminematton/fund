# The protection record — branch one

**Date** 2026-08-20 · **Branch** to be cut off `master` (`41a48dd` or later) · **ADRs**
[0004](../../adr/0004-the-protection-record.md) (the model), [0003](../../adr/0003-reducing-a-stopped-position.md) (the amend path this enables)

> ## ⚠️ Amended 2026-08-20 — read this before anything below
>
> Adversarial review ([findings](../reviews/2026-08-20-protection-record-review.md)) established
> by execution that four things in this spec were wrong. The
> [plan](../plans/2026-08-20-protection-record.md) is now **revision 4**; where the two disagree,
> **the plan wins**. Three further reviews followed, and two more things below are wrong:
>
> - **There is no status column and no state machine.** The `live`/`closed` design in item 2 was
>   itself withdrawn — its sweep raced the alert reader, permanently closed rows on one unreadable
>   read, and never fired when a stop actually triggered. The table is an append-only observation
>   log.
> - **There is no migration.** `state/db.py` now parses `_TABLES` from `schema.sql`, so a table
>   added there reaches an existing database on the next `connect()`. This spec's headline
>   finding was true at `41a48dd` and is false at `894e1b8`.
>
> 1. **The writer is `orchestrator/protection.py`, not the reconcile pass.** Neither this spec nor
>    ADR-0004 considered the module that already reads `open_orders()`. The reconcile placement
>    would have **aborted the trading day** on a transient broker read failure (`daily.py` wraps
>    stage bodies in no try/except), recorded nothing on a resumed day (reconcile is
>    checkpointed), and never run at all on a day with no submitted orders.
> 2. **Two states, not seven.** No branch-one writer could produce `cancelled`, `triggered`,
>    `expired`, `superseded` or `pending`, so rows would stay `live` forever after their stop
>    died — the table asserting protection that is gone. Now `live` and `closed`, with the
>    writing pass closing rows whose order has left the live list. The transition needs an
>    `EDGES`/`KEYS` entry and a `contracts.md` §1 machine, which this spec omitted in breach of
>    CLAUDE.md.
> 3. **`client_order_id` is its own column.** `manual-protective-stop-nvda-2026-08-19` is a
>    *client* id; the `alpaca_order_id` is `5abc139f-…`. This spec asserted the human string as
>    the broker reference — a property the schema could not hold, satisfiable only by making the
>    fake disagree with Alpaca.
> 4. **`provenance` splits into `provenance_kind` + `provenance_ref`.** A single column mixing an
>    id with an enum token can carry no CHECK and cannot be grouped. And there is no path from a
>    broker order back to a ticket, so every row would have been `'adopted'` — including
>    fund-placed legs.

## The problem

The fund models decisions, tickets and orders. It has no record of **what protects a
position** — that is re-derived by join, differently, in three modules. `tickets.stop_price` is
a REAL with nowhere to put a share count, so the fund's "promise" is an entry-time price no
later event updates. The live NVDA stop exists because a human hand-placed it to make the broker
agree with a record that could not describe reality.

This branch adds that record. It ships no amend capability — that is branch two.

**The rule everything here serves: this table is never the source of what protection EXISTS.**
`_covering_qty` keeps reading the broker. The table records what the fund knows and intends, and
stays the thing compared *against* broker truth. A table that feeds the coverage number
recreates 2026-08-17 with more ceremony.

## Change 1 — migration `0002`, and `migrations.py` learns to run statements

**This is the change most likely to be skipped, and skipping it ships nothing to production.**

`state/db.py:connect()` executes `schema.sql` only when `tickets` is absent — i.e. on a database
this code has never created. `state/migrations.py` says so in its own docstring. So **adding
`CREATE TABLE protection` to `schema.sql` creates it in every test and never on the droplet.**
The offline suite would be green against a table production does not have: the same shape as the
2026-08-17 defect, where fixtures agreed with the code and both disagreed with reality.

`migrations.py` today is hardcoded to `(table, column)` pairs and emits `ALTER TABLE … ADD
COLUMN`. It cannot express a `CREATE TABLE`. It grows a second migration that runs statements,
keeping the existing properties: **additive, idempotent, never drops or rewrites**. `0001`'s
shape is not disturbed.

Idempotence for `0002` is `CREATE TABLE IF NOT EXISTS`, plus the same "already applied → return
`[]`" reporting `0001` uses.

## Change 2 — the table

```sql
CREATE TABLE protection (
  id                TEXT PRIMARY KEY,          -- fund-minted, always
  symbol            TEXT NOT NULL,
  qty               INTEGER NOT NULL CHECK (qty > 0),
  stop_price        REAL NOT NULL CHECK (stop_price > 0),
  alpaca_order_id   TEXT UNIQUE,               -- the broker's reference
  provenance        TEXT NOT NULL,             -- a ticket id, or 'adopted'
  broker_expires_at TEXT,                      -- Alpaca's ~90-day GTC cap; NULL if unreadable
  observed_at       TEXT NOT NULL,             -- when the fund saw or placed this
  status            TEXT NOT NULL DEFAULT 'live'
                    CHECK (status IN ('live','superseded','cancelled',
                                      'triggered','expired','pending','lapsed')),
  created_at        TEXT NOT NULL
);
```

Three things about this are deliberate.

**`id` is fund-minted and is the fund's handle, always.** It doubles as the order's
`client_order_id` only when the fund itself places the order — the amend case, which is branch
two. Branch one never writes a row whose id is a `client_order_id`.

**The CHECK lists every state including the three branch one never writes** (`superseded`,
`pending`, `lapsed`). SQLite cannot alter a CHECK constraint without rebuilding the table, so
omitting them now buys a table rebuild later. This is the one place the spec deliberately writes
ahead of the branch.

**`observed_at`, not `promised_at`.** An adopted row records that the fund *observed* a
protective order at a moment, not that it promises one. The fund did not place it and does not
control it (ADR-0004; `fund-07`, which placed the NVDA stop, asked for exactly this).

`broker_expires_at` is named apart from `tickets.expires_at`, which means the gate's +45-minute
window. These are unrelated quantities and a shared name invites the wrong inference.

## Change 3 — the reconcile pass writes rows

`orchestrator/reconcile.py` gains a pass that reads `open_orders()` and writes a `protection` row
for each live protective order it does not already have. Keyed on `alpaca_order_id` (UNIQUE), so
re-running writes nothing new.

**Not the PostToolUse recorder.** The recorder sees only the immediate place response, and an OTO
leg is not reliably in it — the captured fixture has `"legs": null`, `test_live_smoke.py` polls a
GET ten times because *"the child is created held and can lag the parent"*, and the broker was
measured at a **1.42 s** gap between parent and leg submission. A recorder-written row would land
for some placements and silently miss others, which is worse than none.

`open_orders()` returns legs **flattened** as top-level orders, which is the path
`protection.py` already reads.

**Held legs never become rows, and the broker enforces it**: `AlpacaSource.open_orders` queries
`QueryOrderStatus.OPEN`, and a held OTO child is measurably not returned by it (2026-08-19). No
`held` state exists, and none is needed.

> ⚠️ **Contested region.** `orchestrator/reconcile.py` is also named by `fund-b1` for issue #5
> (detect broker orders with no `orders` row). They may be the same observation serving two
> purposes — an OTO leg has no `orders` row *by construction* — or they may duplicate. Sequence
> this deliberately before starting; do not let whoever lands first define it.

## Change 4 — the fake stops lying about `held`

`tests/fake_alpaca.py:open_orders()` filters on `("new", "accepted", "partially_filled",
"held")`. It **includes held**; the real `AlpacaSource.open_orders` excludes held children. Its
docstring claims to match.

Latent today — a held leg means no filled parent, so no position exists to check. **Change 3
makes it active**: offline, the pass would write rows for held legs; in production it never
would. The suite would prove a behavior production does not have.

This is the failure `test_live_smoke.py` names three lines from the measurement it relies on:
*"the fake picks the leg's status itself, a fixture agreeing with our code while both may
disagree with Alpaca, which is the 2026-08-17 defect exactly."*

**Fix it in this branch, in the same change as Change 3.** It is currently live and unowned.

## Change 5 — `protection.py` reads the record layer

The three layers stay separate:

| layer | owner | changed here |
|---|---|---|
| **intent** — was a stop meant? | `decisions.stop_price` → `tickets.stop_price` | **no** |
| **record** — what was placed or adopted, at what price, for how many shares | `protection` | new |
| **existence** — what the broker holds | `_covering_qty`, broker-read | **no** |

`_promised_stop`'s tri-state is **deliberate and untouched**: a price, `None` for a
charter-sanctioned stopless buy, `_UNKNOWN` for no record at all. The table cannot express the
`None` case — there is no protective order for that row to describe — which is exactly why intent
stays on the decision. The record layer supplies what is actually protecting the position and
where it came from, in the alert text and nowhere else.

## Change 6 — the adoption script

A hand-run script under `scripts/`, **idempotent**: running it twice must not produce a second
row for the same broker order.

Not a migration — that would adopt whatever the broker happened to hold at deploy time, which is
provenance by accident. Not interactive — more machinery than one stop justifies.

- It adopts **whatever protection exists when it runs**, never a hardcoded order id.
- It records `provenance = 'adopted'` and never invents a decision or ticket. `protection.py`'s
  `_UNKNOWN` sentinel is the house precedent for representing something without inventing its
  origin.
- **The adopted id is not normalised.** `manual-protective-stop-nvda-2026-08-19` is deliberately
  not a UUID; the irregularity is the marker that a human placed it outside the pipeline.

> ⚠️ **Do not run it against the live NVDA stop until `fund-07`'s review concludes.** That order
> is under active review for cancellation or resizing. Nothing in this branch touches the order
> itself — record only, no broker mutation — but what gets adopted depends on that outcome.

## Tests

Red first, per `specs/acceptance.md`.

1. **Migration `0002` creates the table on a database that predates it** — the droplet case.
   Build a DB at the old schema, run `apply()`, assert the table exists. Then assert `apply()`
   twice reports the migration once and leaves one table.
2. **The aggregate counts `status = 'live'` and nothing else.** A positive predicate, never a
   `NOT IN` list, so a state added later cannot silently join it. Seed one row of every status
   and assert the aggregate sees exactly the live one. *(Benjamin's Q10 requirement: pin it with a
   test, not a comment — it is the missed-filter-corrupts-silently class and the failure is
   invisible.)*
3. **The fake matches the real contract on `held`** — a test that fails against today's fake.
4. **Reconcile writes one row per live protective order, and re-running writes none.**
5. **Adoption is idempotent** — twice, one row.
6. **`_promised_stop`'s tri-state is unchanged** — a regression pin, since Change 5 edits its
   caller.

## Out of scope

- **The amend capability entirely** — the gate's second verb, the `pending` state, the recorder's
  write path, `replace_order_by_id`. Branch two, ADR-0003.
- **Alerting on `broker_expires_at`.** The column lands; the watch does not. The live NVDA stop
  dies 2026-11-17 and nothing watches for it — that stays true after this branch. When it is
  built it must **read the column**, never compute from a placement date: an amend starts a fresh
  window.
- **`_promised_stop`'s multi-buy semantics.** A position built from several buys at different
  stop prices has no single promised price; it takes most-recent, defended in its docstring. Real,
  narrow, and not this branch.
- **The four ungated mutation verbs** (`cancel_order_by_id`, `cancel_all_orders`,
  `close_position`, `close_all_positions`). Separate exposure, separate owner.

## Constraints carried in

- **`specs/contracts.md` §8** — if a state transition emits an event, its renderer lands in the
  **same commit**. Enforced, not conventional: `tests/test_slackkit.py` asserts every written kind
  has a `RENDERERS` entry, so a missing one is a red test. At runtime an unknown kind dead-letters
  and reddens the audit one step from the cause. Every kind must carry populated `text`.
- **`specs/contracts.md` §2** — `NOT NULL` on vocabulary columns is load-bearing; a NULL drops
  silently out of `GROUP BY` and every `=`. Narrowing in a new table is fine; admitting NULL is
  not.
- **`specs/contracts.md` is contested** with `research/improvement-loops.md` §5, which adds
  columns to `signals`/`decisions`. Different sections, so a conflict is likely mechanical — but
  nobody should bank on that unlooked-at.
- **Invariant 5's rewording** (scope it to placement; drop "or adopted") lands with branch two,
  not here. Branch one writes no `client_order_id`, so rewording it now would document unbuilt
  design.

## Size

Six changes, one new table, one new script, six test seams. The migration and the fake fix are
small and independent; Change 3 is the substantive one and is entangled with a contested file.
Estimate a day, dominated by Change 3 and by sequencing against issue #5 — not by the table.
