# Phase 1a Implementation Plan — Test Scaffolding, Clock, State Machine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The foundation slice of Phase 1: pytest wiring (offline-by-default, `live` marker), the injected `Clock`/`SimClock`, and the `state/` package — `specs/contracts.md` §2 DDL, §3 models, §1 CAS `transition()` — all green via `make test`. This plan is the prerequisite for `plans/phase-1b.md` (the rest of acceptance §0 + Phase 1). Nothing from later phases.

**Architecture:** Purity-linted deterministic core: `orchestrator/clock.py` (Clock protocol + settable/acceleratable SimClock) and `state/` (SQLite DDL, pydantic models, compare-and-swap transitions — the ONLY way workflow rows change status). The single wall-clock implementation (`WallClock`) sits in the impure `agents/` boundary and is injected at composition roots. SQLite is the source of truth.

**Tech Stack:** Python 3.12+ (dev venv is 3.14), sqlite3 (stdlib), pydantic v2, pytest ≥8; `claude-agent-sdk` and `slack-bolt` are pinned into `pyproject.toml` in Task 1 but first used in phase-1b. Offline tests use no network and no keys.

## Global Constraints

Every task implicitly includes all of these. Copied from `CLAUDE.md` (the 7 invariants, verbatim):

1. **Paper only.** `ALPACA_PAPER_TRADE=true` everywhere. Never add live-trading code paths, config flags, or TODOs pointing at live trading.
2. **Only the Execution Trader seat has the `trading` toolset.** Every other seat: read-only Alpaca toolsets, plus SDK-level `disallowed_tools` deny on `mcp__alpaca__place_*`.
3. **`gate/`, `stratgate/`, and `calibration/` import no LLM code.** No `claude_agent_sdk`, no `anthropic`, no prompt strings. Pure Python + SQLite. Enforced in CI by `scripts/check_purity.py` (AST lint: forbidden imports + wall-clock calls; runs in `make test`). Gate thresholds change only by human commit — never by an agent.
4. **Default is HOLD.** Any error, timeout, malformed tool input, or ambiguity anywhere in the pipeline resolves to no action — never to a guess.
5. **Orders are idempotent.** `client_order_id` = gate ticket id, always. Alpaca 422-rejects duplicates; never mint a new id on retry.
6. **SQLite is the source of truth; Slack is a projection.** Never read workflow state from Slack; never trigger execution from a Slack event.
7. **Agents emit structured data only via MCP tools** (`submit_signal`, `submit_decision`, strict schemas) — never as free text that code parses.

Additional binding constraints:

- **NEVER update a golden fixture, expected hash, or expected value to make a test pass. Report BLOCKED instead.** (CLAUDE.md Test invariants: tests are the spec — a failing test means the implementation is wrong.)
- **Run tests with: `make test`.** It bootstraps `.venv` on first run (works from a clean checkout or a fresh git worktree) and re-syncs deps whenever `pyproject.toml` changes. All commands below assume repo root `/Users/benjaminmatton/Developer/fund`.
- `scripts/check_purity.py` lints `gate/`, `stratgate/`, `fundbt/`, `calibration/`, `orchestrator/`, `state/` — the new `state/` and `orchestrator/` packages are auto-covered the moment they exist. No `datetime.now()`/`datetime.utcnow()`/`date.today()`/`time.sleep()` and no `claude_agent_sdk`/`anthropic`/`slack_bolt`/`slack_sdk`/`agents` imports there.
- Do not import from `agents/` inside `gate/` or `orchestrator/` (inject callables instead).
- Schemas are **verbatim** from `specs/contracts.md` §1–§3. Zero schema invention. State changes only via `state.transition()` (compare-and-swap); illegal transition = raise, never overwrite.
- Time comes only from the injected `Clock`. Timestamps stored as ISO8601 UTC via `orchestrator.clock.iso()`.
- Never put per-run values (timestamps, uuids, tmp paths) into prompts; pass them to tools out-of-band.
- Offline-by-default tests: `make test` needs no network, no keys. `@pytest.mark.live` tests are excluded by default.
- Spec-minimal: no abstractions or flexibility the spec doesn't require. Everything in phase-1b or later (slackkit, gate, runtime hooks, execution stage, charter) is OUT of scope here.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`.
- Work directly on `master` (kickoff: work on the default branch until Phase 1 lands).

## Decisions (settled)

Spec-silent gaps, ruled at plan review (2026-07-10). Both phase-1 plans are written against these rulings — they are binding, not open. (Task numbers 4+ refer to `plans/phase-1b.md`.)

1. **`mcp__fund__list_open_tickets` read tool (1b Task 7): adopted.** Prompts may not carry per-run values (ticket uuids, expiries), so the trader learns tickets through a tool; it is read-only and exec-seat-only, so agent→state writes remain `submit_*`-only (invariant 7 intact).
2. **Recording JSONL line format (1b Task 7): `{"seat": str, "tool": str, "args": object}`, one object per line; files named `<run_date>-<seat>-<stage>.jsonl`.** The minimal shape that captures a tool-call decision; anything richer would bake per-run context into recordings and poison replays.
3. **Test recordings live in `tests/recordings/*.jsonl`, checked in (1b Tasks 8–9): adopted.** Runtime `recordings/` is a gitignored volume, so acceptance fixtures cannot live there.
4. **Fill-message ticket id is `id[:8]`, per contracts §8 (1b Task 4): adopted.** `contracts.md` is canonical; golden-day's `(ticket a3f9)` is an abridging ellipsis, not a 4-char spec.
5. **`WallClock` lives in `agents/wallclock.py` (1a Task 2): adopted.** `orchestrator/` is purity-linted (wall-clock calls forbidden), so the one real-clock implementation sits in the impure boundary and is injected at composition roots only.
6. **Checkpoint found `running` at stage start = crash resume → re-run the idempotent body (1b Task 8): adopted.** Matches contracts §6 ("stages `done` never re-run" — non-done stages do re-run); same-process duplicate-trigger suppression beyond `done`-skip is deferred while Phase 1 is single-process.
7. **Charters stay at top-level `charters/` (not design.md §6's `agents/charters/`) (1b Task 10): adopted.** CLAUDE.md and the existing repo layout govern.

## Acceptance checklist → task map

| `specs/acceptance.md` item | Task |
|---|---|
| §0 pytest markers (offline default, `@live` excluded) | 1 |
| §0 `Clock` protocol + `SimClock` | 2 |
| §0 fixtures: temp SQLite from contracts DDL | 3 |
| P1 DDL applies cleanly; `transition()` rejects every non-edge | 3 |
| P1 `make test` green, no network/keys | every task |

All remaining §0 and Phase-1 lines are closed by `plans/phase-1b.md`.

## Produces for phase-1b — the handoff contract

phase-1b consumes exactly these; do not rename or reshape them:

- `orchestrator.clock.Clock` — protocol: `now() -> datetime` (tz-aware)
- `orchestrator.clock.SimClock(start: datetime)` — `.now() -> datetime`, `.set(dt: datetime) -> None`, `.advance(*, seconds=0, minutes=0, hours=0, days=0) -> None`; rejects naive datetimes with `ValueError`
- `orchestrator.clock.iso(dt: datetime) -> str` — ISO8601 UTC, seconds precision (`"2026-07-06T15:30:00+00:00"`)
- `agents.wallclock.WallClock` — `.now() -> datetime` (aware, UTC)
- `state.db.connect(db_path: str | Path) -> sqlite3.Connection` — Row factory, `PRAGMA foreign_keys = ON`, applies `state/schema.sql` (contracts §2 verbatim) when tables are absent
- `state.transition.transition(conn, table: str, key: dict, from_status: str, to_status: str, now_iso: str) -> None` — raises `IllegalTransition` (non-edge) / `StaleTransition` (CAS miss); touches `updated_at` on checkpoints
- `state.transition.try_transition(conn, table, key, from_status, to_status, now_iso) -> bool` — False on CAS miss; still raises `IllegalTransition` on non-edges
- `state.transition.EDGES: dict[str, set[tuple[str, str]]]` and `state.transition.KEYS: dict[str, tuple[str, ...]]` — tables: `decisions`, `tickets`, `orders`, `checkpoints`
- `state.models.Side` (`Literal["buy", "sell"]`), `state.models.Ticket`, `state.models.GateResult` — contracts §3 verbatim
- pytest fixture `fund_db` (`tests/conftest.py`) — temp SQLite connection with the full DDL applied
- pytest `live` marker registered; `make test` runs pytest offline-by-default (`addopts = "-m 'not live' -q"`)

---

### Task 1: Test scaffolding — deps, pytest markers, Makefile

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile` (test target only)
- Create: `tests/test_markers.py`

**Interfaces:**
- Produces: `pytest` as the `make test` runner with `live` marker excluded by default; `pydantic`, `claude-agent-sdk`, `slack-bolt` importable in the venv.

- [ ] **Step 1: Add dependencies and pytest config to `pyproject.toml`**

Replace the `dependencies` list and add the pytest section (keep the existing `[project]` fields and comments):

```toml
dependencies = [
    "numpy>=1.23,<3.0",
    "pandas>=2.0,<3.0",
    "pydantic>=2.7,<3",
    "claude-agent-sdk>=0.1",
    "slack-bolt>=1.18,<2",
]
```

and append at the bottom of the file:

```toml
[tool.pytest.ini_options]
addopts = "-m 'not live' -q"
markers = [
    "live: hits real APIs (Alpaca paper, Slack, Anthropic) — excluded from make test; run manually with: pytest -m live <file> -v",
]
```

Then tighten the SDK pin to the latest published version: run `.venv/bin/pip index versions claude-agent-sdk` and change `>=0.1` to `~=<latest>` (CLAUDE.md: deps pinned in pyproject).

- [ ] **Step 2: Sync the venv**

Run: `make lint`
Expected: the Makefile re-syncs `.venv` from the edited `pyproject.toml` (`scripts/sync_deps.py`, gated on a content hash of the file — not mtime), then the purity lint passes. Verify: `.venv/bin/python -c "import pydantic, slack_bolt, claude_agent_sdk, pytest; print('ok')"` prints `ok`.

- [ ] **Step 3: Write the marker canary test**

`tests/test_markers.py`:

```python
"""Proves the offline-by-default marker config (acceptance §0): a live-marked
test that always fails must never run under `make test`."""

import pytest


@pytest.mark.live
def test_live_canary_is_excluded_from_default_run():
    raise AssertionError(
        "live-marked test executed in an offline run — pytest marker config is broken"
    )
```

- [ ] **Step 4: Point `make test` at pytest**

In `Makefile`, replace the `test` recipe line `$(PYTHON) tests/run_tests.py` with:

```make
test: lint
	$(PYTHON) -m pytest tests/
```

(`$(PYTHON)` already resolves to `.venv/bin/python3`; `addopts` supplies `-m 'not live' -q`. `tests/run_tests.py` stays in place as the zero-dep fallback runner.)

- [ ] **Step 5: Verify**

Run: `make test`
Expected: purity lint clean, all existing tests pass (34 at time of writing), `test_live_canary` NOT run, exit 0.

Run: `.venv/bin/pytest tests/test_markers.py -m live` 
Expected: 1 failed (the canary runs and fails — proving `-m live` selects it). This failure is correct and expected here only.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Makefile tests/test_markers.py
git commit -m "feat: pytest as offline-by-default test runner with live marker"
```

---

### Task 2: Clock protocol + SimClock

**Files:**
- Create: `orchestrator/__init__.py` (empty), `orchestrator/clock.py`
- Create: `agents/__init__.py` (empty), `agents/wallclock.py`
- Test: `tests/test_clock.py`

**Interfaces:**
- Produces: `orchestrator.clock.Clock` (protocol, `now() -> datetime` aware), `orchestrator.clock.SimClock(start)` with `.set(dt)` / `.advance(seconds=, minutes=, hours=, days=)`, `orchestrator.clock.iso(dt) -> str` (ISO8601 UTC, seconds precision), `agents.wallclock.WallClock`.

- [ ] **Step 1: Write the failing tests**

`tests/test_clock.py`:

```python
from datetime import datetime, timezone

import pytest

from orchestrator.clock import SimClock, iso


UTC = timezone.utc


def test_simclock_returns_start():
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    assert c.now() == datetime(2026, 7, 6, 15, 30, tzinfo=UTC)


def test_simclock_set_and_advance():
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    c.advance(minutes=45)
    assert c.now() == datetime(2026, 7, 6, 16, 15, tzinfo=UTC)
    c.advance(days=5)  # acceleratable: jump days at will
    assert c.now() == datetime(2026, 7, 11, 16, 15, tzinfo=UTC)
    c.set(datetime(2026, 7, 6, 12, 0, tzinfo=UTC))
    assert c.now() == datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def test_simclock_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 7, 6, 15, 30))
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    with pytest.raises(ValueError):
        c.set(datetime(2026, 7, 6))


def test_iso_normalizes_to_utc_seconds():
    assert iso(datetime(2026, 7, 6, 15, 30, tzinfo=UTC)) == "2026-07-06T15:30:00+00:00"


def test_wallclock_is_aware_utc():
    from agents.wallclock import WallClock

    now = WallClock().now()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_clock.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator'`.

- [ ] **Step 3: Implement**

`orchestrator/clock.py`:

```python
"""Injected time (design.md §4 Testability). Business logic never reads the
wall clock; it receives a Clock. The only real-clock implementation lives in
agents/wallclock.py — this package is purity-linted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


def iso(dt: datetime) -> str:
    """Canonical timestamp format for the DB: ISO8601 UTC, seconds precision."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime — all fund datetimes are tz-aware")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class SimClock:
    """Settable, acceleratable clock for tests and sim-day (acceptance §0)."""

    def __init__(self, start: datetime):
        self._now = _aware(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = _aware(dt)

    def advance(self, *, seconds: int = 0, minutes: int = 0, hours: int = 0,
                days: int = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours,
                               days=days)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("SimClock requires tz-aware datetimes")
    return dt
```

`agents/wallclock.py`:

```python
"""The one real-clock implementation. Lives outside the purity-linted
packages (orchestrator/ may not call datetime.now); injected at composition
roots only — live-paper mode and the @live smoke test."""

from datetime import datetime, timezone


class WallClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
```

`orchestrator/__init__.py` and `agents/__init__.py`: empty files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_clock.py`
Expected: 5 passed.

Run: `.venv/bin/python scripts/check_purity.py`
Expected: `PURITY LINT: clean` — and the package list now includes `orchestrator`.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/ agents/ tests/test_clock.py
git commit -m "feat: Clock protocol, SimClock, WallClock (acceptance §0)"
```

---

### Task 3: state/ — DDL, models, transition()

**Files:**
- Create: `state/__init__.py` (empty), `state/schema.sql`, `state/db.py`, `state/models.py`, `state/transition.py`
- Create: `tests/conftest.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces:
  - `state.db.connect(db_path) -> sqlite3.Connection` (Row factory, FK ON, applies DDL if tables absent)
  - `state.transition.transition(conn, table, key: dict, from_status, to_status, now_iso) -> None` (raises `IllegalTransition` on a non-edge, `StaleTransition` when CAS finds the row not in `from_status`)
  - `state.transition.try_transition(...) -> bool` (same, but False instead of `StaleTransition` — for idempotent handlers; still raises `IllegalTransition` on non-edges)
  - `state.transition.EDGES`, `state.transition.KEYS`
  - `state.models.Ticket`, `state.models.GateResult`, `state.models.Side`
  - pytest fixture `fund_db` (temp SQLite with full DDL)

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import pytest

from state.db import connect


@pytest.fixture
def fund_db(tmp_path):
    """Temp SQLite with the full contracts.md §2 DDL applied (acceptance §0)."""
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()
```

`tests/test_state.py`:

```python
import pytest

from state.db import connect
from state.transition import (EDGES, IllegalTransition, StaleTransition,
                              transition, try_transition)

NOW = "2026-07-06T15:30:00+00:00"

TABLES = {"signals", "critiques", "decisions", "tickets", "orders",
          "resolutions", "checkpoints", "events", "costs"}

STATUSES = {
    "decisions": ["submitted", "approved", "rejected", "executed", "failed", "expired"],
    "tickets": ["open", "consumed", "expired"],
    "orders": ["submitted", "filled", "partially_filled", "canceled", "rejected"],
    "checkpoints": ["pending", "running", "done", "failed"],
}

NON_EDGES = [(t, a, b) for t, ss in STATUSES.items()
             for a in ss for b in ss if (a, b) not in EDGES[t]]


def test_ddl_applies_cleanly_and_is_idempotent(tmp_path):
    path = tmp_path / "fund.sqlite"
    conn = connect(path)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    conn.close()
    conn2 = connect(path)  # re-open existing DB: must not error or wipe
    assert conn2.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"] == 0
    conn2.close()


def test_foreign_keys_enforced(fund_db):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        fund_db.execute(
            "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
            " expires_at, created_at) VALUES ('t1', 999, 'NVDA', 'buy', 1, ?, ?)",
            (NOW, NOW))


@pytest.mark.parametrize("table,frm,to", NON_EDGES)
def test_every_non_edge_raises(fund_db, table, frm, to):
    key = {"id": 1} if table != "checkpoints" else {
        "run_date": "2026-07-06", "stage": "execution", "ticker": "*"}
    if table == "orders":
        key = {"client_order_id": "x"}
    with pytest.raises(IllegalTransition):
        transition(fund_db, table, key, frm, to, NOW)


def _seed_decision(conn, status="submitted"):
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06', 'NVDA', 'buy', 67, 't', 'i', ?, ?)", (status, NOW))
    conn.commit()
    return cur.lastrowid


def test_cas_moves_row(fund_db):
    did = _seed_decision(fund_db)
    transition(fund_db, "decisions", {"id": did}, "submitted", "approved", NOW)
    row = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["status"] == "approved"


def test_cas_stale_raises_and_leaves_row(fund_db):
    did = _seed_decision(fund_db, status="approved")
    with pytest.raises(StaleTransition):
        transition(fund_db, "decisions", {"id": did}, "submitted", "approved", NOW)
    row = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["status"] == "approved"  # never overwritten


def test_try_transition_returns_false_on_stale(fund_db):
    did = _seed_decision(fund_db, status="approved")
    assert try_transition(fund_db, "decisions", {"id": did},
                          "submitted", "approved", NOW) is False
    assert try_transition(fund_db, "decisions", {"id": did},
                          "approved", "executed", NOW) is True


def test_checkpoint_transition_touches_updated_at(fund_db):
    fund_db.execute(
        "INSERT INTO checkpoints (run_date, stage, ticker, status, updated_at)"
        " VALUES ('2026-07-06', 'execution', '*', 'pending', 'old')")
    fund_db.commit()
    key = {"run_date": "2026-07-06", "stage": "execution", "ticker": "*"}
    transition(fund_db, "checkpoints", key, "pending", "running", NOW)
    row = fund_db.execute(
        "SELECT status, updated_at FROM checkpoints WHERE run_date='2026-07-06'"
        " AND stage='execution' AND ticker='*'").fetchone()
    assert row["status"] == "running" and row["updated_at"] == NOW


def test_unknown_table_or_bad_key_raises(fund_db):
    with pytest.raises(IllegalTransition):
        transition(fund_db, "signals", {"id": 1}, "a", "b", NOW)
    with pytest.raises(ValueError):
        transition(fund_db, "decisions", {"wrong_col": 1}, "submitted", "approved", NOW)


def test_ticket_and_gateresult_models_validate():
    from state.models import GateResult, Ticket

    t = Ticket(id="a3f90000-0000-4000-8000-000000000001", decision_id=1,
               ticker="NVDA", side="buy", max_qty=67, stop_price=None,
               expires_at="2026-07-06T16:00:00+00:00")
    assert t.max_qty == 67
    with pytest.raises(Exception):
        Ticket(id="x", decision_id=1, ticker="NVDA", side="buy", max_qty=0,
               expires_at="2026-07-06T16:00:00+00:00")
    r = GateResult(approved=False, reason="gate_error")
    assert r.ticket is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_state.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Implement**

`state/schema.sql` — the DDL from `specs/contracts.md` §2, **verbatim, comments included** (copy the entire ```sql block contents — all nine CREATE TABLE statements and their trailing comment lines — into this file, changing nothing).

`state/db.py`:

```python
"""SQLite is the source of truth (invariant 6). One connect() for app + tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "tickets" not in have:
        conn.executescript(_SCHEMA.read_text())
        conn.commit()
    return conn
```

`state/models.py`:

```python
"""Pydantic models — contracts.md §3, verbatim. Phase 1 needs Ticket and
GateResult; Signal/Critique/Decision arrive with Phase 2 seats."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]


class Ticket(BaseModel):
    id: str
    decision_id: int
    ticker: str
    side: Side
    max_qty: int = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    expires_at: datetime


class GateResult(BaseModel):
    approved: bool
    ticket: Ticket | None = None
    reason: str | None = None                # required when approved=False
```

`state/transition.py`:

```python
"""Compare-and-swap state transitions — contracts.md §1. The ONLY way any
workflow row changes status. Illegal transition = raise, never overwrite."""

from __future__ import annotations

import sqlite3

EDGES: dict[str, set[tuple[str, str]]] = {
    "decisions": {("submitted", "approved"), ("submitted", "rejected"),
                  ("approved", "executed"), ("approved", "failed"),
                  ("approved", "expired")},
    "tickets": {("open", "consumed"), ("open", "expired")},
    "orders": {("submitted", "filled"), ("submitted", "partially_filled"),
               ("submitted", "canceled"), ("submitted", "rejected"),
               ("partially_filled", "filled"), ("partially_filled", "canceled")},
    "checkpoints": {("pending", "running"), ("running", "done"),
                    ("running", "failed")},
}

KEYS: dict[str, tuple[str, ...]] = {
    "decisions": ("id",),
    "tickets": ("id",),
    "orders": ("client_order_id",),
    "checkpoints": ("run_date", "stage", "ticker"),
}


class IllegalTransition(Exception):
    """The requested edge does not exist in the state machine."""


class StaleTransition(Exception):
    """Legal edge, but the row is not currently in from_status (CAS failed)."""


def try_transition(conn: sqlite3.Connection, table: str, key: dict,
                   from_status: str, to_status: str, now_iso: str) -> bool:
    """CAS the row from from_status to to_status. False if the row is not in
    from_status (lets idempotent handlers no-op on re-run, contracts §5.2)."""
    if table not in EDGES:
        raise IllegalTransition(f"no state machine for table {table!r}")
    if (from_status, to_status) not in EDGES[table]:
        raise IllegalTransition(
            f"{table}: {from_status!r} -> {to_status!r} is not a legal edge")
    if set(key) != set(KEYS[table]):
        raise ValueError(f"{table} key must be exactly {KEYS[table]}, got {tuple(key)}")
    sets = "status = ?" + (", updated_at = ?" if table == "checkpoints" else "")
    params: list = [to_status] + ([now_iso] if table == "checkpoints" else [])
    where = " AND ".join(f"{col} = ?" for col in KEYS[table]) + " AND status = ?"
    params += [key[col] for col in KEYS[table]] + [from_status]
    cur = conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", params)
    conn.commit()
    return cur.rowcount == 1


def transition(conn: sqlite3.Connection, table: str, key: dict,
               from_status: str, to_status: str, now_iso: str) -> None:
    """CAS that raises StaleTransition when the row is not in from_status."""
    if not try_transition(conn, table, key, from_status, to_status, now_iso):
        raise StaleTransition(
            f"{table} {key}: not in {from_status!r} (or missing) — refusing to overwrite")
```

`state/__init__.py`: empty file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_state.py`
Expected: all pass. The non-edge parameterization alone is 70 cases (all ordered status pairs per table, self-pairs included, minus the 16 legal edges: 31+7+19+13); every one must raise `IllegalTransition`.

Run: `make test`
Expected: green; purity lint now covers `state` too.

- [ ] **Step 5: Commit**

```bash
git add state/ tests/conftest.py tests/test_state.py
git commit -m "feat: state package — contracts DDL, models, CAS transition()"
```

---


## SDD execution notes (for the controller; not part of any task)

- Dispatch order: strictly 1 → 2 → 3; no parallel implementers (shared files: conftest, pyproject).
- Model tiers: Task 2 is transcription (complete code above) — cheapest tier. Tasks 1 and 3 are mechanical with judgment at the edges — cheap/mid tier.
- The whole-branch review happens once, at the end of `plans/phase-1b.md`; after phase-1a, run only the per-task reviews.
- If any implementer needs to touch `tests/test_golden.py`, `fixtures/golden-strategy.md`, or starter-kit packages (`fundbt/`, `stratgate/`, `calibration/`) — STOP and escalate; those are frozen.
