# Improvement Loops (buildable half) Implementation Plan

<!-- plan-status -->
> **Status: DELIVERED, one task outstanding — 2026-08-25.** `build_trace()` (`evals/live.py`), `scripts/score_day.py` and `reflection_frame()` (`orchestrator/reflect.py`) are all on `master`.
>
> Follow-up on the board: #4 — the daily cycle still has no reflection *stage*, so `resolutions.reflection` has no writer. Filed from this plan's Task 5.
>
> **Checkbox state is not a progress signal and nothing reads it.** Measured 2026-08-24 across
> every plan file in this directory: 359 unchecked boxes, zero checked, including plans whose work
> demonstrably shipped. Ticking them is friction for the ticker and invisible to everyone else.
> Work in flight lives on the board — the `wayfinder:map` issue and its children. This plan is the
> *how*, referenced from an issue; it is never read as state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a trading day reviewable — persist every seat turn as a trace, attribute every graded call to a charter version, and produce a daily severity-ranked scorecard of the turns worth reading.

**Architecture:** Four additions, none of which grant any seat new authority. A pure `build_trace()` called from the composition root's per-seat wrapper records each turn; a schema migration adds attribution columns; a zero-dependency `score_day.py` reads the day's existing rows and appends one outbox event; a pure `reflection_frame()` computes the factual half of a reflection so the seat only interprets.

**Tech Stack:** Python 3.12, stdlib `sqlite3`, pydantic v2 (existing models only), pytest. No new dependencies.

## Global Constraints

- Paper only. `ALPACA_PAPER_TRADE=true`. No live-trading code path, flag, or TODO.
- No seat gains a tool, toolset, or settings source. The Execution Trader's `tools=[...]` allow-array is untouched.
- `gate/`, `stratgate/`, `calibration/` import no LLM code — `scripts/check_purity.py` must stay clean.
- Time comes from the injected `Clock`. No `datetime.now()` or `time.sleep()` in business logic. `scripts/` ops code is outside this rule (as `ops/backup.sh` documents).
- Never update a golden fixture, expected hash, or expected value to make a test pass. Stop and ask.
- `specs/contracts.md` is canonical for DDL — schema changes land there in the same commit.
- Structured data leaves agents only through MCP tool schemas. No parsing tickers, actions, or sizes out of free text.
- `make test` must pass before every commit.

## Coordination — read before starting

Two other plans are in flight in this repo:

- `docs/superpowers/plans/2026-08-18-critic-seat.md` — its Task 5 modifies `evals/trace.py` (adds `brief_subjects`, defaulted), `evals/grade.py` (adds `seat_registry()`, `grade_traces(invariants=None)`), and the runner. **Additive and compatible: this plan modifies none of those files.**
- **Conflict:** that plan also modifies `state/schema.sql`, `state/db.py`, `state/models.py`, and `specs/contracts.md`. So does Task 2 here. These must land sequentially, not concurrently. Check `git log` before starting Task 2; if the critic-seat schema task has landed, rebase onto it first.
- **`state/db.py` — complementary, not competing.** The critic-seat plan's Task 2 makes every statement in `schema.sql` `IF NOT EXISTS` and applies the script on every open, which fixes *new tables* never reaching an existing database. `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists, so that does nothing for a new *column* — which is what `state/migrations.py` (Task 2 here) is for. The two halves compose: apply the schema first, then `migrations.apply(conn)`. Land theirs first and call the migration from the end of `connect()`, after their apply.
- `docs/superpowers/plans/2026-08-18-second-analyst-seat.md` — modifies `specs/design.md` and `orchestrator/daily.py`. Task 5 here touches `orchestrator/`; check for conflicts before starting it.

Per `docs/adr/0001`, the second analyst is News/Sentiment, not Fundamentals. Do not introduce a `fundamentals` seat name in any new code or fixture.

## File Structure

| File | Responsibility |
|---|---|
| `evals/live.py` (new) | Pure `build_trace()` mapping one live seat turn to a `Trace`, plus a `file_sink()` factory. No SDK, no DB, no clock. |
| `scripts/run_day.py` (modify) | Composition root: passes a trace sink into `_seat_session`; assigns the per-day turn sequence. |
| `ops/backup.sh` (modify) | Adds the trace root to the nightly tarball. |
| `state/schema.sql` (modify) | `charter_version` and `model_id` on `signals` and `decisions`. |
| `state/migrations.py` (new) | Idempotent `ALTER TABLE` migration for existing databases; `connect()` only creates schema when absent. |
| `agents/tools/fund_server.py` (modify) | Two insert sites write the new columns. |
| `orchestrator/daily.py` (modify) | The `pm_timeout` default-decision insert writes the new columns. |
| `scripts/score_day.py` (new) | Zero-dependency severity-ranked scorecard; never exits non-zero on a low score. |
| `orchestrator/reflect.py` (new) | Pure `reflection_frame()` over `resolutions` + `signals`. |

---

### Task 1: Live traces

**Files:**
- Create: `evals/live.py`
- Create: `tests/test_live_trace.py`
- Modify: `scripts/run_day.py` (the `_seat_session` wrapper at line 202, and its call sites)
- Modify: `ops/backup.sh`
- Modify: `.env.example` (add `FUND_TRACES`)

**Interfaces:**
- Consumes: `evals.trace.Trace` (unchanged).
- Produces:
  - `evals.live.build_trace(*, seat, run_date, turn_seq, git_sha, charter_text, model, snapshot, brief_tickers, tool_names, result) -> Trace`
  - `evals.live.file_sink(root: str) -> Callable[[Trace], None]`
  - `_seat_session(cfg, db_path, clock, prompt, snapshot, *, trace_sink=None)` in `run_day.py`

**Why `case = f"live-{run_date}"`:** `Trace.case` and `Trace.trial` exist for the eval rig, where a case is a named scenario run N times. A live turn has neither. The `live-` prefix keeps the overload self-documenting and makes the corpus trivially separable (`case.startswith("live-")`) if `Trace` is later split into a turn payload plus a provenance discriminator. That split is deliberately deferred until after the first error-analysis pass — see the spec's Further Notes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_trace.py
"""A live seat turn becomes a Trace the existing grader can read."""
from __future__ import annotations

import json
from pathlib import Path

from evals.live import build_trace, file_sink
from evals.trace import Trace


class _Result:
    """Stands in for the SDK's ResultMessage — matched by type name upstream,
    so a plain object with the same attributes is a faithful double."""
    num_turns = 3
    total_cost_usd = 0.0141
    duration_ms = 8120
    is_error = False


def test_build_trace_maps_a_live_turn():
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=2, git_sha="abc1234",
        charter_text="# Portfolio Manager — v6\n", model="claude-opus-5",
        snapshot={"cash": 1000.0, "positions": [], "allowed_actions": ["hold"]},
        brief_tickers=["NVDA"], tool_names=["mcp__fund__submit_decision"],
        result=_Result())

    assert t.case == "live-2026-08-18"
    assert t.trial == 2
    assert t.seat == "pm"
    assert t.tool_names == ["mcp__fund__submit_decision"]
    assert t.cost_usd == 0.0141
    assert t.turns == 3
    assert t.is_error is False


def test_build_trace_keeps_missing_cost_none_not_zero():
    """A fabricated 0.0 makes real spend look free — the lie agents/runtime.py
    refuses to tell, and what invariant I5 pairs with the cost_unavailable
    alert."""
    class _NoCost:
        num_turns = 1
        total_cost_usd = None
        duration_ms = 10
        is_error = False

    t = build_trace(
        seat="analyst", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_NoCost())
    assert t.cost_usd is None


def test_build_trace_survives_a_turn_that_produced_no_result():
    """A seat that timed out hands back None. That is a trace, not an
    exception — invariant 4: the day continues."""
    t = build_trace(
        seat="critic", run_date="2026-08-18", turn_seq=1, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=None)
    assert t.is_error is True
    assert t.error == "no result message"
    assert t.cost_usd is None


def test_file_sink_writes_where_the_grader_reads(tmp_path):
    sink = file_sink(str(tmp_path))
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Result())
    sink(t)

    written = tmp_path / "abc1234" / "live-2026-08-18" / "0.json"
    assert written.exists()
    assert Trace.read(written).seat == "pm"
    assert json.loads(written.read_text())["case"] == "live-2026-08-18"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.live'`

- [ ] **Step 3: Write minimal implementation**

```python
# evals/live.py
"""One live seat turn -> a Trace the eval grader already knows how to read.

Pure: no SDK import, no DB, no clock, no filesystem (the writer is a separate
factory). That is what lets the whole trace path be tested offline.

`case`/`trial` are the eval rig's provenance fields — a named scenario run N
times. A live turn has neither, so the run_date carries a `live-` prefix: the
overload stays self-documenting, and the corpus is separable by
`case.startswith("live-")` if Trace is later split.
"""

from __future__ import annotations

from typing import Callable

from evals.trace import Trace


def build_trace(*, seat: str, run_date: str, turn_seq: int, git_sha: str,
                charter_text: str, model: str, snapshot: dict,
                brief_tickers: list[str], tool_names: list[str],
                result: object | None) -> Trace:
    """Map one completed seat turn onto a Trace.

    A None result is a turn that produced no ResultMessage — a timeout or a
    crashed session. That is recorded as an errored trace rather than raised:
    the day continues on its defaults (invariant 4), and an errored trace is
    INCONCLUSIVE to every grader rather than a manufactured failure."""
    return Trace(
        case=f"live-{run_date}",
        trial=turn_seq,
        seat=seat,
        git_sha=git_sha,
        charter_sha="",
        charter_text=charter_text,
        model=model,
        snapshot=snapshot,
        brief_tickers=list(brief_tickers),
        tool_names=list(tool_names),
        turns=getattr(result, "num_turns", None),
        cost_usd=getattr(result, "total_cost_usd", None),
        duration_ms=getattr(result, "duration_ms", None),
        is_error=result is None or bool(getattr(result, "is_error", False)),
        error=None if result is not None else "no result message",
    )


def file_sink(root: str) -> Callable[[Trace], None]:
    """A sink that writes each trace under <root>/<git_sha>/<case>/<trial>.json
    — exactly where evals.grade.grade_traces looks."""
    def _write(trace: Trace) -> None:
        trace.write(root)
    return _write
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_trace.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add evals/live.py tests/test_live_trace.py
git commit -m "feat: one live seat turn becomes a Trace the grader can read"
```

- [ ] **Step 6: Write the failing test for the composition-root wiring**

Add to `tests/test_run_day.py` (it already loads the script by module path — reuse that loader, do not add a second one):

```python
def test_seat_session_emits_one_trace_per_turn(monkeypatch, tmp_path):
    """The sink is injected, so this asserts the wiring with no filesystem and
    no SDK: production passes evals.live.file_sink, tests pass list.append."""
    collected = []
    run_day = _load_run_day()          # existing loader in this module

    run_day.run_all_seat_turns(
        seats=[("pm", {"model": "m", "charter": "x"})],
        db_path=str(tmp_path / "fund.sqlite"),
        clock=_FrozenClock("2026-08-18T09:40:00+00:00"),
        run_date="2026-08-18",
        trace_sink=collected.append,
        session=_fake_session(tool_names=["mcp__fund__submit_decision"]))

    assert len(collected) == 1
    assert collected[0].case == "live-2026-08-18"
    assert collected[0].seat == "pm"
```

- [ ] **Step 7: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_day.py -k trace -v`
Expected: FAIL — `_seat_session` takes no `trace_sink`

- [ ] **Step 8: Thread the sink through the composition root**

In `scripts/run_day.py`, `_seat_session` gains a keyword-only `trace_sink=None`. After `run_seat_turn` returns, build and emit the trace. The seat's own `cfg` supplies model and charter text; `snapshot` and the brief tickers are already in scope. `turn_seq` is a per-day counter owned by the caller, not by the seat.

```python
async def _seat_session(cfg: dict, db_path: str, clock, prompt: str,
                        snapshot: dict, *, seat: str, run_date: str,
                        turn_seq: int, git_sha: str,
                        trace_sink=None):
    options = build_seat_options(cfg, db_path, clock, snapshot=snapshot,
                                 ...)                 # unchanged
    async with ... as client:
        tool_names, result = await run_seat_turn(client, prompt,
                                                 REQUIRED_SERVERS)
    if trace_sink is not None:
        # Never let a trace-write failure cost the fund a trading day: the
        # trace is evidence, not control flow. Same posture as
        # record_turn_result's cost path.
        try:
            trace_sink(build_trace(
                seat=seat, run_date=run_date, turn_seq=turn_seq,
                git_sha=git_sha, charter_text=cfg.get("charter_text", ""),
                model=cfg.get("model", ""), snapshot=snapshot,
                brief_tickers=snapshot.get("brief_tickers", []),
                tool_names=tool_names, result=result))
        except Exception as exc:
            log(f"ALERT trace_write_failed {seat} — "
                f"{type(exc).__name__}: {exc}; trading continues")
    return tool_names, result
```

- [ ] **Step 9: Run it to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_run_day.py -v`
Expected: PASS, including the pre-existing tests

- [ ] **Step 10: Run the full suite**

Run: `make test`
Expected: PASS. If `scripts/check_purity.py` flags `evals/live.py`, stop — it means the purity lint's scope was widened by another branch and the module placement needs rethinking, not a lint exemption.

- [ ] **Step 11: Commit**

```bash
git add scripts/run_day.py tests/test_run_day.py
git commit -m "feat: live runs persist one trace per seat turn"
```

- [ ] **Step 12: Add the trace root to config and backup**

In `.env.example`, beside `FUND_JOURNALS`:

```bash
FUND_TRACES=/var/lib/fund/traces
```

In `ops/backup.sh`, after the journals block (same shape — enumerate, never sweep):

```sh
if [ -n "${FUND_TRACES:-}" ] && [ -d "$FUND_TRACES" ]; then
    TTMP="${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz.tmp"
    tar -czf "$TTMP" -C "$(dirname "$FUND_TRACES")" "$(basename "$FUND_TRACES")"
    mv "$TTMP" "${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz"
    echo "backup: wrote ${FUND_BACKUP_DIR}/traces-${STAMP}.tar.gz"
fi
```

No pruning. Measured trace size is ~6.5 KB, so 10–25 turns/day is 16–40 MB/year — the same order as the DB snapshots `backup.sh`'s no-prune argument was written for. Adding a delete path would make this the only destructive operation in the deployment.

- [ ] **Step 13: Verify the backup change**

Run: `FUND_DB=/tmp/t.sqlite FUND_BACKUP_DIR=/tmp/bk FUND_TRACES=/tmp/tr sh -c 'mkdir -p /tmp/tr && touch /tmp/tr/x.json && sqlite3 /tmp/t.sqlite "create table tickets(id text)" && sh ops/backup.sh'`
Expected: two `backup: wrote ...` lines, one of them `traces-<date>.tar.gz`

- [ ] **Step 14: Commit**

```bash
git add ops/backup.sh .env.example
git commit -m "feat: traces ride the nightly backup, no retention policy"
```

---

### Task 2: Attribution columns

**Coordination:** check `git log` first. If the critic-seat plan's schema task has landed, rebase before starting.

**Files:**
- Modify: `state/schema.sql`
- Create: `state/migrations.py`
- Create: `tests/test_migrations.py`
- Modify: `state/db.py` (call the migration from `connect()`)
- Modify: `agents/tools/fund_server.py:52` and `:103`
- Modify: `orchestrator/daily.py:154` and `:185-190` — **both owned by this task**
- Modify: `state/critiques.py:18` — the `critiques` writer, same treatment
- Modify: `specs/contracts.md` (§1 DDL — canonical, same commit)

**Landing order — this task lands last, and that decides ownership.** The
second-analyst branch (`SEAT_CAPS` refactor, then its Task 1) is settled to land
before the critic-seat plan's Task 3, and both land before this DDL exists.
Neither can bind columns that are not yet in the schema, so their INSERTs merge
with seven columns and this migration's `DEFAULT 'unknown'` would then stamp
those rows — exactly the collapse the three-value scheme exists to prevent.
Therefore the same commit that adds the columns updates every writer that must
say `'none'`. Rebase onto both branches before starting, and re-check the line
numbers above: the second-analyst Task 1 rewrites `run_research`, so `:154`
will have moved.

**Interfaces:**
- Produces: `state.migrations.apply(conn) -> list[str]` returning the names of migrations applied (empty when already current).

**Why a migration and not just a schema edit:** `state/db.py`'s `connect()` executes `schema.sql` only when the `tickets` table is absent. Every existing database — including the live one on the droplet — would silently never gain the columns. SQLite permits `ADD COLUMN ... NOT NULL` only with a non-null default, which lands exactly on the agreed `unknown` backfill.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations.py
"""Attribution columns reach databases that already exist — connect() only
runs schema.sql on a fresh file, so a live DB would otherwise never gain them."""
from __future__ import annotations

import sqlite3

import pytest

from state.db import connect
from state.migrations import apply


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_existing_db_gains_the_columns(tmp_path):
    path = tmp_path / "fund.sqlite"
    conn = connect(path)                       # creates the schema
    conn.execute("ALTER TABLE signals DROP COLUMN charter_version")
    conn.execute("ALTER TABLE signals DROP COLUMN model_id")
    conn.commit()

    assert apply(conn) == ["0001_attribution"]
    assert {"charter_version", "model_id"} <= _columns(conn, "signals")


def test_historical_rows_read_unknown_not_a_guessed_version(tmp_path):
    """pm.md is already at v6 — historical rows span versions with no record of
    which, so a constant would be a fabrication."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute("ALTER TABLE signals DROP COLUMN charter_version")
    conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, model_id) VALUES"
        " ('2026-08-01','news','NVDA','bullish',60,'s','2026-08-01T13:00:00Z','m')")
    conn.commit()
    apply(conn)
    row = conn.execute("SELECT charter_version FROM signals").fetchone()
    assert row["charter_version"] == "unknown"


def test_apply_is_idempotent(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    assert apply(conn) == []
    assert apply(conn) == []


def test_the_three_populations_are_distinguishable(tmp_path):
    """'unknown' (history, attribution lost), 'none' (orchestrator default — no
    charter produced it), and a real version must never collapse into each
    other. This is what lets a charter comparison exclude the rows that measure
    seat reliability rather than charter judgment."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, charter_version, model_id) VALUES"
        " ('2026-08-18','news','NVDA','bullish',60,'s','2026-08-18T13:00:00Z',"
        " 'v3','claude-sonnet-5')")
    conn.execute(                                  # the orchestrator's silence
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, charter_version, model_id) VALUES"
        " ('2026-08-18','news','AMD','neutral',0,'no report',"
        " '2026-08-18T13:00:00Z','none','none')")
    conn.commit()

    versions = {r["ticker"]: r["charter_version"] for r in conn.execute(
        "SELECT ticker, charter_version FROM signals")}
    assert versions == {"NVDA": "v3", "AMD": "none"}


def test_a_defaulted_row_reads_none_not_unknown(tmp_path):
    """The load-bearing one. schema.sql carries the same DEFAULT 'unknown' that
    the migration needs, so a fresh DB has it too — meaning an orchestrator
    INSERT that does not bind explicitly produces a row that is distinguishable
    in the design and identical in practice. This test is what makes the
    three-value scheme real rather than documentation."""
    from orchestrator.daily import StageCtx, run_research

    conn = connect(tmp_path / "fund.sqlite")
    # research_seats is REQUIRED with no default (a default would need manual
    # syncing with production config and would fail silently — a seat quietly
    # stops being graded). run_research RAISES on an empty tuple, so () is not
    # a way to get a quiet no-op in a fixture.
    ctx = StageCtx(conn=conn, run_date="2026-08-18",
                   clock=_FrozenClock("2026-08-18T13:00:00+00:00"),
                   slack=_FakeSlack(), research_seats=("news",),
                   market_inputs={})
    run_research(ctx, active=["AMD"])

    row = conn.execute(
        "SELECT charter_version, model_id, summary FROM signals"
        " WHERE ticker = 'AMD'").fetchone()
    assert row["summary"] == "no report"          # it IS the defaulted row
    assert row["charter_version"] == "none"       # not 'unknown'
    assert row["model_id"] == "none"


def test_a_defaulted_decision_reads_none_not_unknown(tmp_path):
    """Same property for run_decision's pm_timeout default. This writer has no
    owner on any other branch — it is not in the second-analyst diff — so if
    this task misses it, nothing else catches it."""
    conn = connect(tmp_path / "fund.sqlite")
    _default_pm_decision(conn, run_date="2026-08-18", ticker="AMD")

    row = conn.execute(
        "SELECT charter_version, model_id FROM decisions"
        " WHERE ticker = 'AMD'").fetchone()
    assert row["charter_version"] == "none"
    assert row["model_id"] == "none"


def test_no_signal_row_ever_holds_null_attribution(tmp_path):
    """NOT NULL, deliberately. A NULL charter_version drops silently out of a
    GROUP BY and out of every `=` comparison, so a defaulted row would leave a
    charter's score by accident rather than by an explicit WHERE clause."""
    conn = connect(tmp_path / "fund.sqlite")
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(signals)")}
    assert cols["charter_version"]["notnull"] == 1
    assert cols["model_id"]["notnull"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: FAIL — `No module named 'state.migrations'`

- [ ] **Step 3: Add the columns to the canonical schema**

In `state/schema.sql`, `signals` gains (after `summary`):

```sql
  charter_version TEXT NOT NULL DEFAULT 'unknown',   -- attribution: which charter
  model_id      TEXT NOT NULL DEFAULT 'unknown',     -- attribution: which model
```

and `decisions` gains the same two lines after `invalidation`. The defaults exist for the migration path, not as a licence to omit them — every production insert writes both explicitly (Steps 6–7).

- [ ] **Step 4: Write the migration**

```python
# state/migrations.py
"""Schema migrations for databases that already exist.

state/db.py's connect() executes schema.sql only when the DB is empty, so a
column added to schema.sql never reaches a live database. Every migration here
is idempotent and additive; nothing drops or rewrites a column.

SQLite permits ADD COLUMN ... NOT NULL only with a non-null default, which is
why historical rows read 'unknown' — the honest value, since charters were
already versioned (pm.md is at v6) and no record survives of which version
wrote which row.
"""

from __future__ import annotations

import sqlite3

_ATTRIBUTION = (
    ("signals", "charter_version"),
    ("signals", "model_id"),
    ("decisions", "charter_version"),
    ("decisions", "model_id"),
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def apply(conn: sqlite3.Connection) -> list[str]:
    """Bring `conn` up to date. Returns the migrations applied, empty if none."""
    applied: list[str] = []
    missing = [(t, c) for t, c in _ATTRIBUTION if c not in _columns(conn, t)]
    if missing:
        for table, column in missing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}"
                         " TEXT NOT NULL DEFAULT 'unknown'")
        conn.commit()
        applied.append("0001_attribution")
    return applied
```

- [ ] **Step 5: Call it from `connect()`**

In `state/db.py`, after the schema-creation branch:

```python
    from state.migrations import apply as _apply_migrations
    _apply_migrations(conn)
    return conn
```

- [ ] **Step 6: Run the migration tests**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6b: Pin both directions of the fallback alert**

The negative direction is the one that catches a normalizer regression, and it is the direction a `model_fallback_used` test would otherwise never cover. Both go in `tests/test_runtime_hooks.py` beside the existing cost-alert tests.

```python
def test_a_matching_model_raises_no_alert(conn):
    """The canary. If this ever fails, the alert has become always-on — which
    is worse than absent, because a daily alert is one people skip."""
    class _R:
        total_cost_usd = 0.01
        model_usage = {"claude-sonnet-5": {"inputTokens": 10}}
    record_turn_result(conn, "2026-08-18", "pm", _R(), "2026-08-18T13:00:00Z")
    assert _alerts(conn, "model_fallback_used") == []


def test_a_resolved_id_still_matches_the_configured_alias(conn):
    """modelUsage's keys may be resolved dated ids while the yaml pins an
    alias. That is a match, not a fallback."""
    class _R:
        total_cost_usd = 0.01
        model_usage = {"claude-sonnet-5-20250929": {"inputTokens": 10}}
    record_turn_result(conn, "2026-08-18", "pm", _R(), "2026-08-18T13:00:00Z")
    assert _alerts(conn, "model_fallback_used") == []


def test_a_genuine_fallback_alerts(conn):
    """analyst pins haiku with a sonnet fallback — the live divergence path."""
    class _R:
        total_cost_usd = 0.01
        model_usage = {"claude-sonnet-5": {"inputTokens": 10}}
    record_turn_result(conn, "2026-08-18", "analyst", _R(),
                       "2026-08-18T13:00:00Z")
    assert len(_alerts(conn, "model_fallback_used")) == 1


def test_a_mixed_turn_alerts_on_the_unmatched_key(conn):
    """The test that separates the two quantifiers — every other case here is
    single-key or None and passes under both. A turn that ran haiku for most of
    it and fell back to sonnet for part must alert, naming only sonnet."""
    class _R:
        total_cost_usd = 0.01
        model_usage = {"claude-haiku-4-5-20251001": {"inputTokens": 90},
                       "claude-sonnet-5": {"inputTokens": 10}}
    record_turn_result(conn, "2026-08-18", "analyst", _R(),
                       "2026-08-18T13:00:00Z")
    alerts = _alerts(conn, "model_fallback_used")
    assert len(alerts) == 1
    assert alerts[0]["served"] == ["claude-sonnet-5"]      # not both keys


def test_absent_model_usage_raises_nothing(conn):
    """None is not a mismatch — the existing cost alert already covers that
    turn, and inventing a second alert for it would double-count."""
    class _R:
        total_cost_usd = 0.01
        model_usage = None
    record_turn_result(conn, "2026-08-18", "analyst", _R(),
                       "2026-08-18T13:00:00Z")
    assert _alerts(conn, "model_fallback_used") == []
```

- [ ] **Step 6c: Verify the existing equality assertion still holds**

Run: `.venv/bin/python -m pytest tests/test_evals_runner.py::test_a_turn_with_a_cost_estimate_raises_no_alert -v`
Expected: PASS. That test asserts `trace.alerts == []` by equality, not substring, so it is the canary for an always-on alert reaching the eval rig. If it reddens, the comparison is wrong — fix `_served_matches`, never the assertion.

- [ ] **Step 7: Write both columns at all four insert sites**

There are four writers, not three, and they fall into two semantic classes.

**Agent writes — bind the seat's real values.** `agents/tools/fund_server.py:52` (`submit_signal`) and `:103` (`submit_decision`) add both columns and bind the seat's configured charter version and model id.

**Orchestrator writes — bind the literal `'none'`.** `run_research`'s defaulted "no report" signal and `run_decision`'s `pm_timeout` defaulted decision record *silence*: no charter and no model produced those rows, and claiming one would be a fabrication. They write `'none'` for both columns.

The signal-side INSERT now sits inside a loop over `(seat, ticker)` pairs rather than a flat loop over tickers, and `DEFAULT_ANALYST` no longer exists as a name — do not grep for it. The edit is still two columns and two `'none'` binds on one statement; only the surrounding control flow differs.

**The rule, stated generally so it stops being a per-table decision:** every row
recording an agent judgment carries its attribution, in one vocabulary. That is
`signals`, `decisions`, and — added after review — `critiques`, whose rows are
today written entirely by the orchestrator's `no_critic_seat` placeholder at
`state/critiques.py:18` and so bind `'none'`. When the Critic seat lands, its
real handler binds real values with no schema change. A table that records a
judgment and omits attribution is an exception, and exceptions to this rule
erode it.

**`model_id` holds the seat's CONFIGURED model, and a divergence raises an alert.** The MCP handlers see `seat` and `args`; they never see the `ResultMessage`, which does not exist until the turn ends. So the value bound at INSERT can only be the configured model from the seat's yaml. That is quietly wrong exactly when a fallback served the turn — and the divergence path is live on the primary table: `analyst.yaml` is `claude-haiku-4-5-20251001` with a `claude-sonnet-5` fallback, and the analyst is what writes `signals`. Only `pm.yaml` sets both to the same id.

The served model is recoverable: `ResultMessage.model_usage` is `dict[str, Any] | None` keyed by model id, so its keys name the models that actually ran. `agents/runtime.py:record_turn_result` already reads attributes off that message after **every** seat turn — the seam exists.

Do not retro-edit rows. After recording cost, compare `model_usage`'s keys against the seat's configured model and append a `model_fallback_used` alert on any mismatch, naming seat, configured, and served.

**The comparison must not be set equality.** The SDK passes `modelUsage` straight through from the CLI (`_internal/message_parser.py:305`), so whether its keys are the alias the yaml pins (`claude-sonnet-5`) or a resolved dated id is not determinable from source, and no recorded trace carries the field — `Trace.model` comes from the yaml via `evals/config.py:load_eval_seat`, so the archive says what the config says, not what served. A naive `set(model_usage) != {configured}` would therefore fire on **every clean turn** if the keys are resolved ids, which is worse than not having the alert: an alert that fires daily is one you learn to skip, and you lose it for the day it means something.

Match with bidirectional prefix comparison, which is correct under either form without knowing which:

```python
def _served_matches(key: str, configured: str) -> bool:
    """True when `key` and `configured` name the same model.

    modelUsage's keys come from the CLI unchanged, so they may be the alias the
    yaml pins ('claude-sonnet-5') or a resolved dated id
    ('claude-sonnet-5-<date>'). Prefix in EITHER direction covers both, and
    exact equality covers the already-dated configs (analyst/exec pin
    'claude-haiku-4-5-20251001'). Deliberately not `==` — see the
    fallback-alert tests for the two directions it protects.

    NOT exact: two genuinely different models in a prefix relationship (a
    hypothetical 'claude-opus-5' and 'claude-opus-5-mini') would match and stay
    silent. No such pair exists in these configs; a suffix allowlist would cost
    more than the risk, so this is written down rather than defended against."""
    return key == configured or key.startswith(configured) \
        or configured.startswith(key)


def _unmatched_models(model_usage: dict | None, configured: str) -> list[str]:
    """The served keys that are NOT the configured model.

    THE QUANTIFIER IS THE WHOLE POINT. model_usage is a dict because a turn can
    run more than one model — that is the mid-turn fallback this alert exists
    to catch. `any(_served_matches(...))` would match on the haiku key of a
    haiku-then-sonnet turn and stay silent on exactly the case that motivated
    using model_usage at all. Alert when ANY key fails, not when none match."""
    if not model_usage:
        return []
    return sorted(k for k in model_usage
                  if not _served_matches(k, configured))
```

The alert payload names the unmatched keys, not the whole dict — a mixed turn should read "fell back to claude-sonnet-5" rather than making the reader diff two lists.

Confirm the real key form the next time a live turn runs — `make preflight` executes exactly one — by logging `model_usage` rather than by making a call for this alone. If the keys turn out resolved, this function already handles it and the log just retires the uncertainty. Rows stay as written; the divergence becomes visible. This is the same posture `record_turn_result` already takes on a missing cost estimate — alert rather than fabricate — and it means `model_id` is trustworthy precisely when no alert fired, with the exceptions enumerated rather than hidden. A `model_usage` of `None` is not a mismatch and raises nothing; the existing cost alert already covers that turn.

The DDL comment must say `-- the seat's CONFIGURED model at write time` so the column is never read as "which model produced this row" without the alerts beside it.

**Three values, never NULL, never overlapping:**

| Value | Meaning |
|---|---|
| a real version, e.g. `v6` | a seat produced this row under that charter |
| `none` | the orchestrator produced this row because a seat was silent |
| `unknown` | written before attribution existed; genuinely lost |

`NOT NULL` is deliberate. A NULL drops silently out of a `GROUP BY` and out of every `=` comparison, so a defaulted row would leave a charter's score by accident. An explicit sentinel forces the exclusion to be written on purpose.

**Design decision this forces, recorded here because it is load-bearing:** rows with `charter_version IN ('none', 'unknown')` are **excluded from charter comparisons**. A defaulted row measures the seat's *reliability* — an SDK timeout, a silent turn — not the charter's *judgment*, and folding it in would penalize a good charter for infrastructure failure. Seat reliability is already the daily scorecard's severity-0 line (Task 3), which is where it belongs. Any query comparing charter versions carries that WHERE clause explicitly.

This matters more over time: the second-analyst plan changes the defaulted-row guarantee from per-ticker to per-seat, so every silent seat gets its own row rather than one row covering a ticker. The `'none'` population therefore grows with seat count, and `specs/design.md` commits to eleven seats.

- [ ] **Step 8: Run the full suite**

Run: `make test`
Expected: PASS

- [ ] **Step 9: Update the canonical spec and commit**

Update `specs/contracts.md` §1 DDL for both tables in the same commit — it is canonical, and a schema that disagrees with it is a bug in the schema.

```bash
git add state/schema.sql state/migrations.py state/db.py tests/test_migrations.py \
        agents/tools/fund_server.py orchestrator/daily.py specs/contracts.md
git commit -m "feat: every signal and decision carries its charter version and model"
```

---

### Task 3: The daily scorecard

**Files:**
- Create: `scripts/score_day.py`
- Create: `tests/test_score_day.py`
- Modify: `scripts/run_day.py` (invoke after the day, append its event)
- Modify: `Makefile` (add `score-day`)

**Interfaces:**
- Consumes: nothing from earlier tasks — reads only rows that already exist.
- Produces: `score_day.score(db_path, run_date) -> list[dict]`, each `{"severity": int, "kind": str, "detail": str}`, sorted by severity ascending (0 is most severe).

**Contract, stated so it cannot drift:** this script **never** exits non-zero for a low score. The non-zero exit is `audit_day.py`'s alone and is wired into `run_day.py` and the systemd failure path; a scorecard that can fail the day would make every mediocre day an incident.

**Ranking is a fixed severity order, not a weighted score.** A weight is a number that gets tuned until the day looks good — a scoreboard you can p-hack with no LLM involved.

```
0  defaults and malformed   critic_timeout, pm_timeout, malformed feed
1  gate rejections          decisions.status='rejected' + reason
2  execution failures       decisions.status in ('failed','expired')
3  statistical outliers     confidence vs the seat's own recent mean, cost, stage latency
3  model divergence         model_fallback_used alerts — a fallback served the turn,
                            so that seat's rows name a model that did not run
4  coverage                 tickers researched vs decisions produced
```

`invalidated` is deliberately absent: `orchestrator/resolve.py` writes it as a constant 0 because neither invalidation signal the fund has is readable from that job, so ranking on it would silently rank on nothing.

**Severity 0 aggregates by seat, one line per seat, never one per row.** The defaulted-signal guarantee is now per `(seat, ticker)` rather than per ticker, so a 3-ticker day with two silent analysts produces six defaulted rows where it used to produce three — and that population grows with seat count, against the eleven seats `specs/design.md` commits to. A scorecard that emitted one line per row would bury every other severity under a wall of near-identical entries on exactly the days worth reading. Emit `news: silent on 3/3 tickers` and let the detail carry the tickers. The count is the signal; the repetition is not.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_score_day.py
"""The scorecard ranks what to read first, and never fails the day."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_sim_day import golden_day

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_day.py"


def _load():
    """scripts/ is not a package — same loader as tests/test_audit_day.py."""
    spec = importlib.util.spec_from_file_location("score_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_day = _load()


@pytest.fixture
def day(tmp_path):
    sim = golden_day(tmp_path)
    return sim, str(tmp_path / "fund.sqlite")


def test_a_clean_day_ranks_nothing_urgent(day):
    sim, path = day
    rows = score_day.score(path, sim.run_date)
    assert [r for r in rows if r["severity"] <= 2] == []


def test_defaults_outrank_gate_rejects_which_outrank_outliers(day):
    sim, path = day
    conn = sim.conn
    conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    conn.execute("UPDATE decisions SET status = 'rejected' WHERE ticker = 'NVDA'")
    conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, 'pm', 's', 99.0, '2026-08-18T13:00:00Z')",
        (sim.run_date,))
    conn.commit()

    kinds = [r["kind"] for r in score_day.score(path, sim.run_date)]
    assert kinds.index("critic_timeout") < kinds.index("gate_rejected")
    assert kinds.index("gate_rejected") < kinds.index("cost_outlier")


def test_gate_rejection_carries_its_reason(day):
    sim, path = day
    sim.conn.execute(
        "UPDATE decisions SET status = 'rejected' WHERE ticker = 'NVDA'")
    sim.conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES"
        " ('gate_rejected', '{\"decision_id\": 1, \"reason\": \"over cap\"}',"
        " '2026-08-18T13:00:00Z')")
    sim.conn.commit()
    rows = [r for r in score_day.score(path, sim.run_date)
            if r["kind"] == "gate_rejected"]
    assert "over cap" in rows[0]["detail"]


def test_a_terrible_day_still_exits_zero(day):
    """The non-zero exit belongs to audit_day.py alone."""
    sim, path = day
    sim.conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    sim.conn.execute("UPDATE decisions SET status = 'rejected'")
    sim.conn.commit()
    proc = subprocess.run([sys.executable, str(SCRIPT), path, sim.run_date],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_it_runs_on_the_stdlib_alone(day):
    """Zero-dependency, like audit_day.py: it must run on a bare host."""
    sim, path = day
    proc = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), path, sim.run_date],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_score_day.py -v`
Expected: FAIL — the script does not exist

- [ ] **Step 3: Write the scorecard**

`scripts/score_day.py`, stdlib only, argv-driven, mirroring `audit_day.py`'s structure (module docstring stating what it measures and why, `_ET` window helper reused by copy — the script must stay importable with nothing but the stdlib on the path). `score()` returns the ranked list; `main()` prints it and exits 0 unconditionally.

Severity constants and one query per row kind, in the order given above. Confidence outliers compare a seat's confidence today against that seat's mean over its previous 20 signals; a seat with fewer than 5 prior signals is skipped rather than flagged, because a mean of two points is not a baseline.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_score_day.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Write the failing test for the outbox event**

```python
def test_scorecard_posts_even_when_the_pnl_job_posts_nothing(day):
    """close_pnl.py has three paths that log and exit 0 posting nothing. The
    scorecard must not ride them — its absence would read as a quiet day
    rather than a skipped job."""
    sim, path = day
    score_day.append_scorecard_event(sim.conn, sim.run_date,
                                     "2026-08-18T13:40:00Z")
    kinds = [r["kind"] for r in
             sim.conn.execute("SELECT kind FROM events")]
    assert "scorecard" in kinds
```

- [ ] **Step 6: Implement the event and wire it into the day**

`append_scorecard_event` uses `slackkit.outbox.append_event` when available and falls back to a direct insert when the script is run standalone on a bare host. `run_day.py` calls it after `report_audit`, so the scorecard is appended on the normal path and drained with everything else.

- [ ] **Step 7: Run the full suite**

Run: `make test`
Expected: PASS

- [ ] **Step 8: Add the Make target and commit**

```makefile
score-day: deps
	$(PY) scripts/score_day.py "$$FUND_DB" "$$(TZ=America/New_York date +%F)"
```

```bash
git add scripts/score_day.py tests/test_score_day.py scripts/run_day.py Makefile
git commit -m "feat: the daily scorecard ranks which turns are worth reading"
```

---

### Task 4: The regression ratchet

**Files:**
- Create: `evals/cases/pm/r01-<slug>.yaml` (one worked example)
- Modify: `docs/agents/` — a short "promoting a live failure" note
- Test: the promoted case runs in the existing suite

**Interfaces:**
- Consumes: a live trace from Task 1; `evals.cases.Case`; the existing invariant registry.
- Produces: no new code. This task establishes a repeatable process and proves it once.

**Eligibility, and why:** promote only failures fully determined by their recorded inputs. `evals/grade.py` reads a trace and a case and *runs nothing*, so a case whose failure depends on unrecorded state cannot be graded honestly. Promotion is manual — automating it is where an agent starts inventing the failure taxonomy, which invariant 8 forbids. There is deliberately no "must recur twice" rule: that would let the first instance of every failure class through by design.

- [ ] **Step 1: Pick a real failure**

From the trace corpus Task 1 has been accumulating, or from `state/fund-2026-08-17-incident.sqlite`, identify one turn whose wrong output is fully determined by its recorded snapshot and prompt. Write down, in one sentence, what the seat should have done instead.

- [ ] **Step 2: Write the case**

Author `evals/cases/pm/r01-<slug>.yaml` in the shape `evals/cases.py:load_case` expects — `id`, `seat`, `clock` (timezone-aware), `tickers`, `snapshot`, `signals`, `journal`, `expect`, `notes`. Put the trace's snapshot in verbatim. `notes` must name the date of the live failure it came from.

- [ ] **Step 3: Verify the case fails against the trace that motivated it**

Run: `.venv/bin/python scripts/eval_one.py --case r01-<slug>` (check the script's actual flags first)
Expected: the invariant that describes this failure reports FAIL

- [ ] **Step 4: Confirm it passes once the behavior is right**

Either the fix is already in (then it passes and the case is a guard), or it is not (then it stays red and the fix is its own task). Do not weaken the case to make it green — `make test`'s red-test rule applies.

- [ ] **Step 5: Document the process and commit**

A short note in `docs/agents/` covering: which traces are eligible, that promotion is a human writing a case, and that the first instance counts.

```bash
git add evals/cases/pm/ docs/agents/
git commit -m "feat: a live failure becomes a permanent eval case"
```

---

### Task 5: The factual frame

**Coordination:** touches `orchestrator/`; check the second-analyst plan's `orchestrator/daily.py` changes first.

**Files:**
- Create: `orchestrator/reflect.py`
- Create: `tests/test_reflect.py`
- Modify: the reflection stage that writes `resolutions.reflection`

**Interfaces:**
- Consumes: `resolutions` rows written by `orchestrator/resolve.py` (already built).
- Produces: `orchestrator.reflect.reflection_frame(conn, decision_id) -> str`

**Why it omits invalidation:** `orchestrator/resolve.py` writes `invalidated` as a constant 0 and says why — neither invalidation signal the fund has is readable from that job. A frame that rendered it would assert "not invalidated" as fact on every row, which is exactly the confident-but-wrong input the computed frame exists to eliminate. The field re-enters when something actually writes it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reflect.py
"""The factual half of a reflection is computed, never narrated."""
from __future__ import annotations

from orchestrator.reflect import reflection_frame


def test_frame_states_prediction_confidence_and_outcome(resolved_day):
    conn, decision_id = resolved_day
    frame = reflection_frame(conn, decision_id)

    assert "NVDA" in frame
    assert "bullish" in frame          # what was predicted
    assert "72" in frame               # at what confidence
    assert "+6.14%" in frame           # what happened
    assert "+5.04pp" in frame          # alpha vs SPY


def test_frame_makes_no_invalidation_claim(resolved_day):
    """resolve.py writes invalidated as a constant 0 — rendering it would put a
    false fact in every reflection."""
    conn, decision_id = resolved_day
    assert "invalidat" not in reflection_frame(conn, decision_id).lower()


def test_reflection_row_keeps_the_frame_when_the_seat_writes_nothing(
        resolved_day):
    conn, decision_id = resolved_day
    frame = reflection_frame(conn, decision_id)
    store_reflection(conn, decision_id, frame, prose="")
    stored = conn.execute(
        "SELECT reflection FROM resolutions WHERE decision_id = ?",
        (decision_id,)).fetchone()["reflection"]
    assert stored.startswith(frame)
```

The `+6.14%` / `+5.04pp` figures are `fixtures/golden-day.md`'s T+5 vector, which `orchestrator/resolve.py` already passes against — reuse it rather than inventing numbers.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reflect.py -v`
Expected: FAIL — `No module named 'orchestrator.reflect'`

- [ ] **Step 3: Implement the frame**

A pure function joining `resolutions`, `decisions`, and `signals` on `decision_id`, rendering a fixed-format block: ticker, action, the analyst signals and confidences that fed it, realized return, alpha vs SPY. No clock call — every value comes from a row.

- [ ] **Step 4: Store frame and prose together**

The reflection row stores the frame verbatim followed by the seat's prose. The seat cannot write the factual half — that is a property of how the row is assembled, not an instruction the model is asked to follow.

- [ ] **Step 5: Run the tests, then the suite**

Run: `.venv/bin/python -m pytest tests/test_reflect.py -v && make test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator/reflect.py tests/test_reflect.py
git commit -m "feat: the seat interprets its record, it no longer narrates the facts"
```

---

## Self-Review

**Spec coverage.** All 38 user stories map to a task: 1–7 → Task 1; 8–21 → Task 3; 22–25 → Task 2; 26–31 → Task 5; 32–36 → Task 4; 37–38 → Global Constraints, asserted by the existing `tests/test_exec_seat_tool_surface.py` and `scripts/check_purity.py`.

**One spec correction, made during planning.** The spec named `run_seat_turn` as the seam. It is the wrong one: that function receives `(client, prompt, required)` and none of `seat`, `model`, `charter_text`, `snapshot`, or `brief_tickers`, all of which `Trace` requires. The seam is `_seat_session` in the composition root plus a pure `build_trace()`. Still one new seam; the spec should be amended to match.

**Deferred deliberately.** `Trace` conflates a turn payload with eval-rig provenance (`case`, `trial`). The clean split is a turn record plus a provenance discriminator. Not done here: it collides with the critic-seat plan's in-flight `evals/` work, and — per invariant 8 — the shape of that split is exactly what the first error-analysis pass over ~100 real traces should decide. The `live-` prefix makes the eventual migration a filter rather than a refactor.

**Open placeholder, flagged not hidden.** Task 4 Step 1 requires a human to choose a real failure from the corpus. It cannot be pre-written because the corpus does not exist until Task 1 has run for some days. That is a genuine sequencing dependency, not a missing plan step.
