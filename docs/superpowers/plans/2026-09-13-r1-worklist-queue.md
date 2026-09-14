# Phase 6 R1 — worklist queue + dispatcher + sweep (no LLM) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Lane B work queue (`worklist` table + state machine), a pure-Python dispatcher that claims rows by CAS + lease and hands each to an injected callable, and a deterministic sweep that fails over-lease claims and expires past-due rows — each with a `#risk` alert — plus an uninstalled systemd unit. Issue #228 (part of #170).

**Architecture:** `state/worklist.py` owns row operations (enqueue with a kind-by-producer allow-list, claim via `state/transition.py` CAS, finish/fail). `orchestrator/dispatch.py` owns the loop and the sweep and is the only place alerts are appended (`slackkit.outbox.append_alert`). `scripts/run_dispatcher.py` is the composition root (real clock, real sleep, lock, guard) and in R1 wires a `run_wake` that has no consumer, so every claimed row fails loudly — R2 registers the first consumer. Design source: `docs/superpowers/specs/2026-08-28-resident-seats.md` ("The work queue", "The wake", "Build order → R1").

**Tech Stack:** Python 3.12, sqlite3, pytest. No new dependencies.

## Global Constraints

- **Paper only; no LLM anywhere in this lane.** No import of `claude_agent_sdk`, `anthropic`, or `agents/` from `state/`, `orchestrator/`, `fundbt/` — `scripts/check_purity.py` runs in `make test` and scans `orchestrator`, `state`, `fundbt`, `slackkit`.
- **Injected time.** `datetime.now()`, `time.sleep()`, `time.time()` are forbidden outside `scripts/`. Every timestamp is a `now_iso` argument or `iso(clock.now())` from an injected `Clock`. Sleeping is an injected `Callable[[float], None]` (precedent `orchestrator/reconcile.py:220`).
- **Transitions only through `state/transition.py`.** An illegal edge raises `IllegalTransition`; a CAS miss raises `StaleTransition` (or `try_transition` returns False). Never `UPDATE … SET status` anywhere else.
- **Alert codes are string literals** matching `^[a-z][a-z0-9_]*$`, passed positionally to `append_alert` — `scripts/check_alert_codes.py` (in `make lint`) rejects anything else. Never interpolate an id into the code; put it in `text`.
- **DDL is a spec change first.** `specs/contracts.md` §2 is edited in its own commit before `state/schema.sql`; `tests/test_schema_contract.py` compares them column-by-column (names, order, type, NOT NULL, DEFAULT, CHECK text). `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` in `schema.sql` are load-bearing (`state/db.py:12`).
- **Design-vs-code rulings (fund-d5, 2026-09-13), all binding on this plan:** the status column is named `status` (not `state`); there is **no** `state_version` column; the unit is `Type=simple`, **no** `Restart=`, `OnFailure=fund-alert@%n.service`, no `WatchdogSec`; daily Lane B budget caps are **deferred to R2**; `work_id` is minted by a public `work_id()` in `fundbt/hashing.py`.
- **Never update a golden fixture or expected value to make a test pass.** Extending a parametrization (adding `worklist` to `STATUSES` in `tests/test_state.py`) is allowed; weakening an assertion is not.
- Conventional commits: `feat:`, `test:`, `docs:`. No `Co-Authored-By` trailer. Run `make test` before every commit (≈50 s).
- Run everything from the worktree root `/Users/benjaminmatton/Developer/fund/.claude/worktrees/r1-worklist-queue`. Never `cd` to `/Users/benjaminmatton/Developer/fund`.

## File map

| File | Responsibility |
|---|---|
| `specs/contracts.md` (modify §1 line ~13, §2 fence before line 176) | canonical `worklist` state machine + DDL |
| `state/schema.sql` (append) | executable mirror of the DDL + index |
| `state/transition.py` (modify) | `worklist` edges/keys; optional `extra` columns set in the same CAS UPDATE |
| `fundbt/hashing.py` (append) | `work_id()` — the one sanctioned hasher |
| `state/worklist.py` (create) | `PRODUCER_KINDS`, `allowed`, `enqueue`, `claim_next`, `finish`, `fail` — row ops, no alerts |
| `orchestrator/dispatch.py` (create) | `sweep`, `dispatch_once`, `run_dispatcher` — loop + alerts |
| `scripts/run_dispatcher.py` (create) | composition root; `no_consumer` wake; `--cycles` |
| `ops/fund-dispatcher.service` (create), `ops/README.md` (units table row) | uninstalled unit |
| `tests/test_state.py`, `tests/test_worklist.py`, `tests/test_dispatch.py`, `tests/test_run_dispatcher.py`, `tests/test_ops_units.py` | tests |

---

### Task 1: Canonical DDL + state machine in `specs/contracts.md`

**Files:**
- Modify: `specs/contracts.md` — §1 (after the `**checkpoint stage**` line, ~line 13) and §2 (inside the ```sql fence, immediately before the closing ``` at ~line 176, after the `protection` table)

**Interfaces:**
- Produces: the column set every later task uses — `work_id, kind, producer, seat, subject, payload, status, attempts, not_before, expires_at, claim_expires_at, created_at, claimed_at, finished_at`.

- [ ] **Step 1: Write the failing test** — the contract test compares spec against schema; after this task the spec has a table the schema lacks. That is the red. Confirm the current state first:

Run: `.venv/bin/python3 -m pytest tests/test_schema_contract.py -q`
Expected: `25 passed`

- [ ] **Step 2: Add the §1 line.** After the line `**checkpoint stage**: \`pending → running → done | failed\`` add:

```markdown
**worklist**: `open → claimed | expired` · `claimed → done | failed` (done/failed/expired terminal; nothing auto-requeues — a human re-enqueue is a NEW row with `attempts`+1, so its `work_id` differs)
```

- [ ] **Step 3: Add the §2 DDL.** Inside the ```sql fence, after the `protection` table's closing `);` and before the fence's closing ```:

```sql

-- worklist: Lane B scheduling intent (docs/superpowers/specs/2026-08-28-
-- resident-seats.md, R1). NEVER truth — the thing a row points at lives in its
-- own table (strategy_critiques, events, ...), and a wake re-reads it from
-- there. Only deterministic code writes rows: state/worklist.py enforces a
-- kind-by-producer allow-list; an agent never enqueues work (invariant 6).
--
-- The column is `status`, not the design doc's `state`: state/transition.py's
-- CAS is written against `status`, and tests/test_state.py pins that every
-- table with a `status` column has a §1 machine. `strategies` keeps its own
-- `state`/`state_version` shape per strategy-contracts.md §4 — do not "fix"
-- either to match the other. There is no state_version here: the CAS is on
-- the status value itself, and nothing reads a version token.
CREATE TABLE worklist (
  work_id          TEXT PRIMARY KEY,          -- fundbt.hashing.work_id(kind, subject, dedupe_key)
  kind             TEXT NOT NULL,             -- 'spec_review' | 'alert_triage' | ... (allow-list in code)
  producer         TEXT NOT NULL,             -- the code path that wrote it: 'orchestrator' | 'alert_filer'
  seat             TEXT NOT NULL,             -- the one seat that may consume it
  subject          TEXT NOT NULL,             -- id of the thing (spec_id, alert code, ...)
  payload          TEXT NOT NULL DEFAULT '{}', -- JSON, small; the wake re-reads truth from the DB
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','done','failed','expired')),
  attempts         INTEGER NOT NULL DEFAULT 0, -- human re-enqueue increments; part of the dedupe key
  not_before       TEXT,                      -- ISO8601 UTC; debounce/backoff; NULL = claimable now
  expires_at       TEXT NOT NULL,             -- ISO8601 UTC; sweep: open past this -> expired (+ alert)
  claim_expires_at TEXT,                      -- lease, set at claim; sweep: claimed past this -> failed (+ alert)
  created_at       TEXT NOT NULL,
  claimed_at       TEXT,
  finished_at      TEXT
);
CREATE INDEX idx_worklist_dispatch ON worklist(seat, status, not_before);
```

- [ ] **Step 4: Run the contract test to verify it fails for the right reason**

Run: `.venv/bin/python3 -m pytest tests/test_schema_contract.py -q 2>&1 | tail -15`
Expected: FAIL — a message naming `worklist` as declared in the spec but absent from `state/schema.sql` (the test that fails is the spec→schema direction; `test_spec_ddl_executes` must still PASS, proving the DDL is valid SQLite). If `test_spec_ddl_executes` fails, fix the SQL before continuing.

- [ ] **Step 5: Do NOT commit yet.** The suite is red until Task 2 mirrors the DDL; Task 2 Step 7 commits both together. The PR body names the `specs/contracts.md` hunk as the canonical edit.

---

### Task 2: Mirror the DDL in `state/schema.sql`; register the machine in `state/transition.py`

**Files:**
- Modify: `state/schema.sql` (append at end of file)
- Modify: `state/transition.py:8-26` (EDGES, KEYS)
- Test: `tests/test_state.py:10-21, 59-66`

**Interfaces:**
- Produces: `EDGES["worklist"]`, `KEYS["worklist"] == ("work_id",)`.

- [ ] **Step 1: Append to `state/schema.sql`** (after the `protection` table — the last statement in the file):

```sql

-- worklist: Lane B scheduling intent, verbatim from specs/contracts.md §2 —
-- canonical, do not add fields here. Never truth: a wake re-reads the subject
-- from its own table. Column is `status` (not the design doc's `state`) so
-- state/transition.py's CAS applies unchanged; see the contracts.md comment.
--
-- IF NOT EXISTS is load-bearing on BOTH statements: state/db.py:12 matches the
-- table string to build _TABLES, and connect() re-runs this whole file when
-- any table is missing, so a bare CREATE INDEX would raise on that pass.
CREATE TABLE IF NOT EXISTS worklist (
  work_id          TEXT PRIMARY KEY,          -- fundbt.hashing.work_id(kind, subject, dedupe_key)
  kind             TEXT NOT NULL,             -- 'spec_review' | 'alert_triage' | ... (allow-list in code)
  producer         TEXT NOT NULL,             -- the code path that wrote it: 'orchestrator' | 'alert_filer'
  seat             TEXT NOT NULL,             -- the one seat that may consume it
  subject          TEXT NOT NULL,             -- id of the thing (spec_id, alert code, ...)
  payload          TEXT NOT NULL DEFAULT '{}', -- JSON, small; the wake re-reads truth from the DB
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','done','failed','expired')),
  attempts         INTEGER NOT NULL DEFAULT 0, -- human re-enqueue increments; part of the dedupe key
  not_before       TEXT,                      -- ISO8601 UTC; debounce/backoff; NULL = claimable now
  expires_at       TEXT NOT NULL,             -- ISO8601 UTC; sweep: open past this -> expired (+ alert)
  claim_expires_at TEXT,                      -- lease, set at claim; sweep: claimed past this -> failed (+ alert)
  created_at       TEXT NOT NULL,
  claimed_at       TEXT,
  finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_worklist_dispatch ON worklist(seat, status, not_before);
```

- [ ] **Step 2: Run the contract test; then the state test to see the machine pin go red**

Run: `.venv/bin/python3 -m pytest tests/test_schema_contract.py tests/test_state.py -q 2>&1 | tail -8`
Expected: `test_schema_contract.py` all PASS; `tests/test_state.py::test_every_status_table_has_a_state_machine` FAILS (worklist has a `status` column and no EDGES entry).

- [ ] **Step 3: Register the machine.** In `state/transition.py` replace the `EDGES` and `KEYS` dicts with:

```python
EDGES: dict[str, set[tuple[str, str]]] = {
    "decisions": {("submitted", "approved"), ("submitted", "rejected"),
                  ("submitted", "held"),
                  ("approved", "executed"), ("approved", "failed"),
                  ("approved", "expired")},
    "tickets": {("open", "consumed"), ("open", "expired")},
    "orders": {("submitted", "filled"), ("submitted", "partially_filled"),
               ("submitted", "canceled"), ("submitted", "rejected"),
               ("partially_filled", "filled"), ("partially_filled", "canceled")},
    "checkpoints": {("pending", "running"), ("running", "done"),
                    ("running", "failed")},
    "worklist": {("open", "claimed"), ("open", "expired"),
                 ("claimed", "done"), ("claimed", "failed")},
}

KEYS: dict[str, tuple[str, ...]] = {
    "decisions": ("id",),
    "tickets": ("id",),
    "orders": ("client_order_id",),
    "checkpoints": ("run_date", "stage", "ticker"),
    "worklist": ("work_id",),
}
```

- [ ] **Step 4: Extend the test's enumerations.** In `tests/test_state.py`:

Line 10-11, add `"worklist"` to `TABLES`:
```python
TABLES = {"signals", "critiques", "decisions", "tickets", "orders",
          "resolutions", "checkpoints", "events", "costs", "offered", "weights",
          "worklist"}
```
Line 13-18, add to `STATUSES`:
```python
    "worklist": ["open", "claimed", "done", "failed", "expired"],
```
Line 61-64 (`test_every_non_edge_raises`), add the key branch after the `orders` branch:
```python
    if table == "worklist":
        key = {"work_id": "wk_x"}
```

- [ ] **Step 5: Run the state tests**

Run: `.venv/bin/python3 -m pytest tests/test_state.py -q`
Expected: all PASS (the parametrized non-edge count grows by 5×5−4 = 21 cases, all raising `IllegalTransition`).

- [ ] **Step 6: Bump the table-count tripwire.** `tests/test_preflight_schema.py::test_the_expected_table_count_is_pinned` pins the number of tables in `state/schema.sql` and its own docstring says: "It IS a second edit, on purpose: bump it in the same commit that adds the table." Change `17` → `18` at both `assert len(preflight.expected_schema()) == 17` and `assert "none of the 17 tables" in proc.stderr`, and append a changelog paragraph to that docstring after the `15 -> 17` entry:

```
    17 -> 18 on 2026-09-13 — issue #228 (Phase 6 R1)
    (https://github.com/benjaminematton/fund/issues/228) — `worklist`, the
    Lane B work queue, character-exact to contracts.md §2. Column is `status`
    (not the design doc's `state`) so state/transition.py's CAS applies.
```

- [ ] **Step 7: Full suite, then commit (ONE commit for Tasks 1+2 — a spec-only commit leaves `test_schema_contract` red, and `make test` must pass before every commit)**

Run: `make test 2>&1 | tail -3`
Expected: `PURITY LINT: clean`, all tests pass.

```bash
git add specs/contracts.md state/schema.sql state/transition.py tests/test_state.py tests/test_preflight_schema.py
git commit -m "feat(state): worklist table + open/claimed/done/failed/expired machine (#228)"
```

---

### Task 3: `try_transition(..., extra=)` — set lease/timestamp columns in the same CAS UPDATE

**Files:**
- Modify: `state/transition.py:37-63`
- Test: `tests/test_state.py` (append)

**Interfaces:**
- Produces: `try_transition(conn, table, key, from_status, to_status, now_iso, *, extra: dict[str, object] | None = None) -> bool` and the same keyword on `transition(...)`. `extra` maps column name → value, applied in the **same** `UPDATE` as the status CAS (atomic: a claim that wins the CAS always carries its lease). Column names come only from code, never from input.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_state.py`:

```python
def _seed_work(conn, wid="wk_0000000000000001", status="open"):
    conn.execute(
        "INSERT INTO worklist (work_id, kind, producer, seat, subject, status,"
        " expires_at, created_at) VALUES (?, 'spec_review', 'orchestrator',"
        " 'critic', 'spec_abc', ?, '2026-07-06T20:00:00+00:00', ?)",
        (wid, status, NOW))
    conn.commit()
    return wid


def test_transition_extra_columns_ride_in_the_same_cas_update(fund_db):
    """A claim that wins the CAS must carry its lease atomically — a second
    UPDATE could be lost to a crash and leave a claimed row with no lease."""
    wid = _seed_work(fund_db)
    ok = try_transition(fund_db, "worklist", {"work_id": wid}, "open", "claimed",
                        NOW, extra={"claimed_at": NOW,
                                    "claim_expires_at": "2026-07-06T15:35:00+00:00"})
    assert ok is True
    row = fund_db.execute("SELECT * FROM worklist WHERE work_id=?", (wid,)).fetchone()
    assert (row["status"], row["claimed_at"], row["claim_expires_at"]) == (
        "claimed", NOW, "2026-07-06T15:35:00+00:00")


def test_transition_extra_is_not_applied_when_the_cas_misses(fund_db):
    wid = _seed_work(fund_db, status="claimed")
    ok = try_transition(fund_db, "worklist", {"work_id": wid}, "open", "claimed",
                        NOW, extra={"claimed_at": "should-not-land"})
    assert ok is False
    row = fund_db.execute("SELECT claimed_at FROM worklist WHERE work_id=?", (wid,)).fetchone()
    assert row["claimed_at"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_state.py -q -k extra`
Expected: FAIL — `TypeError: try_transition() got an unexpected keyword argument 'extra'`

- [ ] **Step 3: Implement.** Replace `try_transition` and `transition` in `state/transition.py` with:

```python
def try_transition(conn: sqlite3.Connection, table: str, key: dict,
                   from_status: str, to_status: str, now_iso: str, *,
                   extra: dict[str, object] | None = None) -> bool:
    """CAS the row from from_status to to_status. True if the row moved; false
    if the row is not in from_status (lets idempotent handlers no-op on
    re-run, contracts §5.2).

    `extra` — column -> value pairs written in the SAME UPDATE as the status
    flip, so a caller that wins the CAS always carries them (a worklist claim's
    lease). Column names are code-supplied, never input."""
    if table not in EDGES:
        raise IllegalTransition(f"no state machine for table {table!r}")
    if (from_status, to_status) not in EDGES[table]:
        raise IllegalTransition(
            f"{table}: {from_status!r} -> {to_status!r} is not a legal edge")
    if set(key) != set(KEYS[table]):
        raise ValueError(f"{table} key must be exactly {KEYS[table]}, got {tuple(key)}")
    sets = "status = ?" + (", updated_at = ?" if table == "checkpoints" else "")
    params: list = [to_status] + ([now_iso] if table == "checkpoints" else [])
    for col, val in (extra or {}).items():
        sets += f", {col} = ?"
        params.append(val)
    where = " AND ".join(f"{col} = ?" for col in KEYS[table]) + " AND status = ?"
    params += [key[col] for col in KEYS[table]] + [from_status]
    cur = conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", params)
    conn.commit()
    return cur.rowcount == 1


def transition(conn: sqlite3.Connection, table: str, key: dict,
               from_status: str, to_status: str, now_iso: str, *,
               extra: dict[str, object] | None = None) -> None:
    """CAS that raises StaleTransition when the row is not in from_status."""
    if not try_transition(conn, table, key, from_status, to_status, now_iso,
                          extra=extra):
        raise StaleTransition(
            f"{table} {key}: not in {from_status!r} (or missing) — refusing to overwrite")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_state.py -q`
Expected: all PASS.

- [ ] **Step 5: Full suite, commit**

Run: `make test 2>&1 | tail -3` — expected all pass.

```bash
git add state/transition.py tests/test_state.py
git commit -m "feat(state): transition() takes extra columns for the same CAS update (#228)"
```

---

### Task 4: `work_id()` in `fundbt/hashing.py`

**Files:**
- Modify: `fundbt/hashing.py` (append)
- Test: `tests/test_worklist.py` (create)

**Interfaces:**
- Produces: `work_id(kind: str, subject: str, dedupe_key: str) -> str` → `"wk_" + 16 hex chars`, deterministic.

- [ ] **Step 1: Write the failing test.** Create `tests/test_worklist.py`:

```python
"""state/worklist.py — Lane B row ops (resident-seats R1, #228)."""
import re

import pytest

from fundbt.hashing import work_id

NOW = "2026-07-06T15:30:00+00:00"
LATER = "2026-07-06T20:00:00+00:00"


def test_work_id_is_deterministic_prefixed_and_keyed_on_all_three_parts():
    a = work_id("spec_review", "spec_abc", "0")
    assert re.fullmatch(r"wk_[0-9a-f]{16}", a)
    assert a == work_id("spec_review", "spec_abc", "0")
    assert a != work_id("spec_review", "spec_abc", "1")      # attempts differ
    assert a != work_id("alert_triage", "spec_abc", "0")     # kind differs
    assert a != work_id("spec_review", "spec_xyz", "0")      # subject differs
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_worklist.py -q`
Expected: FAIL — `ImportError: cannot import name 'work_id'`

- [ ] **Step 3: Implement.** Append to `fundbt/hashing.py`:

```python


def work_id(kind: str, subject: str, dedupe_key: str) -> str:
    """worklist row identity (contracts.md §2). Same (kind, subject, dedupe_key)
    -> same id, so a second enqueue is a no-op rather than a duplicate."""
    return _h("wk_", canonical_json({"kind": kind, "subject": subject,
                                     "dedupe_key": dedupe_key}))
```

- [ ] **Step 4: Run tests** — `.venv/bin/python3 -m pytest tests/test_worklist.py -q` → PASS.

- [ ] **Step 5: Full suite, commit**

```bash
make test 2>&1 | tail -3
git add fundbt/hashing.py tests/test_worklist.py
git commit -m "feat(fundbt): work_id() — the worklist row hasher (#228)"
```

---

### Task 5: `state/worklist.py` — enqueue (allow-listed, idempotent), claim (CAS + lease), finish, fail

**Files:**
- Create: `state/worklist.py`
- Test: `tests/test_worklist.py` (append)

**Interfaces:**
- Consumes: `try_transition`/`transition` with `extra=` (Task 3), `work_id` (Task 4).
- Produces:
  - `PRODUCER_KINDS: dict[str, frozenset[str]]` — `{"orchestrator": {"spec_review"}, "alert_filer": {"alert_triage"}}`
  - `class DisallowedWork(ValueError)`
  - `allowed(producer: str, kind: str) -> bool`
  - `enqueue(conn, *, kind, producer, seat, subject, expires_at, now_iso, payload: dict | None = None, not_before: str | None = None, attempts: int = 0) -> str` — returns the `work_id`; raises `DisallowedWork`; a repeat with the same `(kind, subject, attempts)` is a no-op returning the same id.
  - `claim_next(conn, *, now_iso: str, lease_until_iso: str, seat: str | None = None) -> sqlite3.Row | None` — oldest claimable `open` row (`not_before` passed or NULL, `expires_at` in the future), CAS'd to `claimed` with `claimed_at`/`claim_expires_at` set; `None` when nothing is claimable.
  - `finish(conn, work_id, now_iso)` — `claimed → done`, sets `finished_at`; raises `StaleTransition` if not claimed.
  - `fail(conn, work_id, now_iso)` — `claimed → failed`, sets `finished_at`; raises likewise.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_worklist.py`:

```python
from state import worklist
from state.db import connect
from state.transition import StaleTransition


def _enqueue(conn, **kw):
    args = dict(kind="spec_review", producer="orchestrator", seat="critic",
                subject="spec_abc", expires_at=LATER, now_iso=NOW)
    args.update(kw)
    return worklist.enqueue(conn, **args)


def _row(conn, wid):
    return conn.execute("SELECT * FROM worklist WHERE work_id=?", (wid,)).fetchone()


def test_enqueue_writes_an_open_row_with_sorted_json_payload(fund_db):
    wid = _enqueue(fund_db, payload={"b": 1, "a": 2})
    row = _row(fund_db, wid)
    assert row["status"] == "open"
    assert row["payload"] == '{"a": 2, "b": 1}'
    assert row["attempts"] == 0
    assert row["not_before"] is None
    assert (row["claimed_at"], row["claim_expires_at"], row["finished_at"]) == (None, None, None)
    assert row["created_at"] == NOW


def test_enqueue_is_idempotent_on_kind_subject_attempts(fund_db):
    a = _enqueue(fund_db)
    b = _enqueue(fund_db, payload={"ignored": True})
    assert a == b
    assert fund_db.execute("SELECT COUNT(*) c FROM worklist").fetchone()["c"] == 1
    assert _row(fund_db, a)["payload"] == "{}"          # first write wins


def test_reenqueue_with_attempts_incremented_is_a_new_row(fund_db):
    a = _enqueue(fund_db)
    b = _enqueue(fund_db, attempts=1)
    assert a != b and _row(fund_db, b)["attempts"] == 1


@pytest.mark.parametrize("producer,kind", [
    ("slack_listener", "mention"),      # R3's pair, not registered in R1
    ("orchestrator", "alert_triage"),   # wrong producer for the kind
    ("critic", "spec_review"),          # a seat name is never a producer
])
def test_enqueue_refuses_a_pair_outside_the_allow_list(fund_db, producer, kind):
    with pytest.raises(worklist.DisallowedWork):
        _enqueue(fund_db, producer=producer, kind=kind)
    assert fund_db.execute("SELECT COUNT(*) c FROM worklist").fetchone()["c"] == 0


def test_claim_next_takes_the_oldest_claimable_row_and_sets_the_lease(fund_db):
    old = _enqueue(fund_db, subject="s1", now_iso="2026-07-06T15:00:00+00:00")
    _enqueue(fund_db, subject="s2", now_iso="2026-07-06T15:10:00+00:00")
    row = worklist.claim_next(fund_db, now_iso=NOW,
                              lease_until_iso="2026-07-06T15:35:00+00:00")
    assert row["work_id"] == old
    assert (row["status"], row["claimed_at"], row["claim_expires_at"]) == (
        "claimed", NOW, "2026-07-06T15:35:00+00:00")


def test_claim_next_skips_not_before_in_the_future_and_expired_rows(fund_db):
    _enqueue(fund_db, subject="debounced", not_before="2026-07-06T16:00:00+00:00")
    _enqueue(fund_db, subject="stale", expires_at="2026-07-06T15:00:00+00:00")
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER) is None
    ready = _enqueue(fund_db, subject="ready", not_before=NOW)   # not_before == now: claimable
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)["work_id"] == ready


def test_claim_next_filters_by_seat(fund_db):
    _enqueue(fund_db, seat="critic", subject="c")
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER, seat="pm") is None
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER, seat="critic") is not None


def test_concurrent_claimers_on_one_row_yield_exactly_one_claim(tmp_path):
    """CAS under concurrent claim (design R1 acceptance). Two connections to one
    file: both see the row open; the UPDATE ... WHERE status='open' is what
    makes only the first flip land."""
    path = tmp_path / "fund.sqlite"
    a, b = connect(path), connect(path)
    wid = _enqueue(a)
    ra = worklist.claim_next(a, now_iso=NOW, lease_until_iso=LATER)
    rb = worklist.claim_next(b, now_iso=NOW, lease_until_iso=LATER)
    assert ra is not None and ra["work_id"] == wid
    assert rb is None
    assert b.execute("SELECT COUNT(*) c FROM worklist WHERE status='claimed'").fetchone()["c"] == 1
    a.close(); b.close()


def test_finish_and_fail_are_claimed_only_and_stamp_finished_at(fund_db):
    wid = _enqueue(fund_db)
    with pytest.raises(StaleTransition):
        worklist.finish(fund_db, wid, NOW)            # open -> done is not an edge from open
    worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)
    worklist.finish(fund_db, wid, "2026-07-06T15:31:00+00:00")
    row = _row(fund_db, wid)
    assert (row["status"], row["finished_at"]) == ("done", "2026-07-06T15:31:00+00:00")
    with pytest.raises(StaleTransition):
        worklist.fail(fund_db, wid, NOW)              # done is terminal


def test_fail_moves_claimed_to_failed(fund_db):
    wid = _enqueue(fund_db)
    worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)
    worklist.fail(fund_db, wid, NOW)
    assert _row(fund_db, wid)["status"] == "failed"
```

Note for `test_finish_and_fail_are_claimed_only`: `finish` on an `open` row raises `StaleTransition` (legal edge `claimed→done`, row not in `claimed`) — not `IllegalTransition`. That is the helper's contract; the assertion is correct as written.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_worklist.py -q 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'worklist' from 'state'` (or `ModuleNotFoundError`).

- [ ] **Step 3: Implement.** Create `state/worklist.py`:

```python
"""worklist rows — Lane B scheduling intent (contracts.md §1, §2; resident-
seats design R1). Row operations only: no alerts, no loop, no clock — those
live in orchestrator/dispatch.py. A row is never truth; the wake re-reads its
subject from the table that owns it.

Purity-linted: pure Python + sqlite3 + fundbt.hashing (the ONLY permitted
hasher)."""

from __future__ import annotations

import json
import sqlite3

from fundbt.hashing import work_id
from state.transition import transition, try_transition

# Kind-by-producer allow-list: which deterministic code path may enqueue which
# kind. An agent never enqueues work, and a producer never gains a kind by
# accident (invariant 6). R2 (spec_review consumer) and R3 (slack_listener ->
# mention) extend this table in their own lanes.
PRODUCER_KINDS: dict[str, frozenset[str]] = {
    "orchestrator": frozenset({"spec_review"}),
    "alert_filer": frozenset({"alert_triage"}),
}


class DisallowedWork(ValueError):
    """(producer, kind) is not in PRODUCER_KINDS."""


def allowed(producer: str, kind: str) -> bool:
    return kind in PRODUCER_KINDS.get(producer, frozenset())


def enqueue(conn: sqlite3.Connection, *, kind: str, producer: str, seat: str,
            subject: str, expires_at: str, now_iso: str,
            payload: dict | None = None, not_before: str | None = None,
            attempts: int = 0) -> str:
    """Insert an open row and return its work_id. Idempotent: the id is
    work_id(kind, subject, attempts), so a repeat is a no-op and the first
    payload wins. Re-enqueue after a failure is a human action that passes
    attempts+1 — a new row, never a flip of the failed one."""
    if not allowed(producer, kind):
        raise DisallowedWork(f"{producer!r} may not enqueue {kind!r}")
    wid = work_id(kind, subject, str(attempts))
    conn.execute(
        "INSERT OR IGNORE INTO worklist (work_id, kind, producer, seat, subject,"
        " payload, attempts, not_before, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, kind, producer, seat, subject,
         json.dumps(payload or {}, sort_keys=True), attempts, not_before,
         expires_at, now_iso))
    conn.commit()
    return wid


def claim_next(conn: sqlite3.Connection, *, now_iso: str, lease_until_iso: str,
               seat: str | None = None) -> sqlite3.Row | None:
    """Oldest claimable open row, CAS'd to claimed with its lease. None when
    nothing is claimable. Two claimers racing on one row: the UPDATE's
    `status = 'open'` predicate lets exactly one win; the loser moves on to
    the next candidate.

    Timestamps compare as strings: every value is orchestrator.clock.iso()
    (UTC, seconds precision, fixed width), so lexical order is time order."""
    where = ("status = 'open' AND (not_before IS NULL OR not_before <= ?)"
             " AND expires_at > ?")
    params: list = [now_iso, now_iso]
    if seat is not None:
        where += " AND seat = ?"
        params.append(seat)
    candidates = conn.execute(
        f"SELECT work_id FROM worklist WHERE {where}"
        " ORDER BY created_at, work_id", params).fetchall()
    for c in candidates:
        if try_transition(conn, "worklist", {"work_id": c["work_id"]},
                          "open", "claimed", now_iso,
                          extra={"claimed_at": now_iso,
                                 "claim_expires_at": lease_until_iso}):
            return conn.execute("SELECT * FROM worklist WHERE work_id = ?",
                                (c["work_id"],)).fetchone()
    return None


def finish(conn: sqlite3.Connection, wid: str, now_iso: str) -> None:
    """claimed -> done. Raises StaleTransition if the row is not claimed."""
    transition(conn, "worklist", {"work_id": wid}, "claimed", "done", now_iso,
               extra={"finished_at": now_iso})


def fail(conn: sqlite3.Connection, wid: str, now_iso: str) -> None:
    """claimed -> failed. Raises StaleTransition if the row is not claimed.
    Never requeues: re-enqueue is a human action (design, 'No silent states')."""
    transition(conn, "worklist", {"work_id": wid}, "claimed", "failed", now_iso,
               extra={"finished_at": now_iso})
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_worklist.py -q`
Expected: all PASS.

- [ ] **Step 5: Full suite (purity lint must stay clean), commit**

```bash
make test 2>&1 | tail -3
git add state/worklist.py tests/test_worklist.py
git commit -m "feat(state): worklist enqueue/claim/finish/fail with producer allow-list (#228)"
```

---

### Task 6: `orchestrator/dispatch.py` — sweep, dispatch_once, run_dispatcher

**Files:**
- Create: `orchestrator/dispatch.py`
- Test: `tests/test_dispatch.py` (create)

**Interfaces:**
- Consumes: `state.worklist.{claim_next, finish, fail, allowed}`, `slackkit.outbox.{append_alert, drain}`, `orchestrator.clock.{Clock, iso}`, `state.transition.try_transition`.
- Produces:
  - `DEFAULT_LEASE_S = 300`, `DEFAULT_POLL_S = 5.0`
  - `@dataclass(frozen=True) SweepResult(reaped: tuple[str, ...], expired: tuple[str, ...])`
  - `sweep(conn, now_iso: str) -> SweepResult` — `claimed` past `claim_expires_at` (or with NULL lease) → `failed` + alert `work_lease_expired`; `open` past `expires_at` → `expired` + alert `work_expired`. One alert per row.
  - `@dataclass(frozen=True) DispatchResult(handled: str | None, swept: SweepResult)`
  - `dispatch_once(conn, clock, run_wake: Callable[[dict], None], *, lease_s: int = DEFAULT_LEASE_S) -> DispatchResult` — sweep, then claim ≤1 row and run it. A raise from `run_wake` → `fail` + alert `work_failed`, never re-raised, never requeued. A claimed row whose `(producer, kind)` is not allowed → `fail` + alert `work_disallowed` without calling `run_wake`.
  - `run_dispatcher(conn, slack, clock, run_wake, *, sleep: Callable[[float], None], poll_s: float = DEFAULT_POLL_S, lease_s: int = DEFAULT_LEASE_S, max_cycles: int | None = None) -> int` — the resident loop; drains the outbox only after a cycle that wrote something; sleeps `poll_s` on an idle cycle; returns cycles run. `max_cycles=None` runs until killed.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_dispatch.py`:

```python
"""orchestrator/dispatch.py — Lane B dispatcher + sweep (resident-seats R1, #228).
No LLM: the wake is a callable. Alerts are events(kind='alert'); there is no
alerts table."""
import json
from datetime import datetime, timezone

import pytest

from orchestrator import dispatch
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state import worklist
from state.transition import StaleTransition

START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return SimClock(START)


def _enqueue(conn, clock, **kw):
    args = dict(kind="spec_review", producer="orchestrator", seat="critic",
                subject="spec_abc", expires_at="2026-07-06T20:00:00+00:00",
                now_iso=iso(clock.now()))
    args.update(kw)
    return worklist.enqueue(conn, **args)


def _status(conn, wid):
    return conn.execute("SELECT status FROM worklist WHERE work_id=?", (wid,)).fetchone()["status"]


def _alerts(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]


def _costs(conn):
    return conn.execute("SELECT COUNT(*) c FROM costs").fetchone()["c"]


# --- sweep ------------------------------------------------------------------

def test_sweep_reaps_an_over_lease_claim_to_failed_with_one_alert(fund_db, clock):
    wid = _enqueue(fund_db, clock)
    worklist.claim_next(fund_db, now_iso=iso(clock.now()),
                        lease_until_iso="2026-07-06T15:35:00+00:00")
    clock.advance(minutes=4)
    assert dispatch.sweep(fund_db, iso(clock.now())) == dispatch.SweepResult((), ())
    clock.advance(minutes=1)                       # 15:35: lease is up
    res = dispatch.sweep(fund_db, iso(clock.now()))
    assert res.reaped == (wid,) and res.expired == ()
    assert _status(fund_db, wid) == "failed"
    codes = [a["code"] for a in _alerts(fund_db)]
    assert codes == ["work_lease_expired"]
    assert wid in _alerts(fund_db)[0]["text"]


def test_sweep_treats_a_claimed_row_with_no_lease_as_over_lease(fund_db, clock):
    """Defensive: a claim always carries its lease (Task 3's atomic extra=),
    but a NULL lease must reap, never sit claimed forever."""
    wid = _enqueue(fund_db, clock)
    fund_db.execute("UPDATE worklist SET status='claimed' WHERE work_id=?", (wid,))
    fund_db.commit()
    assert dispatch.sweep(fund_db, iso(clock.now())).reaped == (wid,)


def test_sweep_expires_a_past_due_open_row_with_one_alert(fund_db, clock):
    wid = _enqueue(fund_db, clock, expires_at="2026-07-06T15:40:00+00:00")
    clock.advance(minutes=10)
    res = dispatch.sweep(fund_db, iso(clock.now()))
    assert res.expired == (wid,) and res.reaped == ()
    assert _status(fund_db, wid) == "expired"
    assert [a["code"] for a in _alerts(fund_db)] == ["work_expired"]


def test_sweep_is_idempotent(fund_db, clock):
    _enqueue(fund_db, clock, expires_at="2026-07-06T15:40:00+00:00")
    clock.advance(minutes=10)
    dispatch.sweep(fund_db, iso(clock.now()))
    assert dispatch.sweep(fund_db, iso(clock.now())) == dispatch.SweepResult((), ())
    assert len(_alerts(fund_db)) == 1


# --- dispatch_once ----------------------------------------------------------

def test_dispatch_once_runs_the_wake_with_the_row_and_marks_done(fund_db, clock):
    wid = _enqueue(fund_db, clock, payload={"k": "v"})
    seen = []
    res = dispatch.dispatch_once(fund_db, clock, seen.append)
    assert res.handled == wid
    assert seen[0]["work_id"] == wid and seen[0]["kind"] == "spec_review"
    assert seen[0]["payload"] == '{"k": "v"}'
    assert _status(fund_db, wid) == "done"
    assert _alerts(fund_db) == []


def test_dispatch_once_claims_with_the_lease_from_the_clock(fund_db, clock):
    wid = _enqueue(fund_db, clock)
    leases = []
    dispatch.dispatch_once(fund_db, clock, lambda row: leases.append(row["claim_expires_at"]),
                           lease_s=120)
    assert leases == ["2026-07-06T15:32:00+00:00"]


def test_dispatch_once_wake_raise_fails_the_row_alerts_and_does_not_reraise(fund_db, clock):
    wid = _enqueue(fund_db, clock)

    def boom(row):
        raise RuntimeError("no consumer registered")

    res = dispatch.dispatch_once(fund_db, clock, boom)
    assert res.handled == wid
    assert _status(fund_db, wid) == "failed"
    alerts = _alerts(fund_db)
    assert [a["code"] for a in alerts] == ["work_failed"]
    assert "RuntimeError: no consumer registered" in alerts[0]["text"]
    assert wid in alerts[0]["text"]
    # never requeued: the next cycle is idle
    assert dispatch.dispatch_once(fund_db, clock, boom).handled is None


def test_dispatch_once_refuses_a_disallowed_pair_without_running_the_wake(fund_db, clock):
    """The allow-list is enforced at claim too, not only at enqueue: a row
    written by hand (or by a future producer whose table entry was removed)
    never reaches a wake."""
    wid = _enqueue(fund_db, clock)
    fund_db.execute("UPDATE worklist SET producer='slack_listener' WHERE work_id=?", (wid,))
    fund_db.commit()
    called = []
    res = dispatch.dispatch_once(fund_db, clock, called.append)
    assert res.handled == wid and called == []
    assert _status(fund_db, wid) == "failed"
    assert [a["code"] for a in _alerts(fund_db)] == ["work_disallowed"]


def test_dispatch_once_sweeps_before_claiming(fund_db, clock):
    stale = _enqueue(fund_db, clock, subject="stale", expires_at="2026-07-06T15:31:00+00:00")
    clock.advance(minutes=5)
    fresh = _enqueue(fund_db, clock, subject="fresh")
    seen = []
    res = dispatch.dispatch_once(fund_db, clock, seen.append)
    assert res.swept.expired == (stale,)
    assert res.handled == fresh and seen[0]["work_id"] == fresh


def test_dispatch_once_idle_touches_nothing(fund_db, clock):
    res = dispatch.dispatch_once(fund_db, clock, lambda row: pytest.fail("no row to wake"))
    assert res == dispatch.DispatchResult(None, dispatch.SweepResult((), ()))
    assert _alerts(fund_db) == [] and _costs(fund_db) == 0


# --- run_dispatcher ---------------------------------------------------------

def test_run_dispatcher_sleeps_only_when_idle_and_drains_after_writes(fund_db, clock):
    _enqueue(fund_db, clock, subject="a")
    _enqueue(fund_db, clock, subject="b")
    slack = FakeSlack()
    naps = []

    def _sleep(s):
        naps.append(s)
        clock.advance(seconds=int(s))

    def boom(row):
        raise RuntimeError("x")

    cycles = dispatch.run_dispatcher(
        fund_db, slack, clock, boom, sleep=_sleep, poll_s=5.0, max_cycles=4)
    assert cycles == 4
    assert naps == [5.0, 5.0]                      # two busy cycles, two idle
    assert len(slack.posts.get("#risk", [])) == 2  # both work_failed alerts drained
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"] == 0


def test_idle_queue_costs_nothing(tmp_path):
    """Design R1 acceptance: a completed sim-day plus an empty queue -> zero
    wakes, zero new cost rows, zero new alerts across several cycles."""
    from tests.test_sim_day import golden_day
    sim = golden_day(tmp_path)
    costs_before = _costs(sim.conn)
    alerts_before = len(_alerts(sim.conn))
    clock = SimClock(START)
    wakes = []
    dispatch.run_dispatcher(sim.conn, sim.slack, clock, wakes.append,
                            sleep=lambda s: clock.advance(seconds=int(s)),
                            max_cycles=5)
    assert wakes == []
    assert _costs(sim.conn) == costs_before
    assert len(_alerts(sim.conn)) == alerts_before


def test_dispatcher_killed_mid_claim_is_reaped_on_the_next_start(tmp_path, clock):
    """Kill mid-claim (design R1 acceptance): process 1 claims and dies; process
    2 starts after the lease, sweeps the row to failed with an alert, and the
    row is neither lost nor run twice."""
    from state.db import connect
    path = tmp_path / "fund.sqlite"
    p1 = connect(path)
    wid = _enqueue(p1, clock)
    worklist.claim_next(p1, now_iso=iso(clock.now()),
                        lease_until_iso="2026-07-06T15:35:00+00:00")
    p1.close()                                     # died holding the claim

    clock.advance(minutes=6)
    p2 = connect(path)
    wakes = []
    res = dispatch.dispatch_once(p2, clock, wakes.append)
    assert res.swept.reaped == (wid,) and res.handled is None and wakes == []
    assert _status(p2, wid) == "failed"
    assert [a["code"] for a in _alerts(p2)] == ["work_lease_expired"]
    p2.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_dispatch.py -q 2>&1 | tail -3`
Expected: FAIL — `ImportError: cannot import name 'dispatch' from 'orchestrator'`.

- [ ] **Step 3: Implement.** Create `orchestrator/dispatch.py`:

```python
"""Lane B dispatcher + sweep (docs/superpowers/specs/2026-08-28-resident-seats.md,
R1). No LLM here: the wake is an injected callable, and in R1 the composition
root (scripts/run_dispatcher.py) wires one that has no consumer. Purity-linted
with the rest of orchestrator/: time is the injected Clock, sleeping is an
injected callable, and nothing imports agents/.

Alerts are events(kind='alert') via slackkit.outbox.append_alert — the design
doc's "alerts table" does not exist. Every silent state the design forbids
gets exactly one alert here: an over-lease claim, a past-due open row, a wake
that raised, a row whose (producer, kind) the allow-list rejects."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from orchestrator.clock import Clock, iso
from slackkit.outbox import append_alert, drain
from state import worklist
from state.transition import try_transition

DEFAULT_LEASE_S = 300      # a wake that outlives this is reaped by the sweep
DEFAULT_POLL_S = 5.0       # idle nap between cycles

RunWake = Callable[[dict], None]


@dataclass(frozen=True)
class SweepResult:
    reaped: tuple[str, ...]    # claimed -> failed (lease expired)
    expired: tuple[str, ...]   # open -> expired (past expires_at)


@dataclass(frozen=True)
class DispatchResult:
    handled: str | None        # work_id claimed this cycle, None when idle
    swept: SweepResult


def sweep(conn: sqlite3.Connection, now_iso: str) -> SweepResult:
    """No silent states. Over-lease claimed -> failed, past-due open -> expired,
    each with a #risk alert. Never requeues (re-enqueue is a human action).
    Idempotent: a row already moved is not in from_status, so the CAS no-ops
    and no second alert is written. Timestamps compare as strings (iso() is
    fixed-width UTC)."""
    reaped: list[str] = []
    for r in conn.execute(
            "SELECT work_id, kind, seat FROM worklist WHERE status = 'claimed'"
            " AND (claim_expires_at IS NULL OR claim_expires_at <= ?)"
            " ORDER BY created_at, work_id", (now_iso,)).fetchall():
        if try_transition(conn, "worklist", {"work_id": r["work_id"]},
                          "claimed", "failed", now_iso,
                          extra={"finished_at": now_iso}):
            reaped.append(r["work_id"])
            append_alert(conn, "work_lease_expired",
                         f"work_lease_expired — {r['kind']} {r['work_id']} for"
                         f" seat {r['seat']} was claimed and never finished;"
                         f" marked failed, not requeued (re-enqueue is a human"
                         f" action)", now_iso=now_iso)
    expired: list[str] = []
    for r in conn.execute(
            "SELECT work_id, kind, seat FROM worklist WHERE status = 'open'"
            " AND expires_at <= ? ORDER BY created_at, work_id",
            (now_iso,)).fetchall():
        if try_transition(conn, "worklist", {"work_id": r["work_id"]},
                          "open", "expired", now_iso,
                          extra={"finished_at": now_iso}):
            expired.append(r["work_id"])
            append_alert(conn, "work_expired",
                         f"work_expired — {r['kind']} {r['work_id']} for seat"
                         f" {r['seat']} was never claimed before its expiry;"
                         f" marked expired, not requeued", now_iso=now_iso)
    return SweepResult(tuple(reaped), tuple(expired))


def dispatch_once(conn: sqlite3.Connection, clock: Clock, run_wake: RunWake, *,
                  lease_s: int = DEFAULT_LEASE_S) -> DispatchResult:
    """Sweep, then claim at most one row and run it. A raise from run_wake
    fails the row with an alert and is NOT re-raised — the loop must outlive
    one bad wake, and the default is HOLD (invariant 4). The allow-list is
    re-checked at claim so a hand-written row never reaches a wake."""
    now = clock.now()
    swept = sweep(conn, iso(now))
    row = worklist.claim_next(
        conn, now_iso=iso(now),
        lease_until_iso=iso(now + timedelta(seconds=lease_s)))
    if row is None:
        return DispatchResult(None, swept)
    wid, kind, seat = row["work_id"], row["kind"], row["seat"]
    if not worklist.allowed(row["producer"], kind):
        done_at = iso(clock.now())
        worklist.fail(conn, wid, done_at)
        append_alert(conn, "work_disallowed",
                     f"work_disallowed — {kind} {wid} for seat {seat} carries"
                     f" producer {row['producer']!r}, which may not enqueue"
                     f" that kind; marked failed without running a wake",
                     now_iso=done_at)
        return DispatchResult(wid, swept)
    try:
        run_wake(dict(row))
    except Exception as exc:
        done_at = iso(clock.now())
        worklist.fail(conn, wid, done_at)
        append_alert(conn, "work_failed",
                     f"work_failed — {kind} {wid} for seat {seat}:"
                     f" {type(exc).__name__}: {exc}; marked failed, not"
                     f" requeued (re-enqueue is a human action)",
                     now_iso=done_at)
        return DispatchResult(wid, swept)
    worklist.finish(conn, wid, iso(clock.now()))
    return DispatchResult(wid, swept)


def run_dispatcher(conn: sqlite3.Connection, slack, clock: Clock,
                   run_wake: RunWake, *, sleep: Callable[[float], None],
                   poll_s: float = DEFAULT_POLL_S,
                   lease_s: int = DEFAULT_LEASE_S,
                   max_cycles: int | None = None) -> int:
    """The resident loop. Each cycle: dispatch_once; drain the outbox only if
    this cycle wrote something (run_day drains its own rows — an unconditional
    drain here would race it into duplicate posts); nap poll_s when idle.
    max_cycles=None runs until the process is killed. Returns cycles run."""
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        res = dispatch_once(conn, clock, run_wake, lease_s=lease_s)
        cycles += 1
        if res.handled is not None or res.swept.reaped or res.swept.expired:
            drain(conn, slack, iso(clock.now()))
        if res.handled is None:
            sleep(poll_s)
    return cycles
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_dispatch.py -q`
Expected: all PASS. If `test_idle_queue_costs_nothing` fails on the import of `tests.test_sim_day`, check how `tests/test_audit_day.py` imports `golden_day` and mirror it exactly.

- [ ] **Step 5: Full suite — purity + alert-code lint must be clean — then commit**

```bash
make test 2>&1 | tail -3
git add orchestrator/dispatch.py tests/test_dispatch.py
git commit -m "feat(orchestrator): Lane B dispatcher + sweep, no LLM (#228)"
```

---

### Task 7: `scripts/run_dispatcher.py` — composition root with a no-consumer wake

**Files:**
- Create: `scripts/run_dispatcher.py`
- Test: `tests/test_run_dispatcher.py` (create)

**Interfaces:**
- Consumes: `run_day.paper_guard`, `run_day.require_env`, `run_day.acquire_lock`, `run_day._alert`, `run_day.parse_channel_overrides`, `run_day.RemappedSlack` (all in `scripts/run_day.py`, import style as `scripts/critic_g1.py:159-171`); `orchestrator.dispatch.run_dispatcher`; `agents.wallclock.WallClock`; `time.sleep` (allowed: `scripts/` is outside the purity lint).
- Produces: `REQUIRED_ENV = ("FUND_DB", "SLACK_BOT_TOKEN")`, `LOCK_NAME = "dispatcher.lock"`, `class NoConsumer(RuntimeError)`, `no_consumer(row: dict) -> None` (always raises), `_build_slack(env, environ)`, `_guarded(conn, slack, clock, body) -> int`, `main(argv) -> int` with `--cycles N` (default: run forever).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_run_dispatcher.py`:

```python
"""scripts/run_dispatcher.py — the Lane B composition root (resident-seats R1,
#228). R1 registers no consumer: every claimed row fails loudly."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state import worklist
from state.db import connect

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_dispatcher", ROOT / "scripts" / "run_dispatcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_dispatcher = _load()
START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
    monkeypatch.setenv("FUND_DB", str(tmp_path / "fund.sqlite"))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_CHANNEL_OVERRIDES", raising=False)


def test_no_consumer_raises_naming_the_kind():
    with pytest.raises(run_dispatcher.NoConsumer, match="spec_review"):
        run_dispatcher.no_consumer({"kind": "spec_review", "work_id": "wk_x"})


def test_main_fails_every_row_loudly_and_exits_zero(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    slack = FakeSlack()
    monkeypatch.setattr(run_dispatcher, "_build_slack", lambda env, environ: slack)
    monkeypatch.setattr(run_dispatcher.time, "sleep", lambda s: None)
    conn = connect(tmp_path / "fund.sqlite")
    wid = worklist.enqueue(conn, kind="spec_review", producer="orchestrator",
                           seat="critic", subject="spec_abc",
                           expires_at="2999-01-01T00:00:00+00:00",
                           now_iso=iso(START))
    conn.close()

    assert run_dispatcher.main(["--cycles", "2"]) == 0

    conn = connect(tmp_path / "fund.sqlite")
    assert conn.execute("SELECT status FROM worklist WHERE work_id=?",
                        (wid,)).fetchone()["status"] == "failed"
    alerts = [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert [a["code"] for a in alerts] == ["work_failed"]
    assert "NoConsumer" in alerts[0]["text"]
    assert len(slack.posts["#risk"]) == 1


def test_main_exits_one_and_alerts_when_the_body_raises(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    slack = FakeSlack()
    monkeypatch.setattr(run_dispatcher, "_build_slack", lambda env, environ: slack)

    def boom(*a, **k):
        raise RuntimeError("db on fire")
    monkeypatch.setattr(run_dispatcher, "run_dispatcher_loop", boom)

    assert run_dispatcher.main(["--cycles", "1"]) == 1
    conn = connect(tmp_path / "fund.sqlite")
    alerts = [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert [a["code"] for a in alerts] == ["dispatcher_failed"]
    assert "db on fire" in alerts[0]["text"]
    assert len(slack.posts["#risk"]) == 1


def test_main_refuses_to_run_without_the_paper_flag(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")
    with pytest.raises(SystemExit):
        run_dispatcher.main(["--cycles", "1"])


def test_main_exits_zero_when_another_dispatcher_holds_the_lock(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    lock = run_dispatcher.run_day.acquire_lock(tmp_path / run_dispatcher.LOCK_NAME)
    assert lock is not None
    monkeypatch.setattr(run_dispatcher, "_build_slack",
                        lambda env, environ: pytest.fail("must not build slack"))
    assert run_dispatcher.main(["--cycles", "1"]) == 0
```

Check before writing: `run_day.paper_guard` raises `SystemExit` when `ALPACA_PAPER_TRADE != "true"` — confirm at `scripts/run_day.py` (grep `def paper_guard`) and, if it raises something else, assert on that type instead. Do not weaken the test; match the real exception.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_run_dispatcher.py -q 2>&1 | tail -3`
Expected: FAIL — `FileNotFoundError` on `scripts/run_dispatcher.py`.

- [ ] **Step 3: Implement.** Create `scripts/run_dispatcher.py`:

```python
"""Composition root for the Lane B dispatcher (resident-seats design, R1).

R1 ships the loop with NO consumer registered — R2 registers spec_review — so
every claimed row fails loudly (work_failed alert, row failed, nothing
requeued). That is the honest R1 behaviour and the correct default: a row the
fund cannot act on must never sit claimed or silently vanish.

scripts/ is deliberately outside the purity lint, which is why WallClock and
time.sleep are instantiated here — and only here. Everything else is injected
into orchestrator.dispatch.run_dispatcher. Runs under ops/fund-dispatcher.service
(committed, NOT installed — installing units is a human act; installed units
are copies, see ops/README.md)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/run_dispatcher.py` anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.clock import iso                    # noqa: E402
from orchestrator.dispatch import run_dispatcher as run_dispatcher_loop  # noqa: E402
from slackkit.outbox import drain                     # noqa: E402
from state.db import connect                          # noqa: E402

# No Alpaca, no Anthropic: R1 places no orders and runs no model.
REQUIRED_ENV = ("FUND_DB", "SLACK_BOT_TOKEN")
# Its own lock: two dispatchers on one queue would both claim and both sweep.
LOCK_NAME = "dispatcher.lock"


def log(msg: str) -> None:
    print(f"run_dispatcher: {msg}", flush=True)


class NoConsumer(RuntimeError):
    """R1 has no wake for any kind; R2 registers the first."""


def no_consumer(row: dict) -> None:
    raise NoConsumer(f"no consumer registered for kind {row['kind']!r}"
                     f" (work {row['work_id']}); R1 ships the loop only")


def _build_slack(env: dict, environ):
    """Named seam so tests can drive main() without a network client."""
    from slackkit.real import RealSlack
    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)
    return slack


def _guarded(conn, slack, clock, body) -> int:
    """Never a silent death. Exit 1 makes OnFailure=fund-alert@%n.service fire
    (the report path that shares no failure mode with this process); the
    drained alert covers the case where Slack works. Same posture as
    scripts/critic_g1._guarded."""
    try:
        return body()
    except (Exception, SystemExit) as exc:
        text = (f"dispatcher_failed — {type(exc).__name__}: {exc}. The"
                " dispatcher stopped; open rows expire on schedule (each with"
                " its own alert), nothing retries itself (invariant 4).")
        log(f"ALERT {text}")
        try:
            run_day._alert(conn, clock, "dispatcher_failed", text)
            drain(conn, slack, iso(clock.now()))
        except Exception as inner:
            log(f"could not record/post that alert ({type(inner).__name__}:"
                f" {inner}) — the failure above is the one that matters;"
                " systemd's OnFailure carries it out of the box")
        return 1


def main(argv: list[str] | None = None) -> int:
    from agents.wallclock import WallClock

    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=None,
                        help="stop after N cycles (tests); default runs until killed")
    args = parser.parse_args(argv)

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    db_path = env["FUND_DB"]
    lock = run_day.acquire_lock(Path(db_path).parent / LOCK_NAME)  # kept in scope
    if lock is None:
        log("another dispatcher holds the lock — exiting 0 rather than racing it")
        return 0

    clock = WallClock()
    conn = connect(db_path)
    slack = _build_slack(env, environ)

    def _body() -> int:
        cycles = run_dispatcher_loop(conn, slack, clock, no_consumer,
                                     sleep=time.sleep, max_cycles=args.cycles)
        log(f"stopped after {cycles} cycle(s)")
        return 0

    return _guarded(conn, slack, clock, _body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

Note: `main` receives `sys.argv[1:]` (argparse convention), unlike `critic_g1.main(sys.argv)` — the tests pass `["--cycles", "2"]`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_run_dispatcher.py -q`
Expected: all PASS.

- [ ] **Step 5: Full suite (alert-code lint scans `scripts/`), commit**

```bash
make test 2>&1 | tail -3
git add scripts/run_dispatcher.py tests/test_run_dispatcher.py
git commit -m "feat(scripts): run_dispatcher composition root with a no-consumer wake (#228)"
```

---

### Task 8: `ops/fund-dispatcher.service` (committed, not installed) + README row + unit test

**Files:**
- Create: `ops/fund-dispatcher.service`
- Modify: `ops/README.md` units table (~line 30-34)
- Test: `tests/test_ops_units.py` (append)

- [ ] **Step 1: Write the failing test.** Append to `tests/test_ops_units.py`:

```python
DISPATCHER = (ROOT / "ops" / "fund-dispatcher.service").read_text()


def test_dispatcher_unit_is_resident_but_never_restarts_itself():
    """Phase 6 R1 (#228). The fund's first long-lived unit keeps the firm-wide
    rule: no Restart= (invariant 4 — a dead dispatcher alerts and waits for a
    human; queued rows expire on schedule, each with its own alert)."""
    directives = [l for l in DISPATCHER.splitlines() if l and not l.startswith("#")]
    assert "Type=simple" in directives
    assert not any(l.startswith("Restart=") for l in directives), directives
    assert not any(l.startswith("WatchdogSec=") for l in directives), directives
    assert "OnFailure=fund-alert@%n.service" in directives
    assert _exec_starts(DISPATCHER) == [
        "/opt/fund/.venv/bin/python3 /opt/fund/scripts/run_dispatcher.py"]


def test_dispatcher_unit_is_documented_as_not_installed():
    assert "fund-dispatcher.service" in OPS_README
    assert "not installed" in OPS_README.split("fund-dispatcher.service", 1)[1][:400]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_ops_units.py -q 2>&1 | tail -3`
Expected: FAIL — `FileNotFoundError: ... ops/fund-dispatcher.service` (collection error).

- [ ] **Step 3: Create `ops/fund-dispatcher.service`:**

```ini
[Unit]
Description=fund — Lane B dispatcher (resident-seats R1; no consumer registered until R2)
Documentation=file:///opt/fund/ops/README.md
# A dispatcher that dies must say so. OnFailure reaches Slack by curl from
# /etc/fund/alert-env — no DB, no python, no fund imports — so it shares no
# failure mode with the process it reports on.
OnFailure=fund-alert@%n.service

[Service]
Type=simple
User=fund
Group=fund
WorkingDirectory=/opt/fund
EnvironmentFile=/etc/fund/env
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/run_dispatcher.py
# NO Restart=. Invariant 4: the default is HOLD. A dead dispatcher alerts (above)
# and waits for a human; open rows expire on their own schedule, each with an
# alert, so nothing is lost and nothing retries itself. No WatchdogSec either:
# there is nothing to watch until R2 registers a consumer.

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Add the README row.** In `ops/README.md`, after the `fund-alert@.service` row of the units table, add:

```markdown
| `fund-dispatcher.service` | **not installed** — committed by #228 (Phase 6 R1); install is a human act at R2, when a consumer exists | `scripts/run_dispatcher.py` (resident; `Type=simple`, no `Restart=`) |
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python3 -m pytest tests/test_ops_units.py -q`
Expected: all PASS.

- [ ] **Step 6: Full suite, commit**

```bash
make test 2>&1 | tail -3
git add ops/fund-dispatcher.service ops/README.md tests/test_ops_units.py
git commit -m "feat(ops): fund-dispatcher.service, committed not installed (#228)"
```

---

### Task 9: Region journal entries

**Files:**
- Modify: `.claude/regions/state.md` (append under `# Journal`)
- Modify: `.claude/regions/orchestrator.md` (append under `# Journal`)

Append only. Never edit prior entries. Content is what the next owner would re-derive, not status.

- [ ] **Step 1: Append to `.claude/regions/state.md`:**

```markdown

## 2026-09-13 · #228 · fund #170 overseer
- `try_transition` CASes on the **value of `status`**, hardcoded; `state_version`
  is read by nothing anywhere (`strategies` declares it unused). A table whose
  column is `state` cannot use the helper — which is why `worklist` deviates
  from the design doc's DDL and calls its column `status`, and why it has no
  `state_version`. `tests/test_state.py:41` pins EDGES == {tables with a
  `status` column}, so a new status table must land in `STATUSES` there too.
- `try_transition(..., extra={col: val})` now writes extra columns in the same
  UPDATE as the CAS — the only way a claim's lease lands atomically with it.
- `enqueue` is idempotent by `work_id(kind, subject, str(attempts))` via
  `INSERT OR IGNORE`; the first payload wins. There is no `alerts` table —
  the design doc's line 82 has no referent; alerts are `events(kind='alert')`.
```

- [ ] **Step 2: Append to `.claude/regions/orchestrator.md`:**

```markdown

## 2026-09-13 · #228 · fund #170 overseer
- `orchestrator/dispatch.py` is the first long-lived loop in the package.
  There was no scheduler/sweep concept to sit beside: the only prior sweep is
  `gate/tickets.py:expire_open_tickets`, called from inside a stage, and the
  only prior loop is `reconcile.py`'s bounded fill-poll. Sleep stays an
  injected callable (`reconcile.py:220` precedent); `Clock` has no sleep.
- `run_dispatcher` drains the outbox only after a cycle that wrote something.
  An unconditional drain would race `run_day`'s per-stage drain on the same
  `events` rows — the outbox tolerates duplicates but nobody wants them.
- Daily Lane B budget caps are NOT here (deferred to R2): no `costs` column
  separates Lane B spend from Lane A, and nothing in R1 spends.
- `ops/README.md:44` + `tests/test_ops_units.py` make "no `Restart=`" a
  firm-wide unit rule; the resident unit follows it and relies on `OnFailure`.
```

- [ ] **Step 3: Commit**

```bash
git add .claude/regions/state.md .claude/regions/orchestrator.md
git commit -m "docs(regions): state + orchestrator journal entries for #228"
```

---

## Self-review (done by the plan author)

**Spec coverage against the design doc's R1 paragraph and the issue's acceptance list:**
- DDL + `transition()` edges → Tasks 1–3. ✔
- dispatcher → Task 6 (`dispatch_once`, `run_dispatcher`), composition Task 7. ✔
- sweep → Task 6 `sweep`, alerts `work_lease_expired` / `work_expired`. ✔
- systemd supervision → Task 8 (uninstalled, ruling 3). ✔
- Producers orchestrator + alert_filer only → `PRODUCER_KINDS`, Task 5; enforced at enqueue (Task 5) and at claim (Task 6). ✔
- CAS under concurrent claim → `test_concurrent_claimers_on_one_row_yield_exactly_one_claim`. ✔
- lease reap alerts / expiry alerts → Task 6 tests. ✔
- sim-day with empty queue → zero wakes, zero cost rows → `test_idle_queue_costs_nothing`. ✔
- dispatcher kill mid-claim → reap + alert, no lost row → `test_dispatcher_killed_mid_claim_is_reaped_on_the_next_start`. ✔
- illegal transition raises → Task 2's parametrized non-edges. ✔
- Budget caps → deferred to R2 by ruling 4 (recorded on #170). ✔
- Not in R1 by design: recordings of wakes (no wake exists), `dedupe_key` beyond `attempts`, any Slack listener.

**Type consistency:** `claim_next` returns `sqlite3.Row`; `dispatch_once` passes `dict(row)` to `run_wake` — tests index `seen[0]["work_id"]` on a dict. `SweepResult`/`DispatchResult` are frozen dataclasses compared by value in tests. `transition`/`try_transition` `extra` is keyword-only everywhere.
