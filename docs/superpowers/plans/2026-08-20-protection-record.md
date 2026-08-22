# The Protection Record (branch one) Implementation Plan

> # ⛔ READ THIS BOX BEFORE EXECUTING
>
> **Revision 5 — 2026-08-21. Scope cut 6 tasks → 4. Base current and re-measured. Task 1 built.**
>
> **Revisions 1–4 were each found NOT EXECUTABLE.** 1 wrong on design, 2 on mechanism, 3 on wiring
> and wording — each by a fresh-context reviewer, records in `../reviews/`. **Revision 4 was found
> non-executable too, by the session that executed it**, and it failed in precisely the way its own
> warning predicted: review 3's standing finding is that every revision fixes the previous critique
> by inverting it and never checks the new endpoint. Revision 4 applied its three inversions to the
> **prose** and left the **tests** at revision 3:
>
> - **`id_factory`** — declared absent in three places; then passed in fifteen test lines, with
>   Task 4 Step 3 instructing "Ids from the injected `id_factory`". One test asserted
>   `log_observed(...) == ["p1"]`, which is only true for a minted id. (Line numbers deliberately
>   omitted: they were revision 4's, and this document has since moved. Read `432c6a7~1` for them.)
> - **The query** — Interfaces declared `last_observed_before` with strictly-before semantics; every
>   test called `observed_at_run`, a name defined nowhere, asserting this-run semantics.
> - **Task 5** — preamble retitled "Why this reads the PAST, not this run" above a heading, two test
>   names and two test bodies that all still read this run.
>
> Four for four. **The lesson is not "review harder" — it is that a revision which edits rationale
> without editing the tests underneath it has not been revised.** Check the tests, not the prose.
>
> ## What revision 5 changes
>
> **Tasks 5 and 6 are cut** (Benjamin, 2026-08-21, in-session). They are recorded under *Not in this
> plan*. Two of the three contradictions above die with them — the query existed only to serve
> Task 5 — leaving the `id_factory` fix, applied below.
>
> **Consequence, stated plainly because it is the honest cost of the cut: the table ships
> WRITE-ONLY.** Task 5 was branch one's only reader. Nothing in this branch consumes a row.
> The justification for the table is branch two; branch one lands the record ahead of its use.
> Benjamin was told this and accepted it. Do not "fix" it by inventing a consumer.
>
> **Task 6's subject no longer exists — confirmed at the broker, not reported.** The task adopted
> the hand-placed NVDA stop at 80 shares. `manual-protective-stop-nvda-2026-08-20-40` **filled 40
> @ 214.85 at 2026-08-21T14:24:35Z**; the 80-share leg reads `replaced`, filled 0; NVDA is 40 shares
> with **zero open orders** (verified independently by `fund-8b`). There is nothing to adopt.
>
> ## The base — level with master is not the same as verified against it
>
> **Keep this distinction even though the numbers below are now green.** The commit count stops
> showing the gap the moment it reaches zero, so "0 behind master" is not evidence any claim was
> re-checked. Revision 4 was verified against `894e1b8` and master then ran ~32 commits past it.
>
> **What was actually measured**, in the worktree at `a78e0c5` after rebasing onto `4420e38`:
>
> | Claim | Result |
> |---|---|
> | `state/db.py:12` regex is `CREATE TABLE IF NOT EXISTS (\w+)` | TRUE — the load-bearing one |
> | `orchestrator/protection.py` is 421 lines | TRUE, exactly |
> | `daily.py:490` / `:492` call both assertions | TRUE, exact lines |
> | `source_alpaca.open_orders` returns five keys | TRUE |
> | `state/` imports nothing from `orchestrator/` (review 1, H5) | TRUE |
> | Suite at the base | 1133 passed, 1 skipped, 7 deselected |
>
> **Three line numbers had drifted and are corrected in place below**: `fake_alpaca.py` 212-220 →
> **239-247**, `_promised` 48 → **49**, `source_alpaca` 114-138 → **115-139**. Those three are why
> the distinction earns its place in this box: the branch was level with master and three claims
> were still wrong.
>
> **Task 1 is committed** — `432c6a7`. Red asserted first, failed on the assertion rather than a
> `TypeError`, then green. Suite **1134 passed, 1 skipped, 7 deselected**.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fund a record of what it has observed protecting each position.

**Revision 5 narrows this, and the original wording was aspirational for branch one.** It read
"…so protection stops being re-derived by join from order history." That is branch two's goal.
Branch one **writes the record and changes no existing derivation**: `_covering_qty` still reads the
broker, `assert_positions_protected` still compares against `decisions.stop_price`, and no consumer
is added or moved. The deliverable here is a populated, trustworthy log — nothing more.

**Architecture:** One new SQLite table, `protection` — an **append-only observation log**, not a state machine. `orchestrator/protection.py` appends one row per protective order it sees, from the live-order list it already reads. Three layers stay separate: **intent** on the decision (unchanged), **record** in this log (new), **existence** at the broker (unchanged).

**Tech Stack:** Python 3.12, SQLite, pytest. No new dependencies.

> ## Revision 4 — rebased onto current master, and two fixes that dissolved
>
> **The base moved 45 commits while this was being written.** Revisions 1–3 and all three
> reviews were verified against `41a48dd`; the branch is now on `894e1b8`. Two consequences:
>
> - **Task 2's migration is obsolete.** `state/db.py` now parses `_TABLES` out of `schema.sql`
>   and re-runs the script whenever a listed table is missing, so a new table reaches an existing
>   database — the droplet included — with no migration. The task collapses to one DDL block, and
>   review 3's H2 (the schema/migration drift test) vanishes with the duplication it tested.
>   **The DDL must use `CREATE TABLE IF NOT EXISTS`** — that exact string is what the regex
>   matches, and a bare `CREATE TABLE` would create the table on fresh databases and never
>   register it, reintroducing the very failure this task existed to prevent.
> - **`orchestrator/protection.py` grew from 251 to 421 lines.** It now carries a second
>   assertion, `assert_positions_accounted`, and `daily.py:490-492` calls both.
>
> Applying review 3's process finding — *every revision inverts the last finding and stops at its
> new endpoint* — two fixes were checked at the endpoint and **dissolved instead of flipping**:
>
> - **C1 (`id_factory` breaks 30 tests):** the row's natural key is
>   `(alpaca_order_id, observed_at)`. Derive the id from it. No signature change, no call-site
>   churn, and review 3's L2 (`ctx.id_factory` shared with tickets → `StopIteration` in a future
>   sim day) goes too.
> - **C2 (the nap re-read logs stale content) and H1 (the wording is false):** both came from
>   Task 5 reading *this run's* log, which review 3 showed is a round-trip of the same broker read.
>   Task 5 now reads the **most recent observation strictly before this run** — information the
>   current read cannot produce. This run's write becomes irrelevant to this run's alert, so C2
>   has nothing to corrupt and H1's contrast becomes true.
>
> Also fixed: **H4** (the write is now after the alert is computed, in its own try — it cannot
> affect the day), **M1** (`stop_price` nullable, because `trailing_stop` has none and
> `_STOP_TYPES` counts it), **M6** (`_CLOSING_SIDE` moves with `STOP_TYPES` and `_qty`), ~~**M7**
> (`provenance_kind` now has a consumer)~~. **H3** is fixed in ADR-0004 and the spec, not here.
>
> **M7 is struck by revision 5.** Cutting Task 6 removes the only writer of `'adopted'` and cutting
> Task 5 removes every reader, so `provenance_kind` has **no consumer** in branch one after all.
> The column stays — see *Not in this plan* — but do not cite M7 as a reason it earns its place.
>
> ## Revision 3 — a different shape, not another patch
>
> Two adversarial reviews ([one](../reviews/2026-08-20-protection-record-review.md),
> [two](../reviews/2026-08-20-protection-record-review-2.md)) both found the plan not executable,
> and review 2 found that revision 2's *fixes* introduced sharper problems than the bugs they
> solved. That is the signal to change approach rather than patch again.
>
> **The diagnosis: every serious finding traced to the table trying to be *current*.** A `status`
> column, a close sweep, staleness. C5, N1, N3, N5, N9, N10 and N11 are all that one decision.
> It was always in tension with the table's own standing rule — *never the source of what
> protection exists*.
>
> **Revision 3 removes the status column entirely.** The table becomes an append-only log: one row
> per protective order per observation, stamped `observed_at`. Nothing closes, so nothing races,
> nothing strands, and no ambiguity can resolve into a claim that protection is gone.
>
> This is a house pattern, not an exception: `tests/test_state.py` lists nine tables in `TABLES`
> and only four in `STATUSES`. `events`, `signals`, `resolutions` and `costs` have no state
> machine either. CLAUDE.md's "every workflow table is a state machine" governs workflow tables;
> a log is not one, which is why `events` has no status and needs none.
>
> Findings that **vanish** rather than being fixed: C5, N1, N3, N5, N9, N10, N11, N15, N17.
> Findings fixed directly: N2, N4, N6, N7, N8, N12, N13, N14, N16, N18.

## Global Constraints

- **The table must NEVER source what protection EXISTS.** `_covering_qty` keeps reading the broker.
- **A row is an observation, never an assertion of currency.** `observed_at` is what makes it honest. No consumer may read a row from an earlier run as a statement about now.
- **`gate/`, `stratgate/`, `calibration/` import no LLM code.** This branch touches none of them.
- **Never weaken a red test or re-record a fixture to make something pass.** One task legitimately widens a test's *contract* and says so with its reason. Anywhere else: STOP and ask.
- **Time comes from an injected `Clock`.** This table's ids are *derived* from `(alpaca_order_id, observed_at)` rather than minted, so no `id_factory` is threaded and determinism is structural — sim-day and replay are safe without one.
- **Timestamps use `orchestrator.clock.iso()`.** Every other timestamp in the DB does.
- **`make test` must pass before every commit.**

## The 🔏 `specs/contracts.md` question — resolved by assumption, reversible in one commit

**The DDL goes in `state/schema.sql` only.** Benjamin did not rule this explicitly; it was carried
as a stated assumption when he cleared the work, and it is cheap to reverse.

The reasoning, which is precedent rather than argument: `contracts.md` §1 is state machines and §2
is DDL. This table is an append-only log with no status column and no transitions, so it has no §1
entry to make. `strategy_specs` and `strategy_critiques` landed with PR #21 registered in neither
`TABLES` nor `STATUSES`, and `tests/test_state.py` asserts `TABLES <= names` — a **subset** — so the
suite stays green. That precedent is someone else's merged work, not a case built for this table.

**If it is ruled the other way**, the DDL is duplicated into §2 and something must keep the two
copies honest — review 2's N8, never satisfactorily answered. One commit either way.

There is no `reconcile.py` contention: branch one does not touch that file at all. (Revision 2 justified this with an unverifiable claim about another session's work — N13. The real reason is simply that the writer lives in `orchestrator/protection.py`.)

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/fake_alpaca.py` | fake broker; `open_orders` must match the real contract | 1 ✅, 3 |
| `state/schema.sql` | the log — picked up on an existing database by `_TABLES` | 2 |
| `orchestrator/broker.py`, `market/source_alpaca.py` | the widened `open_orders` contract | 3 |
| `state/protection.py` | **new** — `STOP_TYPES`, `CLOSING_SIDE`, `qty_of`, the append | 4 |
| `orchestrator/protection.py`, `orchestrator/daily.py` | import the moved helpers; call the writer | 4 |

---

### Task 1: The fake stops lying about `held` — ✅ DONE, `432c6a7`

**Built 2026-08-21.** Red asserted first and failed on the assertion, not a `TypeError`; then green.
Suite **1134 passed, 1 skipped, 7 deselected** — one more than the 1133 baseline, and nothing else
affected, so the plan's claim below held. The steps are kept as the record of what was done.

`tests/fake_alpaca.py:open_orders()` includes `"held"`; `AlpacaSource.open_orders` queries `QueryOrderStatus.OPEN`, which measurably excludes held OTO children (2026-08-19, cited in `tests/test_live_smoke.py`). The fake's docstring claims to match.

**Files:** Modify `tests/fake_alpaca.py:239-247` (was 212-220 at the old base) · Test `tests/test_fake_alpaca.py`

**Interfaces:** Consumes nothing. Produces a fake whose `open_orders()` excludes `held`.

- [ ] **Step 1: Write the failing test**

```python
def test_open_orders_excludes_held_children_like_the_real_broker():
    """AlpacaSource.open_orders queries QueryOrderStatus.OPEN, and a held OTO
    child is NOT returned by it — measured 2026-08-19, cited in
    tests/test_live_smoke.py. A fake that returns held legs lets a writer pass
    offline and record nothing in production: the 2026-08-17 shape."""
    broker = FakeAlpaca({"NVDA": 180.0})
    broker.place_order({"client_order_id": "t1", "symbol": "NVDA",
                        "side": "buy", "qty": 80,
                        "stop_loss_stop_price": "215.0"})
    assert broker.orders["t1-stop"]["status"] == "held", "setup: leg not held"
    assert [o for o in broker.open_orders() if o["type"] == "stop"] == [], (
        "the fake returns a held stop leg; the real broker does not")
```

`prices` is a **required positional** — verified.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py -v -k held_children`
Expected: FAIL on the **assertion**, not a `TypeError`.

- [ ] **Step 3: Remove `"held"` from the filter**

```python
                if o["status"] in ("new", "accepted", "partially_filled")]
```

Update the docstring to state what it now matches and why.

- [ ] **Step 4: Run the whole suite**

Run: `make test`
Expected: **PASS, nothing else affected** — verified by both reviews. `tests/test_protection.py` uses its own hand-rolled `Broker`, not `FakeAlpaca`. Do not go looking for something to fix there.

If anything does fail, the fix is filling the parent — the real sequence. Restoring `"held"` is forbidden. **If a failure cannot be fixed that way, stop and ask.**

- [ ] **Step 5: Commit**

```bash
git add tests/fake_alpaca.py tests/test_fake_alpaca.py
git commit -m "fix: the fake broker hides held legs, exactly as the real one does"
```

---

### Task 2: The log

**No longer gated.** The 🔏 question is resolved by assumption above — `schema.sql` only.

`state/db.py` parses `_TABLES` from `schema.sql` and re-runs the script whenever a listed table
is missing, so adding the table there is all that is required — an existing database gains it on
the next `connect()`. **Revisions 1–3 built a migration for this; it is unnecessary on current
master** (`b1d8c50`).

**Files:** Modify `state/schema.sql` · Test `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_database_without_the_log_gains_it_on_reconnect(tmp_path):
    """The droplet case. _TABLES is parsed from schema.sql, so a table added
    there is created on an existing database at the next connect() — no
    migration. This pins that the new table is actually picked up by that
    mechanism, which depends on the DDL saying CREATE TABLE IF NOT EXISTS."""
    path = tmp_path / "fund.sqlite"
    conn = connect(path)
    conn.execute("DROP TABLE protection")
    conn.commit()
    conn.close()

    conn = connect(path)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='protection'"
    ).fetchone() is not None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_state.py -v -k gains_it_on_reconnect`
Expected: FAIL — `no such table: protection` on the DROP.

- [ ] **Step 3: Add the DDL to `state/schema.sql`**

```sql
-- protection: an append-only OBSERVATION LOG. One row each time the fund sees a
-- protective order at the broker. Deliberately NO status column — see ADR-0004.
-- What the fund KNOWS it saw and when; never what EXISTS now (_covering_qty
-- reads the broker for that). Like events/costs this is a log, not a workflow
-- table, so contracts.md §1 has no machine for it.
CREATE TABLE IF NOT EXISTS protection (
  id                TEXT PRIMARY KEY,          -- "<alpaca_order_id>@<observed_at>"
  symbol            TEXT NOT NULL,
  qty               INTEGER NOT NULL CHECK (qty > 0),
  stop_price        REAL CHECK (stop_price IS NULL OR stop_price > 0),
  alpaca_order_id   TEXT NOT NULL,
  client_order_id   TEXT,
  provenance_kind   TEXT NOT NULL
                    CHECK (provenance_kind IN ('observed','adopted')),
  broker_expires_at TEXT,
  observed_at       TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  UNIQUE (alpaca_order_id, observed_at)
);
```

**`CREATE TABLE IF NOT EXISTS` is load-bearing, not style.** `state/db.py:12` matches that exact
string to build `_TABLES`. A bare `CREATE TABLE` would create the table on fresh databases and
never register it, so no existing database would ever gain it — the precise failure this task
exists to prevent, reintroduced by the DDL that prevents it.

**`id` is derived, not minted:** `f"{alpaca_order_id}@{observed_at}"`. No `id_factory`, so
`assert_positions_protected`'s signature is untouched and its 30 existing call sites stand
(review 3, C1). Determinism is structural rather than injected, which also keeps sim-day and
replay safe without sharing `ctx.id_factory` with tickets (review 3, L2).

**`stop_price` is NULLABLE.** `_STOP_TYPES` counts `trailing_stop`, which carries a trail rather
than a stop price, and `test_stop_limit_and_trailing_stop_both_count` pins that. A NOT NULL
column would make the log silently skip an order `_covering_qty` counts, under-reporting against
the number in the same alert (review 3, M1).

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_state.py tests/test_migrations.py -v`
Expected: PASS. `test_ddl_applies_cleanly_and_is_idempotent` covers the new table for free.

- [ ] **Step 5: Commit**

```bash
git add state/schema.sql tests/test_state.py
git commit -m "feat: an append-only log of what the fund has seen protecting a position"
```

---

### Task 3: `open_orders()` carries the fields a row needs

**Files:** Modify `orchestrator/broker.py:26`, `market/source_alpaca.py:115-139` (was 114-138), `tests/fake_alpaca.py` · Test `tests/test_source_alpaca_helpers.py`, `tests/test_fake_alpaca.py`

**Interfaces:** Consumes nothing (this task's test fills the parent, so it is independent of Task 1). Produces `open_orders()` dicts carrying `id`, `client_order_id`, `stop_price`, `expires_at`.

- [ ] **Step 1: Write the failing test**

```python
def test_open_orders_carries_the_fields_a_protection_row_needs():
    """A row references the broker's UUID, stores the client id verbatim, and
    records the stop price and the ~90-day GTC expiry."""
    broker = FakeAlpaca({"NVDA": 180.0})
    broker.place_order({"client_order_id": "t1", "symbol": "NVDA",
                        "side": "buy", "qty": 80,
                        "stop_loss_stop_price": "215.0"})
    broker.tick()                       # fill the parent; the leg activates
    leg = [o for o in broker.open_orders() if o["type"] == "stop"][0]
    assert leg["id"], "no broker order id"
    assert leg["client_order_id"] == "t1-stop"
    assert leg["stop_price"] == "215.0"
    assert leg["expires_at"] == "2026-11-17T21:00:00+00:00"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py -v -k protection_row`
Expected: FAIL with `KeyError: 'id'`.

- [ ] **Step 3: Widen the port contract**

In `orchestrator/broker.py`, extend `open_orders`'s docstring to name all eight keys, and state that `expires_at` is an **ISO string in the repo's canonical form**, not a datetime. **Restate the prohibition unchanged** — this port never places and must never grow a method that does.

- [ ] **Step 4: Implement in `market/source_alpaca.py`**

```python
        return [{"symbol": o.symbol, "side": _enum_str(o.side),
                 "qty": _enum_str(o.qty), "type": _enum_str(o.order_type),
                 "status": _enum_str(o.status), "id": _enum_str(o.id),
                 "client_order_id": o.client_order_id,
                 "stop_price": _enum_str(o.stop_price) if o.stop_price is not None else None,
                 "expires_at": iso(o.expires_at) if o.expires_at is not None else None}
                for o in orders]
```

**`iso()`, not `_enum_str()`** (review 2, N7). `Order.expires_at` is a `datetime`, and `_enum_str` would yield `'2026-11-17 21:00:00+00:00'` — space separator, no `T` — which compares and sorts against nothing else in the database. This is the one column the November expiry watch will depend on.

Plain attribute access, not `getattr` — alpaca-py 0.44's `Order` has the field, and a silent `None` on a future rename would hide the failure.

- [ ] **Step 5: Implement in `tests/fake_alpaca.py`**

`open_orders()` returns the same eight keys. Give the leg dict (`fake_alpaca.py:127-143`, was 99-117) `"stop_price": args["stop_loss_stop_price"]` — from the **request args**, not the leg's own null field — and an `"expires_at"` value, so `broker_expires_at` is exercised offline at all (review 2, N18).

- [ ] **Step 6: Widen the existing helper test — a contract change, not a weakened assertion**

`tests/test_source_alpaca_helpers.py` fails with exactly:

```
AttributeError: '_Clock' object has no attribute 'id'
```

Its `_Clock` stub is `self.__dict__.update(attrs)`, so it needs the four new attributes; its exact-dict assertion needs the four new keys.

**This is the only place in this plan where an existing expectation legitimately changes**, and only because Step 3 changed the port's contract. Widening a stub to match a deliberately widened interface is not the forbidden move. Anywhere else, stop and ask.

- [ ] **Step 7: Run the suite**

Run: `make test`
Expected: PASS. `_covering_qty` reads only `symbol`, `side`, `type`, `qty`.

- [ ] **Step 8: Commit**

```bash
git add orchestrator/broker.py market/source_alpaca.py tests/fake_alpaca.py tests/test_fake_alpaca.py tests/test_source_alpaca_helpers.py
git commit -m "feat: open_orders carries the ids, stop price and expiry a record needs"
```

---

### Task 4: `orchestrator/protection.py` logs what it already reads

**Files:** Create `state/protection.py`, `tests/test_state_protection.py` · Modify `orchestrator/protection.py`, `orchestrator/daily.py`

**Interfaces:**
- Consumes Task 2's table, Task 3's widened `open_orders()`.
- Produces:
  - `state.protection.STOP_TYPES = ("stop", "stop_limit", "trailing_stop")`,
    `state.protection.CLOSING_SIDE` and `state.protection.qty_of(value)` — **all three live here**,
    and `orchestrator/protection.py` imports them back. Nothing in `state/` imports `orchestrator/`
    today and this does not change that (review 1, H5 — re-verified 2026-08-21). Moving `_qty`
    settles review 2's N16 (one broker-side coercer) and moving `_CLOSING_SIDE` settles review 3's
    M6 (one protective-order predicate) — in one place each, unable to drift.
  - `state.protection.log_observed(conn, orders, *, now_iso) -> list[str]` — appends one row per
    protective order; returns the ids **actually inserted**. **No `id_factory`**: the id is
    `f"{alpaca_order_id}@{observed_at}"`.

> **Revision 5 — what changed in this task and why.** Revision 4 declared "No `id_factory`" here and
> then passed `id_factory=` in every test below, with Step 3 instructing the opposite. The derived id
> is the correct side: it is what Task 2's DDL already documents, it keeps
> `assert_positions_protected`'s signature untouched (review 3, C1) and it avoids sharing
> `ctx.id_factory` with tickets (review 3, L2). The `id_factory=` arguments are removed.
>
> **`last_observed_before` is gone with Task 5.** It had exactly one consumer and that task is cut,
> so the function, the `observed_at_run` name its tests actually called, and the two tests pinning
> its semantics are all deleted rather than reconciled. **This leaves the table with no reader in
> branch one** — see the ⛔ box.
>
> **The return value is kept, and it is not redundant.** With a derived id the caller could
> recompute what the ids *would* be, but not which rows `INSERT OR IGNORE` actually wrote. That
> difference is the idempotence signal the second test asserts.

- [ ] **Step 1: Write the failing tests**

```python
def _leg(**kw):
    base = {"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
            "status": "new", "id": "alp-0002", "client_order_id": "t1-stop",
            "stop_price": "215.0", "expires_at": "2026-11-17T21:00:00+00:00"}
    return {**base, **kw}


def test_a_protective_order_is_logged(fund_db, sim_clock):
    now = iso(sim_clock.now())
    assert log_observed(fund_db, [_leg()], now_iso=now) == [f"alp-0002@{now}"]
    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert (row["symbol"], row["qty"], row["stop_price"]) == ("NVDA", 80, 215.0)
    assert row["alpaca_order_id"] == "alp-0002"
    assert row["client_order_id"] == "t1-stop"
    assert row["provenance_kind"] == "observed"
    assert row["broker_expires_at"] == "2026-11-17T21:00:00+00:00"
    assert row["observed_at"] == now


def test_the_id_is_derived_from_the_order_and_the_observation(fund_db, sim_clock):
    """The id is a function of (alpaca_order_id, observed_at), not minted. That
    is what keeps assert_positions_protected's signature untouched (review 3,
    C1) and sim-day deterministic without sharing ctx.id_factory with tickets
    (review 3, L2). Pinned so a later 'tidy-up' cannot reintroduce a factory."""
    now = "2026-08-20T20:05:00+00:00"
    log_observed(fund_db, [_leg()], now_iso=now)
    assert fund_db.execute(
        "SELECT id FROM protection").fetchone()["id"] == f"alp-0002@{now}"


def test_the_same_run_logs_one_row_per_order(fund_db, sim_clock):
    """assert_positions_protected re-reads after its nap; the second call must
    not double-log the same observation. The empty return is the signal that
    nothing was written — which a caller cannot derive from the ids alone."""
    now = iso(sim_clock.now())
    log_observed(fund_db, [_leg()], now_iso=now)
    assert log_observed(fund_db, [_leg()], now_iso=now) == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_later_run_logs_a_second_observation(fund_db, sim_clock):
    """This is a LOG. The same order seen on two days is two rows — that is
    what makes observed_at meaningful rather than decorative."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    log_observed(fund_db, [_leg()], now_iso="2026-08-21T20:05:00+00:00")
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 2


def test_an_order_that_vanished_leaves_the_log_alone(fund_db, sim_clock):
    """Nothing is ever closed or rewritten. A stop that dies simply stops
    appearing in later runs; the earlier row stays exactly as written, because
    a log records what was seen and never revises it."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    assert log_observed(fund_db, [], now_iso="2026-08-21T20:05:00+00:00") == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_non_protective_order_is_not_logged(fund_db, sim_clock):
    """A sell LIMIT is a take-profit: it caps the upside and leaves the
    downside exposed. Same predicate as STOP_TYPES."""
    assert log_observed(fund_db, [_leg(type="limit", stop_price=None)],
                        now_iso=iso(sim_clock.now())) == []


def test_a_buy_stop_is_not_logged(fund_db, sim_clock):
    """CLOSING_SIDE, not just STOP_TYPES. A buy stop on a long position is an
    entry, not protection; counting it would inflate cover against a position
    it does nothing to protect."""
    assert log_observed(fund_db, [_leg(side="buy")],
                        now_iso=iso(sim_clock.now())) == []


def test_an_unreadable_order_is_skipped_and_nothing_else_changes(fund_db, sim_clock):
    """Invariant 4. Skipping costs one observation; under revision 2's sweep it
    permanently closed a live row with no way back (review 2, N3). Seed a prior
    row so that regression could be seen if it ever returned.

    Note stop_price is NOT in this list — it is nullable, because trailing_stop
    carries no stop price (review 3, M1)."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    later = "2026-08-21T20:05:00+00:00"
    for bad in ({"qty": "eighty"}, {"qty": None}, {"id": None}):
        assert log_observed(fund_db, [_leg(**bad)], now_iso=later) == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_trailing_stop_is_logged_with_no_stop_price(fund_db, sim_clock):
    """_STOP_TYPES counts trailing_stop and _covering_qty counts it toward
    cover, so the log must too. A NOT NULL stop_price would silently skip an
    order the alert's own number includes (review 3, M1)."""
    now = iso(sim_clock.now())
    assert log_observed(fund_db, [_leg(type="trailing_stop", stop_price=None)],
                        now_iso=now) != []
    assert fund_db.execute(
        "SELECT stop_price FROM protection").fetchone()["stop_price"] is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_state_protection.py -v`
Expected: FAIL — `ModuleNotFoundError: state.protection`.

- [ ] **Step 3: Write `state/protection.py`**

Pure Python + SQLite. `log_observed` filters on `STOP_TYPES` and `CLOSING_SIDE`; coerces broker numerics with `qty_of` (moved here from `orchestrator/protection.py:_qty` — broker output, not adversarial agent input). Anything missing a readable qty or id is **skipped, never guessed** (invariant 4); skipping now costs exactly one observation and can corrupt nothing. **A missing `stop_price` is not a skip** — `trailing_stop` has none and `_covering_qty` counts it (review 3, M1).

Every row is `provenance_kind='observed'`. **The id is derived — `f"{alpaca_order_id}@{observed_at}"`, no factory.** `INSERT OR IGNORE` against `UNIQUE (alpaca_order_id, observed_at)` gives the re-read idempotence, and the return value reports what was actually inserted.

Update `orchestrator/protection.py` to import `STOP_TYPES`, `CLOSING_SIDE` and `qty_of` from here, deleting its own `_STOP_TYPES` (`:39`), `_CLOSING_SIDE` (`:44`) and `_qty` (`:51`). **Verify the direction of the import**: `state/` must still import nothing from `orchestrator/` when this is done.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_state_protection.py -v`
Expected: PASS.

- [ ] **Step 5: Call the writer, after the alerts are computed**

**No signature change.** The id is derived, so nothing needs injecting and the 30 existing call sites are untouched.

**Place the write at the very end, after the alert loop, using the order list the final evaluation used.** That ordering resolves three findings at once:

- No alert depends on this write — in revision 5 nothing reads the table at all, so review 2's N1 and review 3's C2 have nothing left to corrupt. (Revision 4 argued this from Task 5 reading the past; with Task 5 cut, the property holds for a simpler reason.)
- The nap-and-re-read question disappears: only the final list is logged, so there is no stale-vs-fresh race to lose (review 3, C2).
- A SQLite failure cannot cost an alert, because the alerts are already written.

**Wrap it in its own `try` whose failure becomes an alert**, and let the day continue. Not bare — review 3's H4 verified that a bare write propagates through `daily.py`, emits zero alerts and skips `run_close`. Not inside the read `try` either — review 2's N4 verified that reports a false `UNVERIFIED — could not read live orders`. The alert text must name what actually failed: recording, not reading.

- [ ] **Step 6: Run the suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add state/protection.py tests/test_state_protection.py orchestrator/protection.py orchestrator/daily.py
git commit -m "feat: the fund logs what it sees protecting each position"
```

---

---

## Not in this plan

**Cut from this plan by Benjamin on 2026-08-21, having been written and reviewed:**

- **The alert clause naming the observation (was Task 5).** The full text survives in git —
  `git show 432c6a7~1:docs/superpowers/plans/2026-08-20-protection-record.md` — and it is worth
  reading before rebuilding it, because it is where revision 4's inversion was left half-applied.
  **It was branch one's only reader**, so cutting it is what makes the table write-only. Rebuild it
  by deciding *first* whether it reads this run or the previous one; revisions 3, 4 and 5 each
  answered that differently and the tests only ever matched revision 3.
- **The adoption script (was Task 6).** Cut because its subject no longer exists at the broker, not
  because adoption is wrong. `provenance_kind='adopted'` stays in the DDL's CHECK with **no writer
  in this branch** — deliberately, since narrowing the vocabulary now and widening it in branch two
  is a schema change for nothing. Note the consequence: only `'observed'` is ever written here, so
  the CHECK is untested against real usage.

**Deferred from the start:**

- **The amend capability.** Branch two, ADR-0003.
- **Any notion of a protective order being closed, cancelled, triggered or expired.** A log records what was seen; establishing why something stopped being seen needs order history. Branch two.
- **Alerting on `broker_expires_at`.** The column lands; the watch does not. The live NVDA stop dies 2026-11-17 and nothing watches for it — still true after this branch. When built it must **read the column**, never compute from a placement date.
- **Invariant 5's rewording**, **`_promised_stop`'s multi-buy semantics**, **the eight ungated mutation verbs.** Separate, separately owned.

## Verification before this branch is called done

`make test` is necessary and not sufficient — this runs unattended at 09:35 with nobody watching.

- `make sim-day` — a full simulated day through the real gate and DB.
- **Droplet evidence no diff can show, and note the mechanism is NOT a migration.** Revision 4
  removed the migration: `state/db.py` re-runs `schema.sql` whenever a table in `_TABLES` is
  missing (`db.py:38-41`), and `state/migrations.py:apply()` runs afterwards for **columns**, which
  this branch does not add. So the thing to evidence is the `_TABLES` path, not `apply()`: copy the
  droplet database, open it with `connect()`, record before/after — table absent, then present —
  then `connect()` again and confirm no second write. The suite exercises this on a database built
  by `schema.sql`, which is exactly the case the droplet is not.
- **One real day's log.** After the first live run, confirm the rows written match what the broker held — the only evidence that `observed_at` and the id columns are populated as intended against the real API rather than the fake.
- **Nothing reads the table**, so there is no read path to verify and no alert whose text can be
  checked. That is the cut's cost, and it means the first real evidence that the rows are *correct*
  rather than merely present arrives with branch two's first consumer.
