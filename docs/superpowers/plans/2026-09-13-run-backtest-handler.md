# `run_backtest` Handler (#171 half two) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `handle_run_backtest` — the MCP-side wrapper of `specs/strategy-contracts.md` §3.2's enforcement order — callable, directly tested, and served to **no seat**, in the exact G-2(iii) shape `submit_strategy_spec` shipped in (`docs/superpowers/plans/2026-08-29-spec-registration-seam.md` "How (iii) is expressed").

**Architecture:** One handler in `agents/tools/fund_server.py` that does the DB-side checks (§3.2 step 1, step 2 in full, the D2 rule pre-check, the budget event of step 3, and step 7's lifecycle move), and delegates every computation to the shipped engine `fundbt.run_backtest.run_backtest` (3's count-and-log, 4, 5, 6-as-recorded, 7's trial INSERT). The single `strategies` transition it needs lives in `state/specs.py` as a CAS function; `state/transition.py` is **not** edited (D4 revised — #170 is appending there today). Price data arrives through a `close_provider` callable that is a **required keyword parameter of the handler**; the future `@tool` closure binds it; no loader is built and `build_fund_server` is untouched.

**Rulings 2026-09-13 (overseer, on the eight open questions of the first draft) — folded in below:** (1) the handler owns step 2 fully, reusing the engine's reason spellings; (2) `budget_exhausted` → `#research`; (3) no `stale_transition` event kind now; (4) no `build_fund_server` kwarg; (5) one happy-path test registers through `handle_submit_strategy_spec`; (6) `fundbt` imports deferred into the handler body; (7) `StaleTransition` after a run → tool error, trial stands, state stays SPEC, re-run is cached and re-attempts the edge; (8) the `transition.py` follow-up is filed by the overseer when the PR opens, `advance_to_backtest` stays the named entry point.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"`), sqlite3 (`state.db.connect` → `sqlite3.Row`, `PRAGMA foreign_keys = ON`), pandas DataFrame from `tests/synthetic.py:make_market()`, pytest.

## Global Constraints

Copied from `CLAUDE.md` and the specs; every task's requirements include these.

- **Paper only.** No live-trading code paths, flags, or TODOs.
- **`gate/`, `stratgate/`, and `calibration/` import no LLM code.** `scripts/check_purity.py` lints `PURE_PACKAGES = ["gate", "stratgate", "fundbt", "calibration", "orchestrator", "state", "market", "slackkit", "devcheck"]` (`scripts/check_purity.py:58-59`) for `FORBIDDEN_IMPORTS = ("claude_agent_sdk", "anthropic", "slack_bolt", "slack_sdk", "agents", "slackkit.real")` (`:64-65`) and wall-clock refs (`:68-77`). `agents/` is **not** in `PURE_PACKAGES`, and `fundbt` is not in `FORBIDDEN_IMPORTS`, so `agents/tools/fund_server.py` importing `fundbt` is permitted; `state/specs.py` already imports `fundbt.hashing` (`state/specs.py:19`). `state/` IS linted, so the new function there takes `now_iso` and reads no clock.
- **Default is HOLD.** Any error, malformed input, or ambiguity → tool error, nothing written — except the engine's own budget-rejection trial row, which §3.2 step 3 says IS logged.
- **Time comes from an injected `Clock`.** The handler receives `now_iso`; never `datetime.now()`.
- **Agents emit structured data only through MCP tools.** Do not parse anything out of free text.
- **Do not invent fields.** `BacktestRequest`/`BacktestResult` are exactly §3.2.
- **Gate thresholds change only by human commit.** Do not touch the DSR cold-start prior (`fundbt/run_backtest.py:188-189`), cost floors, or holdout months.
- **NEVER update a golden fixture or expected value to make a test pass.** STOP and ask.
- **No `@tool`, no `SEAT_CAPS` entry, §4 row `not served`** (D3, confirmed by the overseer).
- **Do not edit `state/transition.py`** (D4 revised). Do not touch `fundbt/`, `StrategySpec`, or `charters/`.
- **Commits:** conventional (`feat:`/`test:`/`docs:`), no Co-Authored-By trailer, no AI attribution. `make test` green before each commit.
- **Worktree only:** `/Users/benjaminmatton/Developer/fund-wt/lane-171`, branch `lane-171-spec-registration`. Never `cd` elsewhere, never checkout/switch/reset.

---

## Decisions in force (overseer; do not reopen)

| | Decision | Evidence in this tree |
|---|---|---|
| D1 (as ruled 2026-09-13, Q4) | `close` arrives via `close_provider`, a **required keyword parameter of the handler** (callable returning the close DataFrame). `build_fund_server` gains **no** kwarg — an unread kwarg is speculative; the future `@tool` closure binds it. A provider that has no data raises `LookupError` → tool error, nothing written. Tests pass a lambda over `make_market()`. No loader. | `fundbt.run_backtest.run_backtest(*, spec, params, close: pd.DataFrame, registry, seat, now_iso, seed=0, holdout_months=18)` (`fundbt/run_backtest.py:121-131`). |
| D2 | `signal_rule` lacking `name`, or name ∉ `RULES` → tool error `unknown_rule`, nothing written. | Engine reads `spec["signal_rule"]["name"]` at `:137` — a missing key is a `KeyError`, not a `BacktestError`; `:138-139` raises `BacktestError("unknown_rule")` only for a present-but-unregistered name. `RULES` is populated only by importing `fundbt.rules` (`fundbt/rules.py:17`, `@register_rule("dip_buyer")`); `tests/test_run_backtest.py:8` does exactly that import. |
| D3 | No `@tool`, no cap, §4 row `not served`, seats cell empty, schema cell `strategy-contracts.md` §3.2. | Precedent rows `contracts.md:285` (`submit_critique`) and `:287-289` (empty seats cells). Why it stays green: see "Why a `not served` row with no decorator is green" below. |
| D4 (revised) | ONE transition, `strategies` SPEC→BACKTEST, as a function in `state/specs.py` with §4 CAS semantics. `transition.py` untouched. | `strategies` DDL `state/schema.sql:207-216`: `state_version INTEGER NOT NULL DEFAULT 0`, `updated_at TEXT NOT NULL`, no `status` column. `IllegalTransition`/`StaleTransition` exist at `state/transition.py:29-34` and are imported, not redefined. |
| D5 | Budget exhausted: engine logs the rejection and raises (`run_backtest.py:158-165`); handler additionally appends a `budget_exhausted` event via `append_event`. Fix `acceptance.md:91`. | `append_event(conn, kind, payload, now_iso) -> int` (`slackkit/outbox.py:29-31`), commits internally (`:20-26`). **Forced companion:** `tests/test_slackkit.py:829-832` `test_every_written_kind_has_a_renderer` AST-scans every `append_event` kind literal outside `tests/` and requires a `RENDERERS` entry (`slackkit/render.py:342-356`), so a renderer is mandatory, not optional. |
| D6 (amended by Q1 ruling) | Step 6 stays "computed and recorded, not verified" (`strategy-contracts.md:185`). Steps 4 and 5 are engine-side. **Step 2 is handler-side in full** (ruling Q1): declared, numeric-typed where the bounds are numeric, in range — before the engine is called, with the engine's own reason spellings (`undeclared_param:<p>`, `param_out_of_range:<p>`, plus `param_type:<p>`). `fundbt/` is not changed; the engine's own step 2 (`:140-146`) then re-runs on inputs that already passed. | `snapshot_hash` recorded at `run_backtest.py:150`; holdout `:167-173`; cost floors + 2×/3× `:175-181`; engine step 2 `:140-146` (spellings at `:143`, `:146`). |
| D7 | Add the `acceptance.md:89` test: no seat's `tools/list` contains `evaluate_holdout`, `run_backtest`, or any `stratgate` evaluator. | Lives in `tests/test_tool_surface_canon.py` (reason in T3). Evaluators: `stratgate/gate.py:65 evaluate_g2`, `:110 evaluate_g3`; `fundbt/run_backtest.py:233 evaluate_holdout`. |
| D8 | Registry = `TrialRegistry(conn)` on the fund DB connection; `seat=` calling seat; `now_iso=` injected. | `TrialRegistry.__init__(self, conn: sqlite3.Connection)` (`fundbt/registry.py:50`). `fund_server.py:17-32` imports nothing from `fundbt` today. |
| D9 | `BacktestRequest` in `state/models.py` beside `StrategySpec`/`SpecCritique` (`state/models.py:120-171`), `model_config = ConfigDict(extra="forbid")` as `:133`. Output = the engine's `BacktestResult` dict; `cached` as the engine gives it (`:156`, `:224`). | |
| D10 | Refusals return `{"ok": False, "error": ...}` (the wrapper shape at `fund_server.py:841-844` turns that into `is_error: True`). No bare `except Exception`. | `handle_submit_strategy_spec:325-331` catches only `(ValidationError, TypeError)`. |

### Which §3.2 step lives where

| §3.2 step | Where | Line refs |
|---|---|---|
| 1. spec exists and `strategies.state ∈ {SPEC, BACKTEST}` | **handler** (`strategy-contracts.md:186` says so explicitly) | new code, T2 |
| D2 rule pre-check (not a numbered step; guards the engine's `KeyError`) | **handler** | `run_backtest.py:137-139` |
| 2. params declared, typed, within `param_ranges` | **handler** (`_check_params`, T2), before the engine; the engine repeats its own check at `run_backtest.py:140-146` on inputs that already passed | new code, T2 |
| 3. budget count, log rejection, raise | **engine** | `run_backtest.py:158-165` |
| 3. `budget_exhausted` **event** | **handler** | new code, T2 |
| 4. holdout window excluded | **engine** | `run_backtest.py:167-173` |
| 5. cost floors by bucket; 2×/3× always | **engine** | `run_backtest.py:175-181`, `fundbt/costs.py:14,17` |
| 6. snapshot hash (recorded, not verified — D6) | **engine** | `run_backtest.py:150`, `:207` |
| 7. run + INSERT trial row (idempotent on `run_key`) | **engine** | `run_backtest.py:153-156` (cache), `:227-229` (log) |
| 7. `strategies.state → BACKTEST` | **handler**, via `state/specs.py:advance_to_backtest` (T1) | new code |

### Why a `not served` row with no decorator is green (D3, verified against `tests/test_tool_surface_canon.py`)

- `_canon()` `:83-132` parses the §4 table; `:119-123` accepts exactly `served` or a status starting with `not served`; `:116-117` turns an empty seats cell into `frozenset()` so `:124-128`'s unknown-seat check passes (precedent: the three `improvement.md` rows at `contracts.md:287-289`).
- `_canon_served()` `:135-136` includes only `served` rows, so the new row is absent from it.
- `_declared()` `:139-143` regexes `@tool("name"` out of the source; with no decorator it is unchanged. `test_every_registered_tool_has_a_canonical_entry` `:179-184` asserts `_declared() == _canon_served()` — both unchanged → green. (Adding a decorator would redden it **by design**.)
- `test_served_tool_set_is_exactly_the_section_4_table` `:170-176`: `_served()` unchanged, `_canon_served()` unchanged → green.
- `test_each_tool_reaches_exactly_the_seats_the_table_names` `:187-198`: for a not-served row `want = set()` (`:197`) and `served.get(tool, set()) == set()` → green.
- `test_a_row_pointing_its_schema_elsewhere_points_somewhere_real` `:201-213`: schema cell names `strategy-contracts.md`; the file exists and contains `run_backtest` (`strategy-contracts.md:152`) → green.
- `test_the_wrong_seat_payloads_cover_every_canonical_tool` `:224-228`: `ARGS` must equal `_canon_served()` — so **do not add an `ARGS` entry**.
- `test_a_wrong_seat_caller_gets_a_tool_error` `:238-257`: every seat calls the unregistered name with `{}`; the MCP layer answers "Tool not found", which `_is_error` reports true; row counts unchanged → green.
- `test_the_handler_refuses_even_when_registration_is_wrong` `:271-306` skips non-served rows (`:282-283`).
- `test_the_canon_parser_rejects_what_it_cannot_read` `:309-324` needs `| not served — Phase 3 |` and `| \`submit_decision\` | \`pm\` |` to survive verbatim — they do.
- `tests/test_fund_tools.py:362-375` pins each seat's registered names and `:672-678` pins every non-`read_` cap as a registered name: no registration, no cap → both untouched and green.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `state/specs.py` | `strategy_specs`/`strategies` read-write path | **Add** `advance_to_backtest(conn, spec_id, *, expected_state_version, now_iso) -> bool` (T1) |
| `state/schema.sql:198-202` | `strategies` comment | **Correct** the "Nothing TRANSITIONS a row" sentence (T1) |
| `tests/test_state_specs.py` | Tests for `state/specs.py` (where `insert_strategy_spec` is tested today, `:110-172`) | **Add** 6 tests (T1) |
| `state/models.py` | Pydantic tool models | **Add** `BacktestRequest` (T2) |
| `agents/tools/fund_server.py` | Handlers + server | **Add** `handle_run_backtest` and `_check_params`; `build_fund_server` **untouched**; **no** `@tool`, **no** cap (T2) |
| `slackkit/render.py` | Event renderers | **Add** `_render_budget_exhausted` + `RENDERERS` entry (T2) |
| `tests/test_slackkit.py` | Renderer tests | **Add** payload constant to `BLOCK_KINDS` + one channel test (T2) |
| `tests/test_run_backtest_handler.py` | Handler tests | **Create** (T2) |
| `specs/contracts.md` §4, §8 | Canonical tool table; machinery-kinds list | **Add** one `not served` row + one sentence; add `budget_exhausted` to the machinery list (T3) |
| `specs/acceptance.md:91` | Phase 5 spec-enforcement criterion | **Correct** "no trial row" for the budget case (T3) |
| `specs/strategy-contracts.md:25` | §2 unification note | **Correct** the "Nothing yet TRANSITIONS" sentence (T3) |
| `tests/test_tool_surface_canon.py` | Served surface IS §4 | **Add** the D7 evaluator-toolbelt test (T3) |

**Not touched:** `state/transition.py`, `fundbt/`, `stratgate/`, `charters/`, `agents/seats.py`, `scripts/`, `build_fund_server`, `SEAT_CAPS`, `cap_tools`, `tests/test_fund_tools.py`, `tests/synthetic.py`.

---

## Task 1: `strategies` SPEC→BACKTEST as one CAS function in `state/specs.py`

**Files:**
- Modify: `state/specs.py` (append after `insert_strategy_spec`, before `_refuse_orphaned_specs`)
- Modify: `state/schema.sql:198-202` (comment only)
- Test: `tests/test_state_specs.py`

**Interfaces:**
- Consumes: `state.transition.IllegalTransition`, `state.transition.StaleTransition` (`state/transition.py:29-34`, imported, never edited).
- Produces: `advance_to_backtest(conn: sqlite3.Connection, spec_id: str, *, expected_state_version: int, now_iso: str) -> bool` — `True` if the row moved SPEC→BACKTEST, `False` if it was already in BACKTEST (no write). Raises `LookupError` (no row), `IllegalTransition` (any other state), `StaleTransition` (SPEC but `state_version` ≠ expected: nothing written).

Why one function and not `EDGES`: D4 revised — #170 is appending to `state/transition.py` today, and `try_transition` hard-codes a `status` column (`transition.py:49-53`) that `strategies` does not have (`schema.sql:209`, column is `state`). General `strategies` support in `transition.py` is a follow-up after #170 merges (Self-Review).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state_specs.py` (imports: extend the existing `from state.specs import (...)` at `:16-17` with `advance_to_backtest`; add `from state.transition import IllegalTransition, StaleTransition`):

```python
# --- advance_to_backtest: the one strategies edge this tree implements ------

def _lifecycle(conn, sid):
    return conn.execute("SELECT state, state_version, updated_at FROM strategies"
                        " WHERE strategy_id = ?", (sid,)).fetchone()


def test_first_run_moves_spec_to_backtest_and_bumps_the_cas_token(conn):
    """strategy-contracts.md §4 row 1: SPEC -> BACKTEST on the first
    run_backtest. §4's CAS: state_version is the token, so it moves with the
    state; updated_at is the injected clock."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    later = "2026-07-07T15:00:00+00:00"
    assert advance_to_backtest(conn, sid, expected_state_version=0,
                               now_iso=later) is True
    row = _lifecycle(conn, sid)
    assert (row["state"], row["state_version"], row["updated_at"]) == (
        "BACKTEST", 1, later)


def test_a_later_run_finds_backtest_and_is_a_no_op(conn):
    """§4 has no BACKTEST -> BACKTEST edge, and §3.2 step 7 runs on every
    call, so the second call must be a no-op — not an error, and not a
    second bump of the token."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    advance_to_backtest(conn, sid, expected_state_version=0, now_iso=NOW)
    before = tuple(_lifecycle(conn, sid))
    assert advance_to_backtest(conn, sid, expected_state_version=1,
                               now_iso="2026-07-08T15:00:00+00:00") is False
    assert tuple(_lifecycle(conn, sid)) == before


def test_a_stale_token_writes_nothing_and_raises(conn):
    """§4: "every transition passes expected_state_version; mismatch ->
    no-op". The row is in SPEC but its token moved under the caller, so the
    UPDATE's WHERE matches nothing and the caller is told, not overwritten."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    conn.execute("UPDATE strategies SET state_version = 3 WHERE strategy_id = ?",
                 (sid,))
    conn.commit()
    with pytest.raises(StaleTransition):
        advance_to_backtest(conn, sid, expected_state_version=0, now_iso=NOW)
    row = _lifecycle(conn, sid)
    assert (row["state"], row["state_version"]) == ("SPEC", 3)


def test_any_other_state_is_an_illegal_edge(conn):
    """REJECTED (terminal) and VALIDATED (past BACKTEST) have no edge to
    BACKTEST in §4. Raise, never overwrite (CLAUDE.md conventions)."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    for state in ("REJECTED", "VALIDATED"):
        conn.execute("UPDATE strategies SET state = ? WHERE strategy_id = ?",
                     (state, sid))
        conn.commit()
        with pytest.raises(IllegalTransition):
            advance_to_backtest(conn, sid, expected_state_version=0,
                                now_iso=NOW)
        assert _lifecycle(conn, sid)["state"] == state


def test_a_missing_lifecycle_row_raises_rather_than_inventing_one(conn):
    """A spec with no strategies row is an orphan (_refuse_orphaned_specs);
    advancing it would be the silent backfill that function refuses."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    conn.execute("DELETE FROM strategies WHERE strategy_id = ?", (sid,))
    conn.commit()
    with pytest.raises(LookupError):
        advance_to_backtest(conn, sid, expected_state_version=0, now_iso=NOW)
    assert _lifecycle(conn, sid) is None


def test_advance_is_committed(tmp_path):
    """Like insert_strategy_spec, the function commits: the handler that
    calls it holds a per-tool-call connection (fundbt/registry.py:44-47)
    and must not leave the move in an open transaction."""
    path = tmp_path / "fund.sqlite"
    c = connect(path)
    sid = insert_strategy_spec(c, StrategySpec(**SPEC), NOW)
    advance_to_backtest(c, sid, expected_state_version=0, now_iso=NOW)
    c.close()
    c2 = connect(path)
    assert _lifecycle(c2, sid)["state"] == "BACKTEST"
    c2.close()
```

- [ ] **Step 2: Run them and read the whole failure list**

Run: `.venv/bin/python3 -m pytest tests/test_state_specs.py -q`

Expected: collection error — `ImportError: cannot import name 'advance_to_backtest' from 'state.specs'`. Every test in the file errors on import; that is the expected red for this step (the six new tests cannot fail individually until the name exists).

- [ ] **Step 3: Implement**

In `state/specs.py`, add to the imports (after `from state.models import StrategySpec` at `:20`):

```python
from state.transition import IllegalTransition, StaleTransition
```

Insert after `insert_strategy_spec` (i.e. after `:109`, before `_refuse_orphaned_specs`):

```python
def advance_to_backtest(conn: sqlite3.Connection, spec_id: str, *,
                        expected_state_version: int, now_iso: str) -> bool:
    """CAS one `strategies` row from SPEC to BACKTEST (strategy-contracts.md
    §4, row 1: "first run_backtest"). True if it moved; False if it was
    already in BACKTEST, which is §3.2 step 7 on every run after the first
    — §4 has no BACKTEST -> BACKTEST edge, so that call is a no-op, not an
    error.

    THIS IS THE ONLY `strategies` EDGE IMPLEMENTED, and it lives here rather
    than in state/transition.py's EDGES on purpose: try_transition hard-codes
    a `status` column (transition.py:49-53) that this table does not have
    (schema.sql:209 — `state`), and the CAS token §4 requires
    (`expected_state_version`) is not part of that helper's contract. The
    general machine for this table is a follow-up that lands after #170's
    EDGES change. The exception classes are transition.py's own, so the
    failure reads the same to an operator whichever helper raised it.

    CAS is the WHERE clause: `state = 'SPEC' AND state_version = ?`. A
    matching row moves and its token bumps; a row whose token moved under
    the caller matches nothing, and that is reported as StaleTransition with
    nothing written rather than overwritten. Any state other than SPEC or
    BACKTEST is an edge §4 does not have -> IllegalTransition. No row at all
    is an orphan (_refuse_orphaned_specs below) -> LookupError; backfilling
    one here would be the silent invention that function exists to refuse.

    Commits, like insert_strategy_spec: the caller holds a per-tool-call
    connection and must not leave the move open.
    """
    row = conn.execute(
        "SELECT state, state_version FROM strategies WHERE strategy_id = ?",
        (spec_id,)).fetchone()
    if row is None:
        raise LookupError(
            f"no `strategies` lifecycle row for {spec_id!r} — nothing to"
            " advance (see OrphanedSpecs for the repair)")
    if row["state"] == "BACKTEST":
        return False
    if row["state"] != "SPEC":
        raise IllegalTransition(
            f"strategies: {row['state']!r} -> 'BACKTEST' is not a legal edge"
            " (strategy-contracts.md §4)")
    cur = conn.execute(
        "UPDATE strategies SET state = 'BACKTEST',"
        " state_version = state_version + 1, updated_at = ?"
        " WHERE strategy_id = ? AND state = 'SPEC' AND state_version = ?",
        (now_iso, spec_id, expected_state_version))
    conn.commit()
    if cur.rowcount != 1:
        raise StaleTransition(
            f"strategies {spec_id!r}: not at state_version"
            f" {expected_state_version} (or no longer SPEC) — refusing to"
            " overwrite")
    return True
```

Then correct the comment at `state/schema.sql:198-202`. Replace the sentence beginning `Nothing TRANSITIONS a row, though — this table has no` through `not because anything reads it.` with:

```
-- Only ONE edge is implemented: state/specs.py's advance_to_backtest CASes
-- SPEC -> BACKTEST on state_version (#171 half two). This table has no
-- state/transition.py machine, so try_transition() raises IllegalTransition
-- for it; the rest of §4's edges are a follow-up once #170's EDGES change
-- lands.
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python3 -m pytest tests/test_state_specs.py -q`
Expected: all pass (the pre-existing count plus 6).

- [ ] **Step 5: Manufacture a red against the passing code**

Temporarily delete ` AND state_version = ?` from the UPDATE's WHERE and drop `expected_state_version` from the params tuple. Run the file. Expected red: exactly `test_a_stale_token_writes_nothing_and_raises` (the row moves to BACKTEST with token 4). Restore. Then temporarily change `return False` (the BACKTEST branch) to fall through to the UPDATE: expected red `test_a_later_run_finds_backtest_and_is_a_no_op` only (StaleTransition, since `state = 'SPEC'` no longer matches). Restore. Record both failure lists in the task report.

- [ ] **Step 6: Full suite, then commit**

Run: `make test`
Expected: green, output pristine. (`scripts/check_purity.py` lints `state/`; the new function reads no clock.)

```bash
git add state/specs.py state/schema.sql tests/test_state_specs.py
git commit -m "feat: strategies SPEC->BACKTEST as a CAS in state/specs.py (#171)"
```

---

## Task 2: `handle_run_backtest`, `BacktestRequest`, `close_provider`, the `budget_exhausted` renderer

**Files:**
- Modify: `state/models.py` (after `SpecCritique`, `:154-171`)
- Modify: `agents/tools/fund_server.py` (imports `:31-32` only; new `_check_params` + handler after `handle_submit_strategy_spec`, i.e. after `:351`; `build_fund_server` **untouched**)
- Modify: `slackkit/render.py` (renderer before `_render_projection_error` at `:336`; `RENDERERS` `:342-356`)
- Modify: `tests/test_slackkit.py` (`BLOCK_KINDS` `:183-189`; one new test after `test_new_event_kinds_render` `:550`)
- Test: `tests/test_run_backtest_handler.py` (create)

**Interfaces:**
- Consumes: `advance_to_backtest` (T1); `run_backtest(*, spec: dict, params: dict, close: pd.DataFrame, registry: TrialRegistry, seat: str, now_iso: str, seed: int = 0, holdout_months: int = HOLDOUT_MONTHS) -> dict` (`fundbt/run_backtest.py:121-131`); `BacktestError` (`:59`), `RULES` (`:38`); `TrialRegistry(conn)` (`fundbt/registry.py:50`); `append_event(conn, kind, payload, now_iso) -> int` (`slackkit/outbox.py:29`); `JSON_COLUMNS = ("universe", "signal_rule", "param_ranges", "predicted")` (`state/specs.py:22`); `StaleTransition` (`state/transition.py:33`).
- Produces: `handle_run_backtest(conn, *, seat: str, args: dict, now_iso: str, close_provider: Callable[[], pd.DataFrame]) -> dict` returning `{"ok": True, "result": <BacktestResult dict>}` or `{"ok": False, "error": str}`; `close_provider` is REQUIRED (no default) — the future `@tool` closure binds it. `_check_params(params: dict, ranges: dict) -> str | None` (a reason string, or `None` when every param passes). `state.models.BacktestRequest`. Event kind `"budget_exhausted"` with payload `{"seat", "spec_id", "family", "search_budget"}`.

**The cap question, decided:** no seat holds `run_backtest`, so `_can(seat, "run_backtest")` is `False` for every seat and the handler refuses everyone. The handler still calls `_can` so that a future `SEAT_CAPS` line is the only switch — exactly as `handle_submit_strategy_spec` did before #198. Tests copy the original #171 pattern (`git show e97b16a:tests/test_submit_strategy_spec.py`, its `granted` fixture): monkeypatch the cap onto one seat for one test; `SEAT_CAPS` on disk is untouched. The seat is `quant` (the research seat; `charters/quant.md`), not `analyst`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_backtest_handler.py`:

```python
"""run_backtest handler — strategy-contracts.md §3.2, the wrapper enforcement
order. The engine (fundbt/run_backtest.py) does steps 2-6 and the trial
INSERT; what is under test here is what the HANDLER adds: step 1 (lifecycle
state), the rule pre-check, step 3's event, step 7's lifecycle move, the
close_provider seam, and every refusal path writing nothing.

THE HANDLER IS CALLED DIRECTLY, never through a built server: there is no
`@tool` and no cap (G-2(iii), same shape as submit_strategy_spec in #171),
so there is no MCP surface to reach. `test_no_shipped_seat_holds_the_cap`
pins that as a fact of the shipped table.

The cap is monkeypatched onto `quant` for the write-path tests. SEAT_CAPS on
disk is untouched, and the guard under test is the handler's own `_can`.
"""
import json

import pytest

from agents.tools import fund_server
from agents.tools.fund_server import (SEAT_CAPS, handle_run_backtest,
                                      handle_submit_strategy_spec)
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_spec,
                             seed_spec_row, spec_payload)

NOW = "2026-09-13T14:00:00Z"
LATER = "2026-09-13T15:00:00Z"

# Built once: make_market() is deterministic (seeded) and the handler never
# mutates it. ~2520x20 floats; re-generating per test is pure waste.
CLOSE = make_market()


def _close():
    return CLOSE


def _no_data():
    """A provider with nothing to serve says so with LookupError — the one
    provider failure the handler reads as a refusal."""
    raise LookupError("no close-price data bound for this test")


@pytest.fixture
def granted(monkeypatch):
    """`quant` holds the cap for one test, and only there."""
    monkeypatch.setitem(fund_server.SEAT_CAPS, "quant",
                        SEAT_CAPS["quant"] | {"run_backtest"})


def _run(fund_db, args, seat="quant", now_iso=NOW, close_provider=_close):
    return handle_run_backtest(fund_db, seat=seat, args=args, now_iso=now_iso,
                               close_provider=close_provider)


def _count(fund_db, table):
    return fund_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _lifecycle(fund_db, sid):
    return fund_db.execute("SELECT state, state_version FROM strategies"
                           " WHERE strategy_id = ?", (sid,)).fetchone()


def _events(fund_db, kind):
    return [json.loads(r["payload"]) for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind = ?", (kind,)).fetchall()]


def _golden(fund_db, **spec_overrides):
    """The dip_buyer spec tests/test_run_backtest.py runs, registered with
    its lifecycle row in SPEC. signal_rule.name == 'dip_buyer' is what makes
    it runnable through the handler."""
    return seed_spec_row(fund_db, make_spec() | spec_overrides)


def _assert_nothing_written(fund_db, sid):
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0
    assert tuple(_lifecycle(fund_db, sid)) == ("SPEC", 0)


# --- surface ---------------------------------------------------------------

def test_no_shipped_seat_holds_the_cap():
    """G-2(iii) again: handler, tests, `not served` row, no caller. A cap
    arrives with the charter and the schedule that justify it."""
    assert [s for s, caps in SEAT_CAPS.items() if "run_backtest" in caps] == []


def test_a_seat_without_the_cap_is_refused_and_nothing_is_written(fund_db):
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "not granted" in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_close_provider_is_a_required_keyword_and_build_fund_server_has_none(
        fund_db, sim_clock):
    """D1 as ruled: the seam is a REQUIRED handler parameter, bound by the
    future @tool closure — not a build_fund_server kwarg nothing reads."""
    import inspect

    from agents.tools.fund_server import build_fund_server

    param = inspect.signature(handle_run_backtest).parameters["close_provider"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
    assert "close_provider" not in inspect.signature(build_fund_server).parameters
    with pytest.raises(TypeError):
        handle_run_backtest(fund_db, seat="quant", args={}, now_iso=NOW)


def test_a_provider_with_no_data_is_a_tool_error_not_a_run(granted, fund_db):
    """No loader exists; a provider that has nothing says so with
    LookupError and the seat hears a refusal — never an empty frame the
    engine would refuse for a misleading reason (insufficient_data)."""
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
             close_provider=_no_data)
    assert r["ok"] is False and "no close-price data" in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_a_provider_that_fails_for_another_reason_is_not_swallowed(granted,
                                                                   fund_db):
    """CLAUDE.md: fail fast, never swallow. Only the named LookupError is a
    refusal; anything else is a bug and must surface as one."""
    sid = _golden(fund_db)

    def broken():
        raise RuntimeError("disk gone")

    with pytest.raises(RuntimeError, match="disk gone"):
        _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
             close_provider=broken)
    _assert_nothing_written(fund_db, sid)


# --- input schema (§3.2 BacktestRequest) -------------------------------------

@pytest.mark.parametrize("args", [
    {"params": GOLDEN_PARAMS},                                   # no spec_id
    {"spec_id": "spec_golden000000f1"},                          # no params
    {"spec_id": "spec_golden000000f1", "params": GOLDEN_PARAMS,
     "seed": "zero"},                                            # bad seed
    {"spec_id": "spec_golden000000f1", "params": GOLDEN_PARAMS,
     "holdout_months": 0},                                       # extra=forbid
    {"spec_id": "spec_golden000000f1", "params": "dip_days=5"},  # not a dict
])
def test_a_malformed_request_is_refused_and_writes_nothing(granted, fund_db,
                                                           args):
    sid = _golden(fund_db)
    r = _run(fund_db, args)
    assert r["ok"] is False
    _assert_nothing_written(fund_db, sid)


# --- step 1: spec exists and strategies.state in {SPEC, BACKTEST} -----------

def test_an_unregistered_spec_is_refused(granted, fund_db):
    r = _run(fund_db, {"spec_id": "spec_neverregistered",
                       "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "not registered" in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0


def test_a_spec_with_no_lifecycle_row_is_refused_not_assumed_spec(granted,
                                                                  fund_db):
    """Invariant 4: a missing lifecycle row is ambiguity, and ambiguity is
    no action — the same posture as state/specs.py:_refuse_orphaned_specs."""
    sid = _golden(fund_db)
    fund_db.execute("DELETE FROM strategies WHERE strategy_id = ?", (sid,))
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "lifecycle" in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0


@pytest.mark.parametrize("state", ["REJECTED", "VALIDATED", "RETIRED"])
def test_a_spec_outside_spec_or_backtest_is_refused(granted, fund_db, state):
    sid = _golden(fund_db)
    fund_db.execute("UPDATE strategies SET state = ? WHERE strategy_id = ?",
                    (state, sid))
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and state in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0
    assert _lifecycle(fund_db, sid)["state"] == state


# --- D2: the rule pre-check ---------------------------------------------------

def test_a_spec_whose_rule_has_no_name_is_refused_as_unknown_rule(granted,
                                                                  fund_db):
    """spec_payload()'s signal_rule carries no `name` — a registered spec the
    engine would KeyError on (run_backtest.py:137). The handler pre-checks so
    the seat gets a refusal with a name, and nothing is written."""
    sid = handle_submit_strategy_spec(fund_db, seat="quant",
                                      args=spec_payload(), now_iso=NOW)["spec_id"]
    fund_db.execute("DELETE FROM events")            # the registration post
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": {"sigma": 1.5}})
    assert r["ok"] is False and "unknown_rule" in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_a_rule_name_the_engine_does_not_register_is_refused(granted, fund_db):
    sid = handle_submit_strategy_spec(
        fund_db, seat="quant",
        args=spec_payload(signal_rule={"name": "no_such_rule"}),
        now_iso=NOW)["spec_id"]
    fund_db.execute("DELETE FROM events")
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": {"sigma": 1.5}})
    assert r["ok"] is False and "unknown_rule" in r["error"]
    _assert_nothing_written(fund_db, sid)


# --- step 2: params declared, typed, in range — HANDLER-side (ruling Q1) ----

@pytest.mark.parametrize("params,reason", [
    ({**GOLDEN_PARAMS, "dip_pct": 0.20}, "param_out_of_range:dip_pct"),
    ({**GOLDEN_PARAMS, "dip_days": 2}, "param_out_of_range:dip_days"),
    ({**GOLDEN_PARAMS, "lookback": 10}, "undeclared_param:lookback"),
    # §3.2 lets params carry str; a str against numeric bounds is a TYPE
    # refusal here, not the TypeError the engine's `lo <= v <= hi` would
    # raise (run_backtest.py:145).
    ({**GOLDEN_PARAMS, "dip_days": "5"}, "param_type:dip_days"),
    ({**GOLDEN_PARAMS, "dip_pct": True}, "param_type:dip_pct"),
])
def test_a_bad_param_is_refused_by_the_handler_and_writes_nothing(
        granted, fund_db, params, reason):
    """The engine's spellings, reused, so a seat sees one vocabulary
    whichever layer refused. Refused BEFORE the engine, so nothing — not
    even a hash — is computed."""
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": params})
    assert r["ok"] is False and reason in r["error"]
    _assert_nothing_written(fund_db, sid)


# --- step 7: run, trial row, SPEC -> BACKTEST ----------------------------------

def test_the_first_run_returns_the_result_logs_one_trial_and_moves_to_backtest(
        granted, fund_db):
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is True
    res = r["result"]
    assert res["spec_id"] == sid and res["cached"] is False
    assert res["run_key"].startswith("run_")
    assert res["n_trials_family"] == 1
    # §3.2 BacktestResult, every field, no more and no less.
    assert set(res) == {
        "run_key", "spec_id", "config_hash", "data_snapshot_hash", "n_trades",
        "span_years", "net_sharpe", "net_sharpe_2x", "net_sharpe_3x",
        "per_period_sharpe", "deflated_sharpe", "n_trials_family", "wfe",
        "max_drawdown", "turnover_annual", "cost_share", "param_neighbors",
        "regime_sharpe", "cached"}
    assert _count(fund_db, "trial_registry") == 1
    trial = fund_db.execute("SELECT seat, created_at FROM trial_registry"
                            ).fetchone()
    assert (trial["seat"], trial["created_at"]) == ("quant", NOW)   # D8
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)
    assert _count(fund_db, "events") == 0                # no post on success


def test_a_spec_registered_through_the_production_write_path_backtests(
        granted, fund_db):
    """Ruling Q5: the golden fixture above seeds its rows by hand
    (tests/synthetic.py:74-77, frozen id). This one registers through
    handle_submit_strategy_spec -> state/specs.py:insert_strategy_spec, so
    the content-addressed id and the registration-written lifecycle row are
    on the tested route end to end. The rule library reads only `params`
    (fundbt/rules.py:25-27: dip_days, dip_pct, trend_days); the engine reads
    signal_rule.name, param_ranges, search_budget, family, liquidity_bucket
    and spec_id from the spec (run_backtest.py:137-176)."""
    payload = spec_payload(signal_rule={"name": "dip_buyer"},
                           param_ranges=make_spec()["param_ranges"])
    reg = handle_submit_strategy_spec(fund_db, seat="quant", args=payload,
                                      now_iso=NOW)
    assert reg["ok"] is True and reg["duplicate"] is False
    sid = reg["spec_id"]
    assert sid != make_spec()["spec_id"]                # content-addressed
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is True and r["result"]["spec_id"] == sid
    assert r["result"]["cached"] is False
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)
    kinds = [row[0] for row in fund_db.execute("SELECT kind FROM events")]
    assert kinds == ["strategy_spec"]                   # registration only


def test_the_same_run_again_is_cached_and_moves_nothing(granted, fund_db):
    """§1: identical run_key -> cached result, no new row. §3.2 step 7 on a
    row already in BACKTEST is a no-op, not an error."""
    sid = _golden(fund_db)
    first = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    second = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
                  now_iso=LATER)
    assert second["ok"] is True and second["result"]["cached"] is True
    assert second["result"]["run_key"] == first["result"]["run_key"]
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)


def test_a_second_config_runs_from_backtest_and_adds_a_trial(granted, fund_db):
    sid = _golden(fund_db)
    _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    r = _run(fund_db, {"spec_id": sid,
                       "params": {**GOLDEN_PARAMS, "dip_days": 6}})
    assert r["ok"] is True and r["result"]["n_trials_family"] == 2
    assert _count(fund_db, "trial_registry") == 2
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)


# --- step 3: budget exhausted IS logged, and projects ------------------------

def test_budget_exhaustion_logs_the_rejection_and_appends_one_event(granted,
                                                                    fund_db):
    """§3.2 step 3: "exceeded -> REJECT and a budget_exhausted event; this
    rejection IS logged". The engine writes the trial row (a spent trial is
    a spent trial); the handler writes the event, through the outbox."""
    from slackkit.render import render

    sid = _golden(fund_db, search_budget=1)
    _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    r = _run(fund_db, {"spec_id": sid,
                       "params": {**GOLDEN_PARAMS, "dip_days": 6}},
             now_iso=LATER)
    assert r["ok"] is False and "budget_exhausted" in r["error"]
    assert _count(fund_db, "trial_registry") == 2
    rejected = fund_db.execute(
        "SELECT stats FROM trial_registry WHERE created_at = ?",
        (LATER,)).fetchone()
    assert json.loads(rejected["stats"]) == {"rejected": "budget_exhausted"}
    events = _events(fund_db, "budget_exhausted")
    assert events == [{"seat": "quant", "spec_id": sid, "family": "F1",
                       "search_budget": 1}]
    post = render("budget_exhausted", events[0])
    assert post.channel == "#research" and post.username is None
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)   # unchanged


def test_a_refused_run_before_the_engine_appends_no_event(granted, fund_db):
    """Only the budget rejection projects. Every other refusal is default
    HOLD: no row, no event."""
    sid = _golden(fund_db)
    _run(fund_db, {"spec_id": sid, "params": {**GOLDEN_PARAMS, "dip_pct": 9}})
    _run(fund_db, {"spec_id": "spec_nope", "params": GOLDEN_PARAMS})
    _run(fund_db, {"spec_id": sid}, seat="exec")
    assert _count(fund_db, "events") == 0
```

- [ ] **Step 2: Run and confirm the red**

Run: `.venv/bin/python3 -m pytest tests/test_run_backtest_handler.py -q`
Expected: collection error `ImportError: cannot import name 'handle_run_backtest' from 'agents.tools.fund_server'`. Nothing else should be reported at this point.

- [ ] **Step 3: Add `BacktestRequest` to `state/models.py`**

Append after `SpecCritique` (`:171`):

```python
class BacktestRequest(BaseModel):
    """strategy-contracts.md §3.2 `BacktestRequest`, verbatim. The agent-facing
    payload of run_backtest: which registered spec, which point inside its
    declared param_ranges, and the seed the result is keyed by. Range
    membership and per-param typing are the handler's check
    (fund_server.py:_check_params), not this model's — they need the spec
    row, which the handler loads."""
    model_config = ConfigDict(extra="forbid")

    spec_id: str
    params: dict[str, float | int | str]
    seed: int = 0
```

- [ ] **Step 4: Add the renderer**

In `slackkit/render.py`, insert before `_render_projection_error` (`:336`):

```python
def _render_budget_exhausted(payload: dict) -> Post:
    """A run_backtest refused because the spec's search budget is spent
    (strategy-contracts.md §3.2 step 3). Machinery, no face: the refusal is
    the registry's count, not a seat's words. The rejected attempt is itself
    a logged trial, so the family's N moved even though nothing ran — which
    is why it is worth a post and not just a tool error."""
    seat = _seat(payload["seat"])
    headline = (f"search budget exhausted · `{payload['spec_id']}`"
                f" · {payload['family']} · {payload['search_budget']} trials")
    return Post("#research", f"{headline} · refused for {seat}",
                [_section(headline),
                 _context(f"refused for {seat}",
                          "the rejection is logged as a trial")])
```

Add to `RENDERERS` (`:342-356`), after `"spec_critique": _render_spec_critique,`:

```python
    "budget_exhausted": _render_budget_exhausted,
```

In `tests/test_slackkit.py`, add after `STRATEGY_SPEC` (`:179-181`):

```python
BUDGET_EXHAUSTED = {"seat": "quant", "spec_id": "spec_0f1e2d3c4b5a6978",
                    "family": "F1", "search_budget": 20}
```

and extend `BLOCK_KINDS` (`:183-189`) with `("budget_exhausted", BUDGET_EXHAUSTED)`. `SPEAKS` (`:397`) is **not** extended — machinery has no face, and `test_only_the_kinds_with_a_model_behind_them_have_a_face` pins that in both directions. Add after `test_new_event_kinds_render` (`:550-566`):

```python
def test_budget_exhaustion_lands_in_research_as_machinery():
    """strategy-contracts.md §3.2 step 3's event. #research, beside the
    spec's registration post; no face, because the count is the engine's."""
    post = render("budget_exhausted", BUDGET_EXHAUSTED)
    assert post.channel == "#research"
    assert "spec_0f1e2d3c4b5a6978" in post.text and "20" in post.text
    assert post.username is None and post.icon_emoji is None
```

- [ ] **Step 5: Write the handler in `agents/tools/fund_server.py`**

Imports — change only `:31-32` (module-level; nothing from `fundbt` is imported here — ruling Q6, see the handler body):

```python
from state.models import (BacktestRequest, Decision, SpecCritique, Signal,
                          StrategySpec)
from state.specs import (JSON_COLUMNS, advance_to_backtest,
                         insert_strategy_spec, specs_awaiting_critique)
from state.transition import StaleTransition
```

Insert after `handle_submit_strategy_spec` (after `:351`):

```python
# §3.2 step 1: the lifecycle states a backtest may run from.
BACKTESTABLE = ("SPEC", "BACKTEST")


def _check_params(params: dict, ranges: dict) -> str | None:
    """§3.2 step 2, in full, before the engine: every param declared in the
    spec's param_ranges, numeric where the declared bounds are numeric, and
    inside [lo, hi]. Returns the refusal reason or None.

    The engine repeats the declared/in-range half (run_backtest.py:140-146)
    and this reuses its spellings so a seat sees one vocabulary; what the
    engine lacks is the type check — §3.2 lets `params` carry str, and a
    str against numeric bounds raises TypeError at run_backtest.py:145, an
    exception rather than a refusal. bool is excluded explicitly: it is an
    int to isinstance and `True` would pass a [0, 1] range as 1.
    """
    for p, v in params.items():
        if p not in ranges:
            return f"undeclared_param:{p}"
        lo, hi, _step = ranges[p]
        numeric = isinstance(lo, (int, float)) and isinstance(hi, (int, float))
        if numeric and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"param_type:{p}"
        try:
            inside = lo <= v <= hi
        except TypeError:
            return f"param_type:{p}"
        if not inside:
            return f"param_out_of_range:{p}"
    return None


def handle_run_backtest(conn: sqlite3.Connection, *, seat: str, args: dict,
                        now_iso: str,
                        close_provider: Callable[[], "pd.DataFrame"]) -> dict:
    """The run_backtest wrapper: strategy-contracts.md §3.2's enforcement
    order, with the computation left to fundbt.run_backtest.run_backtest.

    HANDLER-SIDE: step 1 (spec registered, `strategies.state` in SPEC or
    BACKTEST — §3.2 says this check "lives in the MCP handler"), step 2 in
    full (_check_params, before anything is hashed), a rule pre-check the
    engine lacks (a signal_rule with no `name` KeyErrors at
    run_backtest.py:137; here it is the `unknown_rule` refusal §3.2 means),
    step 3's `budget_exhausted` EVENT, and step 7's lifecycle move through
    state/specs.py:advance_to_backtest. ENGINE-SIDE, passed through and not
    duplicated: step 3's count-and-log, step 4 (holdout excluded), step 5
    (cost floors, 2x/3x), step 6's snapshot hash (recorded, not verified —
    no manifest exists), and the trial INSERT, idempotent on run_key
    (`cached` is the engine's word for a re-run).

    NOTHING IS WRITTEN ON REFUSAL except the engine's own budget-rejection
    trial row, which §3.2 step 3 says IS logged: a spent trial is a spent
    trial and N must move. Exactly that refusal also appends the event; no
    other path projects anything.

    `close_provider` is REQUIRED and bound by the caller — the @tool closure
    that will one day serve this — never by the seat. Only its LookupError
    is a refusal; any other exception is a bug and propagates. The engine's
    BacktestError is the refusal class; nothing else it raises is caught.

    Step 7 on a row already in BACKTEST is a no-op — §4 has no
    BACKTEST -> BACKTEST edge. A StaleTransition after a successful run is
    a tool error with the trial row standing (the engine's own irreversible
    write) and the lifecycle row still in SPEC, so a re-run returns the
    cached result and re-attempts the edge.

    THE fundbt IMPORTS ARE INSIDE THE BODY, deliberately. `import
    fundbt.rules` is what populates RULES (fundbt/rules.py:17) — without it
    every spec is unknown_rule — and it drags pandas/numpy in with it;
    agents/seats.py:263 imports this module to build EVERY seat's server,
    and seats that never backtest must not pay that import.

    NOT REGISTERED AND GRANTED TO NO SEAT (#171 half two, G-2(iii) again):
    the `_can` check below is what a future SEAT_CAPS line switches on. The
    §4 row is `not served`.

    `seat` is the calling seat, bound here; §5 of strategy-contracts.md
    allows a seat to run another seat's spec, logged under the caller.
    """
    import fundbt.rules  # noqa: F401 — populates RULES; see the docstring
    from fundbt.registry import TrialRegistry
    from fundbt.run_backtest import RULES, BacktestError, run_backtest

    if not _can(seat, "run_backtest"):
        return {"ok": False,
                "error": f"run_backtest is not granted to seat {seat!r}"}
    try:
        req = BacktestRequest(**args)
    except (ValidationError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    row = conn.execute(
        "SELECT s.*, st.state AS lifecycle_state, st.state_version"
        " FROM strategy_specs s"
        " LEFT JOIN strategies st ON st.strategy_id = s.spec_id"
        " WHERE s.spec_id = ?", (req.spec_id,)).fetchone()
    if row is None:
        return {"ok": False,
                "error": f"spec {req.spec_id!r} is not registered —"
                         " run_backtest refused"}
    state = row["lifecycle_state"]
    if state is None:
        return {"ok": False,
                "error": f"spec {req.spec_id!r} has no `strategies` lifecycle"
                         " row and cannot advance — run_backtest refused"
                         " (state/specs.py:OrphanedSpecs names the repair)"}
    if state not in BACKTESTABLE:
        return {"ok": False,
                "error": f"spec {req.spec_id!r} is in state {state!r};"
                         " run_backtest runs only from SPEC or BACKTEST"
                         " (strategy-contracts.md §3.2 step 1)"}
    spec = {c: (json.loads(row[c]) if c in JSON_COLUMNS else row[c])
            for c in row.keys() if c not in ("lifecycle_state", "state_version")}
    rule = (spec["signal_rule"].get("name")
            if isinstance(spec["signal_rule"], dict) else None)
    if not isinstance(rule, str) or rule not in RULES:
        return {"ok": False,
                "error": f"unknown_rule: spec {req.spec_id!r} names signal"
                         f" rule {rule!r}; registered rules are"
                         f" {sorted(RULES)}"}
    bad = _check_params(req.params, spec["param_ranges"])
    if bad is not None:
        return {"ok": False, "error": f"run_backtest refused: {bad}"}
    try:
        close = close_provider()
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    try:
        result = run_backtest(spec=spec, params=req.params, close=close,
                              registry=TrialRegistry(conn), seat=seat,
                              now_iso=now_iso, seed=req.seed)
    except BacktestError as e:
        reason = e.args[0]
        if reason == "budget_exhausted":
            append_event(conn, "budget_exhausted",
                         {"seat": seat, "spec_id": spec["spec_id"],
                          "family": spec["family"],
                          "search_budget": spec["search_budget"]}, now_iso)
        return {"ok": False, "error": f"run_backtest refused: {reason}"}
    try:
        advance_to_backtest(conn, spec["spec_id"],
                            expected_state_version=row["state_version"],
                            now_iso=now_iso)
    except StaleTransition as e:
        return {"ok": False,
                "error": f"trial {result['run_key']} is logged but the"
                         f" lifecycle row moved under this run ({e}) —"
                         " it is still SPEC, so a re-run returns the cached"
                         " result and re-attempts the edge"}
    return {"ok": True, "result": result}
```

The `"pd.DataFrame"` annotation is a string under `from __future__ import annotations` (`:15`) — do **not** import pandas into this module for it.

`build_fund_server` is **not** edited (ruling Q4). No `@tool`. No `cap_tools` entry. No `SEAT_CAPS` edit.

- [ ] **Step 6: Run the handler tests**

Run: `.venv/bin/python3 -m pytest tests/test_run_backtest_handler.py -q`
Expected: 28 passed (parametrized cases count individually: 5 surface + 5 malformed + 5 step-1 + 2 rule + 5 step-2 + 4 step-7 + 2 budget).

Then: `.venv/bin/python3 -m pytest tests/test_slackkit.py tests/test_state_specs.py tests/test_submit_strategy_spec.py -q`
Expected: green. If `test_every_written_kind_has_a_renderer` reddens, the `RENDERERS` entry from Step 4 is missing.

- [ ] **Step 7: Manufacture a red against the passing code**

Five mutations, one at a time, each restored before the next; record every failure list in the task report:

1. Delete the `import fundbt.rules` line inside the handler body. Expected red: every test that reaches the rule pre-check with a `dip_buyer` spec — `test_the_first_run_...`, `test_a_spec_registered_through_the_production_write_path_backtests`, `test_the_same_run_again_...`, `test_a_second_config_...`, `test_budget_exhaustion_...`, the five `test_a_bad_param_...[...]` cases, `test_a_provider_with_no_data_...` and `test_a_provider_that_fails_for_another_reason_...` — twelve, all refused as `unknown_rule` before the engine or the provider is reached. This is the evidence that the deferred import is load-bearing, not a lint nit.
2. Delete the `if state not in BACKTESTABLE:` block. Expected red: the three `test_a_spec_outside_spec_or_backtest_is_refused[...]` cases, each failing with `IllegalTransition` escaping from `advance_to_backtest` after the engine has already logged a trial — T1's function is the second lock, and this mutation shows why the handler's pre-check is the first: without it a REJECTED spec spends a trial.
3. Delete the `append_event(...)` call. Expected red: `test_budget_exhaustion_logs_the_rejection_and_appends_one_event` only.
4. Change `except LookupError` to `except Exception` on the provider call. Expected red: `test_a_provider_that_fails_for_another_reason_is_not_swallowed` only.
5. Delete the `_check_params` call (the two lines `bad = ...` / `if bad is not None: ...`). Expected red: exactly the two `param_type` cases of `test_a_bad_param_...` — `dip_days: "5"` escapes as a `TypeError` from `run_backtest.py:145`, and `dip_pct: True` is refused by the engine as `param_out_of_range:dip_pct` (`True` compares as `1 > 0.08`), not `param_type` — while the out-of-range and undeclared cases stay green because the engine refuses them with the same spelling. That is the precise value the handler's step 2 adds over the engine, shown rather than claimed.

- [ ] **Step 8: Full suite, then commit**

Run: `make test`
Expected: green, output pristine. Watch specifically that `tests/test_tool_surface_canon.py` and `tests/test_fund_tools.py` are untouched and green — no registration happened. (The §4 row is T3; between T2 and T3 the handler exists with no row, which is legal because the canon tests compare *registered* and *served* tools, and this one is neither.)

```bash
git add state/models.py agents/tools/fund_server.py slackkit/render.py \
        tests/test_slackkit.py tests/test_run_backtest_handler.py
git commit -m "feat: run_backtest handler — §3.2 enforcement order, served to no seat (#171)"
```

---

## Task 3: §4 row, acceptance/contract corrections, and the evaluator-toolbelt test

**Files:**
- Modify: `specs/contracts.md` §4 table (`:276-289`) + one sentence after `:291`; §8 machinery list (`:441`)
- Modify: `specs/acceptance.md:91`
- Modify: `specs/strategy-contracts.md:25` (last sentence)
- Test: `tests/test_tool_surface_canon.py` (append)

**Interfaces:**
- Consumes: `_served(conn, clock)` (`tests/test_tool_surface_canon.py:153-159`); `stratgate.gate.evaluate_g2`/`evaluate_g3` (`stratgate/gate.py:65,110`); `fundbt.run_backtest.evaluate_holdout` (`fundbt/run_backtest.py:233`).
- Produces: nothing downstream.

**Why the D7 test lives in `tests/test_tool_surface_canon.py`:** the criterion (`acceptance.md:89`) is a property of the *served surface* — what a real per-seat server lists through the MCP handler — and `_served()` is the only instrument in the tree that builds every seat's real server and unions the listing. `tests/test_run_backtest_handler.py` calls the handler directly and never builds a server, so a test there could only assert on `SEAT_CAPS`, which is the derivation, not the surface. The canon file's docstring already frames it as three instruments over one seam; this is a fourth assertion over the same `_served()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tool_surface_canon.py`:

```python
def test_no_seat_is_served_an_evaluator_or_the_backtest_tool(fund_db,
                                                             sim_clock):
    """specs/acceptance.md Phase 5: "`evaluate_holdout` and G2/G3/G4
    evaluators are orchestrator-invoked only (test: seat toolbelt contains
    no evaluator tools)". Through the real per-seat servers, not SEAT_CAPS.

    The evaluator names are DERIVED from stratgate.gate's `evaluate_*`
    callables rather than typed out, so an evaluator added there is covered
    the day it lands; the two known ones are asserted present so the
    derivation cannot go vacuous. `run_backtest` is in the set FOR NOW —
    #171 half two ships its handler served to nobody (contracts.md §4,
    `not served`); the lane that serves it removes that one name here and
    flips the §4 row in the same commit.
    """
    import stratgate.gate as gate
    from fundbt import run_backtest as engine

    evaluators = {n for n in dir(gate)
                  if n.startswith("evaluate_") and callable(getattr(gate, n))}
    assert {"evaluate_g2", "evaluate_g3"} <= evaluators, "instrument broken"
    assert callable(engine.evaluate_holdout) and callable(engine.run_backtest)
    forbidden = evaluators | {"evaluate_holdout", "run_backtest"}

    served = set(_served(fund_db, sim_clock))
    assert served, "served set empty — the instrument is broken"
    assert served & forbidden == set(), sorted(served & forbidden)
```

- [ ] **Step 2: Run it — expect GREEN, then manufacture the red**

Run: `.venv/bin/python3 -m pytest tests/test_tool_surface_canon.py -q -k evaluator`
Expected: 1 passed — nothing serves any of these today, so this test is green on first run and therefore pins nothing until it has been shown to bite. Manufacture: temporarily change `{"evaluate_holdout", "run_backtest"}` to `{"evaluate_holdout", "run_backtest", "get_stage_brief"}` (a tool that IS served). Expected red: `AssertionError: ['get_stage_brief']`. Restore. Record it.

- [ ] **Step 3: Add the §4 row**

In `specs/contracts.md`, add to the table after the `submit_proposal` row (`:289`):

```
| `run_backtest` |  | `strategy-contracts.md` §3.2 | not served — handler only, no driving seat (#171 half two) |
```

Add after the `improvement.md` paragraph (`:291`), as its own paragraph:

```
`run_backtest` follows the same pattern one lane later: `agents/tools/fund_server.py:handle_run_backtest` implements `strategy-contracts.md` §3.2's enforcement order and is directly tested, but carries no `@tool` and no cap (G-2(iii), as `submit_strategy_spec` shipped in #171). Its `seats` cell is empty because §3.2 says "seats listed in the spec's family config" and no such config exists yet; the lane that staffs a backtesting turn grants the cap, registers the tool, and fills the cell together.
```

Before adding the row, confirm nothing counts rows: `grep -rn "twelve\|thirteen\|12 tools\|13 tools" tests/ specs/contracts.md` — expected empty (`a1752d1` removed the last count-carrying sentence from §4; the remaining "three `improvement.md` rows" at `:291` counts a subset that does not change).

In §8, `:441`, extend the machinery list. The sentence currently ends `... \`model_fallback_used\`, \`scorecard\` and \`projection_error\` leave both \`None\``; insert `budget_exhausted` so it reads `... \`model_fallback_used\`, \`scorecard\`, \`budget_exhausted\` and \`projection_error\` leave both \`None\``. (Side observation, not in scope: that sentence's opening claim "Only `signal` and `decision` set `username`/`icon_emoji`" is already stale — `spec_critique` and `strategy_spec` carry a face too (`render.py:296`, `:331`). Leave it; flag it in the task report.)

- [ ] **Step 4: Correct `specs/acceptance.md:91`**

Replace the line with:

```
- [ ] Spec enforcement: `run_backtest` without a registered `spec_id` or with config outside `param_ranges` → refused, no stats, no trial row; beyond `search_budget` → refused, no stats, and the rejection **is** logged as a trial row plus a `budget_exhausted` event (`strategy-contracts.md` §3.2 step 3: a spent trial is a spent trial, so N moves).
```

- [ ] **Step 5: Correct `specs/strategy-contracts.md:25`**

Replace the final sentence of the blockquote paragraph — from `Nothing yet TRANSITIONS a `strategies` row, however:` to `stays in `SPEC`.` — with:

```
One `strategies` edge now has implementing code: `state/specs.py:advance_to_backtest` CASes SPEC → BACKTEST on `state_version` (§3.2 step 7, #171 half two). Every other §4 edge is still unimplemented, and `state/transition.py`'s `EDGES` carries no entry for the table.
```

- [ ] **Step 6: Canon tests, then the full suite**

Run: `.venv/bin/python3 -m pytest tests/test_tool_surface_canon.py tests/test_fund_tools.py -q`
Expected: green. If `test_every_registered_tool_has_a_canonical_entry` or `test_served_tool_set_is_exactly_the_section_4_table` reddens, the row's status cell does not begin with `not served` — fix the row, never the test.

Manufacture a red on the row: temporarily change the status cell to `served`. Expected red: `test_served_tool_set_is_exactly_the_section_4_table`, `test_every_registered_tool_has_a_canonical_entry`, `test_the_wrong_seat_payloads_cover_every_canonical_tool`, and `test_the_handler_refuses_even_when_registration_is_wrong` erroring with `IndexError` on `sorted(row["seats"])[0]` of an empty seats cell. `test_each_tool_reaches_exactly_the_seats_the_table_names` stays GREEN under this mutation — with an empty seats cell `want` is `set()` whether served or not — which is why it is not the test that guards this row. Restore. Record.

Run: `make test`
Expected: green, output pristine.

- [ ] **Step 7: Commit**

```bash
git add specs/contracts.md specs/acceptance.md specs/strategy-contracts.md \
        tests/test_tool_surface_canon.py
git commit -m "docs: run_backtest §4 row (not served), budget-rejection logging in acceptance, evaluator-toolbelt test (#171)"
```

---

## Self-Review

### Spec coverage, per §3.2 step

| Requirement | Task |
|---|---|
| `BacktestRequest` exactly §3.2, `extra="forbid"` | T2 Step 3; pinned by `test_a_malformed_request_is_refused_and_writes_nothing` (extra field, wrong types, missing fields) |
| `BacktestResult` = the engine's dict exactly; `cached` semantics | T2 `test_the_first_run_...` asserts the exact key set; `test_the_same_run_again_...` asserts `cached is True`, no new row |
| Step 1: spec exists, `state ∈ {SPEC, BACKTEST}` | T2 handler + three tests (unregistered, orphaned, wrong state) |
| Step 2: params declared, typed, in range | T2 `_check_params`, before the engine (ruling Q1); five `test_a_bad_param_...` cases; mutation 5 shows the type check is the handler's own contribution |
| Step 3: budget count, log, `budget_exhausted` event | engine logs (`:158-165`); T2 handler appends the event; renderer forced by `tests/test_slackkit.py:829`; `acceptance.md:91` corrected in T3 |
| Step 4: holdout excluded | engine (`:167-173`); not duplicated; `test_the_first_run_...` runs on the full 10y synthetic market and the engine's own `test_planted_edge_detected_and_sane` pins `span_years > 7.5` |
| Step 5: cost floors, 2×/3× | engine (`:175-181`); key set assertion covers `net_sharpe_2x`/`_3x` presence |
| Step 6: snapshot hash recorded, not verified (D6) | engine (`:150`); no manifest work |
| Step 7: run, trial INSERT, `strategies → BACKTEST` | engine INSERT (`:227-229`); T1 `advance_to_backtest` + T2 lifecycle assertions; no-op on second run pinned |
| D2 `unknown_rule` pre-check | T2, two tests (no `name`; unregistered name) |
| D1 `close_provider` as a required handler keyword (ruling Q4) | T2 handler signature + `test_close_provider_is_a_required_keyword_and_build_fund_server_has_none`, `test_a_provider_with_no_data_...`, `test_a_provider_that_fails_for_another_reason_...` |
| Production write path on the tested route (ruling Q5) | T2 `test_a_spec_registered_through_the_production_write_path_backtests` |
| Deferred `fundbt` imports (ruling Q6) | T2 handler body; mutation 1 keeps the import load-bearing |
| D3 no `@tool`, no cap, `not served` row | T2 (no registration), T3 (row); `test_no_shipped_seat_holds_the_cap` |
| D7 evaluator-toolbelt test (`acceptance.md:89`) | T3 |
| D8 `TrialRegistry(conn)`, `seat=`, `now_iso=` | T2 handler; `test_the_first_run_...` asserts the trial row's `seat` and `created_at` |
| D10 no swallowed exceptions | T2 `test_a_provider_that_fails_for_another_reason_is_not_swallowed`; handler catches only `ValidationError/TypeError` (model), `LookupError` (provider), `BacktestError` (engine), `StaleTransition` (edge); `_check_params` catches `TypeError` only around the one comparison it owns |
| §4 "every transition passes `expected_state_version`; mismatch → no-op" | T1: the UPDATE's WHERE is the CAS; mismatch writes nothing and raises `StaleTransition`; no `stale_transition` event kind is added (ruling Q3 — part of the follow-up below) |

**Gap, named:** the rest of §4's `strategies` edges, and general `strategies` support in `state/transition.py`, are **not** delivered — see Follow-ups. `specs/acceptance.md`'s "Lifecycle: illegal strategy transitions raise (state machine per §4)" is therefore only partially met (one edge raises correctly on illegal states; the table as a whole has no machine).

### Placeholder scan

No TBD/TODO. Every code step shows the code. `"pd.DataFrame"` in the handler annotation is a deliberate string annotation, not a placeholder (`from __future__ import annotations` at `fund_server.py:15`); pandas is imported only inside the handler body, transitively, by the deferred `fundbt` imports.

### Type consistency across hops

- `advance_to_backtest(conn, spec_id, *, expected_state_version: int, now_iso: str) -> bool` — identical in T1's definition, T1's tests, and T2's handler call (`expected_state_version=row["state_version"]`, an `int` from `state_version INTEGER NOT NULL DEFAULT 0`).
- `handle_run_backtest(conn, *, seat, args, now_iso, close_provider)` — identical in T2's definition and `_run` helper. Return `{"ok": True, "result": dict}` / `{"ok": False, "error": str}` — every test asserts against `r["ok"]`, `r["result"]`, `r["error"]` only.
- `run_backtest(spec=..., params=req.params, close=..., registry=TrialRegistry(conn), seat=seat, now_iso=now_iso, seed=req.seed)` — keyword-only per `run_backtest.py:121-131`; `req.params` is `dict[str, float|int|str]`, `req.seed` is `int`.
- `append_event(conn, "budget_exhausted", {...}, now_iso)` — literal kind (the AST scan at `tests/test_slackkit.py:784-826` requires a string constant); payload keys `{"seat","spec_id","family","search_budget"}` match the renderer's reads and `BUDGET_EXHAUSTED` in the tests.
- `close_provider` is keyword-only with no default in the handler signature, the `_run` helper, and `test_close_provider_is_a_required_keyword_...`; the test-local `_no_data` raises `LookupError` with the substring `no close-price data`, which the handler catches and the test matches.
- `_check_params(params, ranges) -> str | None` returns exactly the spellings `undeclared_param:<p>`, `param_type:<p>`, `param_out_of_range:<p>`; the five parametrized cases assert those substrings; the handler wraps the reason as `run_backtest refused: <reason>`, the same prefix the engine's `BacktestError` path uses.

### Rulings applied (2026-09-13) — the first draft's eight open questions, closed

1. Step 2 is the handler's in full (`_check_params`), engine spellings reused, `fundbt/` untouched, `params: dict[str, float | int | str]` kept as §3.2 says.
2. `budget_exhausted` → `#research` (one literal in `_render_budget_exhausted` if it ever moves).
3. No `stale_transition` event kind now; `StaleTransition` → tool error.
4. No `build_fund_server` kwarg; `close_provider` is a required keyword of the handler.
5. One happy-path test registers through `handle_submit_strategy_spec`; the golden-fixture tests stay.
6. `fundbt` imports deferred into the handler body, with the reason in the docstring; mutation 1 keeps the import pinned.
7. `StaleTransition` after a successful run → tool error, trial stands, row still SPEC, re-run is cached and re-attempts the edge — one sentence in the handler docstring.
8. `advance_to_backtest` stays the named entry point; the follow-up is the overseer's to file.

### Follow-ups (not this lane)

- **General `strategies` support in `state/transition.py`** — all §4 edges, `KEYS["strategies"] = ("strategy_id",)`, `state` instead of `status`, `state_version` CAS, and the `stale_transition` event kind (with its renderer, since `tests/test_slackkit.py:829` will require one) — filed by the overseer when this lane's PR opens, after #170's `EDGES` change lands. `advance_to_backtest` remains the entry point run_backtest calls.
- **`StrategySpec.param_ranges` is an unvalidated `dict`** (`state/models.py:142`): a range that is not a 3-list (`[lo, hi, step]`) raises `ValueError`/`TypeError` out of `_check_params`' unpacking, and out of the engine's `_neighbor_params` (`run_backtest.py:108`) even without the handler. Registration-time validation belongs to the model, which this lane does not touch (D2's fence).
- **Side observation, left alone:** `contracts.md:441` says only `signal` and `decision` carry a face; `spec_critique` and `strategy_spec` do too (`render.py:296`, `:331`).
