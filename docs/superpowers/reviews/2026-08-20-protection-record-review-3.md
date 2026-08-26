# Adversarial review 3 — branch one plan, revision 3

**Date** 2026-08-20 · Fresh context, ran Tasks 1–6 end-to-end in a throwaway tree against the real
suite (baseline 956 green).

**Verdict: not executable — but the *shape* is right for the first time.** Removing the status
column is a genuine fix, not a relocation, for 8 of the 9 findings it claims to vanish. What
breaks it is two defects revision 3 introduced, plus a documentation layer that now contradicts
the plan on every point the plan cites it for. **Distance to executable: ~4 edits, no redesign.**

## The shape holds — verified

- **C5, N9, N10, N11 genuinely vanish.** `tests/test_state.py:26` asserts `TABLES <= names`
  (subset), `STATUSES` lists 4 tables, `contracts.md` §1 names 4 machines, and five existing
  tables have no status column. **The plan's CLAUDE.md argument is correct**: a status-less table
  in §2 with no §1 entry is the house pattern, not an exception.
- **N1 verified vanished** — the wired path renders the record clause.
- **N3, N5, N15, N17 verified vanished.** Skipping an unreadable order now costs one observation
  and can corrupt nothing.
- **Volume is fine** (a handful of rows/day at 1–3 positions).

One consequence the plan does not acknowledge: because `observed_at_run` scopes to the run that
just wrote rows from the list `_evaluate` already holds, **the record layer is informationally
identical to the existence layer for its only consumer.** Safe, but Task 5 ships a *restatement*,
not a comparison — and its wording claims otherwise (H1).

## Critical

**C1 — Task 4 Step 5 breaks 30 existing tests; Step 6's "Expected: PASS" is false. [verified]**
`assert_positions_protected` has **31 call sites in `tests/test_protection.py`**, none passing
`id_factory`. Making it required keyword-only with no default →
`TypeError … missing 1 required keyword-only argument`, **30 failed**. Task 4's file list omits
that test file. Patching all 31 call sites turns the suite green, so the fix is mechanical — but
an implementer meets a red suite with no instruction, in a repo whose CLAUDE.md says *"a failing
test means the implementation is wrong."* Review 2's N6 was fixed into a harder blocker.

**C2 — the nap re-read discards the *fresher* observation. [verified]**
`INSERT OR IGNORE` with the same `now_iso` is a no-op, and what it drops is the second read. With
a stop reading 40 pre-nap and 70 post-nap, the alert says *"the broker covers only 70 of 80 …
the fund's record for this run: 40 @ 215 observed"* — one sentence, two numbers, three seconds
apart, presented as simultaneous. The plan's own constraint (*"no consumer may read a row from an
earlier run as a statement about now"*) is honored across runs and violated inside one.

## High

**H1 — the proposed wording is false by construction. [verified]** *"…which the broker does not
confirm"* — the observation came from this run's broker read; the broker did confirm it, in the
same sentence. Leftover reasoning from revision 2, where the record could predate the read.

**H2 — the honesty test cannot see the constraint idempotence depends on. [verified]**
`PRAGMA table_info` exposes name/type/notnull/default and nothing else. The reviewer corrupted the
migration copy — added a value to the `provenance_kind` CHECK and **deleted
`UNIQUE (alpaca_order_id, observed_at)`** — and the test still passed. The droplet could receive a
table with no UNIQUE, which is the sole mechanism behind `INSERT OR IGNORE`; every re-read would
double-log with the suite green. N8 unfixed.

**H3 — ADR-0004 and the spec now contradict the plan. [verified]** The plan tells the implementer
to write `-- Deliberately NO status column — see ADR-0004` into `schema.sql`, while ADR-0004
(marked **accepted**) says branch one ships `live`/`closed`, needs an `EDGES` entry and a §1
machine, aggregates on `status = 'live'`, uses `ticket|oto_leg|adopted`, and still names the
reconcile pass as the writer. The spec's amendment block says *"the plan is revision 2 and is
correct."* **Revision 3 changed shape without amending either — the exact failure that amendment
block was created to fix.**

**H4 — the write outside the try aborts the day. [verified]** A SQLite failure propagates through
`daily.py:484`, which has no try/except: zero alerts written, `run_close` never runs. And the
justification is unreachable — **`state/db.py:34` calls `migrations.apply()` on every
`connect()`**, so a stale DB gains the table on reopen. What remains reachable (`database is
locked`, disk full) now takes the day down. Revision 2 swallowed the write and lost the alert;
revision 3 crashes and loses the alert *and* the close stage.

## Medium

**M1** `stop_price NOT NULL` excludes `trailing_stop`, which `_STOP_TYPES` counts and
`test_stop_limit_and_trailing_stop_both_count` pins — the log silently under-reports against the
number in the same alert · **M2** `iso` is not imported in `market/source_alpaca.py`; the snippet
does not compile · **M3** the `_Clock` stub's `expires_at` must be a **tz-aware datetime**, not a
string, or `iso()` raises · **M4** Task 5's snippets reference four names that file does not have ·
**M5** `make sim-day` writes **zero** protection rows — the headline verification cannot observe
the feature, and no test proves the *wired* path · **M6** `_CLOSING_SIDE` is not moved with
`STOP_TYPES`/`_qty`, so the predicates can still drift · **M7** N2 relocated: `adopted` is carried
by nothing but which script wrote the row, and no consumer filters on it — both Task 6 tests are
green and vacuous.

## Low

**L1** "eight keys" is nine · **L2** `protection.id` shares `ctx.id_factory` with tickets;
`test_sim_day.py:216`'s strict `_id_sequence` would `StopIteration` on a future sim with a live
leg · **L3** no index, never dispositioned in writing · **L4** the fake hardcodes the real NVDA
expiry · **L5** `iso()` raises where `_enum_str` could not, turning one odd value into a day-long
`UNVERIFIED`.

## Tasks 1–3, re-verified

Task 1 ✅ (assertion-fails then suite-green). Task 2 ✅ but H2. Task 3 ✅ — `KeyError: 'id'` and the
single `AttributeError` land exactly as predicted, `iso()` produces the `T` form, and alpaca-py
0.44's `Order` exposes all four fields. Gaps M2, M3.

## Minimum to executable

1. `id_factory` default, **or** a step updating all 31 call sites (C1).
2. UPSERT on re-observation, or log only after the final read (C2).
3. Rewrite the clause as what it is — a per-order breakdown — and drop the false contrast (H1).
4. Compare `sqlite_master.sql` + `PRAGMA index_list`, not `table_info` (H2).
5. Wrap the write in its own try whose failure becomes an alert (H4).
6. Amend ADR-0004 and the spec (H3).

## The process finding

> Three failed reviews on one artifact is itself the finding, but the trend is real: revision 1
> was wrong on design, revision 2 on mechanism, revision 3 only on wiring and wording. The
> recurring pattern is narrower than "the author can't self-review": **every revision fixes the
> previous review's finding by inverting it and stops there** — status → sweep → no status; write
> inside the try → write outside the try; `adopted` → `oto_leg` → `observed`. Each inversion is
> correct in the direction it moves and unexamined at its new endpoint. A fourth revision should
> be checked specifically at the endpoints of the three inversions it makes, which is where C1, C2
> and H4 all live.
