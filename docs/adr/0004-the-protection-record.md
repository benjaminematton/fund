# The protection record: what protects a position is a fact the fund stores

Status: accepted (2026-08-20)

The fund models decisions, tickets and orders. It does not model a **position**, and it does
not model the **protection** on one. Both are re-derived by join from order history, by three
modules with three different queries. This ADR introduces one table — a record of each
protective order the fund knows about — and states the rule that keeps it from becoming the
2026-08-17 failure again.

It came out of designing the amend path in [ADR-0003](0003-reducing-a-stopped-position.md).
That design kept having nowhere to put things, and this is why.

## Three layers, and the confusion was collapsing them

- **Intent** — was a stop meant? Lives on the decision (`decisions.stop_price`). Unchanged.
- **Record** — what did we place or adopt, at what price, for how many shares? **This table.**
- **Existence** — what does the broker actually hold? Broker-read, always.

The bug class this repo keeps hitting is intent being read as existence. On 2026-08-17 the
database said "stop at 215" for two sessions while the broker held nothing, because intent was
the only layer that existed and it was doing the other two jobs.

**Standing rule, and it is the load-bearing one: this table must NEVER be the source of what
protection EXISTS.** `orchestrator/protection.py`'s `_covering_qty` keeps reading the broker.
The table records what the fund knows and intends and remains the thing compared *against*
broker truth. A protection table that feeds the coverage number recreates 08-17 with more
ceremony.

## Why intent could not hold the record

`tickets.stop_price` is a REAL. There is nowhere in it for a share count, so the fund's
"promise" is an entry-time price that no later event ever updates. `PROGRESS.md` records the
cost: the live NVDA stop exists because a human hand-placed it so the broker would "agree with
the source of truth," which "already asserted a stop at 215." Reality was reconciled to the
record by hand because the record had no way to describe reality.

## The shape

**Protection is aggregate** (a property of the position, realized by one or more stop orders),
not per-lot. Settled on broker fact rather than preference: `qty_available` is position-level,
the `Order` model carries no lot id, and `avg_entry_price` proves Alpaca averages entries into
one position. Per-lot would be a fiction nothing can confirm.

**One row per protective order**, carrying symbol, qty, stop price, provenance, a
`broker_expires_at`, and status. Content is immutable once written — qty, price and ids never
change; only status transitions.

**Identity and reference are separate columns.** The row's `id` is always fund-minted and is
the fund's handle for that protection. `alpaca_order_id` is the broker's reference, which every
order has including legs. The row id doubles as the order's `client_order_id` **only** when the
fund itself places the order — which is the amend case and nothing else. This is what makes the
three origins uniform instead of one-plus-exceptions:

| origin | row id | broker reference |
|---|---|---|
| amend replacement | fund-minted, used as `client_order_id` | the new order |
| OTO stop leg | fund-minted | **Alpaca-generated** — verified: leg `client_order_id` `910638b1-…` bears no relation to ticket `a14aa36b-…` |
| adopted (hand-placed) | fund-minted | the existing order |

A fund-minted id on an adopted row is a *record handle*, not fabricated provenance — which is
what "record and reference, never invent" requires in practice. The fund never invents a
decision or ticket for protection it did not authorize; `protection.py`'s `_UNKNOWN` sentinel is
the existing precedent for representing something without inventing its origin.

**States:** `live`, `superseded`, `cancelled`, `triggered`, `expired`, plus `pending` once amend
ships. There is deliberately **no `rejected`** — `_extract_order` returns `None` on a rejection
payload ("a rejection is never recorded", invariant 4), so a rejected stop produces no row at
all, and adding the state for symmetry would create rows the code cannot write.

**The aggregate is `status = 'live'`** — a positive predicate, never a `NOT IN` list, so a state
added later cannot silently join the aggregate by omission. That is pinned by a test rather than
a comment: it is the same missed-filter-corrupts-silently class that decided the ticket
question, and the failure is invisible.

`pending` is excluded for the same reason `held` never becomes a row: a stop that is authorized
but not yet effective protects nothing, and counting it would be the table asserting existence.

## Who writes it

**The reconcile pass writes rows for OTO-placed stops**, reading `open_orders()`. Not the
PostToolUse recorder — this reverses an earlier decision, on evidence:

- the captured place response has `"legs": null`;
- `tests/fake_alpaca.py` echoes the request's flat leg parameters rather than returning a legs array;
- `test_live_smoke.py` polls a GET up to ten times because *"on an oto the child is created held and can lag the parent in the API by a moment"*;
- measured at the broker: parent submitted `16:56:59.302479Z`, leg `16:57:00.723760Z` — **1.42 s later**.

The recorder sees only the immediate place response, so it would write a row for some
placements and silently miss others — worse than writing none. `open_orders()` returns legs
flattened as top-level orders, which is the path `protection.py` already reads.

**A held leg never becomes a row**, and the broker enforces this rather than our discipline:
`AlpacaSource.open_orders` queries `QueryOrderStatus.OPEN`, and a held OTO child is measurably
not returned by it (2026-08-19). No `held` state is needed.

> **Carried debt.** `tests/fake_alpaca.py`'s `open_orders()` filters on
> `("new", "accepted", "partially_filled", "held")` — it **includes held**, while claiming in its
> docstring to match `AlpacaSource.open_orders`. Latent today (a held leg means no filled parent,
> so no position exists to check) but active the moment reconcile writes rows: the offline suite
> would prove a behavior production does not have. That is the 2026-08-17 defect exactly, named
> in `test_live_smoke.py`'s own comment. **Fix the fake's filter in the same change.**

**The recorder writes on amend** — where the fund places the replacement itself and mints its
id — which is branch two, not branch one.

**Adoption gets its own entry point**: a hand-run, **idempotent** script under `scripts/`.
Not a migration, which would adopt whatever the broker happened to hold at deploy time — that is
provenance by accident. Idempotence is not optional: it is a hand-run script against a live
record, and re-running after a partial failure is the normal case.

Three constraints on adoption, from the sessions that own the pieces it touches:

- **An adopted row is an observation at a timestamp, not a standing promise.** The fund did not
  place the order and does not control it. Recording it as a promise would have
  `protection.py` pass on NVDA correctly today and wrongly the moment the stop is cancelled —
  which is the standing rule again, arrived at from the other direction. The row says *what was
  observed and when*, and the broker stays the authority on whether it still exists.
- **The adopted id is deliberately not a UUID.** `manual-protective-stop-nvda-2026-08-19` was
  chosen so the format itself marks an order a human placed outside the pipeline. Do not
  normalise it into a UUID column shape; the irregularity carries the meaning.
- **Adoption targets whatever protection exists when the script runs**, never a hardcoded order
  id. The NVDA stop is under active review for cancellation or resizing, and branch one is
  behind `/to-spec` and a 🔏 ruling — so the order adopted may not be the order that exists
  today. This does not change *whether* to adopt; it changes what the script may assume.

## Consequences

- **Invariant 5 is reworded**, because the OTO leg falsifies it. It currently reads
  "`client_order_id` = gate ticket id, always"; the leg carries an id the fund neither minted nor
  adopted. It becomes a statement about placement — *every order the fund submits carries an id
  the fund minted, and a retry always reuses it* — with the case list in `specs/contracts.md`
  describing this table's reference column. The invariant's real content was always idempotency;
  "= gate ticket id" was the mechanism that delivered it when there was one mechanism.
- **`orchestrator/protection.py` is untouched in its existing behavior.** `_promised_stop`'s
  tri-state stays exactly as it is — a price, `None` for a charter-sanctioned stopless buy, and
  `_UNKNOWN` for no record at all. The table cannot express the `None` case (there is no
  protective order for that row to describe), which is precisely why intent stays on the decision.
- **`broker_expires_at`** is named apart from `tickets.expires_at`, which means the gate's
  +45-minute window. Alpaca caps GTC near 90 days — the live NVDA stop dies 2026-11-17 and nothing
  watches for it. The column lands now; the alerting is separate work and must read the column
  rather than compute from a placement date. An amend starts a fresh window, so computing would be
  wrong as well as fragile.
- **If a state transition emits an event, its renderer lands in the same commit.** This is
  enforced, not conventional: `tests/test_slackkit.py` asserts every written kind has a
  `RENDERERS` entry, so a missing one is a red test. At runtime an unknown kind makes `render()`
  raise, the row dead-letters, and a `projection_error` reddens the audit — one step removed from
  the cause, which is the expensive kind of failure to debug. `specs/contracts.md` §8 also
  requires every kind carry populated `text`.
- **Nothing else in the schema is disturbed.** Enumerated at `41a48dd`: exactly one foreign key in
  the whole schema touches `tickets` or `orders` (`orders.client_order_id REFERENCES tickets(id)`),
  no index or CHECK references either, and every code reader of `orders` keys on
  `client_order_id` or joins through `tickets.id`. A separate table appears in none of them.

## Branch one

The table, the adoption script, the reconcile pass writing rows, the fake's filter fixed, and
`protection.py` reading the record layer. The existing NVDA stop is adopted as its first real
row — safe, because NVDA is fully covered today, so `_evaluate` short-circuits at
`covered >= held` and never reaches provenance. An adoption path with nothing adopted through it
is untested code.

Amend — the pending state, the gate's second verb, the recorder's write path — is branch two,
and is [ADR-0003](0003-reducing-a-stopped-position.md)'s subject.
