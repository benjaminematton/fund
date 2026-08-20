# The Protection Record (branch one) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fund a stored record of what protects each position, so protection stops being re-derived by join from order history.

**Architecture:** One new SQLite table, `protection`, written by a reconcile-style pass that reads the broker's live orders. Three layers stay separate and only the middle one is new: **intent** on the decision (unchanged), **record** in this table (new), **existence** at the broker (unchanged). The table is never the source of what protection exists.

**Tech Stack:** Python 3.12, SQLite, pytest. No new dependencies.

## Global Constraints

- **The table must NEVER source what protection EXISTS.** `orchestrator/protection.py:_covering_qty` keeps reading the broker. Verbatim from the spec: *"A table that feeds the coverage number recreates 2026-08-17 with more ceremony."*
- **`gate/`, `stratgate/`, `calibration/` import no LLM code.** Enforced by `scripts/check_purity.py` in `make test`. This branch touches none of them.
- **Never weaken a red test, re-record a golden fixture, or change an expected value to make something pass.** STOP and ask. This binds Task 1 specifically, which will break other tests.
- **Time comes from an injected `Clock`.** Never `datetime.now()` or `time.sleep()` in business logic.
- **`specs/contracts.md` §8:** if a state transition emits an event, its renderer lands in the **same commit**. `tests/test_slackkit.py` asserts every written kind has a `RENDERERS` entry — a missing one is a red test. Every kind carries populated `text`.
- **`specs/contracts.md` §2:** `NOT NULL` on vocabulary columns is load-bearing. A NULL drops silently out of `GROUP BY` and every `=`.
- **`make test` must pass before every commit.**

## ⚠️ Two gates before starting

1. **The 🔏 `specs/contracts.md` DDL ruling has not landed.** Task 2 writes the DDL. Do not start Task 2 without it.
2. **`orchestrator/reconcile.py` is contested** with `fund-b1`'s issue #5. Task 4 lives there. Sequence with Benjamin first.

Tasks 1, 3, 5 and 6 are unaffected by both gates.

## Amendment to the spec, found while planning

The spec assumed the reconcile pass could build a protection row from `open_orders()`. It cannot: `market/source_alpaca.py:open_orders` returns only `symbol, side, qty, type, status`. A protection row needs `alpaca_order_id`, `stop_price`, and `broker_expires_at`. **The port has to widen first** — that is Task 3, which the spec did not contain. Adding read fields does not violate `BrokerPort`'s prohibition, which is on methods that *place*.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/fake_alpaca.py` | fake broker; `open_orders` must match the real contract | 1, 3 |
| `state/schema.sql` | the `protection` DDL, for fresh databases | 2 |
| `state/migrations.py` | carries the table to databases that already exist | 2 |
| `orchestrator/broker.py` | `BrokerPort` protocol — the widened `open_orders` contract | 3 |
| `market/source_alpaca.py` | real implementation of the widened contract | 3 |
| `orchestrator/protection.py` | `STOP_TYPES` promoted; record-layer read in the alert text | 3, 5 |
| `state/protection.py` | **new** — row writes and the live-aggregate query | 4 |
| `orchestrator/reconcile.py` | the pass that calls it | 4 |
| `scripts/adopt_protection.py` | **new** — hand-run adoption | 6 |

---

### Task 1: The fake stops lying about `held`

`tests/fake_alpaca.py:open_orders()` includes `"held"`; the real `AlpacaSource.open_orders` queries `QueryOrderStatus.OPEN`, which measurably excludes held OTO children (2026-08-19, recorded in `tests/test_live_smoke.py`). The fake's docstring claims to match. Latent today; Task 4 makes it active.

**Files:**
- Modify: `tests/fake_alpaca.py:212-220`
- Test: `tests/test_fake_alpaca.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a fake whose `open_orders()` excludes `held`. Tasks 4 and 5 depend on this being true.

- [ ] **Step 1: Write the failing test**

```python
def test_open_orders_excludes_held_children_like_the_real_broker():
    """AlpacaSource.open_orders queries QueryOrderStatus.OPEN, and a held OTO
    child is NOT returned by it — measured 2026-08-19, cited in
    tests/test_live_smoke.py. A fake that returns held legs lets a
    protection-row writer pass offline and write nothing in production, which
    is the 2026-08-17 defect shape."""
    broker = FakeAlpaca()
    broker.place_order({"client_order_id": "t1", "symbol": "NVDA",
                        "side": "buy", "qty": 80,
                        "stop_loss_stop_price": "215.0"})
    assert broker.orders["t1-stop"]["status"] == "held", "setup: leg not held"
    assert [o for o in broker.open_orders() if o["type"] == "stop"] == [], (
        "the fake returns a held stop leg; the real broker does not")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py::test_open_orders_excludes_held_children_like_the_real_broker -v`
Expected: FAIL — the list contains the held leg.

- [ ] **Step 3: Remove `"held"` from the filter**

In `tests/fake_alpaca.py:open_orders()`:

```python
                if o["status"] in ("new", "accepted", "partially_filled")]
```

Update the docstring to say what it now matches and why: `QueryOrderStatus.OPEN` excludes held children, so a held leg is invisible here exactly as it is in production.

- [ ] **Step 4: Run the new test**

Run: `pytest tests/test_fake_alpaca.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and expect fallout**

Run: `make test`

**Expect failures in `tests/test_protection.py`.** Tests that placed an OTO and asserted coverage without filling the parent were relying on the fake's held leg being visible.

**The fix is to fill the parent — which is the real sequence — never to restore `"held"`.** A protective leg only protects once the parent fills; that is the fact this change encodes. Adjusting a test's *setup* to match reality is correct. Changing the assertion, or reverting the filter, is the forbidden move under Global Constraints.

If any failure cannot be fixed by filling the parent, **stop and ask** — it means the change means something more than this plan understood.

- [ ] **Step 6: Commit**

```bash
git add tests/fake_alpaca.py tests/test_fake_alpaca.py tests/test_protection.py
git commit -m "fix: the fake broker hides held legs, exactly as the real one does"
```

---

### Task 2: The `protection` table, and a migration that reaches production

**Gated on the 🔏 `contracts.md` ruling.**

`state/db.py:connect()` runs `schema.sql` only when `tickets` is absent. Adding a table to that file creates it in every test and **never on the droplet**. `state/migrations.py` handles only `(table, column)` pairs and cannot express `CREATE TABLE`.

**Files:**
- Modify: `state/schema.sql`, `state/migrations.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `protection`; `state.migrations.apply(conn) -> list[str]` now able to return `"0002_protection"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_live_database_gains_the_protection_table(tmp_path):
    """The droplet case. schema.sql only runs on a database this code has
    never created, so a new table reaches an existing one only here."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute("DROP TABLE protection")
    conn.commit()

    assert apply(conn) == ["0002_protection"]
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='protection'"
    ).fetchone() is not None


def test_applying_twice_is_idempotent(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute("DROP TABLE protection")
    conn.commit()

    assert apply(conn) == ["0002_protection"]
    assert apply(conn) == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_migrations.py -v -k protection`
Expected: FAIL — `no such table: protection` on the DROP.

- [ ] **Step 3: Add the DDL to `state/schema.sql`**

```sql
CREATE TABLE protection (
  id                TEXT PRIMARY KEY,          -- fund-minted, always
  symbol            TEXT NOT NULL,
  qty               INTEGER NOT NULL CHECK (qty > 0),
  stop_price        REAL NOT NULL CHECK (stop_price > 0),
  alpaca_order_id   TEXT UNIQUE,               -- the broker's reference
  provenance        TEXT NOT NULL,             -- a ticket id, or 'adopted'
  broker_expires_at TEXT,                      -- Alpaca's ~90-day GTC cap
  observed_at       TEXT NOT NULL,             -- when the fund saw or placed this
  status            TEXT NOT NULL DEFAULT 'live'
                    CHECK (status IN ('live','superseded','cancelled',
                                      'triggered','expired','pending','lapsed')),
  created_at        TEXT NOT NULL
);
```

The CHECK lists three states this branch never writes (`superseded`, `pending`, `lapsed`). SQLite cannot alter a CHECK without rebuilding the table, so omitting them buys a rebuild later. Comment that in the file.

- [ ] **Step 4: Teach `migrations.py` to run statements**

Keep `0001` untouched. Add alongside it:

```python
_0002_PROTECTION = """
CREATE TABLE IF NOT EXISTS protection (
  ... exactly the DDL from schema.sql ...
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

Additive and idempotent, like `0001`. Nothing drops or rewrites.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS, including `0001`'s existing tests.

- [ ] **Step 6: Commit**

```bash
git add state/schema.sql state/migrations.py tests/test_migrations.py
git commit -m "feat: the protection table, and the migration that carries it to a live database"
```

---

### Task 3: `open_orders()` carries the fields a protection row needs

**The spec amendment.** `open_orders()` returns `symbol, side, qty, type, status`. A row needs the broker's order id, its stop price, and its expiry.

**Files:**
- Modify: `orchestrator/broker.py`, `market/source_alpaca.py:114-138`, `tests/fake_alpaca.py:212-220`, `orchestrator/protection.py`
- Test: `tests/test_source_alpaca_helpers.py`, `tests/test_fake_alpaca.py`

**Interfaces:**
- Consumes: Task 1's fake.
- Produces: `open_orders()` dicts gain `id: str`, `stop_price: str | None`, `expires_at: str | None`. `orchestrator.protection.STOP_TYPES` is public (was `_STOP_TYPES`), unchanged in value: `("stop", "stop_limit", "trailing_stop")`.

- [ ] **Step 1: Write the failing test**

```python
def test_open_orders_carries_the_fields_a_protection_row_needs():
    """A protection row references the broker's order id and records the stop
    price and the ~90-day GTC expiry. None of the three were returned before."""
    broker = FakeAlpaca()
    broker.place_order({"client_order_id": "t1", "symbol": "NVDA",
                        "side": "buy", "qty": 80,
                        "stop_loss_stop_price": "215.0"})
    broker.tick()                       # fill the parent; the leg activates
    leg = [o for o in broker.open_orders() if o["type"] == "stop"][0]
    assert leg["id"], "no broker order id"
    assert leg["stop_price"] == "215.0"
    assert "expires_at" in leg
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_fake_alpaca.py -v -k protection_row`
Expected: FAIL — `KeyError: 'id'`.

- [ ] **Step 3: Widen the port contract**

In `orchestrator/broker.py`, extend `open_orders`'s docstring: each dict carries `symbol`, `side`, `qty`, `type`, `status`, `id`, `stop_price`, `expires_at`. **Restate the prohibition unchanged** — this port never places and must never grow a method that does. Adding read fields is not that.

- [ ] **Step 4: Implement in both adapters**

`market/source_alpaca.py`:

```python
        return [{"symbol": o.symbol, "side": _enum_str(o.side),
                 "qty": _enum_str(o.qty), "type": _enum_str(o.order_type),
                 "status": _enum_str(o.status), "id": _enum_str(o.id),
                 "stop_price": _enum_str(o.stop_price) if o.stop_price is not None else None,
                 "expires_at": _enum_str(o.expires_at) if getattr(o, "expires_at", None) else None}
                for o in orders]
```

`tests/fake_alpaca.py:open_orders()` returns the same keys, reading `o["id"]` and `o.get("stop_price")`. The fake's leg dict currently has no `stop_price`; set it from `stop_loss_stop_price` at leg creation (`tests/fake_alpaca.py:99-116`).

- [ ] **Step 5: Promote `_STOP_TYPES`**

In `orchestrator/protection.py`, rename `_STOP_TYPES` to `STOP_TYPES` and update its two uses. Task 4 imports it rather than restating the predicate — a duplicated "what counts as protection" predicate drifts, and drift here is invisible.

- [ ] **Step 6: Run the tests**

Run: `make test`
Expected: PASS. `_covering_qty` ignores unknown keys, so widening the dict does not affect it.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/broker.py market/source_alpaca.py tests/fake_alpaca.py orchestrator/protection.py tests/test_fake_alpaca.py tests/test_source_alpaca_helpers.py
git commit -m "feat: open_orders carries the order id, stop price and expiry"
```

---

### Task 4: The reconcile pass writes protection rows

**Gated: `orchestrator/reconcile.py` is contested with issue #5.**

**Files:**
- Create: `state/protection.py`, `tests/test_state_protection.py`
- Modify: `orchestrator/reconcile.py`, `orchestrator/daily.py:475` area

**Interfaces:**
- Consumes: Task 2's table; Task 3's widened `open_orders()` and `STOP_TYPES`.
- Produces:
  - `state.protection.record_live(conn, orders: list[dict], *, now_iso: str) -> list[str]` — writes a row per live protective order not already recorded, keyed on `alpaca_order_id`. Returns ids written.
  - `state.protection.live_for(conn, symbol: str) -> list[dict]` — rows with `status = 'live'` for a symbol.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_live_stop_becomes_a_row(fund_db, sim_clock):
    orders = [{"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
               "status": "new", "id": "alp-0002", "stop_price": "215.0",
               "expires_at": "2026-11-17T21:00:00Z"}]
    written = record_live(fund_db, orders, now_iso=iso(sim_clock.now()))

    assert len(written) == 1
    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert row["symbol"] == "NVDA"
    assert row["qty"] == 80
    assert row["stop_price"] == 215.0
    assert row["alpaca_order_id"] == "alp-0002"
    assert row["status"] == "live"
    assert row["broker_expires_at"] == "2026-11-17T21:00:00Z"


def test_running_twice_writes_one_row(fund_db, sim_clock):
    orders = [{"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
               "status": "new", "id": "alp-0002", "stop_price": "215.0",
               "expires_at": None}]
    now = iso(sim_clock.now())
    record_live(fund_db, orders, now_iso=now)
    assert record_live(fund_db, orders, now_iso=now) == []
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_non_protective_order_is_not_recorded(fund_db, sim_clock):
    """A sell LIMIT is a take-profit: it caps the upside and leaves the
    downside exposed. Same reasoning as protection.py's STOP_TYPES."""
    orders = [{"symbol": "NVDA", "side": "sell", "qty": "80", "type": "limit",
               "status": "new", "id": "alp-0003", "stop_price": None,
               "expires_at": None}]
    assert record_live(fund_db, orders, now_iso=iso(sim_clock.now())) == []


def test_the_aggregate_counts_live_and_nothing_else(fund_db, sim_clock):
    """A POSITIVE predicate, never a NOT IN list — a state added later must not
    be able to join the aggregate by omission. This is the missed-filter class
    and the failure is invisible."""
    now = iso(sim_clock.now())
    for i, status in enumerate(("live", "superseded", "cancelled", "triggered",
                                "expired", "pending", "lapsed")):
        fund_db.execute(
            "INSERT INTO protection (id, symbol, qty, stop_price,"
            " alpaca_order_id, provenance, observed_at, status, created_at)"
            " VALUES (?, 'NVDA', 40, 215.0, ?, 'adopted', ?, ?, ?)",
            (f"p{i}", f"alp-{i}", now, status, now))
    fund_db.commit()

    live = live_for(fund_db, "NVDA")
    assert [r["status"] for r in live] == ["live"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_state_protection.py -v`
Expected: FAIL — `ModuleNotFoundError: state.protection`.

- [ ] **Step 3: Write `state/protection.py`**

Pure Python + SQLite, no LLM imports. `record_live` filters on `STOP_TYPES` and a closing side, coerces qty and price the way `gate/tickets.py:_as_share_count` and `_as_price` do (broker numerics arrive as strings), mints `id` with `uuid4()`, sets `provenance='adopted'` only from Task 6 — here it is the ticket id when the order resolves to one, else `'adopted'`.

**An unreadable order is skipped, never guessed** (invariant 4). `live_for` is a single `WHERE status = 'live' AND symbol = ?`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_state_protection.py -v`
Expected: PASS.

- [ ] **Step 5: Call it from the reconcile stage**

In `orchestrator/reconcile.py`, after the fill-poll settles, call `record_live` with `broker.open_orders()`. `open_orders()` **raises** rather than returning empty — let it raise; the caller turns that into an alert, exactly as `protection.py` does. A swallowed failure here writes no rows and looks identical to "nothing to protect."

- [ ] **Step 6: Run the whole suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add state/protection.py tests/test_state_protection.py orchestrator/reconcile.py orchestrator/daily.py
git commit -m "feat: the fund records what is protecting each position"
```

---

### Task 5: `protection.py` names the record in its alert

**Files:**
- Modify: `orchestrator/protection.py:_evaluate`
- Test: `tests/test_protection.py`

**Interfaces:**
- Consumes: `state.protection.live_for`.
- Produces: no new interface. Alert text only.

**`_promised_stop`'s tri-state is untouched.** A price, `None` for a charter-sanctioned stopless buy, `_UNKNOWN` for no record at all. The table cannot express the `None` case — there is no protective order for that row to describe — which is why intent stays on the decision.

- [ ] **Step 1: Write the failing test**

```python
def test_the_shortfall_alert_names_the_recorded_protection(fund_db, sim_clock):
    """Before this, a shortfall was reported against an entry-time promise with
    no quantity, which no later event updates. The record layer says what is
    actually protecting the position and where it came from."""
    now = iso(sim_clock.now())
    fund_db.execute(
        "INSERT INTO protection (id, symbol, qty, stop_price, alpaca_order_id,"
        " provenance, observed_at, status, created_at)"
        " VALUES ('p1', 'NVDA', 40, 215.0, 'alp-9', 'adopted', ?, 'live', ?)",
        (now, now))
    fund_db.commit()

    alerts = _evaluate(fund_db,
                       [{"symbol": "NVDA", "qty": "80", "side": "long"}],
                       [])
    assert len(alerts) == 1
    assert "40" in alerts[0], f"the recorded cover is not named: {alerts[0]}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_protection.py -v -k names_the_recorded`
Expected: FAIL — the alert names only the promised price.

- [ ] **Step 3: Read the record layer in the alert branch**

In `_evaluate`'s `promised is not None` branch, append what `live_for` holds for the symbol. **Do not let it touch `covered`** — that stays broker-sourced. This is text, not arithmetic.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_protection.py -v`
Expected: PASS, including the untouched tri-state tests.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/protection.py tests/test_protection.py
git commit -m "feat: a shortfall alert names what the fund believes is protecting the position"
```

---

### Task 6: The adoption script

**Files:**
- Create: `scripts/adopt_protection.py`, `tests/test_adopt_protection.py`

**Interfaces:**
- Consumes: `state.protection.record_live`, Task 3's `open_orders()`.
- Produces: a hand-run entry point. Nothing imports it.

⚠️ **Do not run this against the live NVDA stop until `fund-07`'s review concludes.** That order is under review for cancellation or resizing. The script touches the fund's *record*, never the order.

- [ ] **Step 1: Write the failing tests**

```python
def test_adoption_records_an_observation_not_a_promise(fund_db, sim_clock):
    """The fund did not place this order and does not control it. observed_at
    says when it was seen; the broker stays the authority on whether it still
    exists."""
    broker = _BrokerWithManualStop()
    adopt(fund_db, broker=broker, now_iso=iso(sim_clock.now()))

    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert row["provenance"] == "adopted"
    assert row["observed_at"] == iso(sim_clock.now())
    assert row["alpaca_order_id"] == "manual-protective-stop-nvda-2026-08-19", (
        "the human-placed id was normalised; its irregularity is the marker")


def test_adoption_twice_writes_one_row(fund_db, sim_clock):
    broker = _BrokerWithManualStop()
    now = iso(sim_clock.now())
    adopt(fund_db, broker=broker, now_iso=now)
    adopt(fund_db, broker=broker, now_iso=now)
    assert fund_db.execute("SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_adopt_protection.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the script**

It reads `broker.open_orders()`, writes rows with `provenance='adopted'`, and adopts **whatever protection exists when it runs** — never a hardcoded order id. Idempotence comes free from `alpaca_order_id`'s UNIQUE constraint, but assert it in the code rather than relying on the exception. The id is stored verbatim: `manual-protective-stop-nvda-2026-08-19` is deliberately not a UUID, and the irregularity is what marks it as human-placed.

Print what it wrote and what it skipped. It is hand-run against a live record; silence is the wrong default.

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
- **Alerting on `broker_expires_at`.** The column lands; the watch does not. The live NVDA stop dies 2026-11-17 and nothing watches for it — still true after this branch. When built, it must **read the column**, never compute from a placement date: an amend starts a fresh window.
- **Invariant 5's rewording.** Lands with branch two. This branch writes no `client_order_id`.
- **`_promised_stop`'s multi-buy semantics.** Real, narrow, not here.
- **The four ungated mutation verbs.** Separate exposure, separate owner.

## Verification before this branch is called done

`make test` is necessary and not sufficient. This runs unattended at 09:35 with no human present, so also:

- `make sim-day` — a full simulated day through the real gate and DB.
- **Migration evidence, which no diff can show.** Take a copy of the droplet database, run `apply()` against it, and record before/after: the table absent, then present, then `apply()` again returning `[]`. A green suite proves the migration works on a database built by `schema.sql` — the exact case the droplet is not.
