# The Protection Record (branch one) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fund a stored record of what protects each position, so protection stops being re-derived by join from order history.

**Architecture:** One new SQLite table, `protection`, written by `orchestrator/protection.py` from the live-order list it already reads. Three layers stay separate and only the middle one is new: **intent** on the decision (unchanged), **record** in this table (new), **existence** at the broker (unchanged). The table is never the source of what protection exists.

**Tech Stack:** Python 3.12, SQLite, pytest. No new dependencies.

> **Revision 2, 2026-08-20.** Rewritten after adversarial review
> ([the findings](../reviews/2026-08-20-protection-record-review.md)) established by execution
> that revision 1 was not runnable: the writer's placement would have aborted a trading day,
> three tasks contained tests that could not pass, and the schema could not hold a value the
> spec asserted. Every change below traces to a numbered finding.

## Global Constraints

- **The table must NEVER source what protection EXISTS.** `_covering_qty` keeps reading the broker. *"A table that feeds the coverage number recreates 2026-08-17 with more ceremony."*
- **Every workflow table is a state machine** (CLAUDE.md). Transitions live in `specs/contracts.md` §1 and are applied **only** through `state/transition()`. This branch adds one transition and must add its `EDGES`/`KEYS` entry.
- **`gate/`, `stratgate/`, `calibration/` import no LLM code.** This branch touches none of them.
- **Never weaken a red test, re-record a golden fixture, or change an expected value to make something pass.** Where a task legitimately requires widening a test's *contract*, it says so explicitly and gives the reason. Anywhere else: STOP and ask.
- **Time comes from an injected `Clock`.** Ids come from an injected `id_factory` (`daily.py:53`) — never a bare `uuid4()`, or `sim-day` and replay stop being deterministic.
- **`specs/contracts.md` §8:** if a transition emits an event, its renderer lands in the **same commit**. `tests/test_slackkit.py` asserts every written kind has a `RENDERERS` entry.
- **`make test` must pass before every commit.**

## ⚠️ Gate before Task 2

**The 🔏 `specs/contracts.md` ruling has not landed.** Task 2 writes the DDL *and* the §1 state machine. Do not start it without the ruling. Tasks 1, 3, 5 and 6 are unaffected.

The `reconcile.py` contention gate from revision 1 is **gone** — the writer moved out of that file (C4/H1), and `fund-b1`'s guard moved into `protection.py` additively. No contention remains.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/fake_alpaca.py` | fake broker; `open_orders` must match the real contract | 1, 3 |
| `state/schema.sql` | the `protection` DDL | 2 |
| `state/migrations.py` | carries the table to databases that already exist | 2 |
| `state/transition.py` | the `live → closed` edge | 2 |
| `specs/contracts.md` | §1 state machine, §2 DDL — 🔏 | 2 |
| `orchestrator/broker.py` | `BrokerPort` — the widened `open_orders` contract | 3 |
| `market/source_alpaca.py` | real implementation of it | 3 |
| `state/protection.py` | **new** — `STOP_TYPES`, row writes, the live aggregate | 4 |
| `orchestrator/protection.py` | calls the writer; names the record in its alert | 4, 5 |
| `scripts/adopt_protection.py` | **new** — hand-run adoption | 6 |

---

### Task 1: The fake stops lying about `held`

`tests/fake_alpaca.py:open_orders()` includes `"held"`; `AlpacaSource.open_orders` queries `QueryOrderStatus.OPEN`, which measurably excludes held OTO children (2026-08-19, cited in `tests/test_live_smoke.py`). The fake's docstring claims to match.

**Files:**
- Modify: `tests/fake_alpaca.py:212-220`
- Test: `tests/test_fake_alpaca.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a fake whose `open_orders()` excludes `held`.

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

`FakeAlpaca({"NVDA": 180.0})` — `prices` is a **required positional**; revision 1 called `FakeAlpaca()` and failed with a `TypeError` instead of the assertion, which defeats the red gate (H4).

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py::test_open_orders_excludes_held_children_like_the_real_broker -v`
Expected: FAIL on the **assertion** — the list contains the held leg. If it fails with `TypeError`, the snippet is wrong, not the code.

- [ ] **Step 3: Remove `"held"` from the filter**

```python
                if o["status"] in ("new", "accepted", "partially_filled")]
```

Update the docstring to say what it now matches and why.

- [ ] **Step 4: Run the whole suite**

Run: `make test`
Expected: **PASS, with no other test affected.**

Revision 1 predicted failures in `tests/test_protection.py`. **That prediction was false** (H3) — that file uses its own hand-rolled `Broker`, not `FakeAlpaca`, and the suite stays green. Do not go looking for something to fix there.

If anything *does* fail, the fix is to fill the parent — the real sequence, since a leg only protects once the parent fills. Restoring `"held"` is the forbidden move. **If a failure cannot be fixed by filling the parent, stop and ask** — it means this change means more than the plan understood.

- [ ] **Step 5: Commit**

```bash
git add tests/fake_alpaca.py tests/test_fake_alpaca.py
git commit -m "fix: the fake broker hides held legs, exactly as the real one does"
```

---

### Task 2: The table, its state machine, and a migration that reaches production

**Gated on the 🔏 ruling.**

`state/db.py:connect()` runs `schema.sql` only when `tickets` is absent, so a new table reaches an existing database — including the droplet's — only through a migration. `state/migrations.py` handles `(table, column)` pairs and cannot express `CREATE TABLE`.

**Files:**
- Modify: `state/schema.sql`, `state/migrations.py`, `state/transition.py`, `specs/contracts.md`
- Test: `tests/test_migrations.py`, `tests/test_state.py`

**Interfaces:**
- Produces: table `protection`; `apply()` able to return `"0002_protection"`; `EDGES["protection"]` and `KEYS["protection"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_database_without_the_table_gains_it(tmp_path):
    """Stands in for the droplet, which predates this table. The real
    droplet evidence is a manual dry-run — see the plan's final section;
    this pins that apply() creates and is idempotent."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute("DROP TABLE protection")
    conn.commit()

    assert apply(conn) == ["0002_protection"]
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='protection'"
    ).fetchone() is not None
    assert apply(conn) == []


def test_the_protection_transition_is_registered():
    """CLAUDE.md: every workflow table is a state machine applied only through
    state/transition(). A status column with no EDGES entry is a table whose
    transitions nothing validates."""
    from state.transition import EDGES, KEYS
    assert EDGES["protection"] == {("live", "closed")}
    assert KEYS["protection"] == ("id",)
```

The docstring says what the test actually proves rather than claiming to be the droplet case (M5).

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_migrations.py -v -k protection`
Expected: FAIL — `no such table: protection`.

- [ ] **Step 3: Add the DDL to `state/schema.sql`**

```sql
-- protection: live -> closed (contracts.md §1). What the fund KNOWS protects a
-- position — never what EXISTS; _covering_qty reads the broker for that.
CREATE TABLE protection (
  id                TEXT PRIMARY KEY,          -- fund-minted, always
  symbol            TEXT NOT NULL,
  qty               INTEGER NOT NULL CHECK (qty > 0),
  stop_price        REAL NOT NULL CHECK (stop_price > 0),
  alpaca_order_id   TEXT UNIQUE,               -- the broker's UUID
  client_order_id   TEXT,                      -- the broker's client id, verbatim;
                                               -- Alpaca-minted for an OTO leg, and
                                               -- 'manual-…' for an adopted order
  provenance_kind   TEXT NOT NULL
                    CHECK (provenance_kind IN ('ticket','oto_leg','adopted')),
  provenance_ref    TEXT,                      -- ticket id when kind='ticket'
  broker_expires_at TEXT,                      -- Alpaca's ~90-day GTC cap
  observed_at       TEXT NOT NULL,             -- when the fund saw or placed this
  status            TEXT NOT NULL DEFAULT 'live'
                    CHECK (status IN ('live','closed')),
  created_at        TEXT NOT NULL
);
```

**Two states, not seven.** Revision 1 listed `cancelled`, `triggered`, `expired`, `superseded`, `pending` — none of which any branch-one writer can produce (C5). A state no writer can reach makes a test look like a guard while guarding nothing. `closed` means "no longer live at the broker, reason unknown"; distinguishing the reasons needs order history, which is branch two. Branch two adds its states by migration.

**`client_order_id` is a separate column** from `alpaca_order_id` (C1). `manual-protective-stop-nvda-2026-08-19` is a client id; the broker UUID is `5abc139f-…`.

**`provenance_kind` + `provenance_ref`, not one column** (C3, M3). A single column mixing an id with an enum token can carry no CHECK and cannot be grouped.

- [ ] **Step 4: Register the transition**

In `state/transition.py`, add `EDGES["protection"] = {("live", "closed")}` and `KEYS["protection"] = ("id",)`, following the existing entries' shape.

- [ ] **Step 5: Write the §1 machine and §2 DDL into `specs/contracts.md`** — 🔏, per the ruling. `specs/` is canonical; the ADR records *why*, contracts records *what*.

- [ ] **Step 6: Teach `migrations.py` to run statements**

`0001` untouched. Add:

```python
_0002_PROTECTION = """
CREATE TABLE IF NOT EXISTS protection (
  ...exactly the DDL from schema.sql...
);
"""


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None
```

and in `apply()`, after the `0001` block:

```python
    if not _has_table(conn, "protection"):
        conn.executescript(_0002_PROTECTION)
        conn.commit()
        applied.append("0002_protection")
```

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_migrations.py tests/test_state.py -v`
Expected: PASS, `0001`'s tests included.

- [ ] **Step 8: Commit**

```bash
git add state/schema.sql state/migrations.py state/transition.py specs/contracts.md tests/test_migrations.py tests/test_state.py
git commit -m "feat: the protection table, its one transition, and the migration that carries it"
```

---

### Task 3: `open_orders()` carries the fields a row needs

`open_orders()` returns `symbol, side, qty, type, status`. A row needs the broker's order id, its client id, its stop price and its expiry.

**Files:**
- Modify: `orchestrator/broker.py`, `market/source_alpaca.py:114-138`, `tests/fake_alpaca.py`
- Test: `tests/test_source_alpaca_helpers.py`, `tests/test_fake_alpaca.py`

**Interfaces:**
- Consumes: nothing. (Revision 1 claimed a dependency on Task 1 that does not exist — this task's test fills the parent, so it passes either way.)
- Produces: `open_orders()` dicts gain `id: str`, `client_order_id: str | None`, `stop_price: str | None`, `expires_at: str | None`.

- [ ] **Step 1: Write the failing test**

```python
def test_open_orders_carries_the_fields_a_protection_row_needs():
    """A row references the broker's UUID, records the stop price and the
    ~90-day GTC expiry, and stores the client id verbatim — an OTO leg's is
    Alpaca-minted and unrelated to the ticket's."""
    broker = FakeAlpaca({"NVDA": 180.0})
    broker.place_order({"client_order_id": "t1", "symbol": "NVDA",
                        "side": "buy", "qty": 80,
                        "stop_loss_stop_price": "215.0"})
    broker.tick()                       # fill the parent; the leg activates
    leg = [o for o in broker.open_orders() if o["type"] == "stop"][0]
    assert leg["id"], "no broker order id"
    assert leg["client_order_id"] == "t1-stop"
    assert leg["stop_price"] == "215.0"
    assert "expires_at" in leg
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py -v -k protection_row`
Expected: FAIL with `KeyError: 'id'` — not a `TypeError`.

- [ ] **Step 3: Widen the port contract**

In `orchestrator/broker.py`, extend `open_orders`'s docstring to name all eight keys. **Restate the prohibition unchanged** — this port never places and must never grow a method that does. Read fields are not that.

- [ ] **Step 4: Implement in `market/source_alpaca.py`**

```python
        return [{"symbol": o.symbol, "side": _enum_str(o.side),
                 "qty": _enum_str(o.qty), "type": _enum_str(o.order_type),
                 "status": _enum_str(o.status), "id": _enum_str(o.id),
                 "client_order_id": o.client_order_id,
                 "stop_price": _enum_str(o.stop_price) if o.stop_price is not None else None,
                 "expires_at": _enum_str(o.expires_at) if o.expires_at is not None else None}
                for o in orders]
```

Plain attribute access on `expires_at`, not `getattr(o, "expires_at", None)`. alpaca-py 0.44's `Order` has the field (verified in `.venv`), and a silent `None` on a future rename would break the November expiry watch invisibly — the one column that watch depends on.

- [ ] **Step 5: Implement in `tests/fake_alpaca.py`**

`open_orders()` returns the same eight keys. The leg dict (`fake_alpaca.py:99-117`) currently sets `"stop_loss_stop_price": None` and has no `stop_price`; give it `"stop_price": args["stop_loss_stop_price"]` at creation — read from the **request args**, not from the leg's own null field (L1).

- [ ] **Step 6: Widen the existing helper test — this is a contract change, not a weakened assertion**

`tests/test_source_alpaca_helpers.py` will fail:

```
AttributeError: '_Clock' object has no attribute 'id'
```

Its `_Clock` stub is `self.__dict__.update(attrs)`, so it needs `id`, `client_order_id`, `stop_price` and `expires_at` added; and its exact-dict assertion needs the four new keys.

**This is the one place in this plan where an existing expectation legitimately changes**, and only because the port's contract changed in Step 3. Widening a stub and its expected dict to match a deliberately widened interface is not the forbidden move. Anywhere else, stop and ask. (H2 — revision 1 said "Expected: PASS" here, which was false and would have stopped an implementer cold.)

- [ ] **Step 7: Run the suite**

Run: `make test`
Expected: PASS. `_covering_qty` reads only `symbol`, `side`, `type`, `qty` and ignores unknown keys.

- [ ] **Step 8: Commit**

```bash
git add orchestrator/broker.py market/source_alpaca.py tests/fake_alpaca.py tests/test_fake_alpaca.py tests/test_source_alpaca_helpers.py
git commit -m "feat: open_orders carries the ids, stop price and expiry a record needs"
```

---

### Task 4: `protection.py` records what it already reads

**The writer is `orchestrator/protection.py`, not the reconcile pass** (C4/H1). It already calls `open_orders()`; runs unconditionally; is deliberately **not** a checkpointed stage; handles `broker is None`; catches every broker exception into an alert; and does the 3-second re-read a lagging leg needs. The reconcile placement would have aborted the trading day on a transient broker read failure, recorded nothing on a resumed day, and never run at all on a day with no submitted orders.

**Files:**
- Create: `state/protection.py`, `tests/test_state_protection.py`
- Modify: `orchestrator/protection.py`

**Interfaces:**
- Consumes: Task 2's table; Task 3's widened `open_orders()`.
- Produces:
  - `state.protection.STOP_TYPES = ("stop", "stop_limit", "trailing_stop")` — **moved here**, not promoted in `orchestrator/protection.py`. Nothing in `state/` imports `orchestrator/` today and `orchestrator/reconcile.py` would import this module: promoting it the other way is a real import cycle (H5). `orchestrator/protection.py` imports it from here.
  - `state.protection.sync_live(conn, orders, *, now_iso, id_factory) -> tuple[list[str], list[str]]` — `(written, closed)`.
  - `state.protection.live_for(conn, symbol) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
def _leg(**kw):
    base = {"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
            "status": "new", "id": "alp-0002", "client_order_id": "t1-stop",
            "stop_price": "215.0", "expires_at": "2026-11-17T21:00:00Z"}
    return {**base, **kw}


def test_a_live_stop_becomes_a_row(fund_db, sim_clock):
    written, closed = sync_live(fund_db, [_leg()], now_iso=iso(sim_clock.now()),
                                id_factory=lambda: "p1")
    assert (written, closed) == (["p1"], [])
    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert (row["symbol"], row["qty"], row["stop_price"]) == ("NVDA", 80, 215.0)
    assert row["alpaca_order_id"] == "alp-0002"
    assert row["client_order_id"] == "t1-stop"
    assert row["provenance_kind"] == "oto_leg"
    assert row["broker_expires_at"] == "2026-11-17T21:00:00Z"
    assert row["status"] == "live"


def test_running_twice_writes_one_row(fund_db, sim_clock):
    now = iso(sim_clock.now())
    sync_live(fund_db, [_leg()], now_iso=now, id_factory=lambda: "p1")
    assert sync_live(fund_db, [_leg()], now_iso=now,
                     id_factory=lambda: "p2") == ([], [])
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_an_order_that_vanished_is_closed(fund_db, sim_clock):
    """The pass holds the full live list, so absence is decidable. Without
    this, a row stays 'live' forever after its stop dies and live_for()
    reports protection the broker does not have."""
    now = iso(sim_clock.now())
    sync_live(fund_db, [_leg()], now_iso=now, id_factory=lambda: "p1")

    assert sync_live(fund_db, [], now_iso=now, id_factory=lambda: "p2") == ([], ["p1"])
    assert fund_db.execute(
        "SELECT status FROM protection").fetchone()["status"] == "closed"
    assert live_for(fund_db, "NVDA") == []


def test_a_non_protective_order_is_not_recorded(fund_db, sim_clock):
    """A sell LIMIT is a take-profit: it caps the upside and leaves the
    downside exposed. Same predicate as protection.py's STOP_TYPES."""
    assert sync_live(fund_db, [_leg(type="limit", stop_price=None)],
                     now_iso=iso(sim_clock.now()),
                     id_factory=lambda: "p1") == ([], [])


def test_an_unreadable_order_is_skipped_not_guessed(fund_db, sim_clock):
    """Invariant 4. protection.py has eight tests for this class of input on
    its own reads; this is the same bar."""
    for bad in ({"qty": "eighty"}, {"qty": None}, {"stop_price": None},
                {"id": None}):
        assert sync_live(fund_db, [_leg(**bad)], now_iso=iso(sim_clock.now()),
                         id_factory=lambda: "p1") == ([], [])
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 0


def test_the_aggregate_counts_live_and_nothing_else(fund_db, sim_clock):
    """A POSITIVE predicate, never a NOT IN list, so a state added later
    cannot join the aggregate by omission."""
    now = iso(sim_clock.now())
    for i, status in enumerate(("live", "closed")):
        fund_db.execute(
            "INSERT INTO protection (id, symbol, qty, stop_price,"
            " alpaca_order_id, provenance_kind, observed_at, status, created_at)"
            " VALUES (?, 'NVDA', 40, 215.0, ?, 'adopted', ?, ?, ?)",
            (f"p{i}", f"alp-{i}", now, status, now))
    fund_db.commit()
    assert [r["status"] for r in live_for(fund_db, "NVDA")] == ["live"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_state_protection.py -v`
Expected: FAIL — `ModuleNotFoundError: state.protection`.

- [ ] **Step 3: Write `state/protection.py`**

Pure Python + SQLite. `sync_live` filters on `STOP_TYPES` and a closing side; coerces broker numerics with the **broker-side** coercer — reuse `protection.py:_qty`'s logic, moved here alongside `STOP_TYPES`, *not* `gate/tickets.py:_as_share_count`, whose docstring says it coerces adversarial agent input and that the two may legitimately diverge (M1).

Any order missing a readable qty, stop price or id is **skipped, never guessed** (invariant 4). Rows are written with `provenance_kind='oto_leg'` — there is no path from a broker order to a ticket (C3), and claiming `'ticket'` would be a fabrication. Ids come from the injected `id_factory` (M2). Closing goes through `state.transition.try_transition`, never a bare UPDATE.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_state_protection.py -v`
Expected: PASS.

- [ ] **Step 5: Call it from `assert_positions_protected`**

In `orchestrator/protection.py`, inside the existing `try` that wraps `read_orders()`, call `sync_live` with the order list already in hand. **Do not add a try/except** — the enclosing one already converts a broker failure into an `UNVERIFIED` alert, which is the correct behavior and the reason this module is the writer.

Place it so it runs on the **first** read. The re-read path after the nap re-runs it harmlessly: `sync_live` is idempotent by `alpaca_order_id`.

- [ ] **Step 6: Run the suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add state/protection.py tests/test_state_protection.py orchestrator/protection.py
git commit -m "feat: the fund records what is protecting each position"
```

---

### Task 5: the alert names the record, and labels it as belief

**Files:**
- Modify: `orchestrator/protection.py:_evaluate`
- Test: `tests/test_protection.py`

**Interfaces:**
- Consumes: `state.protection.live_for`.
- Produces: no new interface. Alert text only.

**`_promised_stop`'s tri-state is untouched.** A price, `None` for a charter-sanctioned stopless buy, `_UNKNOWN` for no record at all. The table cannot express the `None` case, which is why intent stays on the decision.

- [ ] **Step 1: Write the failing test**

```python
def test_the_shortfall_alert_names_the_recorded_protection(fund_db, sim_clock):
    """A shortfall was reported against an entry-time promise with no quantity
    that no later event updates. The record layer says what the fund believes
    is protecting the position — labelled as belief, because this branch is
    reached precisely when the broker disagrees."""
    now = iso(sim_clock.now())
    _seed_filled_buy(fund_db, ticker="NVDA", stop_price=215.0, now=now)
    fund_db.execute(
        "INSERT INTO protection (id, symbol, qty, stop_price, alpaca_order_id,"
        " provenance_kind, observed_at, status, created_at)"
        " VALUES ('p1','NVDA',40,215.0,'alp-9','adopted',?,'live',?)",
        (now, now))
    fund_db.commit()

    alerts = _evaluate(fund_db, [{"symbol": "NVDA", "qty": "80",
                                  "side": "long"}], [])
    assert len(alerts) == 1
    assert "40" in alerts[0]
    assert "fund's record" in alerts[0], (
        "record-layer text must be labelled as belief, not read as fact")
```

`_seed_filled_buy` is **required**, and revision 1 omitted it (C2). Without a filled buy joined to a ticket, `_promised_stop` returns `_UNKNOWN` and `_evaluate` takes the `_UNKNOWN` branch, where `"40"` can never appear. Write the helper if `tests/test_protection.py` has no equivalent.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_protection.py -v -k names_the_recorded`
Expected: FAIL — the alert names the promised price only.

- [ ] **Step 3: Append the record to the `promised is not None` branch only**

Not the `_UNKNOWN` branch. That branch means the fund has no record of opening the position; printing the fund's belief about protection there states a belief it has no standing to hold.

Label it. `"… — the fund's record says 40 @ 215 (adopted), which the broker does not confirm"` reads correctly at 16:05 to someone who was not in this conversation. An unlabelled `[record: 40 @ 215]` invites acting on the 40 (M6). **`covered` stays broker-sourced** — this is text, never arithmetic.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_protection.py -v`
Expected: PASS, tri-state tests included. Those three — `test_a_position_that_was_never_promised_a_stop_is_silent`, `test_a_position_the_fund_has_no_record_of_alerts`, `test_a_promised_stop_that_is_gone_alerts` — already cover the spec's tri-state regression item (L4); no new test is needed for it.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/protection.py tests/test_protection.py
git commit -m "feat: a shortfall alert names what the fund believes protects the position"
```

---

### Task 6: The adoption script

**Files:**
- Create: `scripts/adopt_protection.py`, `tests/test_adopt_protection.py`

**Interfaces:**
- Consumes: `state.protection.sync_live` is **not** reusable here — it writes `provenance_kind='oto_leg'`. Adoption writes its own row with `'adopted'`. (Revision 1's interface claim was broken: `record_live` had no way to express provenance from outside — C3.)
- Produces: a hand-run entry point. Nothing imports it.

⚠️ **Do not run against the live NVDA stop until `fund-07`'s review concludes.** That order is under review for cancellation or resizing. The script touches the fund's record, never the order.

- [ ] **Step 1: Write the failing tests**

```python
def test_adoption_records_an_observation_not_a_promise(fund_db, sim_clock):
    """The fund did not place this order and does not control it. observed_at
    says when it was seen; the broker stays the authority on existence."""
    broker = FakeAlpaca({"NVDA": 180.0})
    broker.orders["manual-protective-stop-nvda-2026-08-19"] = {
        "id": "5abc139f-4817-4a34-aedd-f2ca28203c5c",
        "client_order_id": "manual-protective-stop-nvda-2026-08-19",
        "symbol": "NVDA", "side": "sell", "qty": 80, "status": "new",
        "order_type": "stop", "stop_price": "215.0", "filled_qty": 0,
        "filled_avg_price": None, "order_class": "",
        "stop_loss_stop_price": None, "stop_loss_limit_price": None,
        "take_profit_limit_price": None}

    adopt(fund_db, broker=broker, now_iso=iso(sim_clock.now()),
          id_factory=lambda: "p1")

    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert row["provenance_kind"] == "adopted"
    assert row["observed_at"] == iso(sim_clock.now())
    assert row["alpaca_order_id"] == "5abc139f-4817-4a34-aedd-f2ca28203c5c"
    assert row["client_order_id"] == "manual-protective-stop-nvda-2026-08-19"


def test_adoption_twice_writes_one_row(fund_db, sim_clock):
    ...same setup...
    adopt(fund_db, broker=broker, now_iso=now, id_factory=lambda: "p1")
    adopt(fund_db, broker=broker, now_iso=now, id_factory=lambda: "p2")
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1
```

**The two ids go in two different columns** (C1). `manual-protective-stop-nvda-2026-08-19` is the **client** id; `5abc139f-…` is the broker UUID (`PROGRESS.md:123-124`). Revision 1 asserted the human string as `alpaca_order_id`, which could only be made green by making the fake return it as `id` — a fixture agreeing with our code while both disagree with Alpaca.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_adopt_protection.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the script**

Reads `broker.open_orders()`, writes rows with `provenance_kind='adopted'`, adopting **whatever protection exists when it runs** — never a hardcoded order id. Idempotence is asserted in code, not left to `alpaca_order_id`'s UNIQUE constraint: SQLite permits unlimited NULLs in a UNIQUE column, so that guarantee is only as good as the id being present (M4).

Prints what it wrote and what it skipped. It is hand-run against a live record; silence is the wrong default.

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

- **The amend capability** — the gate's second verb, `pending`, the recorder's write path, `replace_order_by_id`. Branch two, ADR-0003.
- **Distinguishing why a stop closed** (`cancelled` / `triggered` / `expired`). Needs order history. Branch two.
- **Alerting on `broker_expires_at`.** The column lands; the watch does not. The live NVDA stop dies 2026-11-17 and nothing watches for it — still true after this branch. When built it must **read the column**, never compute from a placement date.
- **Invariant 5's rewording.** Branch two. This branch writes no `client_order_id` of its own.
- **`_promised_stop`'s multi-buy semantics**, and **the eight ungated mutation verbs.** Separate, separately owned.

## Open, and deliberately not decided here

**What Task 5 renders when `live_for` returns zero rows, or several** (L5). Multiple rows is realistic — the aggregate is a sum across orders by design. Decide it while writing Step 3 rather than discovering it in production; the shortfall path is where it shows.

**No index on `protection(symbol, status)`** (L6). Fine while `sync_live` closes stale rows, since the live set stays small. Revisit if that ever stops being true.

## Verification before this branch is called done

`make test` is necessary and not sufficient — this runs unattended at 09:35 with nobody watching.

- `make sim-day` — a full simulated day through the real gate and DB.
- **Migration evidence no diff can show.** Take a copy of the droplet database, run `apply()` against it, and record before/after: table absent, then present, then `apply()` again returning `[]`. The suite proves the migration works on a database built by `schema.sql`, which is precisely the case the droplet is not.
