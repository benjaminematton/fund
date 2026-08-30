# Phase 2b (a) — Scoring Job and the `weights` Table — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the first loop in `specs/improvement.md` §8 (a): the pre-gate stage persists its active set to `offered`, a nightly no-LLM job scores every graded seat into a `weights` row, and the stage brief carries that row to the PM (every analyst) and to each analyst (its own).

**Architecture:** One new pure module, `orchestrator/improve.py`, computes rows from `calibration/` and SQLite with an injected `Clock` and an injected config dataclass — it opens no file and reads no wall clock, so it is purity-linted like the rest of `orchestrator/`. A thin composition root, `scripts/weights_day.py`, loads `config/improvement.yaml`, connects the DB, and rides `ops/fund-pnl.service` third (after `resolve_day.py`, before the perishable `reflect_day.py`). The brief gains a `weights` section behind a `read_weights` capability. Two tables land in `state/schema.sql`, and `tests/test_schema_contract.py` starts parsing `specs/improvement.md` §4.

**Tech Stack:** Python 3.12, SQLite via `state/db.py`, numpy (already in `calibration/`), PyYAML (already used by `scripts/run_day.py`), pytest. No new dependencies.

## Global Constraints

Copied from CLAUDE.md, `specs/improvement.md` and `specs/acceptance.md` Phase 2b; every task's requirements include these.

- **Paper only.** No live-trading code path anywhere.
- **`gate/`, `stratgate/`, `calibration/`, `orchestrator/`, `state/` import no LLM code and read no wall clock.** `scripts/check_purity.py` lints them in `make test`. `orchestrator/improve.py` is inside a linted package by construction.
- **Default is no-change** (`improvement.md` §0.7). A scoring job that errors leaves the incumbent `weights` rows standing and raises **one** alert.
- **Every count is raw graded calls (`n_graded`)**; `n_eff = n_graded / horizon_days` is stored beside it (`improvement.md` §2.1).
- **Behavioural rates are over the trailing `window_days` trading days (default 20), read from `config/improvement.yaml`** — never hardcoded (`improvement.md` §2.1).
- **`n_offered` is counted from the `offered` table**, written by the 08:45 pre-gate stage, one row per surviving `(run_date, ticker)`; a `{buy:0, sell:0}` ticker is absent (`improvement.md` §2.1, §4).
- **Brief scope:** the PM receives the latest row for every analyst seat; an analyst receives its own latest row only and never another seat's (`improvement.md` §2.1; contracts.md §4 field matrix).
- **Failure, two cases:** (i) job crash or non-finite value → no row, last good rows stand, the brief carries them with their `as_of_date`, one alert; (ii) `weights` empty → the section is named in `unavailable` and the PM proceeds with equal weights (`improvement.md` §2.1, §6).
- **A second run on unchanged data is a no-op:** same `inputs_hash`, no new row (`specs/acceptance.md` Phase 2b, item 1).
- **Contract-test wiring** (`improvement.md` §8): this lane adds `specs/improvement.md` to `tests/test_schema_contract.py`'s parsed set and lists `lessons` and `proposals` in `NO_SCHEMA_HOME` with the per-table reason recorded in issue #50. `submit_lessons`/`submit_proposal` stay `not served` in contracts.md §4 — that is lane (c)/(e).
- **DDL is `CREATE TABLE IF NOT EXISTS` in `state/schema.sql`** (`state/db.py:12` matches that string) and matches the spec character-for-character after normalisation (`tests/test_schema_contract.py`).
- **Alert codes are string literals matching `^[a-z][a-z0-9_]*$`** (`scripts/check_alert_codes.py`), appended via `append_alert` / `run_day._alert`.
- **Test invariants:** tests are the spec; never update a golden fixture or an expected value to make a test pass — where this plan changes an expected value it says which spec clause mandates the change.
- **Charters change only by human commit.** The one charter edit here (`charters/pm.md` v6 → v7, Task 6) rides this PR and lands when the human merges it; the version bump in the header is what attributes every later PM decision to the new text (`agents/seats.py:_parse_charter_version`).
- **No Co-Authored-By trailer in commit messages** (Benjamin's standing rule). Conventional commits.
- **Every commit passes `make test`** (1669 passed, 1 skipped at branch point `1037519`).

## Scope check

`improvement.md` §8 (a) is one lane: pre-gate `offered` write + S1 job + `weights` + brief sections. Pieces (b)–(g) are separate lanes with their own plans; (c) and (e) depend on this lane's `weights` rows and `latest_weights()` read, so their plans are written after this lands. Nothing here touches `calibration/` (invariant §0.10: the optimiser's instruments stay out of reach) or any charter.

## File structure

| Path | Responsibility | Action |
|---|---|---|
| `specs/improvement.md` | Canonical spec. §4 `weights` DDL gains six nullable descriptive columns; §2.1 defines `n_signalled` and the two column kinds; §6 row wording; header note flipped once parsed. | Modify |
| `specs/contracts.md` | §4 field matrix `weights` row loses its "Phase 2b" marker; §7b paragraph says the tables now live in `schema.sql` and §4 is parsed. | Modify |
| `state/schema.sql` | `offered` and `weights` DDL, verbatim from `improvement.md` §4. | Modify |
| `config/improvement.yaml` | `window_days: 20`, `horizon_days: 5`. Human-committed. | Create |
| `orchestrator/improve.py` | `WeightsConfig`, `window_dates`, `behaviour`, `inputs_hash`, `write_weights`, `latest_weights`. Pure. | Create |
| `orchestrator/daily.py` | `_pre_gate_stage` writes `offered` rows. | Modify (`_pre_gate_stage`, lines 124–144) |
| `agents/tools/fund_server.py` | `read_weights` cap for `analyst`, `news`, `pm`; `_weights` section builder; `weights` key in `handle_get_stage_brief`; tool description names the row. | Modify |
| `scripts/weights_day.py` | Composition root: env, yaml → `WeightsConfig`, connect, `write_and_log`, alerts, exit 0 on a scoring failure. | Create |
| `ops/fund-pnl.service`, `ops/README.md`, `Makefile` | Third ExecStart leg; README table row; `make weights`. | Modify |
| `tests/test_schema_contract.py` | `SPEC_SECTIONS` gains `(improvement.md, "## 4. DDL")`; `NO_SCHEMA_HOME` gains `lessons`, `proposals`. | Modify |
| `tests/test_improve.py` | The job: hand-computed vector, no-op hash, same-night upsert, NaN skip, all-or-nothing, windows, `latest_weights`. | Create |
| `tests/test_weights_job.py` | The script's seams: `REQUIRED_ENV`, config loading, exit-0-with-alert, skipped-seat alert. | Create |
| `tests/test_daily_stages.py` | `offered` written by the stage, idempotent on resume, pure recompute writes nothing. | Modify |
| `tests/test_sim_day.py` | Golden day records its offered set; the two `unavailable == []` pins become "weights only" (spec §2.1 (ii)). | Modify |
| `tests/test_fund_tools.py` | `weights` section scope, empty-table naming, the three existing pins that assumed no `weights` section. | Modify |
| `tests/test_ops_units.py` | Five legs in the committed order. | Modify |
| `tests/test_state.py` | `TABLES` gains `offered`, `weights`. | Modify |
| `tests/test_preflight_schema.py` | Table-count pin 15 → 17, with its docstring entry. | Modify |
| `charters/pm.md` | v7: Inputs and Judgment name the `weights` row; the "Phase 3+" deferral of calibration scores is retired. | Modify |
| `specs/acceptance.md` | Tick the two Phase 2b items this lane completes. | Modify |

---

### Task 1: Spec amendment — nullable descriptive columns and `n_signalled`

The `AgentScore` that `calibration/scoring.py` computes is legitimately undefined in places: `murphy_decomposition` returns NaN for `reliability`/`resolution`/`ece` under `MIN_PER_BIN = 20` calls (`calibration/scoring.py:89-91`), `batting_slugging` returns NaN with no directional call and `inf` slugging with no loss (`:118-124`), and `brier_skill_score` returns NaN on degenerate outcomes (`:66`). Python's `sqlite3` binds `float('nan')` as NULL, so the §4 DDL as written (`REAL NOT NULL` on every column) would refuse a row for every seat under 20 graded calls — the first month of every seat. §6's "NaN → no row" was written for the weight, not for a descriptive term the sample cannot define. This task narrows the spec to say so, and defines `n_signalled` so `coverage` can move: `run_research` (`orchestrator/daily.py:157-193`) writes a neutral/0 row with `charter_version = 'none'` for every silent `(seat, ticker)`, so counting every signals row would make `coverage ≡ 1.0`.

**Files:**
- Modify: `specs/improvement.md` §2.1 (after the "Windows, defined once." paragraph), §4 (`weights` DDL), §6 (first row)

**Interfaces:**
- Produces: the DDL Task 2 copies verbatim; the column-kind rule Task 5 implements (`LOAD_BEARING` vs descriptive).

- [ ] **Step 1: Amend §2.1 — add two paragraphs after "Windows, defined once."**

Insert immediately after the paragraph ending "…not from `weights` rows." (currently line 96):

```markdown
**`n_signalled` counts rows the seat wrote.** `run_research` writes a neutral/0 row with
`charter_version = 'none'` for every `(seat, ticker)` a silent seat left uncovered, so a count
of every `signals` row would make `coverage` 1.0 by construction and the §3.3 default dead.
`n_signalled`, the window abstention count behind `abstention_rate`, and `n_distinct_conf`
are all over rows with `charter_version <> 'none'`. A seat that never spoke in the window has
`n_signalled = 0`, `abstention_rate = 0.0`, `coverage = 0.0`.

**Two kinds of column.** Load-bearing — `n_eff`, `brier`, `bss_shrunk`, `total_skill`,
`weight` — are `NOT NULL`; a non-finite value there skips that seat's row for the night, names
the seat in one alert, and writes the other seats. Descriptive — `bss`, `reliability`,
`resolution`, `ece`, `batting`, `slugging` — store `NULL` where the sample cannot define them
(calibration §1: Murphy terms need ≥20 calls; batting needs a directional call; slugging needs
a loss; BSS is undefined on degenerate outcomes, calibration §5), because a placeholder number
would be read as a measurement. A re-run on unchanged inputs writes nothing (`inputs_hash`
equals the seat's latest row); a re-run the same night on changed inputs replaces that night's
row (`UNIQUE (as_of_date, agent)`).
```

- [ ] **Step 2: Amend §4 — the six descriptive columns lose `NOT NULL`**

In the `weights` DDL block, replace these six lines:

```sql
  bss           REAL NOT NULL,
```
```sql
  reliability   REAL NOT NULL,
  resolution    REAL NOT NULL,
  ece           REAL NOT NULL,                -- descriptive only, never in the weight
  batting       REAL NOT NULL,
  slugging      REAL NOT NULL,
```

with:

```sql
  bss           REAL,                        -- NULL: undefined on degenerate outcomes (§2.1)
```
```sql
  reliability   REAL,                        -- NULL under 20 graded calls (§2.1)
  resolution    REAL,
  ece           REAL,                        -- descriptive only, never in the weight
  batting       REAL,                        -- NULL with no directional call
  slugging      REAL,                        -- NULL with no directional call or no loss
```

Also change the `n_signalled` comment to `-- signals rows the seat wrote (charter_version <> 'none') in the window`.

- [ ] **Step 3: Amend §6 — first row**

Replace:

```markdown
| Scoring job crash or NaN | no row; last good `weights` rows stand and the brief carries them with their `as_of_date`; one alert |
```

with:

```markdown
| Scoring job crash | no row for any seat (one transaction); last good `weights` rows stand and the brief carries them with their `as_of_date`; one alert |
| Non-finite load-bearing value for a seat | that seat's row skipped and named in one alert; the other seats' rows written; descriptive NULLs are not this case (§2.1) |
```

- [ ] **Step 4: Run the spec-side tests that read this file**

Run: `make test`
Expected: PASS — nothing parses `improvement.md` yet (Task 2 wires it). This step exists so the amendment commit is green on its own.

- [ ] **Step 5: Commit**

```bash
git add specs/improvement.md
git commit -m "docs(improvement): weights descriptive columns are nullable; n_signalled counts seat-written rows"
```

---

### Task 2: DDL — `offered` and `weights` in `schema.sql`, `improvement.md` §4 parsed

**Files:**
- Modify: `tests/test_schema_contract.py:101-104` (`SPEC_SECTIONS`), `:121-123` (`NO_SCHEMA_HOME`)
- Modify: `state/schema.sql` (append after the `protection` table)
- Modify: `tests/test_state.py:10-11` (`TABLES`)

**Interfaces:**
- Produces: tables `offered(run_date, ticker, created_at)` and `weights(...)` in every DB `state.db.connect()` opens — including an existing droplet DB, via the `_TABLES <= have` guard (`state/db.py:37-41`).

- [ ] **Step 1: Wire the contract test first, so it goes red**

In `tests/test_schema_contract.py`, replace `SPEC_SECTIONS`:

```python
SPEC_SECTIONS = (
    (ROOT / "specs" / "contracts.md", "## 2. SQLite DDL"),
    (ROOT / "specs" / "strategy-contracts.md", "## 2. DDL"),
    # improvement.md keeps its DDL in §4, not §2 (issue #50's reasoning for a
    # per-file home carried over: contracts.md §7b points here).
    (ROOT / "specs" / "improvement.md", "## 4. DDL"),
)
```

and `NO_SCHEMA_HOME`:

```python
NO_SCHEMA_HOME = frozenset({
    "sleeves", "shadow_fills",
    # improvement.md §4, lanes not yet landed (specs/improvement.md §8): the
    # per-table reason is recorded in issue #50. `lessons` lands with lane (c),
    # `proposals` with lane (e); each removes its own entry.
    "lessons", "proposals",
})
```

And in `_spec_ddl_blocks()` (line ~608), derive the label from the heading so a failure on the new file names §4 rather than §2:

```python
    return tuple((f"{path.parent.name}/{path.name}"
                  f" §{heading.split('.')[0].removeprefix('## ')}",
                  _section_ddl(path, heading))
                 for path, heading in SPEC_SECTIONS)
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_schema_contract.py -v`
Expected: FAIL — `test_every_spec_table_is_declared_in_schema` names `offered (specs/improvement.md §4)` and `weights (specs/improvement.md §4)` as absent from `state/schema.sql`. `test_spec_ddl_executes` and `test_spec_extraction_did_not_come_up_short` PASS (the spec block is valid SQL and yields 4 tables).

- [ ] **Step 3: Append the DDL to `state/schema.sql`**

Append at the end of the file:

```sql
-- The pre-gate's active set, persisted (specs/improvement.md §2.1, §4 —
-- canonical, do not add fields here). Written by orchestrator/daily.py's
-- _pre_gate_stage for every ticker that survives the {buy:0, sell:0} drop:
-- the only durable record of what the desks were asked to look at, and the
-- denominator of weights.coverage. Not a workflow table: no status.
--
-- IF NOT EXISTS is load-bearing here and not style: state/db.py:12 matches
-- that exact string to build _TABLES. §4 spells it CREATE TABLE, per its own
-- convention; the two are the same table.
CREATE TABLE IF NOT EXISTS offered (
  run_date      TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (run_date, ticker)
);

-- The scoreboard: one row per graded seat per night (specs/improvement.md
-- §2.1, §4 — canonical, do not add fields here). Written only by
-- orchestrator/improve.py's write_weights; read by the stage brief's
-- `weights` section. "Latest row per seat" = MAX(as_of_date) per agent; the
-- UNIQUE makes it one row. Nullable columns are the descriptive terms the
-- sample cannot always define (§2.1 "Two kinds of column").
CREATE TABLE IF NOT EXISTS weights (
  id            INTEGER PRIMARY KEY,
  as_of_date    TEXT NOT NULL,                -- scoreboard date (ET)
  agent         TEXT NOT NULL,
  n_graded      INTEGER NOT NULL,
  n_abstain     INTEGER NOT NULL,
  n_eff         REAL NOT NULL,                -- n_graded / horizon_days (§2.1)
  brier         REAL NOT NULL,
  bss           REAL,                        -- NULL: undefined on degenerate outcomes (§2.1)
  bss_shrunk    REAL NOT NULL,
  total_skill   REAL NOT NULL,                -- bss_shrunk * n_graded (the ranking column)
  reliability   REAL,                        -- NULL under 20 graded calls (§2.1)
  resolution    REAL,
  ece           REAL,                        -- descriptive only, never in the weight
  batting       REAL,                        -- NULL with no directional call
  slugging      REAL,                        -- NULL with no directional call or no loss
  n_signalled   INTEGER NOT NULL,             -- signals rows the seat wrote (charter_version <> 'none') in the window
  n_offered     INTEGER NOT NULL,             -- offered rows in the window (§2.1)
  abstention_rate REAL NOT NULL,              -- n_abstain / n_signalled over the window
  n_distinct_conf INTEGER NOT NULL,           -- confidence granularity over the window
  coverage      REAL NOT NULL,                -- n_signalled / n_offered over the window
  cost_usd      REAL NOT NULL,                -- costs.usd_estimate summed over the window (est.)
  weight        REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
  narrowed      INTEGER NOT NULL DEFAULT 0,   -- §2.3: floor released
  inputs_hash   TEXT NOT NULL,                -- hash of the graded rows that produced this row
  created_at    TEXT NOT NULL,
  UNIQUE (as_of_date, agent)
);
```

- [ ] **Step 4: Pin the two tables in `tests/test_state.py`**

```python
TABLES = {"signals", "critiques", "decisions", "tickets", "orders",
          "resolutions", "checkpoints", "events", "costs", "offered", "weights"}
```

- [ ] **Step 5: Bump the preflight table-count pin — an expected-value change, mandated by the test itself**

`tests/test_preflight_schema.py::test_the_expected_table_count_is_pinned` asserts `len(preflight.expected_schema()) == 15` and its docstring says the bump "IS a second edit, on purpose: bump it in the same commit that adds the table." Change the assertion to `== 17` and append to the docstring:

```
    15 -> 17 on 2026-08-30 — issue #205, lane (a)
    (https://github.com/benjaminematton/fund/issues/205) — `offered` (the
    pre-gate's persisted active set) and `weights` (the nightly scoreboard)
    landed, character-exact to improvement.md §4, which
    tests/test_schema_contract.py now parses.
```

- [ ] **Step 6: Run the contract, state and preflight tests**

Run: `.venv/bin/python3 -m pytest tests/test_schema_contract.py tests/test_state.py tests/test_preflight_schema.py -v`
Expected: PASS. `test_schema_matches_spec[offered]` and `[weights]` are now parametrized cases; `test_every_status_table_has_a_state_machine` is unaffected (neither table has a `status` column); `test_a_database_without_the_log_gains_it_on_reconnect` still passes, which is the mechanism that carries both tables onto the droplet's existing DB.

- [ ] **Step 7: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add state/schema.sql tests/test_schema_contract.py tests/test_state.py tests/test_preflight_schema.py
git commit -m "feat(state): offered and weights tables; improvement.md §4 joins the parsed contract set"
```

---

### Task 3: The pre-gate stage writes `offered`

**Files:**
- Modify: `orchestrator/daily.py:124-144` (`_pre_gate_stage`)
- Test: `tests/test_daily_stages.py` (after the pre-gate tests, line ~87), `tests/test_sim_day.py` (new test beside `test_golden_day`)

**Interfaces:**
- Consumes: `offered` DDL (Task 2).
- Produces: one `offered` row per `(run_date, ticker)` in the active set, `created_at = iso(ctx.clock.now())`. `run_pre_gate` (the pure recompute on a `done` checkpoint) still writes nothing.

- [ ] **Step 1: Write the failing stage tests**

Add to `tests/test_daily_stages.py`, importing `_pre_gate_stage` and `run_stage` alongside the existing `orchestrator.daily` imports:

```python
from orchestrator.daily import _pre_gate_stage, run_stage


def _offered(conn):
    return [(r["run_date"], r["ticker"], r["created_at"]) for r in conn.execute(
        "SELECT run_date, ticker, created_at FROM offered ORDER BY ticker")]


def test_pre_gate_stage_records_the_offered_set(fund_db, sim_clock):
    """specs/improvement.md §2.1: the active set otherwise lives only in
    run_pre_gate's return value, and no night job could see it. One row per
    surviving ticker; the {buy:0, sell:0} ticker is absent, not present."""
    market = {"NVDA": _nvda_inputs(),
              "AAPL": _nvda_inputs(ticker="AAPL", cash=0.0, held_qty=0),
              "MSFT": _nvda_inputs(ticker="MSFT", cash=0.0, held_qty=40)}
    ctx = _ctx(fund_db, sim_clock, market)

    active = run_stage(ctx, "pre_gate", lambda: _pre_gate_stage(ctx))

    assert active == ["NVDA", "MSFT"]
    now = iso(sim_clock.now())
    assert _offered(fund_db) == [(RUN, "MSFT", now), (RUN, "NVDA", now)]


def test_pre_gate_offered_write_is_idempotent_on_resume(fund_db, sim_clock):
    """run_stage re-runs a 'running' body on crash-resume. Two runs, one row."""
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    _pre_gate_stage(ctx)
    _pre_gate_stage(ctx)
    assert len(_offered(fund_db)) == 1


def test_pre_gate_recompute_on_a_done_stage_writes_nothing(fund_db, sim_clock):
    """run_day's `done` branch recomputes the active set through run_pre_gate,
    which stays pure: the rows were written by the first run."""
    ctx = _ctx(fund_db, sim_clock, {"NVDA": _nvda_inputs()})
    assert run_pre_gate(ctx) == ["NVDA"]
    assert _offered(fund_db) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_daily_stages.py -k offered -v`
Expected: the first two FAIL (`_offered` returns `[]`); the third PASSES already (it pins existing behaviour so the change cannot break it).

- [ ] **Step 3: Write the rows in `_pre_gate_stage`**

Replace the whole function (`orchestrator/daily.py:124-144`) with:

```python
def _pre_gate_stage(ctx: StageCtx) -> list[str]:
    """The pre_gate stage body: run_pre_gate's pure computation, plus two
    writes. One alert per ticker dropped because BOTH shapes came back
    gate_error — a malformed/NaN feed, not the legitimate no_headroom/
    nothing_held skip, which must stay silent (review Important 5). And one
    `offered` row per surviving ticker (specs/improvement.md §2.1): the
    active set otherwise lives only in this function's return value, and the
    nightly scoring job needs it as the denominator of coverage. INSERT OR
    IGNORE, so a crash-resume that re-runs this body writes each row once.
    One residual: scripts/run_day.py rebuilds market_inputs live per fire,
    so a resume can DROP a ticker the first attempt offered — its row stays,
    that day's n_offered over-counts by one, and the tell is an offered row
    with no signals under it. Accepted: one day, visible, and the honest
    record of what the desks were asked. Only called from inside run_stage,
    never from run_day's pure recompute-on-done branch, so a resumed day
    never re-posts these alerts and run_pre_gate stays write-free."""
    active: list[str] = []
    now = iso(ctx.clock.now())
    for ticker, inputs in ctx.market_inputs.items():
        results = [_sized(inputs, side, "advisory") for side in ("buy", "sell")]
        if any(isinstance(r, Approved) for r in results):
            active.append(ticker)
        elif all(isinstance(r, Rejected) and r.reason == "gate_error" for r in results):
            append_alert(ctx.conn, "gate_error",
                         f"gate_error {ticker} — dropped from"
                         " today's universe (malformed feed)",
                         now_iso=now, ticker=ticker)
    for ticker in active:
        ctx.conn.execute(
            "INSERT OR IGNORE INTO offered (run_date, ticker, created_at)"
            " VALUES (?, ?, ?)", (ctx.run_date, ticker, now))
    ctx.conn.commit()
    return active
```

(The lazy `now = None` dance is gone: the stamp is now needed on every run, and the clock is injected, so reading it once up front costs nothing.)

- [ ] **Step 4: Run the stage tests**

Run: `.venv/bin/python3 -m pytest tests/test_daily_stages.py -v`
Expected: PASS, all of them — including the existing `gate_error` alert tests, whose alert stamp is unchanged.

- [ ] **Step 5: Add the sim-level pin**

In `tests/test_sim_day.py`, after `test_golden_day`:

```python
def test_golden_day_records_its_offered_set(tmp_path):
    """specs/acceptance.md Phase 2b: under sim, the pre-gate stage writes one
    `offered` row per surviving ticker. The golden day offers NVDA alone."""
    sim = golden_day(tmp_path)
    rows = sim.conn.execute(
        "SELECT run_date, ticker FROM offered").fetchall()
    assert [(r["run_date"], r["ticker"]) for r in rows] == [(sim.run_date, "NVDA")]
```

Run: `.venv/bin/python3 -m pytest tests/test_sim_day.py -k offered -v`
Expected: PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add orchestrator/daily.py tests/test_daily_stages.py tests/test_sim_day.py
git commit -m "feat(orchestrator): the pre-gate stage persists its active set to offered"
```

---

### Task 4: `config/improvement.yaml`, `WeightsConfig`, and the window helpers

**Files:**
- Create: `config/improvement.yaml`
- Create: `orchestrator/improve.py` (this task: config + `window_dates` + `behaviour` + `inputs_hash`; Task 5 adds `write_weights` and `latest_weights`)
- Test: `tests/test_improve.py` (created here; Task 5 extends it)

**Interfaces:**
- Produces:
  - `WeightsConfig(window_days: int, horizon_days: int)` — frozen dataclass; raises `ValueError` if either `< 1`.
  - `window_dates(conn, as_of_date: str, window_days: int) -> list[str]` — the trailing `window_days` distinct `signals.run_date` values `<= as_of_date`, oldest first; fewer exist → all of them.
  - `behaviour(conn, seat: str, dates: list[str]) -> dict` with keys `n_signalled`, `n_offered`, `n_distinct_conf`, `abstention_rate`, `coverage`, `cost_usd`.
  - `inputs_hash(seat_rows: list[dict], beh: dict) -> str` — sha256 hex of the JSON of both, `sort_keys=True`.

- [ ] **Step 1: Write the config file**

`config/improvement.yaml`:

```yaml
# The improvement loop's human-committed parameters (specs/improvement.md
# §2.1, §2.3). Same rule as config/watchlist.yaml and the gate thresholds:
# NEVER written by an agent. A Proposer proposal may argue for a change here
# (improvement.md §3); a human commits it.

# Trailing trading days the behavioural rates (n_signalled, n_offered,
# abstention_rate, n_distinct_conf, coverage, cost_usd) are computed over. A
# trading day is a run_date with signals rows.
window_days: 20

# The signal horizon n_eff divides by: n_eff = n_graded / horizon_days
# (calibration.md §4's overlap correction, N_eff ≈ N/5). Displayed beside
# n_graded, never used as a threshold.
horizon_days: 5
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_improve.py`:

```python
"""orchestrator/improve.py — the nightly scoring job (specs/improvement.md
§2.1) and the reads the briefs make of the `weights` table.

Pure by construction: an injected SimClock, an injected WeightsConfig, a temp
SQLite. The fixture below is built so every number in the scoreboard row can
be checked by hand — see test_the_weights_row_carries_the_calibration_values
for the arithmetic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from orchestrator.clock import SimClock, iso
from orchestrator.improve import (WeightsConfig, behaviour, inputs_hash,
                                  window_dates)
from state.db import connect

NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)   # 16:35 ET
AS_OF = "2026-07-13"
CFG = WeightsConfig(window_days=20, horizon_days=5)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fund.sqlite")
    yield c
    c.close()


@pytest.fixture
def clock():
    return SimClock(NIGHTLY)


def _dates(n: int, start: date = date(2026, 4, 1)) -> list[str]:
    """`n` consecutive run_dates, oldest first, all before AS_OF."""
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _day(conn, run_date: str, alpha: float, signals: dict, *,
         ticker: str = "NVDA", offered: tuple[str, ...] = ("NVDA",),
         charter_version: str = "v1", cost: dict | None = None) -> None:
    """One graded trading day: `offered` rows, one signal per seat in
    `signals` ({seat: (direction, confidence)}), one held PM decision on
    `ticker`, and its resolution at `alpha`. calibration/rows.py fans the
    resolution back out to every seat's signal on (run_date, ticker)."""
    stamp = f"{run_date}T15:00:00+00:00"
    for t in offered:
        conn.execute("INSERT OR IGNORE INTO offered (run_date, ticker, created_at)"
                     " VALUES (?, ?, ?)", (run_date, t, stamp))
    for seat, (direction, confidence) in signals.items():
        conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
            " summary, created_at, charter_version, model_id)"
            " VALUES (?, ?, ?, ?, ?, 's', ?, ?, 'm')",
            (run_date, seat, ticker, direction, confidence, stamp, charter_version))
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES (?, ?, 'hold', 0, 't', 'i',"
        " 'held', ?)", (run_date, ticker, stamp))
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?, 5, ?, ?, 0, ?)",
        (cur.lastrowid, alpha, alpha, stamp))
    for seat, usd in (cost or {}).items():
        conn.execute(
            "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
            " recorded_at) VALUES (?, ?, 's', ?, ?)", (run_date, seat, usd, stamp))
    conn.commit()


def _two_seat_history(conn, n_days: int = 60) -> list[str]:
    """Seat `a` calls every day right at 80 (bullish on an up day, bearish on
    a down day); seat `b` abstains every day at 50. Alternating ±1% alpha,
    so outcomes are not degenerate. Seat `a` costs $0.05 a day."""
    dates = _dates(n_days)
    for i, d in enumerate(dates):
        alpha = 0.01 if i % 2 == 0 else -0.01
        _day(conn, d, alpha,
             {"a": ("bullish" if alpha > 0 else "bearish", 80),
              "b": ("neutral", 50)},
             cost={"a": 0.05})
    return dates


# --- config ---------------------------------------------------------------

def test_config_rejects_a_window_or_horizon_below_one():
    for bad in (dict(window_days=0, horizon_days=5),
                dict(window_days=20, horizon_days=0)):
        with pytest.raises(ValueError, match="must be >= 1"):
            WeightsConfig(**bad)


# --- windows --------------------------------------------------------------

def test_window_dates_are_the_trailing_trading_days_oldest_first(conn):
    dates = _two_seat_history(conn)
    assert window_dates(conn, AS_OF, 20) == dates[-20:]
    assert window_dates(conn, AS_OF, 100) == dates          # fewer than asked: all
    assert window_dates(conn, dates[9], 5) == dates[5:10]   # bounded by as_of
    assert window_dates(conn, "2026-01-01", 5) == []


def test_behaviour_counts_the_window_only(conn):
    dates = _two_seat_history(conn)
    window = dates[-20:]
    a = behaviour(conn, "a", window)
    assert a == {"n_signalled": 20, "n_offered": 20, "n_distinct_conf": 1,
                 "abstention_rate": 0.0, "coverage": 1.0,
                 "cost_usd": pytest.approx(1.0)}
    b = behaviour(conn, "b", window)
    assert (b["n_signalled"], b["abstention_rate"], b["cost_usd"]) == (20, 1.0, 0.0)


def test_behaviour_over_no_dates_is_all_zero(conn):
    assert behaviour(conn, "a", []) == {
        "n_signalled": 0, "n_offered": 0, "n_distinct_conf": 0,
        "abstention_rate": 0.0, "coverage": 0.0, "cost_usd": 0.0}


def test_defaulted_rows_are_offered_but_not_signalled(conn):
    """run_research writes neutral/0 rows with charter_version='none' for a
    silent seat. They are graded (calibration invariant 2) but the seat did
    not speak, so they count toward n_offered and not toward n_signalled —
    otherwise coverage is 1.0 by construction (improvement.md §2.1)."""
    dates = _dates(3)
    for d in dates:
        _day(conn, d, 0.01, {"c": ("neutral", 0)}, charter_version="none")
    c = behaviour(conn, "c", dates)
    assert (c["n_offered"], c["n_signalled"]) == (3, 0)
    assert (c["coverage"], c["abstention_rate"], c["n_distinct_conf"]) == (0.0, 0.0, 0)


def test_coverage_is_signalled_over_offered(conn):
    """Two tickers offered, the seat spoke on one."""
    d = _dates(1)[0]
    _day(conn, d, 0.01, {"a": ("bullish", 70)}, offered=("NVDA", "MSFT"))
    assert behaviour(conn, "a", [d])["coverage"] == 0.5


# --- hash -----------------------------------------------------------------

def test_inputs_hash_is_stable_and_sensitive():
    rows = [{"seat": "a", "direction": "long", "confidence": 80, "alpha": 0.01}]
    beh = {"n_signalled": 1, "n_offered": 1, "n_distinct_conf": 1,
           "abstention_rate": 0.0, "coverage": 1.0, "cost_usd": 0.0}
    h = inputs_hash(rows, beh)
    assert h == inputs_hash(list(rows), dict(beh))
    assert h != inputs_hash(rows, {**beh, "cost_usd": 0.01})
    assert h != inputs_hash(rows + rows, beh)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_improve.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'orchestrator.improve'`.

- [ ] **Step 4: Write the module (this task's half)**

Create `orchestrator/improve.py`:

```python
"""The improvement loop's Class A jobs (specs/improvement.md §2): the nightly
scoring job that turns graded signals into `weights` rows, and the read the
stage brief makes of them.

Rows in, rows out. No LLM, no wall clock, no file: the config arrives as a
dataclass the composition root (scripts/weights_day.py) built from
config/improvement.yaml, and time arrives as the injected Clock. Purity-linted
with the rest of orchestrator/, which is what lets the sim month drive it.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass

from calibration.rows import scoreboard_rows
from calibration.scoreboard import score_agents
from calibration.scoring import AgentScore
from orchestrator.clock import Clock, et_run_date, iso


@dataclass(frozen=True)
class WeightsConfig:
    """config/improvement.yaml, validated (improvement.md §2.1).

    window_days: trailing trading days the behavioural rates cover.
    horizon_days: what n_eff = n_graded / horizon_days divides by
    (calibration.md §4's overlap correction)."""
    window_days: int
    horizon_days: int

    def __post_init__(self) -> None:
        if self.window_days < 1 or self.horizon_days < 1:
            raise ValueError(
                "improvement config: window_days and horizon_days must be >= 1,"
                f" got window_days={self.window_days}"
                f" horizon_days={self.horizon_days}")


def window_dates(conn: sqlite3.Connection, as_of_date: str,
                 window_days: int) -> list[str]:
    """The trailing `window_days` trading days ending at `as_of_date`, oldest
    first. A trading day is a run_date with `signals` rows: run_research
    writes one per (seat, ticker) on every day with an active set, so the
    signals table is the fund's own trading calendar and the repo needs no
    other. Fewer days exist than asked for -> all of them."""
    rows = conn.execute(
        "SELECT DISTINCT run_date FROM signals WHERE run_date <= ?"
        " ORDER BY run_date DESC LIMIT ?", (as_of_date, window_days)).fetchall()
    return sorted(r["run_date"] for r in rows)


def behaviour(conn: sqlite3.Connection, seat: str, dates: list[str]) -> dict:
    """The window rates §3.3 grades against, for one seat over `dates`.

    Only rows the SEAT wrote count as signalled: charter_version = 'none'
    marks a row the orchestrator wrote because the seat was silent
    (orchestrator/daily.py run_research), and counting those would make
    coverage 1.0 by construction (improvement.md §2.1). Empty `dates` is a
    fund with no history yet: every count zero, every rate 0.0."""
    if not dates:
        return {"n_signalled": 0, "n_offered": 0, "n_distinct_conf": 0,
                "abstention_rate": 0.0, "coverage": 0.0, "cost_usd": 0.0}
    marks = ", ".join("?" * len(dates))
    spoke = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(direction = 'neutral'), 0) abstain,"
        f" COUNT(DISTINCT confidence) distinct_conf FROM signals"
        f" WHERE agent = ? AND charter_version <> 'none'"
        f" AND run_date IN ({marks})", (seat, *dates)).fetchone()
    n_offered = conn.execute(
        f"SELECT COUNT(*) n FROM offered WHERE run_date IN ({marks})",
        dates).fetchone()["n"]
    cost = conn.execute(
        f"SELECT COALESCE(SUM(usd_estimate), 0.0) c FROM costs"
        f" WHERE agent = ? AND run_date IN ({marks})", (seat, *dates)).fetchone()["c"]
    n = spoke["n"]
    return {"n_signalled": n,
            "n_offered": n_offered,
            "n_distinct_conf": spoke["distinct_conf"],
            "abstention_rate": spoke["abstain"] / n if n else 0.0,
            "coverage": n / n_offered if n_offered else 0.0,
            "cost_usd": float(cost)}


def inputs_hash(seat_rows: list[dict], beh: dict) -> str:
    """Everything that feeds one seat's row, hashed: its graded rows in
    grade order and its window rates. Equal to the seat's latest row's hash
    means nothing changed and nothing is written (improvement.md §2.1)."""
    blob = json.dumps({"rows": seat_rows, "window": beh}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
```

(`AgentScore`, `score_agents`, `scoreboard_rows`, `Clock`, `et_run_date`, `iso`, `math` are imported now and used by Task 5; the purity lint does not flag unused imports, and Task 5 lands on the same branch.)

- [ ] **Step 5: Run the tests and the purity lint**

Run: `.venv/bin/python3 -m pytest tests/test_improve.py -v && .venv/bin/python3 scripts/check_purity.py`
Expected: PASS; `PURITY LINT: clean (...)` with the file count one higher than before.

- [ ] **Step 6: Commit**

```bash
git add config/improvement.yaml orchestrator/improve.py tests/test_improve.py
git commit -m "feat(orchestrator): improvement config and the scoring job's window helpers"
```

---

### Task 5: `write_weights` and `latest_weights`

**Files:**
- Modify: `orchestrator/improve.py` (append)
- Test: `tests/test_improve.py` (append)

**Interfaces:**
- Consumes: `calibration.rows.scoreboard_rows(conn) -> list[dict]` (chronological `{seat, direction, confidence, alpha}`), `calibration.scoreboard.score_agents(rows) -> (list[AgentScore], dict[str, float])`, Task 4's helpers.
- Produces:
  - `write_weights(conn, clock, cfg) -> dict` with keys `as_of_date: str`, `written: list[str]`, `unchanged: list[str]`, `skipped: list[str]`. Raises on any DB/compute error after rolling back — the caller (Task 7) alerts.
  - `latest_weights(conn, agent: str | None = None) -> list[dict]` — every seat's latest row (or one seat's), as plain dicts with every `weights` column; NULL comes back `None`. Agent order.
  - `LOAD_BEARING = ("n_eff", "brier", "bss_shrunk", "total_skill", "weight")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_improve.py`:

```python
# --- the job --------------------------------------------------------------

from orchestrator import improve                                   # noqa: E402
from orchestrator.improve import latest_weights, write_weights     # noqa: E402


def _rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM weights ORDER BY as_of_date, agent")]


def test_the_weights_row_carries_the_calibration_values(conn, clock):
    """Every number below is hand-computed from calibration.md §1–§2 over the
    two-seat fixture (60 graded calls each, alternating ±1% alpha).

    Seat a: p = 0.8 on every up day and 0.2 on every down day, so every
    squared error is 0.04 -> brier 0.04. Base rate 0.5, reference Brier 0.25
    -> BSS 1 - 0.04/0.25 = 0.84 (recency weights cancel on identical errors).
    Pool: 60 rows at 0.04 + 60 at 0.25 -> 0.145 -> pool BSS 0.42.
    Shrink w = 60/(60+30) = 2/3 -> 2/3*0.84 + 1/3*0.42 = 0.70; total 42.0.
    Murphy (exact, two forecast values, n_bins = 3): reliability 0.04,
    resolution 0.25, ECE 0.20. Batting 1.0 (every directional call won);
    slugging is undefined with no loss -> NULL.
    Seat b: brier 0.25, BSS 0.0, shrunk 1/3*0.42 = 0.14, total 8.4; one
    forecast value -> reliability 0, resolution 0, ECE 0; no directional call
    -> batting and slugging NULL. n_abstain 60.
    PM weights: raw 0.70 / 0.14, floor 0.5 * 0.42 = 0.21 lifts b, then
    normalise over 0.91 -> a 0.769231, b 0.230769.
    n_eff = 60 / 5 = 12. Window (20 days): a spoke 20 times at one
    confidence, $1.00 est.; b abstained 20 of 20.
    """
    _two_seat_history(conn)

    out = write_weights(conn, clock, CFG)

    assert out == {"as_of_date": AS_OF, "written": ["a", "b"],
                   "unchanged": [], "skipped": []}
    a, b = _rows(conn)
    assert (a["agent"], a["as_of_date"], a["created_at"]) == ("a", AS_OF, iso(NIGHTLY))
    assert (a["n_graded"], a["n_abstain"], a["n_eff"]) == (60, 0, 12.0)
    assert a["brier"] == pytest.approx(0.04)
    assert a["bss"] == pytest.approx(0.84)
    assert a["bss_shrunk"] == pytest.approx(0.70)
    assert a["total_skill"] == pytest.approx(42.0)
    assert (a["reliability"], a["resolution"], a["ece"]) == (
        pytest.approx(0.04), pytest.approx(0.25), pytest.approx(0.20))
    assert a["batting"] == 1.0 and a["slugging"] is None
    assert (a["n_signalled"], a["n_offered"], a["n_distinct_conf"]) == (20, 20, 1)
    assert (a["abstention_rate"], a["coverage"]) == (0.0, 1.0)
    assert a["cost_usd"] == pytest.approx(1.0)
    assert a["weight"] == pytest.approx(0.70 / 0.91)
    assert (a["narrowed"], len(a["inputs_hash"])) == (0, 64)

    assert (b["n_graded"], b["n_abstain"]) == (60, 60)
    assert b["brier"] == pytest.approx(0.25) and b["bss"] == pytest.approx(0.0)
    assert b["bss_shrunk"] == pytest.approx(0.14)
    assert b["total_skill"] == pytest.approx(8.4)
    assert (b["reliability"], b["resolution"], b["ece"]) == (0.0, 0.0, 0.0)
    assert (b["batting"], b["slugging"]) == (None, None)
    assert (b["abstention_rate"], b["cost_usd"]) == (1.0, 0.0)
    assert b["weight"] == pytest.approx(0.21 / 0.91)
    assert a["weight"] + b["weight"] == pytest.approx(1.0)


def test_a_second_run_on_unchanged_data_writes_nothing(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    before = _rows(conn)

    out = write_weights(conn, clock, CFG)

    assert out["written"] == [] and out["unchanged"] == ["a", "b"]
    assert _rows(conn) == before


def test_a_same_night_rerun_on_changed_data_replaces_that_nights_row(conn, clock):
    """resolve_day re-fired after a failed drain resolves more; the night's
    scoreboard is recomputed. UNIQUE (as_of_date, agent): still one row."""
    dates = _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    first = {r["agent"]: r["inputs_hash"] for r in _rows(conn)}
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})

    out = write_weights(conn, clock, CFG)

    assert out["written"] == ["a", "b"]
    rows = _rows(conn)
    assert len(rows) == 2 and all(r["as_of_date"] == AS_OF for r in rows)
    assert all(r["inputs_hash"] != first[r["agent"]] for r in rows)
    assert rows[0]["n_graded"] == 61


def test_the_next_night_keeps_the_old_row_beside_the_new(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})
    clock.advance(days=1)

    write_weights(conn, clock, CFG)

    assert [(r["as_of_date"], r["agent"]) for r in _rows(conn)] == [
        (AS_OF, "a"), (AS_OF, "b"), ("2026-07-14", "a"), ("2026-07-14", "b")]


def test_a_non_finite_load_bearing_value_skips_that_seat_and_names_it(
        conn, clock, monkeypatch):
    """improvement.md §2.1 "Two kinds of column": a NaN where the weight or
    the ranking column should be is no row for that seat, never a placeholder
    (invariant 4). The other seat is still written."""
    import math

    from calibration.scoring import AgentScore

    _two_seat_history(conn)
    real = improve.score_agents

    def poisoned(rows):
        scores, weights = real(rows)
        broken = [AgentScore(**{**vars(s), "brier": math.nan}) if s.seat == "a"
                  else s for s in scores]
        return broken, weights
    monkeypatch.setattr(improve, "score_agents", poisoned)

    out = write_weights(conn, clock, CFG)

    assert out["skipped"] == ["a"] and out["written"] == ["b"]
    assert [r["agent"] for r in _rows(conn)] == ["b"]


def test_a_raise_mid_job_writes_no_row_at_all(conn, clock, monkeypatch):
    """All-or-nothing (improvement.md §6, "no row for any seat"): the rows are
    computed before any is written and land in one transaction."""
    _two_seat_history(conn)
    real = improve.behaviour

    def boom(c, seat, dates):
        if seat == "b":
            raise sqlite3.OperationalError("disk I/O error")
        return real(c, seat, dates)
    monkeypatch.setattr(improve, "behaviour", boom)

    with pytest.raises(sqlite3.OperationalError):
        write_weights(conn, clock, CFG)
    assert _rows(conn) == []


def test_no_graded_seat_writes_nothing_and_says_so(conn, clock):
    assert write_weights(conn, clock, CFG) == {
        "as_of_date": AS_OF, "written": [], "unchanged": [], "skipped": []}
    assert _rows(conn) == []


# --- the read -------------------------------------------------------------

def test_latest_weights_returns_each_seats_newest_row(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})
    clock.advance(days=1)
    write_weights(conn, clock, CFG)

    rows = latest_weights(conn)
    assert [(r["agent"], r["as_of_date"]) for r in rows] == [
        ("a", "2026-07-14"), ("b", "2026-07-14")]
    assert set(rows[0]) == {c[1] for c in conn.execute("PRAGMA table_info(weights)")}
    assert rows[1]["batting"] is None                    # NULL survives as None
    assert [r["agent"] for r in latest_weights(conn, agent="b")] == ["b"]
    assert latest_weights(conn, agent="nobody") == []
```

Add `import sqlite3` to the file's imports.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_improve.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'latest_weights'`; Task 4's tests still PASS.

- [ ] **Step 3: Append the job and the read to `orchestrator/improve.py`**

```python
# --- the `weights` row --------------------------------------------------------

# Columns whose value the PM acts on. A non-finite value here is "no row for
# this seat tonight" (§2.1, §6), never a placeholder. Every other REAL column
# is descriptive and stores NULL where the sample cannot define it.
LOAD_BEARING = ("n_eff", "brier", "bss_shrunk", "total_skill", "weight")

_COLS = ("as_of_date", "agent", "n_graded", "n_abstain", "n_eff", "brier", "bss",
         "bss_shrunk", "total_skill", "reliability", "resolution", "ece",
         "batting", "slugging", "n_signalled", "n_offered", "abstention_rate",
         "n_distinct_conf", "coverage", "cost_usd", "weight", "narrowed",
         "inputs_hash", "created_at")

# Same night, changed inputs: replace that night's row. UNIQUE (as_of_date,
# agent) is what makes this a replacement and not a second row.
_UPSERT = (f"INSERT INTO weights ({', '.join(_COLS)})"
           f" VALUES ({', '.join(':' + c for c in _COLS)})"
           " ON CONFLICT(as_of_date, agent) DO UPDATE SET "
           + ", ".join(f"{c} = excluded.{c}" for c in _COLS
                       if c not in ("as_of_date", "agent")))

# Each seat's newest row. §4: "latest row per seat" = MAX(as_of_date) per
# agent, and the UNIQUE makes it one.
_LATEST = """
SELECT w.* FROM weights w
  JOIN (SELECT agent, MAX(as_of_date) AS d FROM weights GROUP BY agent) m
    ON m.agent = w.agent AND m.d = w.as_of_date
"""


def _descriptive(value: float | None) -> float | None:
    """NULL for a value the sample cannot define: Murphy terms under 20
    calls, batting with no directional call, slugging with no loss (inf),
    BSS on degenerate outcomes. Python's sqlite3 would bind NaN as NULL
    anyway; this makes it a decision rather than an accident, and turns inf
    — which SQLite would store — into the same NULL."""
    return float(value) if value is not None and math.isfinite(value) else None


def _row(score: AgentScore, weight: float, beh: dict, cfg: WeightsConfig,
         digest: str, as_of_date: str, now_iso: str) -> dict:
    return {
        "as_of_date": as_of_date, "agent": score.seat,
        "n_graded": score.n_graded, "n_abstain": score.n_abstain,
        "n_eff": score.n_graded / cfg.horizon_days,
        "brier": score.brier,
        "bss": _descriptive(score.bss),
        "bss_shrunk": score.bss_shrunk,
        "total_skill": score.total_skill,
        "reliability": _descriptive(score.reliability),
        "resolution": _descriptive(score.resolution),
        "ece": _descriptive(score.ece),
        "batting": _descriptive(score.batting),
        "slugging": _descriptive(score.slugging),
        **beh,
        "weight": weight, "narrowed": 0,
        "inputs_hash": digest, "created_at": now_iso,
    }


def write_weights(conn: sqlite3.Connection, clock: Clock,
                  cfg: WeightsConfig) -> dict:
    """One `weights` row per graded seat for tonight (improvement.md §2.1).
    Returns {"as_of_date", "written", "unchanged", "skipped"}, each list in
    seat order, for the job log.

    All-or-nothing: every row is computed before any is written and one
    commit lands them, so a raise anywhere leaves the table exactly as it
    was (invariant 7 — no-change) and the caller alerts once. A seat whose
    load-bearing values are not finite is skipped and named, never written
    with a placeholder (invariant 4). A seat whose inputs hash to its latest
    row's hash is unchanged and not rewritten; a changed seat the same night
    replaces that night's row.
    """
    as_of_date = et_run_date(clock.now())
    now_iso = iso(clock.now())
    rows = scoreboard_rows(conn)
    scores, weights = score_agents(rows)
    dates = window_dates(conn, as_of_date, cfg.window_days)
    latest = {r["agent"]: r["inputs_hash"] for r in latest_weights(conn)}
    out = {"as_of_date": as_of_date, "written": [], "unchanged": [], "skipped": []}
    pending: list[dict] = []
    try:
        for score in scores:
            beh = behaviour(conn, score.seat, dates)
            seat_rows = [r for r in rows if r["seat"] == score.seat]
            digest = inputs_hash(seat_rows, beh)
            if latest.get(score.seat) == digest:
                out["unchanged"].append(score.seat)
                continue
            row = _row(score, weights[score.seat], beh, cfg, digest,
                       as_of_date, now_iso)
            if any(not math.isfinite(row[k]) for k in LOAD_BEARING):
                out["skipped"].append(score.seat)
                continue
            pending.append(row)
        for row in pending:
            conn.execute(_UPSERT, row)
            out["written"].append(row["agent"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out


def latest_weights(conn: sqlite3.Connection,
                   agent: str | None = None) -> list[dict]:
    """Every seat's newest row — or one seat's — as plain dicts carrying every
    `weights` column, in agent order. A NULL descriptive column comes back
    None. The brief's `weights` section reads this; so does the job, for the
    no-op check."""
    sql = _LATEST + (" WHERE w.agent = ?" if agent is not None else "") \
        + " ORDER BY w.agent"
    params = (agent,) if agent is not None else ()
    return [dict(r) for r in conn.execute(sql, params)]
```

- [ ] **Step 4: Run the tests and the purity lint**

Run: `.venv/bin/python3 -m pytest tests/test_improve.py -v && .venv/bin/python3 scripts/check_purity.py`
Expected: PASS, all; lint clean.

If `test_the_weights_row_carries_the_calibration_values` fails on a number, the docstring's derivation is the spec: re-derive by hand against `calibration/scoring.py` before touching either side, and do not loosen a tolerance to pass.

- [ ] **Step 5: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add orchestrator/improve.py tests/test_improve.py
git commit -m "feat(orchestrator): write_weights scores every graded seat into a weights row"
```

---

### Task 6: The brief carries `weights`

**Files:**
- Modify: `agents/tools/fund_server.py:52-70` (`SEAT_CAPS`), `:211-217` (after `_journal`), `:505-541` (`handle_get_stage_brief`), `:579-593` (tool description)
- Test: `tests/test_fund_tools.py`, `tests/test_sim_day.py:515-526`

**Interfaces:**
- Consumes: `orchestrator.improve.latest_weights`.
- Produces: brief key `weights: list[dict]` for seats holding `read_weights` (`analyst`, `news`, `pm`). Scope rule: a seat that also holds `read_signals` (today: the PM) gets every seat's latest row; every other seat gets its own row only. No row for the scope → `weights` is `[]` and `unavailable` carries `"weights (LookupError: no weights rows yet)"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fund_tools.py`, after `test_unbound_providers_are_named_not_faked`:

```python
# --- the weights section (specs/improvement.md §2.1) --------------------------

_WEIGHTS_COLS = ("as_of_date", "agent", "n_graded", "n_abstain", "n_eff", "brier",
                 "bss", "bss_shrunk", "total_skill", "reliability", "resolution",
                 "ece", "batting", "slugging", "n_signalled", "n_offered",
                 "abstention_rate", "n_distinct_conf", "coverage", "cost_usd",
                 "weight", "narrowed", "inputs_hash", "created_at")


def _weights_row(conn, agent, as_of, weight=0.5):
    """A minimal scoreboard row, written directly: this file tests the READ."""
    vals = dict(as_of_date=as_of, agent=agent, n_graded=60, n_abstain=0,
                n_eff=12.0, brier=0.04, bss=0.84, bss_shrunk=0.7,
                total_skill=42.0, reliability=None, resolution=None, ece=None,
                batting=1.0, slugging=None, n_signalled=20, n_offered=20,
                abstention_rate=0.0, n_distinct_conf=1, coverage=1.0,
                cost_usd=1.0, weight=weight, narrowed=0,
                inputs_hash=f"h-{agent}-{as_of}", created_at=f"{as_of}T20:35:00+00:00")
    conn.execute(f"INSERT INTO weights ({', '.join(_WEIGHTS_COLS)}) VALUES"
                 f" ({', '.join(':' + c for c in _WEIGHTS_COLS)})", vals)
    conn.commit()


def test_pm_brief_carries_every_seats_latest_weights_row(fund_db, tmp_path):
    _weights_row(fund_db, "analyst", "2026-07-02", weight=0.4)
    _weights_row(fund_db, "analyst", "2026-07-03", weight=0.6)
    _weights_row(fund_db, "news", "2026-07-03", weight=0.4)
    brief = _brief(fund_db, journals_root=tmp_path)
    assert [(w["agent"], w["as_of_date"], w["weight"]) for w in brief["weights"]] == [
        ("analyst", "2026-07-03", 0.6), ("news", "2026-07-03", 0.4)]
    assert set(brief["weights"][0]) == set(_WEIGHTS_COLS) | {"id"}
    assert brief["weights"][0]["slugging"] is None             # NULL, not 0
    assert brief["unavailable"] == []


def test_analyst_brief_carries_its_own_row_and_no_other_seats(fund_db, tmp_path):
    """calibration.md §6: seeing your own calibration is the cheapest charter
    tune-up; another seat's is not yours to see (improvement.md §2.1)."""
    _weights_row(fund_db, "analyst", "2026-07-03", weight=0.6)
    _weights_row(fund_db, "news", "2026-07-03", weight=0.4)
    for seat in ("analyst", "news"):
        brief = _brief(fund_db, seat=seat, journals_root=tmp_path)
        assert [w["agent"] for w in brief["weights"]] == [seat]
        assert brief["unavailable"] == []


def test_an_empty_weights_table_is_named_unavailable_not_faked(fund_db, tmp_path):
    """improvement.md §2.1 case (ii): no rows at all is named, so the PM
    proceeds with equal weights knowingly. Case (i) — a crashed job with rows
    present — is the test above: the stale rows are carried with their
    as_of_date and nothing lands in `unavailable`."""
    brief = _brief(fund_db, journals_root=tmp_path)
    assert brief["weights"] == []
    assert brief["unavailable"] == ["weights (LookupError: no weights rows yet)"]


def test_a_seat_with_no_row_of_its_own_is_named_unavailable(fund_db, tmp_path):
    _weights_row(fund_db, "analyst", "2026-07-03")
    brief = _brief(fund_db, seat="news", journals_root=tmp_path)
    assert brief["weights"] == []
    assert brief["unavailable"] == ["weights (LookupError: no weights rows yet)"]
```

(No test asserts `_can("exec", "read_weights")` — that would read the table back to itself. The exec and reflect seats' reachable surface is pinned by `test_tools_by_seat_is_exactly_what_each_seat_owns` and `test_brief_is_refused_to_seats_without_the_capability`: neither holds `get_stage_brief`, the only reader.)

Then update the three existing pins that assumed no `weights` section (each is an expected-value change mandated by `improvement.md` §2.1 (ii): an empty table is *named*):

In `test_analyst_brief_is_the_book_and_its_own_journal`, before `brief = _brief(...)` add `_weights_row(fund_db, "analyst", "2026-07-03")` (the test is about the book and journal; a seeded row keeps `unavailable == []` meaningful).

In `test_pm_brief_adds_todays_signals_and_the_gate_budget`, before `brief = _brief(...)` add `_weights_row(fund_db, "analyst", "2026-07-03")`.

In `test_unbound_providers_are_named_not_faked`, the expected list becomes:

```python
    assert [m.split(" (")[0] for m in brief["unavailable"]] == [
        "account snapshot", "journal", "weights", "allowed actions"]
```

Move the `_WEIGHTS_COLS` / `_weights_row` definitions above `test_analyst_brief_is_the_book_and_its_own_journal` so they are defined before first use.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_fund_tools.py -v`
Expected: the five new tests FAIL (`KeyError: 'weights'` / `_can` returns True for nothing yet is fine — that one passes); the three updated pins FAIL on `unavailable`.

- [ ] **Step 3: Grant the capability and build the section**

In `agents/tools/fund_server.py`, add the import:

```python
from orchestrator.improve import latest_weights
```

Extend the naming-rule comment above `SEAT_CAPS` with one line:

```python
#   read_weights         - brief carries the scoreboard (`weights` rows). Scope
#     follows read_signals: a seat that reads every seat's signals reads every
#     seat's row; any other seat reads its own row only (improvement.md §2.1).
```

Change the three cap sets:

```python
    "analyst": frozenset({"get_stage_brief", "submit_signal", "read_account",
                          "read_weights"}),
    "news":    frozenset({"get_stage_brief", "submit_signal", "read_weights"}),
    "pm":      frozenset({"get_stage_brief", "submit_decision", "read_account",
                          "read_signals", "read_allowed_actions", "read_weights"}),
```

After `_journal` (line 216), add:

```python
def _weights(conn: sqlite3.Connection, seat: str) -> list[dict]:
    """This seat's scoreboard view (improvement.md §2.1): every seat's latest
    row for a seat that reads every seat's signals, its own row otherwise.
    No row in scope is NAMED, not an empty list that reads as "no seats":
    improvement.md §2.1 (ii) — the PM proceeds with equal weights knowing
    why. Stale rows are not this case; they carry their own as_of_date —
    which is also how a retired seat's last row reads: it stays "latest"
    for that seat, dated the night it was last graded.

    Scope follows read_signals rather than a seat NAME: the seat that reads
    every seat's signals is the one aggregating them, and that is the grant
    the weights sit beside. A future seat granted read_signals for another
    reason (design.md §2's Bull/Bear) inherits every row with it — a named
    consequence, revisited if that seat arrives."""
    rows = latest_weights(conn, agent=None if _can(seat, "read_signals") else seat)
    if not rows:
        raise LookupError("no weights rows yet")
    return rows
```

In `handle_get_stage_brief`, after the `read_account` block and before the `read_signals` block:

```python
    if _can(seat, "read_weights"):
        brief["weights"] = _section(missing, "weights",
                                    lambda: _weights(conn, seat), [])
```

Update the docstring's second paragraph to: "Seat-scoped by construction: the analyst gets the book, its own journal and its own scoreboard row; the PM gets those PLUS today's signal rows, every seat's scoreboard row, and the gate's allowed-actions snapshot."

In the `get_stage_brief` tool description, replace `" and your own recent journal entries."` with:

```python
          " your own recent journal entries, and `weights` — your latest"
          " scoreboard row (skill, calibration, behavioural rates, PM weight;"
          " `as_of_date` says how fresh it is)."
```

and after `" {buy, sell} in SHARES per active ticker — that is your sizing"` / `" budget; asking above it just gets resized."` add:

```python
          " The PM's `weights` carries every analyst's row: weigh signals by"
          " it, not by the prose."
```

- [ ] **Step 4: Run the tool tests**

Run: `.venv/bin/python3 -m pytest tests/test_fund_tools.py tests/test_tool_surface_canon.py -v`
Expected: PASS. `test_tool_caps_are_real_registered_tool_names` skips `read_weights` (the `read_` rule); the canon tests see no new tool.

- [ ] **Step 5: Update the two sim pins**

In `tests/test_sim_day.py:515-526`, the golden day runs before any scoring night, so the table is empty and §2.1 (ii) names it. Replace the two `unavailable == []` asserts:

```python
    # No scoring night has run before the golden day, so the weights table is
    # empty and improvement.md §2.1 (ii) NAMES it — never an empty section that
    # reads as "no seats". Everything else built.
    assert [m.split(" (")[0] for m in analyst["unavailable"]] == ["weights"]
```
and
```python
    assert [m.split(" (")[0] for m in pm["unavailable"]] == ["weights"]
```

Then add the acceptance item's own clause — "assert on the rendered brief, not the prompt" — under sim, with a row the real job wrote. The sim opens `tmp_path / "fund.sqlite"`, so a DB seeded and scored before `sim_day` runs is the DB the PM's replayed `get_stage_brief` reads:

```python
def test_the_pm_brief_renders_the_row_the_scoring_job_wrote(tmp_path):
    """specs/acceptance.md Phase 2b item 2, under sim: the PM's brief
    `weights` section equals the latest row for every analyst seat, each
    with its as_of_date — read off the replayed tool result, never
    re-derived. The scoring job ran on an earlier night against the same
    on-disk database the sim then opens."""
    from datetime import datetime, timezone

    from orchestrator.improve import (WeightsConfig, latest_weights,
                                      write_weights)
    from tests.test_improve import _two_seat_history

    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)
    write_weights(conn, SimClock(datetime(2026, 7, 2, 20, 35, tzinfo=timezone.utc)),
                  WeightsConfig(window_days=20, horizon_days=5))
    expected = latest_weights(conn)
    conn.close()

    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  pm_recs=("mvf_pm_brief.jsonl",))

    pm = _brief(sim, "decision")
    assert pm["weights"] == expected
    assert [(w["agent"], w["as_of_date"]) for w in pm["weights"]] == [
        ("a", "2026-07-02"), ("b", "2026-07-02")]
    assert [m.split(" (")[0] for m in pm["unavailable"]] == []
```

Run: `.venv/bin/python3 -m pytest tests/test_sim_day.py -v`
Expected: PASS. If `_brief(sim, "decision")` reports zero briefs, the PM recording in use does not call `get_stage_brief` — `mvf_pm_brief.jsonl` does (it is what `test_briefs_reach_the_seats` replays); do not swap in a recording that skips the call.

- [ ] **Step 6: `charters/pm.md` v7 — the charter stops saying the score is Phase 3+**

`charters/pm.md` v6 tells the PM the calibration score does not exist yet (Inputs: "(Phase 3+: each analyst's rolling calibration score … join the brief.)"; Judgment: "Phase 3+: the brief carries each analyst's calibration score; until then, judge the summary's evidence"), while after step 3 the brief carries it and the tool description says to weigh by it. `calibration.md` §2 states "The PM's charter says: treat analyst signals as evidence weighted by the scoreboard" — this is the edit that makes that sentence true. `charters/_template.md`: bump the header on any change and note it in the changelog. Charters change only by human commit: this rides the PR, and the human's merge is that commit.

First pin the version bump. In `tests/test_migrations.py::test_charter_version_comes_from_the_header`, change `== "v6"` to `== "v7"` (the template's rule is the mandate; the test reads the header). Run: `.venv/bin/python3 -m pytest tests/test_migrations.py -k header -v` → Expected: FAIL (`'v6' == 'v7'`).

Then edit `charters/pm.md`:

Header line 1: `# Portfolio Manager — v7`

Inputs (line 17): replace the final parenthetical `(Phase 3+: each analyst's rolling calibration score and links to the debate threads for contested tickers join the brief.)` with:

```markdown
`weights` is each analyst's latest scoreboard row — `weight` (the deterministic pooling weight), `bss_shrunk`, `total_skill`, `reliability`, `abstention_rate`, `coverage`, and `as_of_date`, which says how fresh it is. An analyst with no row is not in the table; `weights` listed under `unavailable` means no seat has a row yet. (Phase 3+: links to the debate threads for contested tickers join the brief.)
```

Judgment (line 28): replace `- Weight analyst signals by their track record, not their confidence (Phase 3+: the brief carries each analyst's calibration score; until then, judge the summary's evidence).` with:

```markdown
- Weight analyst signals by their `weights` row, not by their confidence or their prose: `weight` is the pooling weight, `reliability` says whether their 80s hit like 80s. A seat with no row, or `weights` under `unavailable`, gets the pool's mean weight — equal weights are hard to beat.
```

Changelog (line 35): append ` · v7 \`weights\` joins Inputs and Judgment — the brief carries each analyst's calibration row (improvement.md §2.1); calibration scores are no longer Phase 3+`.

Run: `.venv/bin/python3 -m pytest tests/test_migrations.py tests/test_fund_tools.py -v` → Expected: PASS. Check the charter is still ≤120 lines (`wc -l charters/pm.md`) and that the seven sections are in template order — nothing here adds a section.

- [ ] **Step 7: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add agents/tools/fund_server.py charters/pm.md tests/test_fund_tools.py tests/test_sim_day.py tests/test_migrations.py
git commit -m "feat(agents): the stage brief carries the weights row; pm.md v7 weighs signals by it"
```

---

### Task 7: `scripts/weights_day.py` — the composition root

**Files:**
- Create: `scripts/weights_day.py`
- Modify: `Makefile` (`.PHONY` line 3; a `weights` target beside `resolve`)
- Test: `tests/test_weights_job.py`

**Interfaces:**
- Consumes: `orchestrator.improve.WeightsConfig`, `write_weights`; `scripts/run_day.py`'s `paper_guard`, `require_env`, `_alert`; `state.db.connect`; `agents.wallclock.WallClock` (in `main` only).
- Produces: `REQUIRED_ENV = ("FUND_DB",)`; `load_config(path) -> WeightsConfig`; `write_and_log(conn, clock, cfg) -> dict` (never raises on a scoring failure: alerts `weights_job_failed` and returns `{"failed": True, ...}`); alerts `weights_seat_skipped` naming skipped seats; `main()` exits 0 after a scoring failure, non-zero only before the DB is open.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weights_job.py`:

```python
"""scripts/weights_day.py — the seams of the nightly scoring job.

A composition root like scripts/resolve_day.py: main() builds real clients
and is never called here. The arithmetic is tests/test_improve.py; what is
pinned here is what the job depends on and how it fails, because each is a
way for the scoreboard to go quietly stale.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator import improve
from orchestrator.clock import SimClock
from orchestrator.improve import WeightsConfig
from state.db import connect
from tests.test_improve import _two_seat_history

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weights_day.py"
NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("weights_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


weights_day = _load()


def _alerts(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def test_the_job_needs_only_the_database():
    """No broker, no Slack token, no Anthropic key: it reads rows and writes
    rows. Requiring anything else would let an unrelated missing var stop the
    scoreboard from ever being written (same posture as resolve_day)."""
    assert set(weights_day.REQUIRED_ENV) == {"FUND_DB"}


def test_config_comes_from_the_committed_yaml():
    assert weights_day.load_config(weights_day.CONFIG_YAML) == WeightsConfig(
        window_days=20, horizon_days=5)


def test_a_config_missing_a_key_fails_loud(tmp_path):
    bad = tmp_path / "improvement.yaml"
    bad.write_text("window_days: 20\n")
    with pytest.raises(KeyError, match="horizon_days"):
        weights_day.load_config(bad)


def test_a_normal_night_writes_and_logs(tmp_path, capsys):
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY),
                                    WeightsConfig(20, 5))

    assert out["written"] == ["a", "b"] and not out.get("failed")
    assert "weights_day: 2026-07-13 · written a, b" in capsys.readouterr().out
    assert _alerts(conn) == []


def test_a_scoring_failure_alerts_once_and_leaves_the_table_alone(
        tmp_path, monkeypatch, capsys):
    """improvement.md §6: no row, last good rows stand, ONE alert. The job
    returns rather than raising so fund-pnl.service's next leg (reflect_day,
    perishable) still runs — see the module docstring."""
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)
    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))
    before = [dict(r) for r in conn.execute("SELECT * FROM weights ORDER BY id")]

    def boom(conn, clock, cfg):
        raise RuntimeError("numpy went away")
    monkeypatch.setattr(weights_day, "write_weights", boom)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    assert out["failed"] is True
    assert [dict(r) for r in conn.execute("SELECT * FROM weights ORDER BY id")] == before
    alerts = _alerts(conn)
    assert [a["code"] for a in alerts] == ["weights_job_failed"]
    assert "RuntimeError: numpy went away" in alerts[0]["text"]
    assert "ALERT" in capsys.readouterr().out


def test_skipped_seats_are_named_in_one_alert(tmp_path, monkeypatch):
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)

    def partial(conn, clock, cfg):
        return {"as_of_date": "2026-07-13", "written": ["b"],
                "unchanged": [], "skipped": ["quant", "macro"]}
    monkeypatch.setattr(weights_day, "write_weights", partial)

    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    alerts = _alerts(conn)
    assert [a["code"] for a in alerts] == ["weights_seat_skipped"]
    assert alerts[0]["text"].endswith(": quant, macro")
    assert "2 seat(s)" in alerts[0]["text"]


def test_a_failed_alert_write_is_logged_and_still_exits_clean(
        tmp_path, monkeypatch, capsys):
    """The likeliest cause of a scoring crash is the database, and the alert
    goes through the same connection. A raise out of the except would exit
    1 and hold back reflect_day; instead it is logged to stdout (journald)
    and the job returns — reflect_day hits the same database and fails loud
    on its own, which is OnFailure='s job."""
    conn = connect(tmp_path / "fund.sqlite")

    def boom(conn, clock, cfg):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(weights_day, "write_weights", boom)

    def alert_boom(conn, clock, code, text, **payload):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(weights_day.run_day, "_alert", alert_boom)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    assert out["failed"] is True
    assert _alerts(conn) == []
    assert "ALERT NOT WRITTEN" in capsys.readouterr().out


def test_the_job_never_drains_the_outbox(tmp_path, monkeypatch):
    """It holds no Slack token. The alert sits in `events` for the next leg's
    drain (reflect_day runs right after it on fund-pnl.service)."""
    conn = connect(tmp_path / "fund.sqlite")

    def boom(conn, clock, cfg):
        raise RuntimeError("x")
    monkeypatch.setattr(weights_day, "write_weights", boom)
    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))
    assert conn.execute("SELECT posted_at FROM events").fetchone()["posted_at"] is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_weights_job.py -v`
Expected: FAIL at module load — `FileNotFoundError` for `scripts/weights_day.py`.

- [ ] **Step 3: Write the script**

Create `scripts/weights_day.py`:

```python
#!/usr/bin/env python3
"""Nightly scoring job — writes the `weights` table (specs/improvement.md §2.1).

    make weights           # == python scripts/weights_day.py

resolve_day.py writes the calibration INPUT and stops at the data; this is
the consumer calibration.md §0 always promised ("Ops runs the scoreboard
job; agents read it"). One row per graded seat per night: every AgentScore
field, the deterministic PM weight, and the behavioural rates the Proposer
is later graded against. The morning brief reads the row as data.

WHY IT RIDES THE 16:35 TIMER, THIRD. It needs tonight's resolutions, which
resolve_day writes one leg earlier. It sits BEFORE reflect_day for two
reasons: reflect_day drains the outbox, so an alert this job appends reaches
Slack the same night without this job holding a token; and reflect_day is
perishable (a reflection missed for seven nights is destroyed) while this
job is not — a missed scoreboard night is recomputed, identically, the next
night. That is also why a SCORING failure exits 0 here: Type=oneshot stops
at the first non-zero ExecStart, and a broken scoreboard must not hold back
the leg that cannot be retried. Only a failure BEFORE the database is open
(a missing env var, a paper-guard trip, a config missing a key) exits
non-zero — nothing can alert yet, and OnFailure= is the alert. If the
database itself is what failed, the alert write fails too: that is logged
to stdout (journald) and the job STILL exits 0 — reflect_day then hits the
same database and fails loud on its own, which is OnFailure='s job.

NO SLACK, NO SEAT, NO BROKER. The job needs the database and the committed
config and nothing else. Requiring a token would let an unrelated missing
var stop the scoreboard from ever being written. The cost of holding no
token: an alert this job appends is posted by reflect_day's drain, and if
that leg exits before its drain (missing key, lock held), the row sits
undrained and reddens the next audit — audit_day's undrained check has no
date bound. The same posture reflect_day's own alerts already have.

Posture (invariant 4 / improvement.md §0.7: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'  -> exit 1 before anything else
  * a missing env var             -> exit 1 naming every missing var
  * config missing a key          -> exit 1 (KeyError names the key)
  * write_weights raises          -> no row for any seat (it rolled back),
                                     last good rows stand, ONE alert
                                     (weights_job_failed), exit 0
  * ...and the alert write raises -> logged "ALERT NOT WRITTEN", exit 0
  * a seat's load-bearing value   -> that seat skipped, the rest written,
    is not finite                    ONE alert naming every such seat
                                     (weights_seat_skipped), exit 0
  * unchanged inputs              -> nothing written, logged as unchanged

Re-running is safe and free: unchanged seats hash to their latest row and
write nothing; a changed seat the same night replaces that night's row.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/weights_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import yaml                                           # noqa: E402

import run_day                                        # noqa: E402
from orchestrator.improve import WeightsConfig, write_weights   # noqa: E402
from state.db import connect                          # noqa: E402

REQUIRED_ENV = ("FUND_DB",)
CONFIG_YAML = ROOT / "config" / "improvement.yaml"


def log(msg: str) -> None:
    print(f"weights_day: {msg}", flush=True)


def load_config(path: Path) -> WeightsConfig:
    """config/improvement.yaml -> WeightsConfig. A missing key raises KeyError
    naming it; a value below 1 raises from the dataclass. Both are exit 1
    before the database is touched: a scoreboard computed over a guessed
    window is worse than none."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return WeightsConfig(window_days=int(data["window_days"]),
                         horizon_days=int(data["horizon_days"]))


def write_and_log(conn, clock, cfg: WeightsConfig) -> dict:
    """Score tonight and report. Never raises on a scoring failure: the
    failure becomes one alert in the outbox and `failed: True` in the
    return, so main() can exit 0 and the perishable leg behind it runs."""
    try:
        out = write_weights(conn, clock, cfg)
    except Exception as exc:
        # write_weights rolled back before re-raising: no row for any seat.
        # The alert rides the same connection; if the DATABASE is what
        # failed it raises too, and a raise out of here would exit 1 and
        # hold back the perishable leg. Log it and return instead.
        text = (f"weights_job_failed — {type(exc).__name__}: {exc};"
                " no weights row written tonight, last good rows stand")
        try:
            run_day._alert(conn, clock, "weights_job_failed", text)
        except Exception as alert_exc:
            log(f"ALERT NOT WRITTEN ({type(alert_exc).__name__}:"
                f" {alert_exc}) — {text}")
        return {"failed": True, "written": [], "unchanged": [], "skipped": []}
    if out["skipped"]:
        run_day._alert(conn, clock, "weights_seat_skipped",
                       f"weights_seat_skipped — {len(out['skipped'])} seat(s)"
                       " had a non-finite load-bearing score and got no row"
                       f" tonight: {', '.join(out['skipped'])}")
    log(f"{out['as_of_date']} · written {', '.join(out['written']) or '—'}"
        f" · unchanged {', '.join(out['unchanged']) or '—'}"
        f" · skipped {', '.join(out['skipped']) or '—'}")
    return out


def main(argv: list[str] | None = None) -> int:
    import os

    from agents.wallclock import WallClock

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)
    cfg = load_config(CONFIG_YAML)

    write_and_log(connect(env["FUND_DB"]), WallClock(), cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Add the Make target**

In `Makefile`, add `weights` to the first `.PHONY` line, and beside the `resolve` target:

```make
# Nightly scoring job: graded signals -> the weights table (improvement.md §2.1).
weights: deps
	$(PYTHON) scripts/weights_day.py
```

- [ ] **Step 5: Run the job tests and both lints**

Run: `.venv/bin/python3 -m pytest tests/test_weights_job.py -v && .venv/bin/python3 scripts/check_alert_codes.py && .venv/bin/python3 scripts/check_purity.py`
Expected: PASS; both lints clean (the two alert codes are string literals through `run_day._alert`, which the lint treats as a forwarder and checks the call sites of).

- [ ] **Step 6: Full suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add scripts/weights_day.py tests/test_weights_job.py Makefile
git commit -m "feat(scripts): weights_day — the nightly scoring job's composition root"
```

---

### Task 8: Ops — the third leg on `fund-pnl.service`

**Files:**
- Modify: `ops/fund-pnl.service` (between the `resolve_day.py` and `reflect_day.py` ExecStart lines), `ops/README.md:33`
- Test: `tests/test_ops_units.py:57-83`

**Interfaces:**
- Produces: the committed leg order `close_pnl, resolve_day, weights_day, reflect_day, critic_g1`.

- [ ] **Step 1: Update the pin first**

In `tests/test_ops_units.py`, rename `test_the_nightly_unit_runs_its_four_legs_in_the_committed_order` to `..._five_legs_...`, add to its docstring after the `resolve_day` line:

```
      weights_day             third: arithmetic, no LLM budget, no token, and
                                     IMPERISHABLE — an unchanged night hashes
                                     to its latest row and a missed night is
                                     recomputed identically the next. Ahead of
                                     reflect so reflect's drain posts its
                                     alert, and because a scoring failure
                                     exits 0 (scripts/weights_day.py) it can
                                     never hold the perishable leg back
```

and change the assertion to:

```python
    assert [Path(cmd.split()[-1]).name for cmd in _exec_starts(PNL)] == [
        "close_pnl.py", "resolve_day.py", "weights_day.py", "reflect_day.py",
        "critic_g1.py"]
```

Run: `.venv/bin/python3 -m pytest tests/test_ops_units.py -v`
Expected: FAIL on the leg list.

- [ ] **Step 2: Add the leg**

In `ops/fund-pnl.service`, after the `ExecStart=... resolve_day.py` line and before the "Third: the reflection turns" comment, insert:

```ini
# Third: the nightly scoring job (specs/improvement.md §2.1) — graded signals
# into the `weights` table the morning brief reads. Arithmetic only: no LLM,
# no Slack token, no broker. It needs tonight's resolutions, so it follows
# resolve_day; it precedes reflect_day so that leg's drain posts any alert it
# appended, and because it is IMPERISHABLE where reflect is not — a scoring
# failure exits 0 by design (see the script's docstring) so it can never
# hold back the leg that cannot be retried.
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/weights_day.py
```

Fix the ordinal words in the two comments below it: "Third: the reflection turns" → "Fourth: the reflection turns"; "Fourth and last: nightly G1 enforcement" → "Fifth and last: nightly G1 enforcement". Update the `Description=` line to add `; scoreboard weights`.

- [ ] **Step 3: Update the README row**

`ops/README.md:33`:

```markdown
| `fund-pnl.timer` | 16:35 ET Mon–Fri | `scripts/close_pnl.py`, then `scripts/resolve_day.py`, then `scripts/weights_day.py`, then `scripts/reflect_day.py`, then `scripts/critic_g1.py` |
```

- [ ] **Step 4: Run the ops tests, then the suite, then commit**

Run: `make test`
Expected: PASS.

```bash
git add ops/fund-pnl.service ops/README.md tests/test_ops_units.py
git commit -m "ops: weights_day rides fund-pnl.service third, ahead of the perishable reflect leg"
```

---

### Task 9: Docs close-out — the lane is parsed, served, and ticked

**Files:**
- Modify: `specs/improvement.md:15-17` (header note), `specs/contracts.md:373` (matrix row), `:422` (§7b paragraph), `specs/acceptance.md` (Phase 2b items 1, 2, and the contract-test item's first half)

- [ ] **Step 1: `specs/improvement.md` header note**

Replace lines 15–17:

```markdown
**Tables and tool schemas here are canonical for the improvement loop.** §4 is parsed by
`tests/test_schema_contract.py` (lane (a), 2026-08-30; `lessons` and `proposals` sit in
`NO_SCHEMA_HOME` until their lanes land). §5 is not yet parsed by
`tests/test_tool_surface_canon.py`; §8 says which lane adds it.
```

- [ ] **Step 2: `specs/contracts.md`**

Field-matrix row (line 373): drop the phase marker —

```markdown
| `weights` | ✓ (own row only) | ✓ (every analyst) | latest `weights` row(s) with `as_of_date` — `specs/improvement.md` §2.1; `orchestrator/improve.py:latest_weights` |
```

§7b paragraph (line 422), replace the last sentence ("Until the first Phase 2b lane lands, …") with:

```markdown
`offered` and `weights` live in `state/schema.sql` and `improvement.md` §4 is parsed by `tests/test_schema_contract.py` (lane (a)); `lessons` and `proposals` follow with their lanes, listed in `NO_SCHEMA_HOME` until then.
```

- [ ] **Step 3: `specs/acceptance.md` Phase 2b**

Tick items 1 ("Scoring job (S1)") and 2 ("Briefs carry `weights`") — `- [x]`. On the last item ("Contract tests widened"), leave the box open and append: `— §4 parsed since lane (a); §5 pending lane (c).`

- [ ] **Step 4: Verify the contract tests still read the edited files, then commit**

Run: `make test`
Expected: PASS (the §4 heading and its one `sql` fence are unchanged; the §7b edit is prose outside any parsed section).

```bash
git add specs/improvement.md specs/contracts.md specs/acceptance.md
git commit -m "docs: lane (a) landed — improvement.md §4 parsed, weights served, acceptance ticked"
```

- [ ] **Step 5: Open the PR against `master`**

Title: `feat: Phase 2b (a) — offered, the scoring job, and weights in the brief (#205)`. Body, in this order:

1. The §8 (a) sentence.
2. **Two human-commit items to read first:** the Task 1 spec amendment (six nullable columns; `n_signalled` definition), and the `charters/pm.md` v6 → v7 edit (Task 6) — every PM decision after the merge is attributed `v7`.
3. **Expected-value changes to existing tests, each with its mandate:** `tests/test_preflight_schema.py` table count 15 → 17 (the test's own docstring); `tests/test_sim_day.py` two `unavailable == []` pins → `["weights"]` (improvement.md §2.1 (ii)); `tests/test_fund_tools.py` two tests now seed a `weights` row (same clause; the empty case gets its own test); `tests/test_ops_units.py` four legs → five; `tests/test_migrations.py` `charter_version_for({"seat": "pm"}) == "v6"` → `"v7"` (`charters/_template.md`: bump the header on any change).
4. **Behaviour change the eval rig will show:** `evals/` builds briefs through `handle_get_stage_brief` against a DB with no `weights` rows, so every live eval case's PM/analyst brief now carries `weights: []` and `unavailable: ["weights (…)"]` until a scoring night has run on that DB. Correct per §2.1 (ii); the LLM-facing prompt changes.
5. **Deploy note:** the droplet's existing DB gains both tables on the next `connect()` (`state/db.py:37-41`; pinned by `tests/test_state.py`); the new unit leg needs `systemctl daemon-reload` after the file lands under `/opt/fund/ops/`; the first scoring night writes rows for every seat with graded history, so the PM's first post-deploy brief already carries them.

No closing keyword — #205 stays open for (b)–(f).

---

## Self-review

**Spec coverage** (`specs/acceptance.md` Phase 2b, items this lane owns):

| Requirement | Task |
|---|---|
| one `weights` row per graded seat, every `AgentScore` field + the six rates + `n_eff` | 5 (`_row`, hand-computed vector) |
| calibration §1–§2 values (abstains at 0.5, total = shrunk × n, floor 0.5× mean) | 5 (asserted numerically) |
| pre-gate writes one `offered` row per surviving ticker under sim; `{buy:0,sell:0}` absent | 3 |
| `n_offered` = offered rows in the window | 4 (`behaviour`), 5 |
| second run on unchanged data is a no-op (same `inputs_hash`) | 5 |
| job crash → no row, last good rows stand, one alert | 5 (rollback), 7 (`weights_job_failed`) |
| PM brief: latest row for every analyst with `as_of_date`; analyst: own row only | 6 (handler tests + the sim test off the replayed tool result) |
| calibration §2: "the PM's charter says: treat analyst signals as evidence weighted by the scoreboard" | 6 (pm.md v7) |
| `tests/test_preflight_schema.py` table-count tripwire bumped in the same commit as the DDL | 2 |
| empty/absent table → named in `unavailable`; crashed job with rows → rows carried, nothing in `unavailable` | 6 |
| windows and thresholds read from `config/improvement.yaml` | 4, 7 |
| `improvement.md` §4 parsed; `lessons`/`proposals` in `NO_SCHEMA_HOME` with #50 reason | 2 |
| purity lint covers `orchestrator/improve.py` | 4, 5 (package membership) |

Not in this lane, deliberately: `narrowed` is written as 0 (lane (d)); `submit_lessons`/`submit_proposal` stay `not served` (lanes (c)/(e)); the weekly `#pnl` projection of the scoreboard (calibration §6) is unchanged from today's `render_markdown` path and is not wired here — it is not a Phase 2b acceptance item.

**Placeholder scan:** no TBD/TODO; every code step shows its code; every command has an expected outcome.

**Type consistency:** `write_weights(conn, clock, cfg) -> dict` in Tasks 5 and 7; `latest_weights(conn, agent=None) -> list[dict]` in Tasks 5 and 6; `behaviour` keys `n_signalled, n_offered, n_distinct_conf, abstention_rate, coverage, cost_usd` in Tasks 4 and 5 (`**beh` spreads into `_row` against `_COLS`); `WeightsConfig(window_days, horizon_days)` positional in Task 7's tests matches the dataclass field order in Task 4; `_pre_gate_stage` keeps its `list[str]` return so `run_day` is untouched.

**Two things the reviewer should push on:** (1) Task 1 is a spec amendment riding an implementation lane — it is small and forced by `calibration/scoring.py`'s NaN semantics, but it is still a human's commit to `improvement.md`; (2) the `read_signals`-implies-all-rows scope rule in Task 6 is a design choice, not in the spec's words, chosen over hardcoding `"pm"` in the handler.
