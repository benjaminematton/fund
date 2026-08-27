# Adversarial review 2 — branch one plan, revision 2

**Date** 2026-08-20 · **Reviewer:** fresh context, no access to the authoring conversation.
Ran the plan's snippets and a faithful prototype of Tasks 1–5 in a throwaway tree.

**Verdict: revision 2 is not executable either.** Tasks 1–3 are genuinely fixed and were verified
by execution. **Task 4 is where it breaks**, and the failures are caused by the fix to the first
review's C5 — the `closed` sweep — not by anything that survived from revision 1.

## First review's findings — disposition

Fixed and verified: **C1** (client id column), **C2** (Task 5's test), **C4/H1** (the abort),
**H2**, **H3**, **H4**, **H5** (though the stated *reason* for H5 was wrong — the real cycle is
`orchestrator/protection.py` ↔ `state/protection.py`).
Partially fixed: **C3**, **C5**. See N2 and N3/N5.

## Critical — all verified by execution

**N1 — Task 4's sweep and Task 5's reader cancel each other.** `_evaluate`'s shortfall branch is
reached *precisely* when the broker holds no covering order. The sweep, three lines earlier, has
already closed that row. Ran end-to-end: the alert renders with **no record clause** and the row
reads `closed`. Task 5's test passes only because it calls `_evaluate` directly and bypasses Task
4. **The feature Task 5 ships is dead code in production.**

**N2 — every branch-one row lands as `oto_leg`, including the hand-placed NVDA stop, and that
kills Task 6.** `sync_live` runs on every order in the live list unconditionally, so
`5abc139f-…` / `manual-protective-stop-nvda-2026-08-19` is written as `oto_leg`. `alpaca_order_id`
is UNIQUE, so Task 6's `adopted` row can never be inserted — confirmed `IntegrityError`. C3's
objection survives, pointed at a different value, and now a value that is **false** rather than
merely uninformative. The plan's own argument against `'ticket'` — *"claiming it would be a
fabrication"* — applies verbatim to `'oto_leg'`.

ADR-0004's safety argument for Q6 also fails: *"safe, because NVDA is fully covered today, so
`_evaluate` short-circuits and never reaches provenance."* The write happens **before** the
coverage check, so a fully-covered position still gets a row.

**N3 — one unreadable read permanently closes a live row, with no way back.** Skipping an
unreadable order removes it from the seen set, so the sweep closes it.
`EDGES = {("live","closed")}` has no reverse edge; `try_transition(…, "closed", "live", …)` raises.
**Invariant 4 violated inside the record layer**: an ambiguity resolves to a positive claim that
protection is gone, permanently. Fires identically on the `SlowLeg` lag the module's 3-second nap
exists for.

## High — all verified

**N4 — putting the write inside the read `try` swallows SQLite write failures**, and
`assert_positions_protected`'s own docstring forbids exactly this: *"A SQLite write failure DOES
propagate, and deliberately… Swallowing a failed write here would mean the alert this module
exists to raise was silently never recorded."* Dropped the table and ran it: the run emits
`UNVERIFIED — could not read live orders`, which is **false** (the read succeeded), and skips the
naked-position evaluation for every position that day. A migration that didn't reach the droplet
silently disables the check this whole branch descends from.

**N5 — the `closed` writer never runs when closure matters.** `broker is None` and `not positions`
both return *before* `read_orders()`. When a stop triggers, fills and closes the position, the next
run sees no positions and the row stays `live` forever.

**N6 — `id_factory` is never threaded to the call site.** `assert_positions_protected`'s signature
has none and `daily.py:484-485` doesn't pass one, while `ctx.id_factory` sits right there. Task 4's
file list omits `orchestrator/daily.py`.

**N7 — `broker_expires_at` gets a format nothing else in the repo uses.** `Order.expires_at` is a
`datetime`, so `_enum_str` yields `'2026-11-17 21:00:00+00:00'` — space, no `T`. Every other
timestamp uses `clock.iso()`. The suite stays green while the one column the November expiry watch
depends on can't compare or sort. Use `iso()`, and give the fake a value so it's exercised.

## Medium

**N8** DDL duplicated verbatim between `schema.sql` and `migrations.py` with nothing comparing them
· **N9** `protection` not added to `test_state.py`'s `TABLES`/`STATUSES`, so `test_every_non_edge_raises`
skips it · **N10** no `closed_at`; *when* a row closed is unrecorded, which is the first thing branch
two needs · **N11** the aggregate test can't express its stated purpose under a two-state CHECK ·
**N12** `_seed_filled_buy` already exists as `_promised` at `test_protection.py:48` · **N13** the
contention-gate dismissal cites an unverifiable claim, and is self-defeating as written · **N14**
ADR-0004's "Branch one" section still says reconcile, contradicting its own amended section ·
**N15** "first read" vs "re-read runs harmlessly" are different placements, and the choice is
load-bearing for N3 · **N16** unresolved whether `_qty` moves · **N17** the sweep's unwritten
precondition is "this list is complete" · **N18** the fake has no expiry concept, so
`broker_expires_at` has no honest test.

## The reviewer's recommended path

> Task 4 needs a redesign of the close rule before it is worth writing: closing on
> absence-from-a-single-read is what produces N1, N3 and N5, and no amount of test-fixing repairs
> it. … I'd take (a): branch one ships `live` only and accepts staleness **explicitly** rather than
> by accident. It makes N1, N3 and N5 vanish, leaves N2 as the only Task 4 blocker, and matches the
> branch's own standing rule that the table never sources what exists.

Tasks 1–3 are executable today once N7's `iso()` and N18's fake value are fixed.
