# Trial Registry Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `trial_registry` + `holdout_evaluations` out of `fundbt/registry.py`'s standalone DDL into `state/schema.sql` with their canonical foreign keys intact, and make `TrialRegistry` write to an injected fund-DB connection instead of minting its own database.

**Architecture:** `state/schema.sql` becomes the single DDL home for both tables, copied character-for-character from `specs/strategy-contracts.md` §2 lines 91–115 (plus `IF NOT EXISTS` on both tables and both indexes, which is load-bearing twice over). `fundbt/registry.py` loses its `DDL` constant entirely and its constructor takes an already-open `sqlite3.Connection` — the registry owns queries, never schema, and never a database. Tests build a fresh fund-schema `:memory:` DB per call so family N starts at zero by construction. `consume_holdout`'s blanket `sqlite3.IntegrityError` catch is narrowed so a foreign-key violation can never masquerade as a consumed holdout. And — by CEO ruling 2026-08-29, E1 variant D — `evaluate_holdout` logs its own `is_holdout=1` trial row, so the FK it has always declared finally has a target: this lane lands #189's fix and closes it (`closes #189`), measurably moving zero frozen numbers.

**Tech Stack:** Python 3.12+, stdlib `sqlite3`, pytest ≥8. No new dependencies.

## Global Constraints

- **`strategies` stays OUT.** Registry tables only: `trial_registry` + `holdout_evaluations` + their two indexes. Do not touch `state/transition.py`. Do not touch `state/specs.py`. If the migration appears to need `strategies`, stop and escalate.
- **Never re-record a fixture.** `CLAUDE.md`: "NEVER update a golden fixture, expected hash, or expected value to make a test pass. STOP and ask. No 'deliberate re-record.'" `fixtures/golden-strategy.md:46`'s `deflated_sharpe (N=1) = 1.000000` must stay true — by per-test family isolation (a fresh registry per test, and no golden test running a further family trial after a G3 holdout), never by adjusting the fixture to match a moved number. Task 6 adds **prose** to that file under an explicit CEO lift; it changes **no number, no hash and no expected value**, and the lift is not licence to touch one.
- **`spec_id="spec_golden000000f1"` cannot change.** It is baked into `tests/test_golden.py:21-22`'s frozen hashes. Seed a matching `strategy_specs` row instead.
- **`IF NOT EXISTS` is mandatory on both tables** — `state/db.py:12` regex-matches that exact string to build `_TABLES`, and a table absent from `_TABLES` never reaches an existing database.
- **`IF NOT EXISTS` is mandatory on both indexes** — `state/db.py`'s `_TABLES <= have` guard re-runs the *entire* `schema.sql` whenever any one table is missing. A bare `CREATE INDEX` raises `index idx_trials_family already exists` on that second pass.
- **DDL must be near-character-exact to `specs/strategy-contracts.md` §2.** `tests/test_schema_contract.py` compares `type`, `default`, `checks` and `references` as normalized text: `INT` vs `INTEGER` and `DEFAULT 0` vs `DEFAULT 0.0` fail.
- **Purity lint stays clean.** `fundbt/` must import no LLM code and call no wall clock (`scripts/check_purity.py`, run by `make test`).
- **Region is a hard boundary.** IN: `fundbt/registry.py`, `state/schema.sql`, `tests/synthetic.py`, `tests/test_run_backtest.py`, `tests/test_golden.py`, `tests/test_preflight_schema.py`, `tests/test_schema_contract.py`, `scripts/preflight_schema.py`, `tests/conftest.py`, `tests/run_tests.py`, plus `fundbt/run_backtest.py` for the `consume_holdout` call-site's error handling **and** for `evaluate_holdout`'s trial-row insert. Everything else — including all of `specs/` — is OUT and goes in Escalations.
- **Two explicit lifts, both by the CEO, both scoped.** (1) `fundbt/run_backtest.py::evaluate_holdout` may log its own trial row — this lane lands #189's fix (E1, variant D). (2) `fixtures/golden-strategy.md` is lifted out of the OUT list for **one prose addition** (Task 6, rider R2). **Authorization of record for lift (2):** [issue #172, comment](https://github.com/benjaminematton/fund/issues/172#issuecomment-5463477474) — that comment, not this plan, is what an implementer verifies against; it also states the lift's limits. Nothing else in `fixtures/` moves, and **`specs/` stays OUT entirely** — including the canonical gap R1 names.
- Repo root for every path below: `/Users/benjaminmatton/Developer/fund-wt/registry-unify`. Branch `registry-unify`, off `origin/master` = `e55f110`.

---

## ✅ READ THIS BEFORE STARTING: E1 is RULED — variant D. Nothing is gated.

**The whole plan is executable. Tasks 1–6 run end to end.**

### The problem this resolves

`holdout_evaluations.run_key REFERENCES trial_registry(run_key)`. `evaluate_holdout` mints a `+holdout` run key that `registry.log()` never receives (#189). Under `PRAGMA foreign_keys = ON`, the insert fails the FK. Measured against a real `state.db.connect(":memory:")` running the canonical §2 DDL:

```
holdout w/ unlogged run_key: IntegrityError SQLITE_CONSTRAINT_FOREIGNKEY
holdout w/ logged   run_key: ok
second holdout:              IntegrityError SQLITE_CONSTRAINT_PRIMARYKEY
```

Left alone, that takes two currently-green tests red: `tests/test_golden.py::test_golden_pass_path` (line 37) and `tests/test_run_backtest.py::test_holdout_single_touch` (both calls).

### The ruling — CEO, 2026-08-29

> The `strategies` fence kept out a *new design* with unresolved interactions; #189's insert is a fix whose content was already ruled correct, and the alternatives are both worse than the hygiene cost: A ships a migration carrying a known landmine (first real holdout → false p-hacking page) until a second lane lands, and B rewrites green tests to assert a wiring error as if it were intended behavior. When the clean-lanes option means deliberately landing a broken seam, hygiene is the wrong thing to optimize. **D with explicit sanction is clean *because* it's sanctioned.**

So: **`evaluate_holdout` logs its own trial row with `is_holdout=1` before calling `consume_holdout`, in this lane.** Ruling 3 ("do not fix the cause here") is lifted for this specific insert, by the person who set it. The PR carries `closes #189`, and #189's content lands unchanged.

### The measurement is a fact now, not a conjecture

Variant D's deciding claim was marked "reasoned, must be confirmed by the run." **It has been run and it HOLDS.** Every frozen number identical: `deflated_sharpe` at full precision `0.9999999351282387` (→ `1.000000`), `n_trials_family` 1, `net_sharpe` `1.827113`, `holdout_days`/`holdout_trades` 391/160, `holdout_sharpe` `3.092703`, every hash, `family_n("F1")` 1 and 3 in their respective tests. `10 passed` → `10 passed`; full suite `1622 passed`. **Zero test edits.** Task 5's "leave both tests exactly as they are" (Step 3) is a recorded result, not a hope.

### Three riders the ruling attaches — none optional

- **R1 — `seat="orchestrator"`, recorded as a design choice** (Task 5, Step 2). It is an **inference from an acceptance criterion, not a schema statement**.
- **R2 — the DSR margin warning goes INTO `fixtures/golden-strategy.md`** (Task 6). Prose only. **No number, hash or expected value is touched.**
- **R3 — the variance subtlety is a code comment at the insert site** (Task 5, Step 1), so it is discoverable where it matters.

### What is still open

E5, E8 and E9 are genuine two-readings and **remain flagged**; the ruling does not touch them. Task 5 was the only gate, and it is lifted.

---

## File Structure

| File | Responsibility after this lane |
|---|---|
| `state/schema.sql` | Sole DDL home for `trial_registry`, `holdout_evaluations` and their two indexes. Grows from 12 tables to 14. |
| `fundbt/registry.py` | Query surface only. No `DDL` constant, no `sqlite3.connect`, no path argument, no import of `state/`. Takes an open connection. |
| `fundbt/run_backtest.py` | Two changes, both in `evaluate_holdout`: an FK violation around the `consume_holdout` call becomes `BacktestError("holdout_wiring_error")` (Task 4), and the function logs its own `is_holdout=1` trial row first (Task 5 — #189's fix, sanctioned). |
| `fixtures/golden-strategy.md` | **Prose only** (Task 6, rider R2): one note that the frozen numbers hold by per-test family isolation, and that a holdout-inclusive vector must derive its N accordingly. Not one number moves. |
| `tests/synthetic.py` | Gains `seed_spec_row()` (the `strategy_specs` row the FK requires) and `make_registry()` (fresh fund-schema DB per call). |
| `tests/test_schema_contract.py` | `NO_SCHEMA_HOME` shrinks from 5 entries to 3, which is what turns on comparison against §2. Gains a schema-idempotency test. |
| `tests/test_preflight_schema.py` | Four tests move to the 14-table world under an explicit CEO-ruling citation. |
| `scripts/preflight_schema.py` | Two comments stating the registry lives in a separate DB become false and are corrected. |
| `tests/run_tests.py` | **Unchanged** — the design deliberately avoids adding pytest fixture parameters to any `fundbt` test, so the parameterless runner keeps working. |

---

## Design decisions

### D1 — How the registry receives its connection

**Chosen: `TrialRegistry(conn: sqlite3.Connection)`. Connection only. No path parameter, no default.**

The registry stops owning a database and owns queries. Callers build the connection with `state.db.connect(path)`, which is what turns `PRAGMA foreign_keys = ON` and what makes `trial_registry.spec_id → strategy_specs(spec_id)` real.

Why this one:

- It is the only variant under which `fundbt/registry.py` imports nothing from `state/`. `state/specs.py:19` already imports `fundbt.hashing`; adding `fundbt.registry → state.db` makes a package-level cycle that is safe today only because `fundbt/__init__.py` is a bare docstring — a latent trap keyed to a file nobody would think to check before editing.
- It matches how every other writer in this repo works. `agents/seats.py` hands `build_fund_server` a `conn_factory`; every fund tool handler takes a `conn`; `state/specs.py:insert_strategy_spec(conn, ...)` takes a `conn`.
- It fixes the concurrency posture noted in recon. `state/db.py`'s `connect()` is called per tool call specifically to avoid holding write locks; `TrialRegistry` held one long-lived connection of its own. With the connection injected, the registry inherits whatever posture its caller has, and a future MCP `run_backtest` tool constructs `TrialRegistry(conn_factory())` per call like everything else. No production caller exists to bind this today (zero production callers — YAGNI), so nothing is built for it now.
- It removes the ability to mint an isolated database, which is the defect #172 was filed against. A `db_path` parameter with a `":memory:"` default is precisely how the registry got its own DB in the first place.

Alternatives, and why not:

- **Keep `db_path=":memory:"`, route it through `state.db.connect`.** Zero call-site churn — and `connect(":memory:")` genuinely works (measured: 12 tables, `foreign_keys=1`). But it puts `import state.db` inside `fundbt/`, inverting the dependency direction and creating the cycle above, and it leaves the registry able to mint its own DB. Rejected on the cycle.
- **Union type `sqlite3.Connection | str | Path`.** Convenience at the cost of two code paths, and the path branch drags the `state.db` import back in. Rejected.
- **`TrialRegistry(conn)` plus a `TrialRegistry.open(path)` classmethod.** Same import problem, deferred one level. Rejected. If a standalone entry point is ever wanted, it belongs in a composition root, not in the registry.

### D2 — Does `TrialRegistry` keep its class shape?

**Yes.** It becomes a one-field wrapper over a connection, which is thin, but `fundbt/run_backtest.py` takes `registry: TrialRegistry` as a keyword parameter in two public signatures, and that parameter is the seam a caller uses to hand in a stub. Collapsing to module-level functions taking `conn` (the `state/specs.py` style) would churn both signatures and remove the seam for no gain in this lane. **This one is defensible both ways** — see Escalation E9.

### D3 — Who owns the transaction

**`log()` and `consume_holdout()` keep their `self.conn.commit()` calls.** On an injected connection this commits the caller's in-flight work too. That is the repo's existing precedent — `state/specs.py:insert_strategy_spec` commits a passed-in `conn` the same way — and #189 explicitly scopes the "same transaction as the verdict" question (`specs/strategy-contracts.md:200`) to itself. Changing it here would silently move #189's premise. **Defensible both ways** — see Escalation E5.

### D4 — Where the narrowing lives

`fundbt/registry.py` re-raises the `sqlite3.IntegrityError` on `SQLITE_CONSTRAINT_FOREIGNKEY` and returns `False` only on the PRIMARY KEY path. `fundbt/run_backtest.py` translates the escaped exception into `BacktestError("holdout_wiring_error")`.

Discrimination is on `exc.sqlite_errorname`, not on the message text. Measured: `SQLITE_CONSTRAINT_FOREIGNKEY` vs `SQLITE_CONSTRAINT_PRIMARYKEY`. `sqlite3.Error.sqlite_errorname` exists in Python 3.11+; `pyproject.toml` requires `>=3.12`.

The registry does not raise its own exception type because `BacktestError` lives in `run_backtest.py`, which imports `registry.py` — importing it back would be a real cycle. The alternative (a new `RegistryError` in `registry.py`) is noted in E-alt but rejected as a second error vocabulary for one call site.

### D5 — Test isolation without a pytest fixture

Tests get their registry from `tests.synthetic.make_registry()`, which does `state.db.connect(":memory:")` + `seed_spec_row()` + `TrialRegistry(conn)` — a fresh database per call.

This preserves per-test isolation by construction (family N starts at zero every time, so `deflated_sharpe (N=1) = 1.000000` holds without anyone adjusting anything), uses the real fund schema with FKs on, and — critically — **adds no parameter to any test signature**, so `tests/run_tests.py`'s parameterless runner keeps working untouched.

The alternative is `tests/conftest.py`'s `fund_db` fixture, which yields a `sqlite3.Connection` over `connect(tmp_path/"fund.sqlite")`. It is closer to production (a real file, real WAL) but forces every `fundbt` test to grow a fixture parameter, which breaks `tests/run_tests.py` silently — it is not in `make test`, so CI would never notice. **Defensible both ways** — see Escalation E8, which contains the exact alternative wiring if Benjamin prefers the fixture.

---

## Task 1: Land the canonical DDL in `state/schema.sql` and bind it in the contract test

**Files:**
- Modify: `state/schema.sql` (insert after line 186, the `);` closing `strategy_critiques`)
- Modify: `tests/test_schema_contract.py:106-112` (`NO_SCHEMA_HOME`)
- Test: `tests/test_schema_contract.py` (existing parametrized comparison + one new test)

**Interfaces:**
- Consumes: nothing.
- Produces: tables `trial_registry(run_key, spec_id, family, config_hash, data_snapshot_hash, engine_version, seed, seat, stats, is_holdout, created_at)` and `holdout_evaluations(spec_id, run_key, passed, detail, created_at)` in every DB opened by `state.db.connect()`, with `trial_registry.spec_id → strategy_specs(spec_id)` and `holdout_evaluations.run_key → trial_registry(run_key)` enforced. Indexes `idx_trials_family`, `idx_trials_spec`.

- [ ] **Step 1: Write the failing test — remove the two entries from `NO_SCHEMA_HOME`**

The contract test's own comment says "A table listed here is not compared, so removing it from the list is what binds it." Removing them first *is* the failing test. Replace `tests/test_schema_contract.py:106-112` with:

```python
# Spec §2 tables that deliberately have no `state/schema.sql` home. Reason per
# table is recorded in issue #50; it is not restated here. A table listed here
# is not compared, so removing it from the list is what binds it.
#
# `trial_registry` and `holdout_evaluations` came OFF this list on 2026-08-29
# under issue #172, which is #50's Group 2: their DDL moved out of
# fundbt/registry.py's standalone string into state/schema.sql, so both are now
# compared character-for-character against strategy-contracts.md §2. #50's
# Group 1 (`strategies`, `sleeves`, `shadow_fills`) is untouched — no DDL for
# those exists anywhere in the repo.
NO_SCHEMA_HOME = frozenset({
    "strategies", "sleeves", "shadow_fills",
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_schema_contract.py -q`

Expected: FAIL. `test_every_spec_table_is_declared_in_schema` fails with
`declared in a canonical spec §2 but absent from state/schema.sql: holdout_evaluations (...), trial_registry (...)`, and the two parametrized cases `test_schema_matches_spec[trial_registry]` / `[holdout_evaluations]` error on a missing key.

- [ ] **Step 3: Add the DDL to `state/schema.sql`**

Insert this block after line 186 (the `);` that closes `strategy_critiques`) and before the blank line preceding the `-- protection:` comment block. Column text is copied verbatim from `specs/strategy-contracts.md` lines 91–115; the only edits are `IF NOT EXISTS` and the added house comment.

```sql

-- Append-only. EVERY backtest by ANY seat. The DSR's N comes from here.
-- Verbatim from specs/strategy-contracts.md §2 — canonical, do not add fields
-- here. This DDL used to live in fundbt/registry.py as a standalone string
-- with every REFERENCES clause stripped; issue #172 (#50's Group 2) moved it
-- here so there is one schema home. The foreign keys are now real, because
-- state/db.py:22 sets PRAGMA foreign_keys = ON: a trial cannot be logged for a
-- spec_id with no strategy_specs row, which is why tests/synthetic.py seeds one.
--
-- IF NOT EXISTS on the TABLES is load-bearing, not style: state/db.py:12
-- matches that exact string to build _TABLES. IF NOT EXISTS on the INDEXES is
-- load-bearing for a DIFFERENT reason and must not be dropped as redundant:
-- connect() re-runs this WHOLE file whenever any single table is missing (the
-- `_TABLES <= have` guard), and a bare CREATE INDEX raises "index
-- idx_trials_family already exists" on that second pass — breaking connect()
-- for every existing database, at a call site nowhere near this line.
CREATE TABLE IF NOT EXISTS trial_registry (
  run_key            TEXT PRIMARY KEY,
  spec_id            TEXT NOT NULL REFERENCES strategy_specs(spec_id),
  family             TEXT NOT NULL,            -- denormalized for fast family-N counts
  config_hash        TEXT NOT NULL,
  data_snapshot_hash TEXT NOT NULL,
  engine_version     TEXT NOT NULL,
  seed               INTEGER NOT NULL,
  seat               TEXT NOT NULL,
  stats              TEXT NOT NULL,            -- JSON: full run_backtest output (§3.2)
  is_holdout         INTEGER NOT NULL DEFAULT 0,
  created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_family ON trial_registry(family);
CREATE INDEX IF NOT EXISTS idx_trials_spec   ON trial_registry(spec_id);

-- One row per strategy, ever. Enforces invariant 6 (holdout touched once).
-- Verbatim from specs/strategy-contracts.md §2 — canonical, do not add fields
-- here. run_key REFERENCES trial_registry(run_key) is the schema stating that
-- a holdout evaluation must have a trial row. evaluate_holdout writes that
-- row itself, before consuming the holdout (#189, landed in this same lane) —
-- so the FK resolves on every real call. The reference still guards a wiring
-- regression (that insert removed, reordered, or a caller that skips it), not
-- a live defect. See fundbt/registry.py:consume_holdout for why a foreign-key
-- violation must never be reported as an already-consumed holdout.
CREATE TABLE IF NOT EXISTS holdout_evaluations (
  spec_id     TEXT PRIMARY KEY REFERENCES strategy_specs(spec_id),
  run_key     TEXT NOT NULL REFERENCES trial_registry(run_key),
  passed      INTEGER NOT NULL,
  detail      TEXT NOT NULL,                   -- JSON: per-check results
  created_at  TEXT NOT NULL
);
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_schema_contract.py -q`

Expected: PASS, and the parametrized count rises from 12 to 14 (`test_schema_matches_spec` now includes `[trial_registry]` and `[holdout_evaluations]`).

If `test_schema_matches_spec[trial_registry]` fails with a `type` or `default` diff, the DDL is not character-exact — the comparator normalizes case, whitespace, comments and `IF NOT EXISTS`, but nothing else. Re-copy from `specs/strategy-contracts.md` lines 92–115 rather than hand-correcting.

- [ ] **Step 5: Write the failing schema-idempotency test**

Append to `tests/test_schema_contract.py`, after `test_spec_ddl_executes`:

```python
def test_schema_sql_survives_a_second_executescript():
    """Every statement in state/schema.sql must be idempotent — not just the
    CREATE TABLEs.

    state/db.py's connect() re-runs the WHOLE file whenever ANY ONE expected
    table is missing (the `_TABLES <= have` guard), which is the mechanism by
    which a table added here reaches an existing database with no migration
    (pinned from the other side by tests/test_state.py:199). That mechanism
    runs every OTHER statement in the file a second time as well.

    The route this closes was opened by issue #172, which added this file's
    first CREATE INDEX statements: `CREATE INDEX idx_trials_family` without
    IF NOT EXISTS raises "index idx_trials_family already exists" on the second
    pass. The symptom would be connect() failing for every live database the
    moment some LATER lane adds an unrelated table — a failure with no visible
    connection to the index that caused it.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA.read_text())
        conn.executescript(SCHEMA.read_text())
    finally:
        conn.close()
```

- [ ] **Step 6: Run it and verify it passes; then verify it is a real guard**

Run: `python3 -m pytest tests/test_schema_contract.py::test_schema_sql_survives_a_second_executescript -q`
Expected: PASS.

Then confirm it can fail: temporarily delete `IF NOT EXISTS` from `CREATE INDEX idx_trials_family` in `state/schema.sql`, re-run, expect FAIL with `index idx_trials_family already exists`, then restore it. Do not commit the temporary edit.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -q`

Expected: `tests/test_preflight_schema.py` now has 4 failures (the 12→14 count and the registry-scope tests). That is Task 2. Nothing else should have moved. Specifically confirm still green: `tests/test_state.py`, `tests/test_state_specs.py`, `tests/test_critic_g1_job.py` (which asserts `"strategies" not in tables` — a live guard on the scope fence), `tests/test_tool_surface_canon.py`.

- [ ] **Step 8: Do NOT commit. Continue straight into Task 2.**

`make test` must pass before every commit (`CLAUDE.md`), and at this point `tests/test_preflight_schema.py` has four known failures — real ones, caused by this task, fixed by the next. Committing here would commit a red suite. Task 1 and Task 2 land as **one commit**, made at the end of Task 2 (its Step 8).

---

## Task 2: Move preflight into the 14-table world, under the CEO ruling's citation

**Files:**
- Modify: `tests/test_preflight_schema.py:142-148`, `:286-296`, `:299-309`, `:388-392`, `:480-487`
- Modify: `scripts/preflight_schema.py:55-59`, `:160-163`

**Interfaces:**
- Consumes: Task 1's `state/schema.sql` (14 tables).
- Produces: nothing consumed by later tasks.

`scripts/preflight_schema.py` needs **no logic change** — `expected_schema()` reads `state/schema.sql` and its "none of the N tables" message is an f-string over `len(expected)`, so it self-updates. Only comments and test assertions are stale.

- [ ] **Step 1: Run the failing tests to see the exact set**

Run: `python3 -m pytest tests/test_preflight_schema.py -q`

Expected: FAIL — `test_the_expected_table_count_is_pinned` (`assert 14 == 12`), `test_an_uninitialized_or_wrong_db_cannot_determine` (`"none of the 12 tables"` not in stderr), `test_a_database_that_is_not_the_fund_db_cannot_determine` (returns `UNEXPLAINED_DIVERGENCE`, not `CANNOT_DETERMINE`), `test_the_registry_tables_are_out_of_scope`.

- [ ] **Step 2: Bump the pinned table count**

Replace `tests/test_preflight_schema.py:142-148`:

```python
def test_the_expected_table_count_is_pinned(tmp_path):
    """14 tables today. expected_schema() reads state/schema.sql, so a table
    added there is checked with no edit to the script — this assertion is the
    tripwire that makes such an addition deliberate. It IS a second edit, on
    purpose: bump it in the same commit that adds the table.

    12 -> 14 on 2026-08-29 by CEO ruling, issue #172 — Group 2 unification
    landed. trial_registry and holdout_evaluations moved out of
    fundbt/registry.py's standalone DDL into state/schema.sql.
    """
    assert len(preflight.expected_schema()) == 14
```

- [ ] **Step 3: Update the two remaining count strings**

`tests/test_preflight_schema.py:286-296` — docstring "Zero of the 12 tables" → "Zero of the 14 tables"; assertion:

```python
    assert "none of the 14 tables" in proc.stderr
```

`tests/test_preflight_schema.py:388-392` — docstring "reads all 12 tables" → "reads all 14 tables". No assertion in that test depends on the count.

- [ ] **Step 4: Re-aim the not-the-fund-DB test**

Its premise — a DB holding only `trial_registry` proves it is not the fund DB — evaporated. Replace `tests/test_preflight_schema.py:299-309`:

```python
def test_a_database_that_is_not_the_fund_db_cannot_determine(tmp_path):
    """A SQLite file holding NONE of state/schema.sql's tables — someone
    else's database sitting on the FUND_DB path.

    Premise inverted by CEO ruling 2026-08-29, issue #172 — Group 2
    unification landed. This test built its stand-in out of `trial_registry`,
    on the (then correct) reading that fundbt's trial registry lived in a
    separate database. `trial_registry` is now one of $FUND_DB's own 14
    tables, so it can no longer stand for "not the fund DB" — it would be read
    as drift and reported as UNEXPLAINED DIVERGENCE. What this test asserts is
    unchanged; only the file it points at moved.
    """
    db = tmp_path / "someone_elses.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    proc = _run(db)
    assert proc.returncode == preflight.CANNOT_DETERMINE
    assert "alembic_version" in proc.stderr        # names what it did find
```

- [ ] **Step 5: Invert the out-of-scope test — do not delete it**

Replace `tests/test_preflight_schema.py:482-487`:

```python
def test_the_registry_tables_are_in_scope(tmp_path):
    """trial_registry and holdout_evaluations ARE $FUND_DB tables, so preflight
    must expect them.

    Premise inverted by CEO ruling 2026-08-29, issue #172 — Group 2
    unification landed. Until that ruling this test read:

        fundbt/registry.py owns trial_registry/holdout_evaluations in a
        SEPARATE database. Expecting them in $FUND_DB would fail every run.

    That was true and this test was right to pin it. What #172 changed is the
    PREMISE, not the assertion's job: preflight's scope is still exactly
    state/schema.sql, and this still pins where that scope's edge falls — now
    from the other side. Recorded this way, and not by deleting the test, so a
    future reader sees a human decision rather than a session that edited a
    guard until it passed (CLAUDE.md: "Do not weaken or delete a red
    acceptance test to make it pass").
    """
    expected = preflight.expected_schema()
    assert "trial_registry" in expected
    assert "holdout_evaluations" in expected
```

- [ ] **Step 6: Correct the two false comments in the script**

`scripts/preflight_schema.py:55-59` — the module docstring's last paragraph currently reads:

> `EXPECTED SCHEMA IS state/schema.sql ALONE. migrations.py is the catch-up path TO that file, not a second source of truth; a union of a target and the mechanism for reaching it is just the target. Scope is `$FUND_DB` only — fundbt/registry.py's trial_registry/holdout_evaluations live in a separate database with its own DDL home, and expecting them here would fail every run.`

Replace the sentence beginning "Scope is `$FUND_DB` only" with:

```
EXPECTED SCHEMA IS state/schema.sql ALONE. migrations.py is the catch-up path
TO that file, not a second source of truth; a union of a target and the
mechanism for reaching it is just the target. Scope is `$FUND_DB` only, and
that file is now the whole strategy pipeline's home too: issue #172 moved
fundbt/registry.py's trial_registry/holdout_evaluations out of a separate
database into state/schema.sql, so preflight checks them like any other table
and needs no special case to do it.
```

`scripts/preflight_schema.py:160-163` — the inline comment above the `if not (set(expected) & live_tables)` guard names the registry DB as an example. Replace:

```python
    # NONE of the expected tables present is not drift, it is the wrong file
    # or an uninitialized one — a zero-byte $FUND_DB, a path typo, or some
    # other project's SQLite file. Reporting that as divergence would send an
    # operator hunting a schema change that never happened. (The example here
    # used to be "the separate fundbt registry DB"; issue #172 merged that
    # database into this one, so a DB holding trial_registry is now the fund
    # DB, not evidence against it.)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_preflight_schema.py -q`
Expected: PASS, all of them.

Then: `python3 -m pytest tests/ -q` — expected: fully green. Task 1 + Task 2 together are a complete, shippable increment.

- [ ] **Step 8: Commit Tasks 1 and 2 together**

Task 1 alone left `tests/test_preflight_schema.py` red; committing there would violate `CLAUDE.md`'s "`make test` … Must pass before every commit." One commit, covering both tasks' files, made only now that the full suite is green:

```bash
git add state/schema.sql tests/test_schema_contract.py \
        tests/test_preflight_schema.py scripts/preflight_schema.py
git commit -m "feat: trial_registry and holdout_evaluations get one schema home (#172)

state/schema.sql gains both tables verbatim from strategy-contracts.md §2,
foreign keys intact, and tests/test_schema_contract.py drops them from
NO_SCHEMA_HOME — which is what turns comparison on. Closes #50's Group 2.

IF NOT EXISTS on the two indexes is load-bearing: connect() re-runs the whole
file when any table is missing. Pinned by
test_schema_sql_survives_a_second_executescript.

preflight_schema moves to the 14-table world in this same commit — landing
the schema change alone would commit tests/test_preflight_schema.py red.
Four tests carried the pre-unification premise and are inverted rather than
deleted, each citing the sanction: CEO ruling 2026-08-29, issue #172 — Group 2
unification landed. The script itself needed no logic change; its
expected-table count is read from state/schema.sql. Two comments claiming the
registry lives in a separate database were true and are now false."
```

---

## Task 3: Seed the `strategy_specs` row the foreign key requires

**Files:**
- Modify: `tests/synthetic.py` (append after `GOLDEN_PARAMS`)
- Test: `tests/test_run_backtest.py` (one new parameterless test)

**Interfaces:**
- Consumes: Task 1's `trial_registry.spec_id REFERENCES strategy_specs(spec_id)`.
- Produces: `tests.synthetic.seed_spec_row(conn: sqlite3.Connection, spec: dict | None = None) -> str` — inserts (idempotently) the `strategy_specs` row matching `make_spec()`'s hardcoded `spec_id`, returns that id.

Why a direct INSERT and not `state.specs.insert_strategy_spec`: that function content-addresses the id via `fundbt.hashing.spec_id(fields)` and therefore **cannot** produce the hand-written `spec_golden000000f1`, which `tests/test_golden.py:21-22`'s frozen hashes depend on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_backtest.py`:

```python
def test_the_golden_spec_has_a_strategy_specs_row():
    """trial_registry.spec_id REFERENCES strategy_specs(spec_id), and
    state/db.py turns foreign keys ON, so registry.log() for the golden spec is
    only possible if this row exists. make_spec()'s id is baked into
    tests/test_golden.py's frozen hashes and cannot be changed, so the row is
    seeded to match the id rather than the other way round (issue #172).
    """
    spec = make_spec()
    conn = connect(":memory:")
    seed_spec_row(conn, spec)
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs WHERE spec_id = ?",
        (spec["spec_id"],)).fetchone()[0] == 1
    seed_spec_row(conn, spec)                    # idempotent: no PK explosion
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs").fetchone()[0] == 1
```

Add to the imports at the top of `tests/test_run_backtest.py`:

```python
from state.db import connect
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_spec,
                             seed_spec_row)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_run_backtest.py::test_the_golden_spec_has_a_strategy_specs_row -q`
Expected: FAIL with `ImportError: cannot import name 'seed_spec_row' from 'tests.synthetic'`.

- [ ] **Step 3: Write `seed_spec_row`**

Add `import json` and `import sqlite3` to the top of `tests/synthetic.py` (it currently imports only `numpy` and `pandas`), then append:

```python
# --- fund-DB fixtures ------------------------------------------------------
# state/schema.sql declares trial_registry.spec_id REFERENCES
# strategy_specs(spec_id) and state/db.py:22 sets PRAGMA foreign_keys = ON, so
# a trial cannot be logged for a spec that has no row (issue #172). Measured:
# INSERT OR IGNORE does NOT swallow a foreign-key violation — SQLite's ON
# CONFLICT algorithms do not apply to foreign keys — so registry.log() raises
# rather than silently dropping the trial.
#
# make_spec()'s spec_id is hardcoded and is baked into tests/test_golden.py's
# frozen config_hash/run_key, so it cannot move. The row is built to match it.
# Written as a direct INSERT rather than through
# state.specs.insert_strategy_spec because that function content-addresses the
# id (fundbt.hashing.spec_id) and cannot produce this hand-written one.
#
# Every column below except spec_id is filler that satisfies NOT NULL and the
# CHECK constraints. Nothing in fundbt reads this row — run_backtest reads the
# spec DICT from make_spec(); the row exists so the foreign key resolves.
_SPEC_ROW_COLUMNS = (
    "spec_id", "family", "seat", "hypothesis", "mechanism_class", "universe",
    "liquidity_bucket", "signal_rule", "param_ranges", "search_budget",
    "holding_period_d", "rebalance", "expected_turnover", "exit_rule",
    "invalidation", "capacity_usd", "predicted", "llm_in_loop", "created_at")

SPEC_ROW_CREATED_AT = "2026-07-09T00:00:00Z"


def seed_spec_row(conn: sqlite3.Connection, spec: dict | None = None) -> str:
    """INSERT the strategy_specs row that `spec`'s trials will reference.

    Idempotent (INSERT OR IGNORE on the primary key). Returns the spec_id.
    """
    spec = spec if spec is not None else make_spec()
    values = (
        spec["spec_id"],
        spec["family"],
        "quant",
        "buyers of 5d dips above trend are compensated for absorbing"
        " short-term selling pressure",
        "behavioral",
        json.dumps({"index": "SYN20", "pit_constituents": True, "filters": []},
                   sort_keys=True),
        spec["liquidity_bucket"],
        json.dumps(spec["signal_rule"], sort_keys=True),
        json.dumps(spec["param_ranges"], sort_keys=True),
        max(int(spec["search_budget"]), 1),      # CHECK(search_budget > 0)
        5,
        "daily",
        2.0,
        "exit on trend break or after holding_period_d",
        "no positive next-day drift after a 5% 5-day dip",
        1e8,
        json.dumps({"net_sharpe": 1.0, "max_dd": 0.25, "hit_rate": 0.55},
                   sort_keys=True),
        0,
        SPEC_ROW_CREATED_AT,
    )
    conn.execute(
        f"INSERT OR IGNORE INTO strategy_specs"
        f" ({', '.join(_SPEC_ROW_COLUMNS)})"
        f" VALUES ({', '.join(['?'] * len(_SPEC_ROW_COLUMNS))})",
        values)
    conn.commit()
    return spec["spec_id"]
```

Note the `max(..., 1)`: `tests/test_run_backtest.py::test_budget_exhaustion_is_logged` mutates `spec["search_budget"] = 2`, and `strategy_specs` has `CHECK(search_budget > 0)`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_run_backtest.py::test_the_golden_spec_has_a_strategy_specs_row -q`
Expected: PASS.

- [ ] **Step 5: Verify the parameterless runner still works**

Run: `python3 tests/run_tests.py`
Expected: every line `PASS`, exit 0. The new test takes no arguments, so the second runner is unaffected.

- [ ] **Step 6: Commit**

```bash
git add tests/synthetic.py tests/test_run_backtest.py
git commit -m "test: seed the strategy_specs row the trial FK requires (#172)

trial_registry.spec_id REFERENCES strategy_specs(spec_id) with FKs on, and
make_spec()'s id is frozen into test_golden's hashes, so the row is built to
match the id. Direct INSERT because state.specs.insert_strategy_spec
content-addresses the id and cannot produce a hand-written one."
```

---

## Task 4: `TrialRegistry` takes an injected connection; narrow the holdout exception

**Files:**
- Modify: `fundbt/registry.py` (whole file)
- Modify: `fundbt/run_backtest.py` (imports; `evaluate_holdout` lines 263-269)
- Modify: `tests/synthetic.py` (append `make_registry`)
- Modify: `tests/test_run_backtest.py` (imports, `setup()`, four new tests)
- Modify: `tests/test_golden.py` (imports, two construction sites)

**Interfaces:**
- Consumes: `tests.synthetic.seed_spec_row` (Task 3); Task 1's tables.
- Produces:
  - `fundbt.registry.TrialRegistry(conn: sqlite3.Connection)` — no path parameter, no default, no module-level `DDL`. Methods unchanged in signature: `get(run_key) -> dict | None`, `log(*, run_key, spec_id, family, config_hash, data_snapshot_hash, engine_version, seed, seat, stats, is_holdout, created_at) -> None`, `family_n(family) -> int`, `spec_trial_count(spec_id) -> int`, `family_sharpe_variance(family) -> float`, `consume_holdout(*, spec_id, run_key, passed, detail, created_at) -> bool`. `consume_holdout` now **raises** `sqlite3.IntegrityError` on a foreign-key violation.
  - `tests.synthetic.make_registry(spec: dict | None = None) -> TrialRegistry` — fresh fund-schema `:memory:` DB, spec row seeded.
  - `fundbt.run_backtest.BacktestError("holdout_wiring_error")` — new error code, distinct from `holdout_already_consumed`.

> ⚠️ **This task ends with exactly two known-red tests** — `tests/test_golden.py::test_golden_pass_path` and `tests/test_run_backtest.py::test_holdout_single_touch`. **This is expected and transient: Task 5 turns them green with no test edit.** E1 is ruled (variant D), so this is a mid-lane state, not a gate. **Do not commit at the end of this task** — Tasks 4 and 5 land as one commit.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_backtest.py`:

```python
def test_the_registry_declares_no_ddl_of_its_own():
    """#172: one schema home. fundbt/registry.py used to carry a standalone
    DDL string with every REFERENCES clause stripped; that string existing at
    all is the defect, because it is a second source of truth for a schema
    specs/strategy-contracts.md §2 already declares."""
    import fundbt.registry as registry_module
    assert not hasattr(registry_module, "DDL"), (
        "fundbt/registry.py still declares DDL — state/schema.sql is the home")


def test_the_registry_writes_the_fund_dbs_tables_with_the_fk_live():
    """The registry writes state/schema.sql's tables, foreign keys and all.
    Reading PRAGMA foreign_key_list rather than trusting the DDL text: what
    matters is what the live database enforces."""
    reg = make_registry()
    fk = reg.conn.execute(
        "PRAGMA foreign_key_list(trial_registry)").fetchall()
    assert [(r["table"], r["from"], r["to"]) for r in fk] == [
        ("strategy_specs", "spec_id", "spec_id")]
    assert reg.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_logging_a_trial_for_an_unregistered_spec_is_refused():
    """The foreign key IS the rule that an unregistered spec cannot have
    trials. Loud, not swallowed: measured, SQLite's ON CONFLICT algorithms do
    not apply to foreign keys, so INSERT OR IGNORE still raises here."""
    reg = make_registry()
    try:
        reg.log(run_key="rk_orphan", spec_id="spec_neverregistered",
                family="F1", config_hash="c", data_snapshot_hash="d",
                engine_version="e", seed=0, seat="quant", stats={},
                is_holdout=False, created_at=NOW)
        raise AssertionError("should have raised")
    except sqlite3.IntegrityError as exc:
        assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert reg.family_n("F1") == 0


def test_holdout_single_touch_at_the_registry():
    """#172 done-means 4, pinned at the level that does not depend on #189.

    With the trial row present so the FK resolves, the FIRST consume_holdout
    writes and the SECOND hits holdout_evaluations' PRIMARY KEY and returns
    False. That False is the p-hacking alarm
    (specs/strategy-contracts.md:273) and it still means exactly what it
    always meant.
    """
    spec = make_spec()
    reg = make_registry(spec)
    reg.log(run_key="rk_h", spec_id=spec["spec_id"], family=spec["family"],
            config_hash="c", data_snapshot_hash="d",
            engine_version="e+holdout", seed=0, seat="quant", stats={},
            is_holdout=True, created_at=NOW)
    args = dict(spec_id=spec["spec_id"], run_key="rk_h", passed=True,
                detail={"holdout_sharpe": 1.0}, created_at=NOW)
    assert reg.consume_holdout(**args) is True
    assert reg.consume_holdout(**args) is False


def test_a_holdout_with_no_trial_row_is_a_wiring_error_not_a_p_hacking_alarm():
    """Issue #172, the narrowing.

    A foreign-key violation and a primary-key hit are the SAME exception class
    (sqlite3.IntegrityError). consume_holdout caught both and returned False,
    so a first-ever holdout against the fund DB — which fails the FK, because
    evaluate_holdout never logs its trial row (#189) — surfaced as
    holdout_already_consumed and paged #risk with "someone/something is
    p-hacking". A false positive on that alarm is its own incident class. The
    FK case escapes; only the PRIMARY KEY case still means consumed.
    """
    spec = make_spec()
    reg = make_registry(spec)
    try:
        reg.consume_holdout(spec_id=spec["spec_id"],
                            run_key="rk_never_logged", passed=True,
                            detail={}, created_at=NOW)
        raise AssertionError("should have raised")
    except sqlite3.IntegrityError as exc:
        assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY"
```

Final import block for `tests/test_run_backtest.py`:

```python
import sqlite3

import numpy as np

import fundbt.rules  # noqa: F401  (registers dip_buyer)
from fundbt.run_backtest import (BacktestError, evaluate_holdout, run_backtest,
                                 snapshot_hash)
from state.db import connect
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_registry,
                             make_spec, seed_spec_row)
```

(`from fundbt.registry import TrialRegistry` is removed — the tests no longer construct one directly.)

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python3 -m pytest tests/test_run_backtest.py -q -k "registry or holdout"`
Expected: FAIL with `ImportError: cannot import name 'make_registry' from 'tests.synthetic'`.

- [ ] **Step 3: Rewrite `fundbt/registry.py`**

```python
"""Append-only trial registry (strategy-contracts.md §2). SQLite, no LLM imports.

Every backtest by any seat lands here — including rejected/abandoned ones.
Family-wide N feeds the deflated Sharpe. An unlogged trial cannot exist because
run_backtest logs inside the same code path that returns the result.

THE SCHEMA LIVES IN state/schema.sql, NOT HERE (issue #172, closing #50's
Group 2). This module owns queries and owns no DDL, no database and no path.
It is handed a connection someone else opened — build it with
state.db.connect(), which is what sets PRAGMA foreign_keys = ON and therefore
what makes trial_registry.spec_id -> strategy_specs(spec_id) and
holdout_evaluations.run_key -> trial_registry(run_key) real rather than
decorative. The standalone DDL this file used to carry stripped every
REFERENCES clause, which is how the missing holdout trial row (#189) stayed
invisible for as long as it did.

Nothing here imports state/. That is deliberate: state/specs.py already imports
fundbt.hashing, so an import back would close a package cycle that is currently
survivable only because fundbt/__init__.py happens to be a bare docstring.
fundbt stays a leaf.
"""

from __future__ import annotations

import json
import sqlite3

# Named, not positional. The old INSERT was `VALUES (?,?,?,?,?,?,?,?,?,?,?)`
# against a DDL string in this same file, so the two could not drift. The DDL
# now lives in state/schema.sql, which other lanes edit: a column inserted
# there would silently shift every value one place to the left.
_TRIAL_COLUMNS = (
    "run_key", "spec_id", "family", "config_hash", "data_snapshot_hash",
    "engine_version", "seed", "seat", "stats", "is_holdout", "created_at")

_HOLDOUT_COLUMNS = ("spec_id", "run_key", "passed", "detail", "created_at")


class TrialRegistry:
    """Query surface over an already-open fund-DB connection.

    Takes the connection rather than a path so the database's identity, its
    lifetime and its PRAGMAs stay with whoever opened it — the posture every
    other writer in this repo already has (state/specs.py, every fund MCP tool
    handler, agents/seats.py's conn_factory). state/db.py's connect() is called
    once per tool call specifically so nothing holds a write lock across turns;
    a registry that opened and kept its own connection could not honour that.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- trials ------------------------------------------------------------

    def get(self, run_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT stats FROM trial_registry WHERE run_key = ?", (run_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def log(self, *, run_key: str, spec_id: str, family: str, config_hash: str,
            data_snapshot_hash: str, engine_version: str, seed: int, seat: str,
            stats: dict, is_holdout: bool, created_at: str) -> None:
        """Append one trial. Re-logging the same run_key is a no-op.

        OR IGNORE covers the PRIMARY KEY only. It does NOT cover the foreign
        key on spec_id: SQLite's ON CONFLICT algorithms do not apply to foreign
        keys (measured), so logging a trial for a spec with no strategy_specs
        row raises SQLITE_CONSTRAINT_FOREIGNKEY. That is the schema saying an
        unregistered spec cannot have trials, and it is meant to be loud.
        """
        self.conn.execute(
            f"INSERT OR IGNORE INTO trial_registry"
            f" ({', '.join(_TRIAL_COLUMNS)})"
            f" VALUES ({', '.join(['?'] * len(_TRIAL_COLUMNS))})",
            (run_key, spec_id, family, config_hash, data_snapshot_hash,
             engine_version, seed, seat, json.dumps(stats), int(is_holdout),
             created_at),
        )
        self.conn.commit()

    def family_n(self, family: str) -> int:
        """N for the DSR: every trial ever run in this family, all seats, all specs.

        `family` is denormalized onto every row, so this one COUNT sweeps the
        whole lineage — including REJECTED ancestors — with no traversal
        (strategy-contracts.md:260, which specifies it unfiltered).
        """
        return self.conn.execute(
            "SELECT COUNT(*) FROM trial_registry WHERE family = ?", (family,)
        ).fetchone()[0]

    def spec_trial_count(self, spec_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM trial_registry WHERE spec_id = ?", (spec_id,)
        ).fetchone()[0]

    def family_sharpe_variance(self, family: str) -> float:
        """Variance of per-period Sharpes across the family's logged trials —
        the V[{SR_n}] input to SR0. Requires >= 2 trials; else 0 (SR0 degenerates)."""
        rows = self.conn.execute(
            "SELECT stats FROM trial_registry WHERE family = ?", (family,)
        ).fetchall()
        srs = []
        for row in rows:
            s = json.loads(row[0]).get("per_period_sharpe")
            if s is not None and isinstance(s, (int, float)):
                srs.append(float(s))
        if len(srs) < 2:
            return 0.0
        import numpy as np
        return float(np.var(np.asarray(srs), ddof=1))

    # -- holdout single-touch (invariant 6) ---------------------------------

    def consume_holdout(self, *, spec_id: str, run_key: str, passed: bool,
                        detail: dict, created_at: str) -> bool:
        """Returns False if the holdout was already consumed (PRIMARY KEY hit).
        A False here is a p-hacking alarm: project to #risk.

        A FOREIGN KEY violation is not that, and must never be reported as it.
        Both arrive as sqlite3.IntegrityError, so the blanket catch this
        replaced could not tell them apart. Under state/db.py's
        PRAGMA foreign_keys = ON, holdout_evaluations.run_key REFERENCES
        trial_registry(run_key); evaluate_holdout now logs its own trial row
        for that run_key before calling this method (#189, landed in this same
        lane), so in the one production call path that FK always resolves.
        This branch is therefore not reachable through evaluate_holdout today
        — it guards a future wiring regression (that insert removed,
        reordered, or a caller other than evaluate_holdout that skips it), not
        a live defect. It IS exercised directly, by a test that calls this
        method without logging a trial row first
        (test_a_holdout_with_no_trial_row_is_a_wiring_error_not_a_p_hacking_alarm),
        which is how the guard stays pinned. If it ever does fire in
        production it means SQLITE_CONSTRAINT_FOREIGNKEY, returned False, and
        surfaced to the operator as holdout_already_consumed, which
        specs/strategy-contracts.md:273 routes to #risk as
        "someone/something is p-hacking". A false positive on that alarm is its
        own incident class. The FK case is a wiring error and is re-raised
        untouched; the PRIMARY KEY path keeps its exact previous meaning.

        Discrimination is on sqlite_errorname (Python 3.11+; pyproject pins
        >=3.12), never on the message text.
        """
        try:
            self.conn.execute(
                f"INSERT INTO holdout_evaluations"
                f" ({', '.join(_HOLDOUT_COLUMNS)})"
                f" VALUES ({', '.join(['?'] * len(_HOLDOUT_COLUMNS))})",
                (spec_id, run_key, int(passed), json.dumps(detail), created_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
                raise
            return False
```

- [ ] **Step 4: Add `make_registry` to `tests/synthetic.py`**

Append after `seed_spec_row`:

```python
def make_registry(spec: dict | None = None) -> "TrialRegistry":
    """A TrialRegistry over a FRESH fund-schema database, spec row seeded.

    In-memory and per call: that rules out CROSS-test pollution by
    construction — no test's family N can carry into another test's registry.
    It does NOT by itself pin fixtures/golden-strategy.md:46's frozen
    `deflated_sharpe (N=1) = 1.000000`: run_backtest computes
    family_n(family) + 1 with no scoping, so a holdout-then-family-trial
    sequence would move N even in a registry this fresh. That number stays
    N=1 because no golden test runs a further family trial after a G3 holdout
    on its own registry — per-test isolation, not construction, is what keeps
    it true (see Task 5 Step 5 and fixtures/golden-strategy.md's own note). A
    registry SHARED across tests would additionally leak N across tests,
    which this function does rule out — but that is a narrower guarantee than
    the docstring here used to claim.

    state.db.connect() applies state/schema.sql and sets
    PRAGMA foreign_keys = ON, so these tests exercise the real fund schema with
    the real foreign keys — which is the whole point of #172. `:memory:` rather
    than a tmp_path file because the guarantee wanted here is a fresh database,
    not a filesystem.

    Deliberately NOT a pytest fixture: tests/run_tests.py is a second,
    zero-dependency runner that calls every test_* with NO arguments, and it is
    not in `make test`. A fixture parameter would break it silently.
    """
    from fundbt.registry import TrialRegistry
    from state.db import connect

    conn = connect(":memory:")
    seed_spec_row(conn, spec)
    return TrialRegistry(conn)
```

(Imports are function-local so `tests/synthetic.py` stays importable by anything that only wants the market generator.)

- [ ] **Step 5: Rewire `tests/test_run_backtest.py`'s `setup()`**

```python
def setup():
    spec = make_spec()
    return make_market(), spec, make_registry(spec)
```

- [ ] **Step 6: Rewire `tests/test_golden.py`**

Replace the import line `from fundbt.registry import TrialRegistry` with nothing, and update the import from synthetic:

```python
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_registry,
                             make_spec)
```

Then both construction sites (lines 16 and 48):

```python
    spec = make_spec()
    close, reg = make_market(), make_registry(spec)
```

- [ ] **Step 7: Translate the escaped exception in `fundbt/run_backtest.py`**

Add `import sqlite3` to the stdlib imports at the top of the file. Then replace lines 263-269 (`fresh = registry.consume_holdout(...)` through `raise BacktestError("holdout_already_consumed")`):

```python
    try:
        fresh = registry.consume_holdout(
            spec_id=spec["spec_id"], run_key=rkey,
            passed=bool(np.isfinite(detail["holdout_sharpe"]) and detail["holdout_sharpe"] > 0),
            detail=detail, created_at=now_iso,
        )
    except sqlite3.IntegrityError as exc:
        # NOT a consumed holdout. holdout_evaluations.run_key REFERENCES
        # trial_registry(run_key); evaluate_holdout logs that trial row itself,
        # immediately above this call (#189, landed in this same lane), so on
        # the fund DB this FK always resolves through evaluate_holdout. This
        # branch guards a future wiring regression (that insert removed,
        # reordered, or a caller that bypasses it), not a live defect — see
        # the docstring below for the test that exercises it directly. If it
        # ever does fire, reporting it as holdout_already_consumed would page
        # #risk with "someone/something is p-hacking"
        # (specs/strategy-contracts.md:273) on what is actually a wiring
        # error. A wiring error gets its own code so it reads as one.
        raise BacktestError("holdout_wiring_error") from exc
    if not fresh:
        raise BacktestError("holdout_already_consumed")  # p-hacking alarm -> #risk
```

Also update `evaluate_holdout`'s docstring line 244 to name both outcomes:

```python
    """G3's one-shot holdout run. Signals get warmup context from before the
    cutoff, but ONLY post-cutoff returns are scored. Single-touch is enforced by
    registry.consume_holdout — a second call returns holdout_already_consumed.
    A foreign-key failure is a different thing entirely and raises
    holdout_wiring_error, never the p-hacking alarm — but not reachable
    through THIS function today, since the trial-row insert above always logs
    before consume_holdout runs. It is a guard against a future wiring
    regression, exercised directly by a test that calls consume_holdout
    without that insert (CEO ruling 2026-08-29, issue #172)."""
```

- [ ] **Step 8: Run the tests and read the failures**

Run: `python3 -m pytest tests/test_run_backtest.py tests/test_golden.py -q`

Expected: all new tests PASS. Exactly two FAIL:
- `tests/test_run_backtest.py::test_holdout_single_touch` — `BacktestError: holdout_wiring_error` on the *first* call.
- `tests/test_golden.py::test_golden_pass_path` — same, at line 37.

Run: `python3 -m pytest tests/ -q` — confirm no other test moved.

Run: `python3 scripts/check_purity.py` — expected: clean, exit 0. `fundbt/registry.py` imports only `json` and `sqlite3`.

- [ ] **Step 9: Do NOT commit. Continue straight into Task 5.**

Tasks 1–3 are committed. Task 4 is implemented and green except the two named tests, which Task 5 fixes at the cause. There is nothing to wait on — E1 is ruled. If either of those two tests is green here, or a *third* test is red, stop and report: the measured baseline does not match the tree.

---

## Task 5: `evaluate_holdout` logs its own trial row (#189's fix, sanctioned), then commit 4+5

**Ruled: variant D, CEO 2026-08-29.** Not gated. The two tests Task 4 left red go green here **with no test edit** — that is measured, not predicted.

**Files:**
- Modify: `fundbt/run_backtest.py::evaluate_holdout` (one insert + one comment block)
- Modify: `tests/test_run_backtest.py::test_holdout_single_touch` — **NO. Do not touch it.**
- Modify: `tests/test_golden.py::test_golden_pass_path` — **NO. Do not touch it.**

**Interfaces:**
- Consumes: Task 4's `TrialRegistry(conn)` and the `holdout_wiring_error` translation; Task 1's FK.
- Produces: a `trial_registry` row with `is_holdout=1` for every holdout evaluation, which is the FK target `holdout_evaluations.run_key` has always required. `evaluate_holdout` end-to-end goes green, so #172's fourth "done means" bullet and `specs/acceptance.md:70` hold end-to-end rather than only at the registry level.

**Why this is in scope.** Ruling 3 said do not fix the cause here; the CEO lifted it for this insert, on 2026-08-29 — the reasoning is paraphrased in the banner at the top of this plan (durable record: #189's ruling comment and #172's authorization comment). A/B/C were considered and rejected (see E1). Short form: A ships a migration with a known landmine (the first real holdout pages `#risk` as p-hacking) until a second lane lands; B rewrites two green tests to assert a wiring error as though it were intended behaviour; C cannot be validated, because the FK #189 satisfies does not exist until #172 lands. **The PR carries `closes #189`, and #189's content lands unchanged.**

- [ ] **Step 1: Insert the holdout's trial row in `evaluate_holdout`**

Immediately before the `try:` added in Task 4 Step 7 (and therefore before `consume_holdout` runs, so the FK target exists when the holdout row is written):

```python
    # The holdout run IS a trial: trial_registry.is_holdout exists for exactly
    # this row, holdout_evaluations.run_key REFERENCES trial_registry(run_key)
    # structurally requires it, and strategy-contracts.md:260 counts N
    # unfiltered. Erring toward a higher N is the conservative direction for a
    # gate. (#189, folded into #172 by CEO ruling 2026-08-29.)
    #
    # seat="orchestrator": trial_registry.seat is TEXT NOT NULL and this
    # function takes no seat parameter, so a value has to be chosen. This one is
    # INFERRED from specs/acceptance.md:69 ("evaluate_holdout and G2/G3/G4
    # evaluators are orchestrator-invoked only") — the honest caller. It is NOT
    # a schema statement: neither specs/strategy-contracts.md §2 nor
    # specs/strategy.md names a value for an evaluator-written row. That
    # canonical gap is real and is on the CEO's own edit list; this lane does
    # not touch specs/.
    #
    # NOTE for anyone enriching `detail` below: this row's `stats` is `detail`,
    # which carries no "per_period_sharpe" key, so a holdout trial never enters
    # family_sharpe_variance's V[{SR_n}] (measured: 0.0 on both sides of this
    # change). Add per_period_sharpe to `detail` and holdout runs silently start
    # contributing to family variance, moving EVERY deflated-Sharpe number
    # fund-wide. Recorded on #189.
    registry.log(run_key=rkey, spec_id=spec["spec_id"], family=spec["family"],
                 config_hash=cfg, data_snapshot_hash=snapshot_hash(close),
                 engine_version=ENGINE_VERSION + "+holdout", seed=0,
                 seat="orchestrator", stats=detail, is_holdout=True,
                 created_at=now_iso)
```

- [ ] **Step 2: Record R1 — `seat="orchestrator"` is a design choice, not a lookup**

Nothing further to edit; the reasoning is in the comment above and must survive review intact. **The PR body must cite `specs/acceptance.md:69` for this choice.** State plainly there that it is an inference from an acceptance criterion, not a schema statement, and that the canonical gap (`strategy-contracts.md` §2 / `strategy.md` name no `seat` value for an evaluator-written row) is on the CEO's edit list and **out of region for this lane**.

- [ ] **Step 3: Leave both tests exactly as they are. No test edit at all.**

`tests/test_run_backtest.py::test_holdout_single_touch` and `tests/test_golden.py::test_golden_pass_path` are **not modified, not trimmed, not re-recorded, not skipped**. If either needs an edit to pass, the tree does not match the measured baseline — stop and report rather than adjusting anything.

- [ ] **Step 4: Verify no frozen number moved**

Run: `python3 -m pytest tests/test_golden.py tests/test_run_backtest.py -q`

**Measured result, 2026-08-29 (recorded on #172, comment 2) — this is what you should reproduce:**

| number | baseline | with the insert |
|---|---|---|
| `deflated_sharpe`, full precision | `0.9999999351282387` | **identical** |
| rounded → `fixtures/golden-strategy.md:46` `N=1 = 1.000000` | `1.0` | **`1.0`** |
| `n_trials_family` | 1 | **1** |
| `net_sharpe` | `1.827113` | **identical** |
| `holdout_days` / `holdout_trades` | 391 / 160 | **identical** |
| `holdout_sharpe` | `3.092703` | **identical** |
| `config_hash` / `run_key` / `data_snapshot_hash` | frozen | **identical** |
| `family_n("F1")` in `test_deterministic_and_cached` | 1 | **1** |
| `family_n("F1")` in `test_budget_exhaustion_is_logged` | 3 | **3** |

`10 passed` → `10 passed`. Full suite `1622 passed`. **Zero test edits.**

Why it holds: the holdout row is written *after* `run_backtest` has already computed and asserted its DSR, and no existing test runs a further family trial after a holdout — so N rises to 2 only after the last assertion that reads it. Also confirmed: the second `evaluate_holdout` call's `log` is a silent no-op (`INSERT OR IGNORE`, same `+holdout` run_key) **for identical params only** — same spec, same config, same everything the run_key is derived from. With different params the run_key differs, so the second call's `log` inserts a genuinely new trial row (family N +1) before `consume_holdout` raises `holdout_already_consumed`. The conclusion is unaffected here (`consume_holdout` still returns `False` off the PRIMARY KEY exactly as before) and the `+holdout` run_key stays distinct from the base run_key either way, so `run_backtest`'s cache is untouched — but a *refused* p-hacking attempt with different params now permanently inflates family N against the thin DSR margin from Step 5, which is a deliberate consequence of "a spent trial is a spent trial" (`fixtures/golden-strategy.md`), not a bug.

**If any frozen number moves, STOP** — do not adjust it (CLAUDE.md). A moved number means the tree diverged from the measured baseline, not that the fixture is stale.

- [ ] **Step 5: Note the margin that this does NOT protect — then do Task 6**

The frozen numbers above hold **by per-test family isolation, not by construction.** Measured: a family trial run *after* a G3 holdout drops `deflated_sharpe` from `0.9966250069502793` to `0.9554598584813883`, against `stratgate/gate.py`'s `min_deflated_sharpe = 0.95` — a margin of **0.0055**. The entire move is N alone (2→3); `family_sharpe_variance` stayed `0.0` on both sides, for the reason in Step 1's comment.

No test asserts this today, and none is added here — it is not a defect, it is the deflation working as designed. It is a trap for the *next* fixture author, which is what Task 6 exists to close.

### After Task 5

- [ ] **Step F1: Full verification**

```bash
python3 -m pytest tests/ -q
python3 tests/run_tests.py
python3 scripts/check_purity.py
make test
```

Expected: all green, `tests/run_tests.py` exits 0 with every line `PASS`, purity lint exits 0.

- [ ] **Step F2: Commit Tasks 4 and 5 together**

```bash
git add fundbt/registry.py fundbt/run_backtest.py tests/synthetic.py \
        tests/test_run_backtest.py tests/test_golden.py
git commit -m "feat: the trial registry writes the fund DB, on an injected connection (#172)

TrialRegistry takes an open sqlite3.Connection and owns no DDL, no path and no
database. Its schema is state/schema.sql's, foreign keys live, because
state/db.py's connect() is what opens it. fundbt imports nothing from state/,
so fundbt stays a leaf and the existing state/specs.py -> fundbt.hashing edge
stays the only one.

consume_holdout no longer reports a foreign-key violation as a consumed
holdout. Both are sqlite3.IntegrityError; the FK case is a wiring error and
escapes as BacktestError('holdout_wiring_error'), so a clean first touch can
never page #risk as p-hacking. The PRIMARY KEY path is unchanged.

evaluate_holdout now logs its own is_holdout=1 trial row before consuming the
holdout — #189's fix, whose content #189 already ruled correct, folded into
this lane by CEO ruling 2026-08-29 because the alternatives meant landing a
known-broken seam or rewriting two green tests. Measured: every frozen number
identical (deflated_sharpe 0.9999999351282387, n_trials_family 1,
holdout_sharpe 3.092703, all hashes), full suite 1622 passed, ZERO test edits.
seat='orchestrator' per specs/acceptance.md:69 (evaluate_holdout is
orchestrator-invoked only) — an inference from an acceptance criterion, not a
schema statement; strategy-contracts.md §2 names no value for an
evaluator-written row.

Tests build a fresh fund-schema :memory: DB per call, so family N starts at
zero by construction and fixtures/golden-strategy.md's N=1 stays true without
any fixture being touched. No pytest fixture parameter was added, so
tests/run_tests.py's parameterless runner still works.

Closes #50's Group 2.

Closes #189"
```

**PR body must contain, at minimum:** `closes #189` (verify GitHub actually parsed it into `closingIssuesReferences` before merging — a body line is not a parsed reference); the `specs/acceptance.md:69` citation for `seat="orchestrator"`, marked as an inference; and the measured no-movement table from Step 4.

---

## Task 6: put the DSR margin warning where the next fixture author will hit it (R2)

**Files:**
- Modify: `fixtures/golden-strategy.md` — **prose only**

**Interfaces:**
- Consumes: Task 5's measurement.
- Produces: nothing code reads. This is a tripwire for a human.

> ⚠️ **REGION LIFT, AND ITS EXACT LIMIT.** `fixtures/` is OUT of region for this lane. **Authorization of record:** [issue #172, comment](https://github.com/benjaminematton/fund/issues/172#issuecomment-5463477474) — the CEO has authorized **this one documentation addition** and nothing else in that directory; that comment states the lift's limits, and an implementer verifies against it, not against this plan. **Add prose only. Do NOT touch a single frozen number, hash or expected value in that file.** `CLAUDE.md`'s rule stands in full — "NEVER update a golden fixture, expected hash, or expected value to make a test pass. STOP and ask. No 'deliberate re-record.'" The lift is licence to *add a sentence*, and is emphatically **not** licence to adjust a value. If a number in this file looks wrong to you, that is an escalation, not an edit.

**Why it goes in the file and not just a comment thread.** The finding: the frozen golden numbers hold **by per-test family isolation, not by construction**. Mechanism: family N is an unfiltered `COUNT(*)` (`specs/strategy-contracts.md:260`), and the G3 holdout now writes its own trial row (#189, landed with #172), so every family trial run after a holdout deflates against a higher N — against `stratgate/gate.py:24`'s `min_deflated_sharpe = 0.95` bar. DSR also depends on that later trial's own `per_period_sharpe`, not on N alone, so no single fixed number can stand in for the check here (an approximate margin for one such trial was measured and recorded on #172's measurement comment, not reproduced in this file — its own trial params aren't named there either, so it isn't re-derivable and doesn't belong in a file that promises every number is exact). A future golden vector that backtests after a holdout is therefore one step closer to flipping a G2 verdict on N alone. **The next fixture author must collide with this, not discover it** — an issue comment is not somewhere they will look.

- [ ] **Step 1: Add the note directly under the G2 metrics table**

Insert after `fixtures/golden-strategy.md:50` (the `param cliff` row, the last row of the G2 table) and before line 52's `**G2: PASS.**`:

```markdown

> **These numbers assume per-test family isolation** — every test builds a
> fresh registry, so N starts at zero and `deflated_sharpe (N=1)` above is a
> genuine N=1. That is a property of the harness, not of the pipeline.
> **Any new vector that backtests AFTER a holdout must derive its N
> accordingly**, because the G3 holdout now writes its own `is_holdout=1`
> trial row (#189, landed with #172) and so increments family N by one before
> the next family trial. N is an unfiltered `COUNT(*)`
> (`strategy-contracts.md:260`), and the bar it must clear is
> `stratgate/gate.py`'s `min_deflated_sharpe = 0.95`. DSR also depends on that
> later trial's own `per_period_sharpe`, not on N alone — no fixed number here
> can stand in for the check, so derive your own vector's N and re-run the
> gate rather than assuming this fixture's numbers still apply. A
> holdout-inclusive vector is one step closer to flipping a G2 verdict on N
> alone.
```

- [ ] **Step 2: Verify nothing but prose moved**

Run: `git diff fixtures/golden-strategy.md`

Expected: **additions only.** Zero `-` lines. If the diff shows a single removed or altered line, revert the file and start over — no number in it may change.

Then: `python3 -m pytest tests/test_golden.py -q` — expected: unchanged, green. (It should be, since nothing executable moved; running it is how you prove that.)

- [ ] **Step 3: Commit**

```bash
git add fixtures/golden-strategy.md
git commit -m "docs: golden vectors hold by test isolation, not construction (#172)

The frozen G2 numbers assume every test builds a fresh registry, so N starts
at zero. Now that the G3 holdout writes its own is_holdout=1 trial row (#189),
family N (an unfiltered COUNT(*), strategy-contracts.md:260) is one higher for
any family trial run after a holdout, against stratgate/gate.py:24's
min_deflated_sharpe = 0.95 bar. DSR also depends on that trial's own
per_period_sharpe, not on N alone, so no fixed number is recorded here — the
next fixture author derives their own N and re-checks the bar instead of
assuming this vector's numbers still apply.

Prose only under an explicit CEO region lift for this one addition
(authorization of record: issue #172 comment). No number, hash or expected
value in this file was touched — nothing was re-recorded."
```

---

## Acceptance mapping

### `specs/acceptance.md:70`

> "Trial registry unified: `fundbt/registry.py` writes to the fund DB; schema matches `specs/strategy-contracts.md` §2 (single source of truth)."

| Clause | Delivered by | Evidence |
|---|---|---|
| "writes to the fund DB" | Task 4 (D1) | `tests/test_run_backtest.py::test_the_registry_writes_the_fund_dbs_tables_with_the_fk_live` — reads `PRAGMA foreign_key_list` off the live DB, not the DDL text |
| "schema matches §2" | Task 1 | `tests/test_schema_contract.py::test_schema_matches_spec[trial_registry]` and `[holdout_evaluations]`, which compare `type`/`default`/`checks`/`references` as normalized text |
| "single source of truth" | Task 1 + Task 4 | `test_the_registry_declares_no_ddl_of_its_own`; `NO_SCHEMA_HOME` shrunk to 3, so a future drift in either direction fails `test_every_spec_table_is_declared_in_schema` / `test_every_schema_table_is_declared_in_a_spec` |
| Holdout quarantine end-to-end (`:73`) | Task 5 | **Holds end-to-end under the variant-D ruling.** `evaluate_holdout` logs its own trial row, so `test_golden_pass_path` exercises the full G3 leg through the fund schema with FKs live — unedited, all frozen numbers intact. This no longer "depends on E1's variant"; E1 is ruled |
| Checkbox tick | **NOT in this lane** | `specs/` is out of region — see Escalation E2 |

### Issue #172's four "done means" bullets

| # | Bullet | Delivered by | Evidence |
|---|---|---|---|
| 1 | Starter-kit suite still green inside the fund repo (`make test`); extend, don't rewrite | Tasks 3–5 | `make test` green; `python3 tests/run_tests.py` exits 0 (the second runner is untouched by design, D5). No `fundbt`/`stratgate` test was deleted; `test_run_backtest.py` grows 6 tests (Task 3's spec-row-seeding test plus Task 4's five) and rewires `setup()` |
| 2 | Registry writes land in the fund DB; family-N counts (DSR) read from it across lineage | Task 4 | No code change was needed for "across lineage": `family` is denormalized onto every row, so `family_n` is one `COUNT(*) WHERE family = ?` that sweeps the whole lineage including REJECTED ancestors (`strategy-contracts.md:260`, unfiltered). Pinned by the existing `test_deterministic_and_cached` (`family_n("F1") == 1`) and `test_budget_exhaustion_is_logged` (`== 3`), which now run against the fund schema. `idx_trials_family` makes it indexed |
| 3 | Purity lint still clean: `fundbt/` free of LLM imports and wall-clock calls | Task 4 (D1) | `python3 scripts/check_purity.py` exits 0. Strengthened, not just preserved: `fundbt/registry.py` now imports only `json` and `sqlite3` |
| 4 | Holdout single-touch preserved: second G3 attempt hits the `holdout_evaluations` PRIMARY KEY | Task 4 + Task 5 | `test_holdout_single_touch_at_the_registry` — supplies the trial row and shows first touch `True`, second touch `False` via the PRIMARY KEY. **End-to-end through `evaluate_holdout` too**, under the variant-D ruling: `test_holdout_single_touch` and `test_golden_pass_path` pass **unedited**, because `evaluate_holdout` now writes the trial row the FK requires. The second call's `log` is a measured no-op (`INSERT OR IGNORE`, same `+holdout` run_key), so `consume_holdout` still returns `False` and `holdout_already_consumed` still raises |

---

## What happens to each affected test file

| File | Change | Any assertion weakened? |
|---|---|---|
| `tests/test_schema_contract.py` | `NO_SCHEMA_HOME` 5→3 entries — this *strengthens* the file, turning on character-exact comparison for two tables that were compared by nothing. One test added (`test_schema_sql_survives_a_second_executescript`). Parametrized cases 12→14. | No. Strictly more is checked. |
| `tests/test_preflight_schema.py` | Four tests move under the CEO citation: count 12→14 (`:148`); `"none of the 12 tables"` string (`:296`); `test_a_database_that_is_not_the_fund_db_cannot_determine` re-aimed at `alembic_version` since `trial_registry` can no longer stand for "not the fund DB" (`:299-309`); `test_the_registry_tables_are_out_of_scope` → `_in_scope` (`:482`). One docstring at `:391`. | No. Each keeps its exit-code assertion; only the premise moved, and each says so with the sanction. Nothing deleted. |
| `tests/synthetic.py` | Gains `seed_spec_row()` and `make_registry()`. `make_spec()` and `make_market()` are **untouched** — `make_spec`'s `spec_id` and every market parameter feed frozen hashes. | N/A — no assertions here. |
| `tests/test_run_backtest.py` | `setup()` returns a fund-schema registry. Imports change. Six tests added (spec-row seeding, no-DDL, live FK, orphan-trial refusal, registry-level single touch, FK-vs-p-hacking). **`test_holdout_single_touch` is NOT edited** — Task 5 fixes the cause instead. | **No.** Under the variant-D ruling not one existing assertion moves. |
| `tests/test_golden.py` | Two construction sites use `make_registry(spec)`. **Every frozen number is untouched, and the G3 block stays exactly as it is** — Task 5 makes it pass rather than trimming it. | **No.** Measured: `10 passed` → `10 passed`, zero test edits. |
| `fixtures/golden-strategy.md` | Task 6: one prose note under the G2 table, under an explicit CEO region lift. Additions only — the diff must have zero `-` lines. | N/A — and **no number, hash or expected value may be touched.** |
| `tests/run_tests.py` | **Nothing.** The design deliberately avoids fixture parameters so the parameterless runner keeps working. Verified as an explicit step in Tasks 3 and 5. | N/A |
| `tests/conftest.py` | **Nothing.** `fund_db` is not used by any `fundbt` test — see E8 for the alternative. | N/A |

---

## Escalations

### E1 — RESOLVED, CEO ruling 2026-08-29: variant D. Not blocking.

**The problem, verified empirically** and left here because it is why Task 5 exists: under the canonical §2 DDL with FKs on, `holdout_evaluations.run_key REFERENCES trial_registry(run_key)` has no target, because `evaluate_holdout` never logs its trial row (#189). Two currently-green tests could not pass: `tests/test_golden.py::test_golden_pass_path` and `tests/test_run_backtest.py::test_holdout_single_touch`. Ruling 3 said do not fix the cause here; ruling 4 sanctioned inverting exactly one preflight test and nothing sanctioned these two; CLAUDE.md forbids weakening a red acceptance test to make it pass. So it went up.

**The ruling, paraphrased** (the durable record is #189's ruling comment and #172's authorization comment — verify there, not here):

> The `strategies` fence kept out a *new design* with unresolved interactions; #189's insert is a fix whose content was already ruled correct, and the alternatives are both worse than the hygiene cost: A ships a migration carrying a known landmine (first real holdout → false p-hacking page) until a second lane lands, and B rewrites green tests to assert a wiring error as if it were intended behavior. When the clean-lanes option means deliberately landing a broken seam, hygiene is the wrong thing to optimize. D with explicit sanction is clean *because* it's sanctioned.

**Sanction:** ruling 3 is lifted **for this specific insert only**, by the person who set it. `evaluate_holdout` logs its own `is_holdout=1` trial row before calling `consume_holdout`, in this lane (Task 5). The PR carries `closes #189`; #189's content lands unchanged. Three riders attach: **R1** `seat="orchestrator"` recorded as an inference from `specs/acceptance.md:69`, cited in the PR body; **R2** the DSR margin warning goes into `fixtures/golden-strategy.md` (Task 6, a scoped region lift); **R3** the variance subtlety carried as a code comment at the insert site.

**The deciding measurement is now a fact.** It was marked "reasoned, must be confirmed by the run"; it has been run and it holds — every frozen number identical, `10 passed` → `10 passed`, full suite `1622 passed`, zero test edits. The table is in Task 5, Step 4.

**A, B and C were considered and rejected** — kept as one line each so the reasoning survives without three dead branches in an executable plan:

- **B (rewrite both tests to assert the wiring error, respecting ruling 3 literally)** — rejected: it rewrites green tests to assert a wiring error as though it were intended behaviour, holds `acceptance.md:70`/#172's fourth bullet only at the registry level, makes `fixtures/golden-strategy.md`'s invariant 2 false (E3), and parks two frozen numbers in a comment block.
- **A (land Tasks 1–3 only, defer the injection to #189)** — rejected: it ships a migration carrying a known landmine, where the first real holdout pages `#risk` as p-hacking, and leaves it armed until a second lane lands.
- **C (sequence #189 first, then #172)** — rejected: #189 cannot be validated before #172 lands, because the FK it satisfies does not exist yet.

### E2 — `specs/acceptance.md:70`'s checkbox is out of region

`specs/` is OUT. The Phase-5 line item stays unticked by this lane even when the work is done. Whoever owns `specs/` should tick it — and under the variant-D ruling the holdout-quarantine line on `:73` **is** fully satisfied, so both lines are tickable. Still not this lane's edit.

A second `specs/` item for the same owner, surfaced by R1: neither `specs/strategy-contracts.md` §2 nor `specs/strategy.md` names a `seat` value for an evaluator-written `trial_registry` row, even though the column is `TEXT NOT NULL`. This lane uses `"orchestrator"` on the authority of `acceptance.md:69` and says so in the code and the PR body. Closing the canonical gap is on the CEO's own edit list; **this lane does not touch `specs/`.**

### E3 — CLOSED. `fixtures/golden-strategy.md`'s invariant 2 stays true under the ruling.

Invariant 2 reads: "the second `evaluate_holdout` call raises `holdout_already_consumed` (PRIMARY KEY, not policy) and alerts `#risk`." That would have become false under variant B, where the *first* call raises `holdout_wiring_error` instead. **Variant D was ruled, so it stays true** — measured: for a second call with **identical params**, `log` is a silent no-op (`INSERT OR IGNORE`, same `+holdout` run_key), so `consume_holdout` still returns `False` and `holdout_already_consumed` still raises, exactly as invariant 2 describes. With **different params** the run_key differs, so `log` inserts a new trial row (family N +1) before `consume_holdout` raises the same `holdout_already_consumed` off the PRIMARY KEY — invariant 2 still holds, but a refused p-hacking attempt now permanently costs a family-N slot against the thin DSR margin (Task 5 Step 5). No escalation remains.

(Task 6 does add prose to this file, under a separate and explicitly scoped CEO region lift. That is R2, not this escalation, and it changes no number and no invariant.)

### E4 — `specs/strategy-contracts.md:200`'s transaction clause

#189 already owns this: if `evaluate_holdout`'s trial insert and `consume_holdout` cannot share one transaction — and they cannot today, since `consume_holdout` commits on its own — the "inside the same transaction as the verdict" clause needs updating. Out of region (`specs/`), and unchanged by the ruling.

Updated only in that the condition is no longer hypothetical: under the variant-D ruling that trial insert lands **in this lane** (Task 5), and `log()` commits before `consume_holdout` runs. So the two-commits shape `:200` describes is now actually in the tree rather than pending #189. This does not change what E4 asks for or who owns it; it changes the tense. See also E5, which is the same seam from the registry's side and is **still open**.

### E5 — STILL OPEN — commit ownership on an injected connection (two readings, not resolved silently)

`log()` and `consume_holdout()` call `self.conn.commit()`. On a connection the registry no longer owns, that commits the caller's in-flight work too. I kept the commits (D3) on the strength of `state/specs.py:insert_strategy_spec`'s identical precedent, and because removing them would silently move #189's premise. The other reading — the caller owns the transaction, the registry only issues statements — is cleaner and would diverge from every other writer in `state/`. If you want it, it should be one decision applied to all of them, not to this file alone.

### E6 — `tests/test_schema_contract.py`'s "11 tables" docstrings are already stale

`test_parsers_found_the_ddl`'s docstring and the module docstring both say 11; the measured `bound` count is 12 today and becomes 14 after Task 1. Pre-existing staleness, not created by this lane, and correcting it means editing prose that describes past measurements. Left alone. Flagging so nobody reads it as this lane's drift.

### E7 — OPERATIONAL: `make preflight` goes RED on the droplet after this merges

Confirmed by reading `scripts/preflight_schema.py:174-196`. A missing *table* is never "explained" by a migration (every migration is an additive `ALTER`), so preflight returns `UNEXPLAINED_DIVERGENCE` (exit 2) against a live DB that predates this schema edit — and `make preflight` gates the deploy. The tables self-heal at the next `connect()` via the `_TABLES <= have` guard, so the fix is to run one `connect()` against `$FUND_DB` before preflight gates. This is the same shape `protection` had when it was added, so there may already be a step in `ops/README.md`'s deploy procedure. `ops/` is OUT of region; routing to whoever owns the deploy loop.

### E8 — STILL OPEN — two readings: `make_registry()` vs `conftest.py`'s `fund_db` (not resolved silently)

Ruling 2 says "Tests must keep per-test isolation (`tests/conftest.py`'s `fund_db` fixture is `connect(tmp_path/"fund.sqlite")`)". I read the parenthetical as describing what isolation looks like here, not mandating the fixture — the region's "`tests/conftest.py` if a fixture is needed" reads the same way. So I chose `make_registry()` (`connect(":memory:")` per call), which gives identical isolation, identical schema, identical FK enforcement, and leaves `tests/run_tests.py` working.

The other reading is that `fund_db` should be used literally. That forces every `fundbt` test signature to grow a `fund_db` parameter, which breaks `tests/run_tests.py` — it calls every `test_*` with no arguments and is not in `make test`, so it would fail silently as far as CI is concerned. If you want that reading, the plan changes as follows: `make_registry` becomes `registry_for(conn, spec)`, a `registry` fixture is added to `tests/conftest.py` wrapping `fund_db`, all 11 tests in `test_run_backtest.py` and both in `test_golden.py` grow the parameter, and `tests/run_tests.py` gains signature inspection to supply one. That is roughly triple the diff and it is the only version that touches `run_tests.py`. Say the word and I'll rewrite Tasks 3–5.

### E9 — STILL OPEN — two readings: whether `TrialRegistry` keeps its class shape

I kept the class (D2). It becomes a one-field wrapper over a connection, which is thin enough that collapsing it into module-level functions taking `conn` — the `state/specs.py`, `state/critiques.py` style — is genuinely defensible and arguably more consistent with where this code now lives. I kept it because `run_backtest`'s two public signatures take `registry: TrialRegistry` and that parameter is the seam a caller uses to substitute a stub; collapsing it churns both signatures for no gain in this lane. Either is fine; I did not want to make that call invisibly.

### E10 — the concurrency posture is improved but not yet exercised

`state/db.py`'s `connect()` is called per tool call to avoid holding write locks; the old `TrialRegistry` held one long-lived connection. Injecting the connection means the registry now inherits its caller's posture, which is the right shape — but there is **no production caller** (no `run_backtest` MCP tool exists), so nothing binds it. When that tool is built, it should construct `TrialRegistry(conn_factory())` per call like every other handler. Not built here: YAGNI, and building it would put this lane in the tool-surface lane's region.

### E-alt — rejected alternative worth recording

`fundbt/registry.py` could raise its own `RegistryWiringError` instead of letting `sqlite3.IntegrityError` escape, which would keep `run_backtest.py` from needing `import sqlite3`. Rejected: `BacktestError` lives in `run_backtest.py`, which imports `registry.py`, so the registry cannot reach it without a cycle — and a second error vocabulary for one call site is worse than one `import sqlite3`.

---

### Critical Files for Implementation

- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/fundbt/registry.py`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/state/schema.sql`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/tests/synthetic.py`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/tests/test_schema_contract.py`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/tests/test_preflight_schema.py`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/fundbt/run_backtest.py`
- `/Users/benjaminmatton/Developer/fund-wt/registry-unify/fixtures/golden-strategy.md` (Task 6, **prose only**)

Two execution options — **E1 is ruled, so nothing waits on an answer**: **subagent-driven** (fresh subagent per task, review between — recommended, since Tasks 1 and 2 are independently reviewable even though they land in one commit at Task 2's end (B5), Task 3 is its own commit, and Task 4 needs a hard stop before its commit into Task 5), or **inline** via `superpowers:executing-plans`.

---

## Revision log

### 2026-08-29 — E1 ruled: variant D. Plan revised in place; no source, test, schema or fixture touched by this revision.

CEO ruling folded in. Reasoning quoted verbatim in the top banner and in E1. Changes, in file order:

0. **Architecture paragraph** — one sentence added: `evaluate_holdout` logs its own `is_holdout=1` trial row under the ruling, so the lane lands #189's fix and carries `closes #189`.
1. **Global Constraints — "never re-record a fixture"** — added that Task 6 adds prose to `fixtures/golden-strategy.md` under an explicit lift, touching no number, hash or expected value, and that the lift is not licence to touch one.
2. **Global Constraints — region fence** — `fundbt/run_backtest.py` is now IN for `evaluate_holdout`'s trial-row insert, not only the `consume_holdout` error handling. Added a constraint naming the **two scoped CEO lifts** (the #189 insert; one prose addition to `fixtures/golden-strategy.md`) and restating that **`specs/` stays OUT entirely**, including the canonical gap R1 names.
3. **Top banner** — ⛔ "Task 5 is blocked on a decision" replaced with ✅ "E1 is RULED — variant D. Nothing is gated." Carries the ruling verbatim, the measured result (every frozen number identical; `10 passed` → `10 passed`; suite `1622 passed`; zero test edits), the three riders R1/R2/R3, and a note that E5/E8/E9 stay open.
4. **File Structure table** — `fundbt/run_backtest.py` row now names both changes; new `fixtures/golden-strategy.md` row (prose only).
5. **Task 4 warning + Step 9** — the two red tests are relabelled expected-and-transient rather than a gate; Step 9 says continue straight into Task 5, and adds a tripwire (a green one of those two, or a *third* red, means the tree does not match the measured baseline).
6. **Task 5 — rewritten.** No longer gated, no longer a menu. Variant D only, with: the insert; **R1** (`seat="orchestrator"` as an explicit design choice, inferred from `specs/acceptance.md:69`, marked NOT a schema statement, cited in the PR body); **R3** (the `family_sharpe_variance` subtlety as a code comment at the insert site, warning that enriching `detail` with `per_period_sharpe` would move every deflated-Sharpe number fund-wide); a hard "do not touch either test" step; the measured no-movement table replacing the old "reasoned, must be confirmed by the run" hedge; and a step recording the 0.0055 DSR margin that hands off to Task 6. Variant A/B/C bodies **deleted** — one-line rejection notes kept in E1.
7. **Task 5 commit message + PR guidance** — records the #189 fold, the measurement and the `acceptance.md:69` citation; adds **`closes #189`** as a trailer, with a reminder to verify GitHub actually parsed it into `closingIssuesReferences` before merge. Notes #189's content lands unchanged.
8. **Task 6 — new (rider R2).** Puts the DSR margin warning into `fixtures/golden-strategy.md` under the G2 table: frozen numbers hold by per-test family isolation, not by construction, and any holdout-inclusive vector must derive its N accordingly. Carries the region lift **and its exact limit** — prose only, `git diff` must show zero `-` lines, CLAUDE.md's no-re-record rule stands in full and the lift is explicitly not licence to adjust a value.
9. **Acceptance mapping** — `acceptance.md:70` gains a row for the `:73` holdout-quarantine clause holding **end-to-end**; #172 bullet 4 no longer reads "depends on E1's variant" and now cites both tests passing unedited plus the measured `INSERT OR IGNORE` no-op on the second call.
10. **"What happens to each affected test file"** — `test_run_backtest.py` and `test_golden.py` rows corrected to "not edited / no assertion weakened"; `fixtures/golden-strategy.md` row added.
11. **E1 — resolved**, not deleted: problem statement kept, ruling quoted, sanction and riders recorded, measurement restated as fact, A/B/C kept as one line each so the reasoning survives without three dead branches.
12. **E2** — notes both `specs/` lines are now tickable under D, and adds the R1 canonical gap (no `seat` value for an evaluator-written row) as a second `specs/`-owner item, explicitly out of region here.
13. **E3 — CLOSED.** `fixtures/golden-strategy.md:91`'s invariant 2 stays true under D; the measured `INSERT OR IGNORE` no-op is why. Notes Task 6's prose is a separate, scoped lift.
14. **E4** — tense only: the two-commits shape `strategy-contracts.md:200` describes is now actually in the tree rather than pending #189. Owner and ask unchanged; still out of region.
15. **E5, E8, E9** — untouched in substance, retitled **STILL OPEN** so no reader mistakes the E1 resolution for a sweep. The ruling does not reach them.
16. **Critical Files** — added `fundbt/run_backtest.py` and `fixtures/golden-strategy.md`; footer no longer says execution waits on E1.

Not changed: E6, E7, E-alt, Design decisions D1–D5, Tasks 1–4's steps, and every code block those tasks specify.

### 2026-08-29 — adversarial re-review fixes B1–B7. Technical core (the `evaluate_holdout` insert, FK satisfaction, NOT NULL coverage, serializability, ordering) unchanged.

- **B1** — Global Constraints' region-fence entry and Task 6's region-lift warning now cite the durable authorization: [issue #172, comment](https://github.com/benjaminematton/fund/issues/172#issuecomment-5463477474), which also states the lift's limits — an implementer verifies against that comment, not against this plan.
- **B2** — Dropped the unreproducible `deflated_sharpe = 0.955460` figure from Task 6's inserted fixture prose (and its rationale paragraph and commit message); replaced with the mechanism (family N is an unfiltered `COUNT(*)`, `strategy-contracts.md:260`) and the threshold (`stratgate/gate.py:24`'s `min_deflated_sharpe = 0.95`), with a pointer to #172's measurement comment for anyone who wants the approximate number — never presented as a fixture value.
- **B3** — Rewrote three comments/docstrings that stated the pre-Task-5 world in the present tense (Task 1 Step 3's `holdout_evaluations` schema comment; the `consume_holdout` docstring in Task 4 Step 3; the except-block comment and `evaluate_holdout` docstring in Task 4 Step 7) to describe the post-lane world and explain that the FK-violation branch now guards a future wiring regression rather than a live defect.
- **B4** — Reworded the Global Constraints "by construction" claim and `make_registry`'s docstring: N=1 holds by per-test family isolation (no golden test runs a family trial after a holdout), not by construction; `make_registry`'s construction guarantee is only that it rules out cross-test pollution.
- **B5** — Folded Task 1's commit into Task 2's: Task 1 Step 8 now says "do not commit, continue into Task 2" (mirroring the existing Task 4→5 pattern); Task 2 Step 8 does one combined commit once the full suite is green. Chosen over deferring inside Task 1 because it matches the plan's own precedent and needs no task renumbering. Footer's execution-options line updated to match.
- **B6** — Added two accuracy notes: (1) the `holdout_wiring_error` FK branch is not reachable through `evaluate_holdout` today — `log()` commits before `consume_holdout` runs — and is exercised only via the test that calls `consume_holdout` directly; noted in the `consume_holdout` docstring, the except-block comment, and the `evaluate_holdout` docstring. (2) The second-call "silent no-op" claim (Task 5 Step 4, E3) now states it holds for identical params only; with different params the run_key differs, a new trial row lands (family N +1) before `holdout_already_consumed` raises, and a refused p-hacking holdout now permanently costs a family-N slot against the thin DSR margin — defensible under "a spent trial is a spent trial," now stated as a deliberate line rather than left implicit.
- **B7** — (a) Relabelled the "ruling, verbatim" quote in E1 as "paraphrased," with a pointer to #189's ruling comment and #172's authorization comment as the durable record. (b) Replaced the fictitious "#172 ruling 3" / "#172 ruling 5" docstring citations (in `test_the_golden_spec_has_a_strategy_specs_row`, `test_a_holdout_with_no_trial_row_is_a_wiring_error_not_a_p_hacking_alarm`, and `evaluate_holdout`'s docstring) with citations to the issue itself. (c) Dropped the stale `fixtures/golden-strategy.md:91` line citation (E3's header, E3's body, and E1's variant-B rejection line) in favor of "invariant 2," since Task 6 inserts prose above that line. (d) Fixed two test-count errors: "Five tests added" → "Six tests added" and "grows 5 tests" → "grows 6 tests" (Task 3 adds one, Task 4 adds five, to `tests/test_run_backtest.py`).