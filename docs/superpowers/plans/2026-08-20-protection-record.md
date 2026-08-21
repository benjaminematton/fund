# The Protection Record (branch one) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fund a record of what it has observed protecting each position, so protection stops being re-derived by join from order history.

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
> `_STOP_TYPES` counts it), **M6** (`_CLOSING_SIDE` moves with `STOP_TYPES` and `_qty`), **M7**
> (`provenance_kind` now has a consumer). **H3** is fixed in ADR-0004 and the spec, not here.
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
- **Time comes from an injected `Clock`; ids from an injected `id_factory`** (`daily.py:53`). No bare `uuid4()`, or `sim-day` and replay stop being deterministic.
- **Timestamps use `orchestrator.clock.iso()`.** Every other timestamp in the DB does.
- **`make test` must pass before every commit.**

## ⚠️ Gate before Task 2

**The 🔏 `specs/contracts.md` ruling has not landed.** Task 2 writes the DDL. Tasks 1, 3, 5, 6 are unaffected. There is no §1 state machine to rule on any more — the log has no transitions.

There is no `reconcile.py` contention: branch one does not touch that file at all. (Revision 2 justified this with an unverifiable claim about another session's work — N13. The real reason is simply that the writer lives in `orchestrator/protection.py`.)

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/fake_alpaca.py` | fake broker; `open_orders` must match the real contract | 1, 3 |
| `state/schema.sql`, `state/migrations.py` | the log, and carrying it to a live database | 2 |
| `specs/contracts.md` | §2 DDL — 🔏 | 2 |
| `orchestrator/broker.py`, `market/source_alpaca.py` | the widened `open_orders` contract | 3 |
| `state/protection.py` | **new** — `STOP_TYPES`, `_qty`, appends, the this-run query | 4 |
| `orchestrator/protection.py`, `orchestrator/daily.py` | calls the writer; names the record in its alert | 4, 5 |
| `scripts/adopt_protection.py` | **new** — hand-run adoption | 6 |

---

### Task 1: The fake stops lying about `held`

`tests/fake_alpaca.py:open_orders()` includes `"held"`; `AlpacaSource.open_orders` queries `QueryOrderStatus.OPEN`, which measurably excludes held OTO children (2026-08-19, cited in `tests/test_live_smoke.py`). The fake's docstring claims to match.

**Files:** Modify `tests/fake_alpaca.py:212-220` · Test `tests/test_fake_alpaca.py`

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

**Gated on the 🔏 ruling.**

`state/db.py` parses `_TABLES` from `schema.sql` and re-runs the script whenever a listed table
is missing, so adding the table there is all that is required — an existing database gains it on
the next `connect()`. **Revisions 1–3 built a migration for this; it is unnecessary on current
master** (`b1d8c50`).

**Files:** Modify `state/schema.sql`, `specs/contracts.md` · Test `tests/test_state.py`

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

- [ ] **Step 4: Write the §2 DDL into `specs/contracts.md`** — 🔏, per the ruling. State
explicitly that there is no §1 entry, and why: it is a log, like `events`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_state.py tests/test_migrations.py -v`
Expected: PASS. `test_ddl_applies_cleanly_and_is_idempotent` covers the new table for free.

- [ ] **Step 6: Commit**

```bash
git add state/schema.sql specs/contracts.md tests/test_state.py
git commit -m "feat: an append-only log of what the fund has seen protecting a position"
```

---

### Task 3: `open_orders()` carries the fields a row needs

**Files:** Modify `orchestrator/broker.py`, `market/source_alpaca.py:114-138`, `tests/fake_alpaca.py` · Test `tests/test_source_alpaca_helpers.py`, `tests/test_fake_alpaca.py`

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

`open_orders()` returns the same eight keys. Give the leg dict (`fake_alpaca.py:99-117`) `"stop_price": args["stop_loss_stop_price"]` — from the **request args**, not the leg's own null field — and an `"expires_at"` value, so `broker_expires_at` is exercised offline at all (review 2, N18).

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
  - `state.protection.STOP_TYPES = ("stop", "stop_limit", "trailing_stop")` and `state.protection.qty_of(value)` — **both live here**, and `orchestrator/protection.py` imports them back. Nothing in `state/` imports `orchestrator/` today (review 1, H5). Moving `_qty` too settles review 2's N16: there is one broker-side coercer, in one place.
  - `state.protection.STOP_TYPES`, `state.protection.CLOSING_SIDE` and `state.protection.qty_of` — **all three** live here, and `orchestrator/protection.py` imports them back. Moving `_CLOSING_SIDE` too settles review 3's M6: one protective-order predicate, in one place, that cannot drift.
  - `state.protection.log_observed(conn, orders, *, now_iso) -> list[str]` — appends one row per protective order; returns ids written. **No `id_factory`** — the id is `f"{alpaca_order_id}@{observed_at}"`.
  - `state.protection.last_observed_before(conn, symbol, now_iso) -> list[dict]` — the most recent observation **strictly before** `now_iso`. This is what Task 5 reads.

- [ ] **Step 1: Write the failing tests**

```python
def _leg(**kw):
    base = {"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
            "status": "new", "id": "alp-0002", "client_order_id": "t1-stop",
            "stop_price": "215.0", "expires_at": "2026-11-17T21:00:00+00:00"}
    return {**base, **kw}


def test_a_protective_order_is_logged(fund_db, sim_clock):
    now = iso(sim_clock.now())
    assert log_observed(fund_db, [_leg()], now_iso=now,
                        id_factory=lambda: "p1") == ["p1"]
    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert (row["symbol"], row["qty"], row["stop_price"]) == ("NVDA", 80, 215.0)
    assert row["alpaca_order_id"] == "alp-0002"
    assert row["client_order_id"] == "t1-stop"
    assert row["provenance_kind"] == "observed"
    assert row["broker_expires_at"] == "2026-11-17T21:00:00+00:00"
    assert row["observed_at"] == now


def test_the_same_run_logs_one_row_per_order(fund_db, sim_clock):
    """assert_positions_protected re-reads after its nap; the second call must
    not double-log the same observation."""
    now = iso(sim_clock.now())
    log_observed(fund_db, [_leg()], now_iso=now, id_factory=lambda: "p1")
    assert log_observed(fund_db, [_leg()], now_iso=now,
                        id_factory=lambda: "p2") == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_later_run_logs_a_second_observation(fund_db, sim_clock):
    """This is a LOG. The same order seen on two days is two rows — that is
    what makes observed_at meaningful rather than decorative."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00",
                 id_factory=lambda: "p1")
    log_observed(fund_db, [_leg()], now_iso="2026-08-21T20:05:00+00:00",
                 id_factory=lambda: "p2")
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 2


def test_an_order_that_vanished_leaves_the_log_alone(fund_db, sim_clock):
    """Nothing is ever closed or rewritten. A stop that dies simply stops
    appearing in later runs, and this run's query returns nothing for it —
    which is how staleness stays impossible to misread."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00",
                 id_factory=lambda: "p1")
    later = "2026-08-21T20:05:00+00:00"
    assert log_observed(fund_db, [], now_iso=later, id_factory=lambda: "p2") == []
    assert observed_at_run(fund_db, "NVDA", later) == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_non_protective_order_is_not_logged(fund_db, sim_clock):
    """A sell LIMIT is a take-profit: it caps the upside and leaves the
    downside exposed. Same predicate as STOP_TYPES."""
    assert log_observed(fund_db, [_leg(type="limit", stop_price=None)],
                        now_iso=iso(sim_clock.now()),
                        id_factory=lambda: "p1") == []


def test_an_unreadable_order_is_skipped_and_nothing_else_changes(fund_db, sim_clock):
    """Invariant 4. Skipping costs one observation; under revision 2's sweep it
    permanently closed a live row with no way back (review 2, N3). Seed a prior
    row so that regression could be seen if it ever returned."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00",
                 id_factory=lambda: "p1")
    later = "2026-08-21T20:05:00+00:00"
    for bad in ({"qty": "eighty"}, {"qty": None}, {"stop_price": None},
                {"id": None}):
        assert log_observed(fund_db, [_leg(**bad)], now_iso=later,
                            id_factory=lambda: "px") == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_the_query_returns_this_run_only(fund_db, sim_clock):
    """A consumer must never read an earlier run's row as a statement about
    now. The query enforces it rather than trusting the caller."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00",
                 id_factory=lambda: "p1")
    assert observed_at_run(fund_db, "NVDA", "2026-08-20T20:05:00+00:00") != []
    assert observed_at_run(fund_db, "NVDA", "2026-08-21T20:05:00+00:00") == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_state_protection.py -v`
Expected: FAIL — `ModuleNotFoundError: state.protection`.

- [ ] **Step 3: Write `state/protection.py`**

Pure Python + SQLite. `log_observed` filters on `STOP_TYPES` and a closing side; coerces broker numerics with `qty_of` (moved here from `orchestrator/protection.py:_qty` — broker output, not adversarial agent input). Anything missing a readable qty, stop price or id is **skipped, never guessed** (invariant 4); skipping now costs exactly one observation and can corrupt nothing.

Every row is `provenance_kind='observed'`. Ids from the injected `id_factory`. `INSERT OR IGNORE` against `UNIQUE (alpaca_order_id, observed_at)` gives the re-read idempotence.

Update `orchestrator/protection.py` to import `STOP_TYPES` and `qty_of` from here.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_state_protection.py -v`
Expected: PASS.

- [ ] **Step 5: Call the writer, after the alerts are computed**

**No signature change.** The id is derived, so nothing needs injecting and the 30 existing call sites are untouched.

**Place the write at the very end, after the alert loop, using the order list the final evaluation used.** That ordering resolves three findings at once:

- Task 5 reads observations from *before* this run, so the alert never depends on this write — review 2's N1 and review 3's C2 have nothing left to corrupt.
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

### Task 5: the alert names this run's observation

**Files:** Modify `orchestrator/protection.py:_evaluate` · Test `tests/test_protection.py`

**Interfaces:** Consumes `state.protection.last_observed_before`. Produces alert text only.

**Why this reads the PAST, not this run.** Review 3 established that reading this run's log makes the record layer informationally identical to the broker read `_evaluate` already holds — a restatement dressed as a comparison, with wording ("which the broker does not confirm") that is false in the same sentence that produces it.

Reading the most recent observation **strictly before** this run is the opposite: *"the broker holds no protective order; the fund last saw 80 @ 215 on 2026-08-19"* is information the current read cannot produce, and the contrast is true. It is also the first thing a human needs at 16:05 — not what is there, which the alert already says, but **when it was last there**.

**`_promised_stop`'s tri-state is untouched.** A price, `None` for a charter-sanctioned stopless buy, `_UNKNOWN` for no record at all. The log cannot express the `None` case, which is why intent stays on the decision.

- [ ] **Step 1: Write the failing test**

```python
def test_a_partial_cover_alert_names_this_run_s_observation(fund_db, sim_clock):
    """The shortfall was reported against an entry-time promise with no
    quantity that no later event updates. This names what the fund actually
    saw, labelled as the fund's record — the broker stays the authority."""
    now = iso(sim_clock.now())
    _promised(fund_db, symbol="NVDA", stop_price=215.0)
    log_observed(fund_db, [_leg(qty="40")], now_iso=now,
                 id_factory=lambda: "p1")

    alerts = _evaluate(fund_db, [{"symbol": "NVDA", "qty": "80",
                                  "side": "long"}],
                       [_leg(qty="40")], now_iso=now)
    assert len(alerts) == 1
    assert "40" in alerts[0]
    assert "fund's record" in alerts[0], (
        "record text must be labelled as belief, not read as fact")


def test_no_observation_this_run_means_no_record_clause(fund_db, sim_clock):
    """The total-shortfall case. Nothing was observed, so the alert must not
    reach for an older row — the failure revision 2 shipped as dead code."""
    now = iso(sim_clock.now())
    _promised(fund_db, symbol="NVDA", stop_price=215.0)
    log_observed(fund_db, [_leg()], now_iso="2026-08-19T20:05:00+00:00",
                 id_factory=lambda: "p1")

    alerts = _evaluate(fund_db, [{"symbol": "NVDA", "qty": "80",
                                  "side": "long"}], [], now_iso=now)
    assert "fund's record" not in alerts[0]
```

`_promised` already exists at `tests/test_protection.py:48` — do not write a new helper (review 2, N12).

Note `_evaluate` gains `now_iso`. It has none today; it needs one to scope the query to this run, and the caller has it.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_protection.py -v -k this_run`
Expected: FAIL — `_evaluate()` takes no `now_iso`.

- [ ] **Step 3: Append the observation, in the `promised is not None` branch only**

Not the `_UNKNOWN` branch: that means the fund has no record of opening the position, and printing a belief about its protection there states something it has no standing to hold.

Label it. `"… — the fund's record for this run: 40 @ 215 observed, which the broker does not confirm"` reads correctly at 16:05 to someone who was not in this conversation. Keep it to one clause; the sentence already carries an em-dash (review 2, Low). **`covered` stays broker-sourced** — this is text, never arithmetic.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_protection.py -v`
Expected: PASS, tri-state tests included. Those three — `test_a_position_that_was_never_promised_a_stop_is_silent`, `test_a_position_the_fund_has_no_record_of_alerts`, `test_a_promised_stop_that_is_gone_alerts` — already cover the spec's tri-state regression item; no new test is needed.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/protection.py tests/test_protection.py
git commit -m "feat: a shortfall alert names what the fund saw this run"
```

---

### Task 6: The adoption script

**Files:** Create `scripts/adopt_protection.py`, `tests/test_adopt_protection.py`

**Interfaces:** Consumes `state.protection` helpers; writes its own row with `provenance_kind='adopted'`. Produces a hand-run entry point; nothing imports it.

**Why this now works.** Revision 2 made `alpaca_order_id` UNIQUE and had the daily writer claim every order as `oto_leg`, so adoption's row could never be inserted — `IntegrityError` on the one order the script exists for (review 2, N2). In a log, an adoption is simply another observation, with a kind that says a human asserted its provenance.

⚠️ **Do not run against the live NVDA stop until `fund-07`'s review concludes.** That order is under review for cancellation or resizing. The script touches the fund's record, never the order.

- [ ] **Step 1: Write the failing tests**

```python
def _manual_stop_broker():
    broker = FakeAlpaca({"NVDA": 180.0})
    broker.orders["manual-protective-stop-nvda-2026-08-19"] = {
        "id": "5abc139f-4817-4a34-aedd-f2ca28203c5c",
        "client_order_id": "manual-protective-stop-nvda-2026-08-19",
        "symbol": "NVDA", "side": "sell", "qty": 80, "status": "new",
        "order_type": "stop", "stop_price": "215.0",
        "expires_at": "2026-11-17T21:00:00+00:00", "filled_qty": 0,
        "filled_avg_price": None, "order_class": "",
        "stop_loss_stop_price": None, "stop_loss_limit_price": None,
        "take_profit_limit_price": None}
    return broker


def test_adoption_records_an_observation_a_human_vouched_for(fund_db, sim_clock):
    """The fund did not place this order and does not control it. observed_at
    says when it was seen; provenance_kind says a human asserted where it came
    from. The broker stays the authority on whether it still exists."""
    now = iso(sim_clock.now())
    adopt(fund_db, broker=_manual_stop_broker(), now_iso=now,
          id_factory=lambda: "p1")

    row = fund_db.execute(
        "SELECT * FROM protection WHERE provenance_kind = 'adopted'").fetchone()
    assert row["observed_at"] == now
    assert row["alpaca_order_id"] == "5abc139f-4817-4a34-aedd-f2ca28203c5c"
    assert row["client_order_id"] == "manual-protective-stop-nvda-2026-08-19"


def test_adoption_twice_writes_one_row(fund_db, sim_clock):
    now = iso(sim_clock.now())
    broker = _manual_stop_broker()
    adopt(fund_db, broker=broker, now_iso=now, id_factory=lambda: "p1")
    adopt(fund_db, broker=broker, now_iso=now, id_factory=lambda: "p2")
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1
```

**The two ids go in two different columns.** `manual-protective-stop-nvda-2026-08-19` is the **client** id; `5abc139f-…` is the broker UUID (`PROGRESS.md:123-124`). Review 1's C1 was the reverse assertion, which could only be made green by making the fake disagree with Alpaca.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_adopt_protection.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the script**

Reads `broker.open_orders()`, writes rows with `provenance_kind='adopted'`, adopting **whatever protection exists when it runs** — never a hardcoded order id. Idempotence comes from `UNIQUE (alpaca_order_id, observed_at)` plus `INSERT OR IGNORE`, and is asserted in code rather than left to the constraint.

Prints what it wrote and what it skipped. Hand-run against a live record; silence is the wrong default.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_adopt_protection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/adopt_protection.py tests/test_adopt_protection.py
git commit -m "feat: adopt a protective order the fund did not place"
```

---

## Not in this plan

- **The amend capability.** Branch two, ADR-0003.
- **Any notion of a protective order being closed, cancelled, triggered or expired.** A log records what was seen; establishing why something stopped being seen needs order history. Branch two.
- **Alerting on `broker_expires_at`.** The column lands; the watch does not. The live NVDA stop dies 2026-11-17 and nothing watches for it — still true after this branch. When built it must **read the column**, never compute from a placement date.
- **Invariant 5's rewording**, **`_promised_stop`'s multi-buy semantics**, **the eight ungated mutation verbs.** Separate, separately owned.

## Verification before this branch is called done

`make test` is necessary and not sufficient — this runs unattended at 09:35 with nobody watching.

- `make sim-day` — a full simulated day through the real gate and DB.
- **Migration evidence no diff can show.** Copy the droplet database, run `apply()` against it, record before/after: table absent, then present, then `apply()` again returning `[]`. The suite proves the migration works on a database built by `schema.sql`, which is exactly the case the droplet is not.
- **One real day's log.** After the first live run, confirm the rows written match what the broker held — the only evidence that `observed_at` and the id columns are populated as intended against the real API rather than the fake.
