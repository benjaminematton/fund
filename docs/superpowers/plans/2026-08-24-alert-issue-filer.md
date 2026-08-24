# Alert Identity and Issue Filer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every alert a stable machine identity, then file an unmatched alert as a GitHub issue so it becomes tracked work.

**Architecture:** A new `append_alert()` in `slackkit/outbox.py` takes `code` as a required positional and is the only way to write a `kind='alert'` row; all 25 emission sites migrate to it; an AST lint in `make lint` makes that permanent. A new stdlib-only `scripts/file_alert_issues.py` reads those codes, groups them into labels, asks an injected tracker what is already open, and files the rest. Dry run is the default.

**Tech Stack:** Python 3.12, stdlib `ast`/`sqlite3`/`json`/`subprocess`, pytest, `gh` CLI.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-24-alert-issue-filer-design.md`. Do not invent fields.
- **No trading behaviour changes.** Every production edit is at the alert-emission layer.
- **`append_alert` never raises.** It runs on the alert path, frequently inside `except` blocks; a raise there converts "something needs human review" into "the day crashed" and violates invariant 4. All validation is static, in the lint.
- **An alert code is a string literal** matching `^[a-z][a-z0-9_]*$` at the call site. Never an f-string, never a variable — a dynamic code is unbounded and files an issue per run.
- `ticker` is a ticker symbol or absent. **Never** an order id, quantity, seat, timestamp or exception type.
- `scripts/` is not a package. Scripts are argv-driven and stdlib-only; siblings are imported via `sys.path.insert(0, Path(__file__).resolve().parent)` (see `scripts/run_day.py:56,60`) and loaded in tests via `importlib.util.spec_from_file_location` (see `tests/test_audit_day.py:_load_audit`).
- Run `make test` before every commit. Baseline on `4685579` is 1133 passed, 1 skipped.

---

### Task 1: `append_alert` — identity at the raise site

**Files:**
- Modify: `slackkit/outbox.py:19-25`
- Test: `tests/test_slackkit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `append_alert(conn, code: str, text: str, *, now_iso: str, ticker: str | None = None, clears: bool = False, **payload) -> int`. Every later migration task calls exactly this.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_slackkit.py`:

`fund_db` is the existing conftest fixture used throughout `tests/test_slackkit.py`; it carries the
full schema including `events`. Do not add a second events fixture.

```python
def test_append_alert_carries_code_ticker_and_extra_payload(fund_db):
    conn = fund_db
    from slackkit.outbox import append_alert
    rowid = append_alert(conn, "unprotected_position", "NVDA 40 exposed",
                         now_iso="2026-08-24T13:37:54+00:00", ticker="NVDA",
                         accounting={"symbol": "NVDA", "held": 40})
    row = conn.execute("SELECT kind, payload FROM events WHERE id=?",
                       (rowid,)).fetchone()
    assert row["kind"] == "alert"
    assert json.loads(row["payload"]) == {
        "text": "NVDA 40 exposed",
        "code": "unprotected_position",
        "ticker": "NVDA",
        "accounting": {"symbol": "NVDA", "held": 40},
    }


def test_append_alert_omits_absent_ticker_and_clears(fund_db):
    conn = fund_db
    from slackkit.outbox import append_alert
    rowid = append_alert(conn, "pm_timeout", "pm_timeout AAPL — defaulted to hold",
                         now_iso="2026-08-24T13:36:11+00:00")
    payload = json.loads(conn.execute(
        "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
    assert "ticker" not in payload and "clears" not in payload


def test_append_alert_marks_a_clearing_alert(fund_db):
    conn = fund_db
    from slackkit.outbox import append_alert
    rowid = append_alert(conn, "accounting_shortfall", "NVDA agrees again at 40",
                         now_iso="2026-08-25T13:00:00+00:00", ticker="NVDA",
                         clears=True)
    payload = json.loads(conn.execute(
        "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
    assert payload["clears"] is True
```


- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_slackkit.py -k append_alert -v`
Expected: FAIL with `ImportError: cannot import name 'append_alert'`

- [ ] **Step 3: Write minimal implementation**

Replace `slackkit/outbox.py:19-25` with:

```python
def _insert(conn: sqlite3.Connection, kind: str, payload: dict,
            now_iso: str) -> int:
    cur = conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), now_iso))
    conn.commit()
    return cur.lastrowid


def append_event(conn: sqlite3.Connection, kind: str, payload: dict,
                 now_iso: str) -> int:
    return _insert(conn, kind, payload, now_iso)


def append_alert(conn: sqlite3.Connection, code: str, text: str, *,
                 now_iso: str, ticker: str | None = None,
                 clears: bool = False, **payload) -> int:
    """Append an alert carrying a stable machine identity.

    `code` is what scripts/file_alert_issues.py keys a GitHub issue on, so it
    must be identical across runs: never interpolate a ticker, order id,
    quantity or exception type into it. `ticker` is the only permitted
    per-entity key, and only where fixing one position would not fix another.

    Deliberately validates nothing. This runs on the alert path, often inside
    an `except`, and a raise here would turn "something needs review" into a
    dead trading day (invariant 4). scripts/check_alert_codes.py enforces the
    code's shape statically instead.
    """
    body: dict = {"text": text, "code": code, **payload}
    if ticker is not None:
        body["ticker"] = ticker
    if clears:
        body["clears"] = True
    return _insert(conn, "alert", body, now_iso)
```

Routing `append_alert` through `_insert` rather than `append_event` is what lets Task 5's lint ban `append_event(..., "alert", ...)` with **no exemption for its own definition site**.

- [ ] **Step 4: Run the full suite**

Run: `make test`
Expected: 1136 passed, 1 skipped. `append_event`'s behaviour is unchanged, so nothing else moves.

- [ ] **Step 5: Commit**

```bash
git add slackkit/outbox.py tests/test_slackkit.py
git commit -m "feat: an alert can carry a stable code, not just prose"
```

---

### Task 2: Migrate `orchestrator/reconcile.py` (10 sites)

**Files:**
- Modify: `orchestrator/reconcile.py:77,90,149,156,161,169,176,193,207,268`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `append_alert` from Task 1.
- Produces: codes `fill_on_unapproved_decision`, `partial_fill_manual_review`, `order_unreconciled`, `order_unfilled_at_cap`, `order_partial_then_dead`, `order_unresolved_at_cap`. No `ticker` on any of them.

- [ ] **Step 1: Write the failing test**

Every one of these paths already has a passing test that drives it. **Add a code assertion to the
existing test rather than re-driving the path** — duplicating the arrange logic is how the two copies
drift. Add this helper at the top of `tests/test_reconcile.py`:

```python
def _codes(conn):
    return [json.loads(r["payload"]).get("code") for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]
```

Then add exactly one assertion to the end of each of these existing tests:

| Existing test in `tests/test_reconcile.py` | Assertion to add |
|---|---|
| `test_fill_with_decision_not_approved_alerts_and_does_not_transition` (:192) | `assert _codes(fund_db) == ["fill_on_unapproved_decision"]` |
| `test_partial_fill_left_submitted_with_alert` (:56) | `assert _codes(fund_db) == ["partial_fill_manual_review"]` |
| `test_broker_error_fails_closed_no_transition` (:70) | `assert _codes(fund_db) == ["order_unreconciled"]` |
| `test_malformed_fill_none_values_leaves_submitted_no_transition` (:81) | `assert _codes(fund_db) == ["order_unreconciled"]` |
| `test_cancel_error_and_still_open_leaves_order_submitted` (:252) | `assert _codes(fund_db) == ["order_unreconciled"]` |
| `test_requery_error_after_cancel_leaves_order_submitted` (:268) | `assert _codes(fund_db) == ["order_unreconciled"]` |
| `test_pending_cancel_is_not_a_confirmed_cancel` (:288) | `assert _codes(fund_db) == ["order_unreconciled"]` |
| `test_never_fills_within_cap_decision_failed_alert` (:38) | `assert _codes(fund_db) == ["order_unfilled_at_cap"]` |
| `test_partial_then_canceled_records_the_shares_held` (:341) | `assert _codes(fund_db) == ["order_partial_then_dead"]` |
| `test_broker_unreachable_at_cap_alerts_loudly` (:114) | `assert _codes(fund_db) == ["order_unresolved_at_cap"]` |

The five `order_unreconciled` rows are the point: five reasons, one condition, one issue. If any of
those five comes back as a distinct code, the migration is wrong.

Then add one new test:

```python
def test_no_reconcile_alert_carries_a_ticker_key(fund_db, sim_clock):
    """An order id or symbol as a key here would file an issue per order,
    forever. Driven through the cheapest alerting path in this file."""
    _submitted_order(fund_db, sim_clock.now())
    _poll(fund_db, sim_clock, _Unreachable())      # the broker used at :114
    payloads = [json.loads(r["payload"]) for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert payloads and all("ticker" not in p for p in payloads)
```

Use whichever unreachable-broker class `test_broker_unreachable_at_cap_alerts_loudly` already
constructs; do not add a second one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_reconcile.py -k "code or ticker_key" -v`
Expected: FAIL — `_codes` returns `[None, ...]`, because the payloads carry no `code` yet.

- [ ] **Step 3: Rewrite each call**

Change the import at the top of `orchestrator/reconcile.py` from `append_event` to `append_alert`, then convert each site. The text is unchanged in every case; only the call shape changes. Site 77:

```python
                    append_alert(conn, "fill_on_unapproved_decision",
                        f"order {coid[:8]} filled but decision "
                        f"{t['decision_id']} was '{dec_status}', not "
                        "'approved' — left as-is, manual review",
                        now_iso=now)
```

Site 90 → `partial_fill_manual_review`. Sites 149, 156, 161, 169, 176 → all `order_unreconciled`, keeping their distinct `stale.format(why=…)` text. Site 193 → `order_unfilled_at_cap`. Site 207 → `order_partial_then_dead`. Site 268 → `order_unresolved_at_cap`.

- [ ] **Step 4: Run the suite**

Run: `make test`
Expected: all green. Existing reconcile tests assert on alert **text**, which is unchanged.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/reconcile.py tests/test_reconcile.py
git commit -m "feat: reconcile's alerts say which condition they are"
```

---

### Task 3: Migrate `daily.py`, `preconditions.py`, `protection.py` (7 sites)

**Files:**
- Modify: `orchestrator/daily.py:139,216,337`, `orchestrator/preconditions.py:56`, `orchestrator/protection.py:196,353,360`
- Test: `tests/test_daily_stages.py`, `tests/test_preconditions.py`, `tests/test_protection.py`

**Interfaces:**
- Consumes: `append_alert` from Task 1.
- Produces: `gate_error` (ticker), `pm_timeout` (no ticker), `ticket_open_after_exec`, `account_precondition_drift`, `unprotected_position` (ticker), `accounting_shortfall` (ticker, and `clears=True` on the clearing path), `accounting_unverified`.

- [ ] **Step 1: Write the failing tests**

Same rule as Task 2: assert on the existing test that already drives the path.

> **The test→code tables below are a starting guess, not verified fact.** Task 2's equivalent table
> had three wrong entries: two tests whose broker raises in the main poll loop never reach the code
> path the table claimed, and one test fires two alerts rather than one. **Trace the actual control
> flow, assert what really fires, and report every correction** — the site→code mapping by line is
> authoritative, the which-test-hits-it mapping is not.

**`tests/test_daily_stages.py`** — reuse the `_codes` helper shape from Task 2 (define it locally in
this file too; the two files do not share a helper module):

| Existing test | Assertion to add |
|---|---|
| `test_pre_gate_drops_garbage_inputs` (:78) | `assert _codes(fund_db) == ["gate_error"]` and the payload's `ticker` equals the dropped ticker |
| `test_decision_timeout_defaults_hold_with_event` (:205) | `assert _codes(fund_db) == ["pm_timeout"]` and `"ticker" not in payload` |

Then one new test in `tests/test_daily_stages.py`, which is the one that actually pins the keying
rule:

```python
def test_pm_timeout_on_three_tickers_is_one_code_with_no_ticker_key(fund_db, sim_clock):
    """One root cause — the PM did not answer. A ticker key here would file
    three issues for one silence."""
    for ticker in ("AAPL", "MSFT", "NVDA"):
        _seed_decision(fund_db, sim_clock, ticker, "hold", 0)
    ctx = _ctx(fund_db, sim_clock, _nvda_inputs(), turns=None)
    run_decision(ctx)                      # the PM turn that never answers
    payloads = [json.loads(r["payload"]) for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]
    assert [p["code"] for p in payloads] == ["pm_timeout"] * 3
    assert all("ticker" not in p for p in payloads)
```

Build the three-ticker arrange with `_seed_decision` / `_ctx` / `_nvda_inputs` exactly as
`test_decision_timeout_defaults_hold_with_event` does; only the ticker count changes.

**`tests/test_protection.py`** — this file already has an `_alerts(conn)` helper (:13); read `code`
off the payloads it returns.

| Existing test | Assertion to add |
|---|---|
| `test_a_promised_stop_that_is_gone_alerts` (:113) | code is `unprotected_position`, `ticker == "NVDA"` |
| `test_a_stop_for_fewer_shares_than_held_alerts` (:185) | code is `unprotected_position`, `ticker == "NVDA"` |
| `test_a_covered_symbol_and_a_naked_one_alert_exactly_once` (:213) | exactly one alert, and its `ticker` is the naked symbol — **not** the covered one |
| `test_a_missing_broker_alerts` (:344) | code is `accounting_unverified`, `"ticker" not in payload` |

Then one new test for the clearing path, which no existing test covers:

```python
def test_the_accounting_clearing_alert_is_marked_clears(fund_db):
    """assert_positions_accounted's clear path must be distinguishable, or
    the filer reads a resolution as a new finding."""
    # arrange a standing shortfall exactly as the accounting tests do, then a
    # broker that agrees again, and run assert_positions_accounted twice
    payload = json.loads(_alerts(fund_db)[-1]["payload"])
    assert payload["code"] == "accounting_shortfall"
    assert payload["clears"] is True
    assert payload["accounting"]["cleared"] is True
```

**`tests/test_preconditions.py`** — add to the existing drift test (the one asserting on the `drift`
payload) that `payload["code"] == "account_precondition_drift"` and that the `drift` dict it already
asserts on is unchanged.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_daily_stages.py tests/test_protection.py tests/test_preconditions.py -k "code or clears or ticker" -v`
Expected: FAIL with `KeyError: 'code'`.

- [ ] **Step 3: Rewrite each call**

`orchestrator/daily.py:139`:

```python
            append_alert(ctx.conn, "gate_error",
                         f"gate_error {ticker} — dropped from"
                         " today's universe (malformed feed)",
                         now_iso=now, ticker=ticker)
```

`daily.py:216` → `pm_timeout`, **no `ticker=`**. `daily.py:337` → `ticket_open_after_exec`, no ticker.

`orchestrator/preconditions.py:56` — the local `alert` closure becomes:

```python
    def alert(text: str, drift: dict) -> int:
        append_alert(conn, "account_precondition_drift", text,
                     now_iso=now_iso, drift=drift)
        return 1
```

`orchestrator/protection.py:196` — the local `alert` closure gains the symbol it already has in scope at each call site; give it a `ticker` parameter and pass the position's symbol:

```python
    def alert(text: str, ticker: str | None = None) -> None:
        append_alert(conn, "unprotected_position", text,
                     now_iso=now_iso, ticker=ticker)
```

`protection.py:353` — `accounting_shortfall`, `ticker=symbol`, and on the clearing branch add `clears=True` (the existing `{"symbol": …, "cleared": True}` accounting dict stays untouched):

```python
    def alert(text: str, accounting: dict) -> None:
        append_alert(conn, "accounting_shortfall", text, now_iso=now_iso,
                     ticker=accounting["symbol"],
                     clears=bool(accounting.get("cleared")),
                     accounting=accounting)
```

`protection.py:360` — the `unverified` closure → `accounting_unverified`, no ticker (it fires when no per-symbol read succeeded).

- [ ] **Step 4: Run the suite**

Run: `make test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/daily.py orchestrator/preconditions.py orchestrator/protection.py tests/
git commit -m "feat: the stage alerts carry a code, and only positions carry a ticker"
```

---

### Task 4: Migrate `scripts/run_day.py` (6 sites) and `agents/runtime.py` (2 sites)

**Files:**
- Modify: `scripts/run_day.py:282,297,341,366,383,428,458`, `agents/runtime.py:114,387`
- Test: `tests/test_run_day.py`, `tests/test_runtime_hooks.py`

**Interfaces:**
- Consumes: `append_alert` from Task 1.
- Produces: `seat_turn_failed`, `exec_turn_violation`, `missing_price_history`, `unmapped_sector`, `audit_failed`, `run_day_failed`, `order_gate_denied`, `cost_unavailable`. None carry a ticker.

- [ ] **Step 1: Write the failing test**

Same rule again: assert on the existing tests that already drive each path — and the same warning as
Task 3: **the table below is a guess. Trace the control flow, assert what actually fires, report
every correction.** The site→code mapping by line is authoritative; the which-test-hits-it mapping
is not.

**`tests/test_run_day.py`:**

| Existing test | Assertion to add |
|---|---|
| `test_a_seat_turn_that_raises_alerts_and_lets_the_stage_default_land` (:503) | code is `seat_turn_failed`, and the text still starts `analyst_turn_failed —` |
| `test_an_exec_tool_call_violation_is_alerted_after_the_cost_is_recorded` (:537) | code is `exec_turn_violation` |
| `test_a_held_ticker_with_no_price_history_is_named_in_an_alert` (:311) | code is `missing_price_history`, and `tickers` is unchanged |
| `test_a_held_ticker_missing_from_sectors_yaml_is_named_in_an_alert` (:355) | code is `unmapped_sector`, and `tickers` is unchanged |
| `test_a_raise_inside_the_day_alerts_slack_and_exits_nonzero` (:225) | code is `run_day_failed` |
| `test_a_drifted_account_setting_alerts_but_does_not_abort_the_day` (:845) | code is `account_precondition_drift` |

The seat-turn one is the case that matters most, so it also gets its own test:

```python
def test_seat_turn_failure_uses_a_literal_code_not_the_seat_name(wired):
    """The seat belongs in the TEXT. A code of f"{seat}_turn_failed" mints a
    new code — and therefore a new issue — for every seat that ever fails."""
    # arrange exactly as test_a_seat_turn_that_raises_alerts... does (:503)
    payload = json.loads(_alerts(wired.conn)[-1]["payload"])
    assert payload["code"] == "seat_turn_failed"
    assert payload["text"].startswith("analyst_turn_failed —")
```

And the rollup, which no existing test asserts the marker on:

```python
def test_the_audit_rollup_keeps_its_self_alert_marker(wired):
    """audit_day.SELF_ALERT_KEY is how BOTH audit_day and the filer avoid
    double-counting the rollup. Migrating must not drop it."""
    # arrange as test_a_zero_ticker_day... (:631) but with a failing audit
    payload = json.loads(_alerts(wired.conn)[-1]["payload"])
    assert payload["code"] == "audit_failed"
    assert payload[audit_day.SELF_ALERT_KEY] is True
```

**`tests/test_runtime_hooks.py`:**

| Existing test | Assertion to add |
|---|---|
| `test_order_gate_deny_appends_one_alert_naming_ticker_reason_and_ticket` (:66) | code is `order_gate_denied` |
| `test_order_gate_deny_alert_survives_malformed_tool_input` (:99) | code is `order_gate_denied` |
| `test_record_turn_result_none_cost_records_nothing_and_alerts` (:402) | code is `cost_unavailable` |

`test_order_gate_deny_survives_an_unrecordable_alert` (:264) must keep passing untouched — it proves
a failed alert write does not undo the denial, and `append_alert` deliberately adds no new raise
path there.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_run_day.py tests/test_runtime_hooks.py -k code -v`
Expected: FAIL with `KeyError: 'code'`.

- [ ] **Step 3: Rewrite `_alert` and each call**

`scripts/run_day.py:341` — the private `_alert` helper gains a code and delegates:

```python
def _alert(conn, clock, code: str, text: str, **payload) -> None:
    log(f"ALERT {text}")
    append_alert(conn, code, text, now_iso=iso(clock.now()), **payload)
```

Then each caller passes its literal code as the third argument: `:282` → `"seat_turn_failed"` (the seat name stays inside the text, which is already `f"{seat}_turn_failed — …"`); `:297` → `"exec_turn_violation"`; `:366` → `"missing_price_history"` (keeps `tickers=gaps`); `:383` → `"unmapped_sector"` (keeps `tickers=gaps`); `:428` → `"audit_failed"`, keeping `**{audit_day.SELF_ALERT_KEY: True}`.

`run_day.py:458` is the last-resort handler inside `guarded` and calls `append_event` directly, deliberately outside `_alert` — convert it to `append_alert(conn, "run_day_failed", text, now_iso=iso(clock.now()))`, keeping its surrounding try/except exactly as it is.

`agents/runtime.py:114` → `append_alert(conn(), "order_gate_denied", alert_text, now_iso=iso(clock.now()))`, leaving the surrounding try/except that logs a failed write. `agents/runtime.py:387` → `cost_unavailable`.

- [ ] **Step 4: Run the suite**

Run: `make test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_day.py agents/runtime.py tests/
git commit -m "feat: the day's own alerts carry codes, and _alert stops being a second way to raise one"
```

---

### Task 5: The lint that makes it permanent

**Files:**
- Create: `scripts/check_alert_codes.py`
- Modify: `Makefile` (the `lint` target)
- Test: `tests/test_markers.py` (or a new `tests/test_alert_codes_lint.py`)

**Interfaces:**
- Consumes: the completed migration from Tasks 2–4.
- Produces: `check_file(path: Path) -> list[str]` and `main() -> int`, matching `scripts/check_purity.py`'s shape exactly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_alert_codes_lint.py`:

```python
"""The lint's own negative controls. A lint whose negative control also
passes is not a lint — this repo has three documented instances of exactly
that, two caught by luck."""
import importlib.util, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_alert_codes.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_alert_codes", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_tree_is_clean():
    assert subprocess.run([sys.executable, str(SCRIPT)]).returncode == 0


def test_a_direct_append_event_alert_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_event(conn, "alert", {"text": "x"}, now)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "append_alert" in errors[0]


def test_an_interpolated_code_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_alert(conn, f"{seat}_turn_failed", "x", now_iso=n)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "string literal" in errors[0]


def test_a_shouty_code_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_alert(conn, "PM Timeout", "x", now_iso=n)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "lower_snake" in errors[0]


def test_a_good_call_is_accepted(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('append_alert(conn, "pm_timeout", "x", now_iso=n)\n')
    assert _load().check_file(p) == []


def test_a_non_alert_append_event_is_accepted(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('append_event(conn, "fill", {"text": "x"}, now)\n')
    assert _load().check_file(p) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_alert_codes_lint.py -v`
Expected: FAIL — `scripts/check_alert_codes.py` does not exist.

- [ ] **Step 3: Write the lint**

```python
#!/usr/bin/env python3
"""Alert-code lint (CI-enforced; see docs/agents/devops.md).

Every kind='alert' row must carry a stable `code`, because that code is what
scripts/file_alert_issues.py keys a GitHub issue on. A site that forgets one
still alerts and still reaches Slack, and is silently invisible to the filer
forever — absence reading as health, the failure shape this repo keeps hitting.

Two checks:
  1. No append_event(..., 'alert', ...) anywhere. Use append_alert().
  2. append_alert's code is a string literal matching ^[a-z][a-z0-9_]*$.
     An f-string code is unbounded and would file an issue per run.

Zero dependencies. Exit 1 on any violation.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ["orchestrator", "agents", "scripts", "slackkit", "gate", "state",
            "market", "evals"]
CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _callee(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", None)


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name == "append_event":
            kind = node.args[1] if len(node.args) > 1 else None
            if isinstance(kind, ast.Constant) and kind.value == "alert":
                errors.append(
                    f"{path}:{node.lineno}: append_event(..., 'alert', ...) —"
                    " use append_alert() so the alert carries a code")
        elif name == "append_alert":
            code = node.args[1] if len(node.args) > 1 else None
            if not isinstance(code, ast.Constant) or not isinstance(code.value, str):
                errors.append(
                    f"{path}:{node.lineno}: append_alert code must be a string"
                    " literal, not an expression — a dynamic code files an"
                    " issue per run")
            elif not CODE_RE.match(code.value):
                errors.append(
                    f"{path}:{node.lineno}: alert code {code.value!r} is not a"
                    " bare lower_snake identifier")
    return errors


def main() -> int:
    errors: list[str] = []
    for package in PACKAGES:
        root = ROOT / package
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            errors.extend(check_file(path))
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        print(f"ALERT CODE LINT: {len(errors)} violation(s)", file=sys.stderr)
        return 1
    print(f"ALERT CODE LINT: clean ({len(PACKAGES)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire it into `make lint`**

In `Makefile`, extend the existing `lint` target:

```make
# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
# Alert-code lint: every alert carries a stable code (docs/agents/devops.md).
lint: deps
	$(PYTHON) scripts/check_purity.py
	$(PYTHON) scripts/check_alert_codes.py
```

- [ ] **Step 5: Run and commit**

Run: `make test`
Expected: `ALERT CODE LINT: clean (8 packages)`, then all tests green.

```bash
git add scripts/check_alert_codes.py tests/test_alert_codes_lint.py Makefile
git commit -m "feat: a site that forgets an alert code fails the build"
```

---

### Task 6: `plan_filings` — all the dedupe logic, no network

**Files:**
- Create: `scripts/file_alert_issues.py`
- Test: `tests/test_file_alert_issues.py`

**Interfaces:**
- Consumes: alert payloads produced by Tasks 2–4; `audit_day.SELF_ALERT_KEY` and `audit_day.et_day_window` via the sibling-import idiom.
- Produces: `Filing` (frozen dataclass with `action: str`, `labels: tuple[str, ...]`, `code: str`, `ticker: str | None`, `title: str`, `body: str`, `issue: int | None = None`), `plan_filings(conn, since: str, tracker, db_path: str = "") -> tuple[list[Filing], list[str]]` returning `(filings, malformed)`, and the tracker protocol `open_issue(labels: tuple[str, ...]) -> int | None`.

**Grouping rule** — this refines spec §3.4 step 5, which was ambiguous about a condition that both fires and clears inside one window:

- Group alerts by their label tuple. A clearing alert never contributes text.
- Group has non-clearing text → an open issue means `skip`, no open issue means `create`. The body notes if it later cleared. *A condition that fired and resolved in the window still files: the symptom clearing is not the defect being fixed.*
- Group has clearing alerts only → an open issue means `comment`; no open issue means nothing at all. Never file an issue to announce a resolution.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_file_alert_issues.py`:

```python
"""The filer's dedupe, against the REAL alert texts from 2026-08-21 and
2026-08-24. Synthetic text would not prove the key survives interpolation,
which is the entire defect this script exists to fix."""
import importlib.util, json, sqlite3, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "file_alert_issues.py"

NVDA_0821 = ("NVDA 80 was ticketed with a stop at 215.0 but the broker covers"
             " only 40 of 80 shares — the position is exposed and no code path"
             " will protect it; place or restore a stop manually")
NVDA_0824 = ("NVDA 40 was ticketed with a stop at 215.0 but the broker has NO"
             " live protective order — the position is exposed and no code path"
             " will protect it; place or restore a stop manually")


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("file_alert_issues", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeTracker:
    """Records every lookup; answers from a fixed open set."""
    def __init__(self, open_issues=None):
        self.open_issues = open_issues or {}      # labels tuple -> issue number
        self.lookups = []

    def open_issue(self, labels):
        self.lookups.append(labels)
        return self.open_issues.get(tuple(labels))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.sqlite"


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT,"
                 " payload TEXT, created_at TEXT, posted_at TEXT)")
    return conn


def _alert(conn, created_at, **payload):
    conn.execute("INSERT INTO events (kind, payload, created_at) VALUES"
                 " ('alert', ?, ?)", (json.dumps(payload), created_at))
    conn.commit()


def test_the_same_condition_with_different_text_files_once(db):
    """THE defect. Both texts are verbatim from the production DBs."""
    _alert(db, "2026-08-21T13:38:02+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0821)
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    filings, _ = _load().plan_filings(db, "2026-08-21", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:unprotected_position", "ticker:NVDA")
    assert NVDA_0821 in filings[0].body and NVDA_0824 in filings[0].body


def test_negative_control_two_codes_file_two_issues(db):
    """If this passed with one issue, the test above would prove nothing."""
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    _alert(db, "2026-08-24T13:37:54+00:00", code="accounting_shortfall",
           ticker="NVDA", text="NVDA: recorded 80, broker holds 40")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert [f.action for f in filings] == ["create", "create"]


def test_pm_timeout_on_three_tickers_is_one_issue(db):
    for t in ("AAPL", "MSFT", "NVDA"):
        _alert(db, "2026-08-18T13:36:11+00:00", code="pm_timeout",
               text=f"pm_timeout {t} — defaulted to hold")
    filings, _ = _load().plan_filings(db, "2026-08-18", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:pm_timeout",)


def test_two_tickers_of_one_code_are_two_issues(db):
    for t in ("NVDA", "MSFT"):
        _alert(db, "2026-08-24T13:00:00+00:00", code="unprotected_position",
               ticker=t, text=f"{t} exposed")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert sorted(f.labels[1] for f in filings) == ["ticker:MSFT", "ticker:NVDA"]


def test_an_already_open_issue_files_nothing(db):
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    tracker = FakeTracker({("alert:unprotected_position", "ticker:NVDA"): 41})
    filings, _ = _load().plan_filings(db, "2026-08-24", tracker)
    assert [f.action for f in filings] == ["skip"]
    assert filings[0].issue == 41


def test_a_closed_issue_does_not_suppress_a_recurrence(db):
    """Only OPEN issues match, so a recurrence gets a fresh issue."""
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker({}))
    assert [f.action for f in filings] == ["create"]


def test_a_clearing_alert_comments_and_never_closes(db):
    _alert(db, "2026-08-25T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    tracker = FakeTracker({("alert:accounting_shortfall", "ticker:NVDA"): 42})
    filings, _ = _load().plan_filings(db, "2026-08-25", tracker)
    assert [f.action for f in filings] == ["comment"]
    assert filings[0].issue == 42
    assert all(f.action != "close" for f in filings)


def test_a_clearing_alert_with_no_open_issue_does_nothing(db):
    _alert(db, "2026-08-25T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    filings, _ = _load().plan_filings(db, "2026-08-25", FakeTracker())
    assert filings == []


def test_a_condition_that_fired_and_cleared_in_one_window_still_files(db):
    """The symptom cleared; the code defect did not."""
    _alert(db, "2026-08-24T13:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", text="NVDA: recorded 80, broker holds 40")
    _alert(db, "2026-08-24T15:00:00+00:00", code="accounting_shortfall",
           ticker="NVDA", clears=True, text="NVDA agrees again at 40")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert [f.action for f in filings] == ["create"]


def test_the_audit_rollup_never_files(db):
    _alert(db, "2026-08-24T13:38:01+00:00", code="audit_failed",
           audit_report=True, text="audit 2026-08-24 FAILED: alert events raised: 2")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert filings == []


def test_a_payload_with_no_code_is_reported_not_guessed(db):
    _alert(db, "2026-08-24T13:00:00+00:00", text="something old and codeless")
    _alert(db, "2026-08-24T13:01:00+00:00", code="pm_timeout", text="pm_timeout AAPL")
    filings, malformed = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert len(malformed) == 1 and "codeless" in malformed[0]
    assert [f.action for f in filings] == ["create"]      # the rest still process


def test_alerts_before_the_since_date_are_ignored(db):
    _alert(db, "2026-08-20T13:00:00+00:00", code="pm_timeout", text="old")
    filings, _ = _load().plan_filings(db, "2026-08-24", FakeTracker())
    assert filings == []


def test_a_changing_order_id_does_not_file_a_new_issue_each_day(db):
    """ticket_open_after_exec embeds a fresh order id every run. Keyed on the
    id it would file one issue per trading day, forever — the single worst
    outcome available here."""
    _alert(db, "2026-08-20T13:38:23+00:00", code="ticket_open_after_exec",
           text="ticket c0a9ae97 open after exec turn — no order")
    _alert(db, "2026-08-24T13:38:23+00:00", code="ticket_open_after_exec",
           text="ticket 7f31b204 open after exec turn — no order")
    filings, _ = _load().plan_filings(db, "2026-08-20", FakeTracker())
    assert [f.action for f in filings] == ["create"]
    assert filings[0].labels == ("alert:ticket_open_after_exec",)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_file_alert_issues.py -v`
Expected: FAIL — the script does not exist.

- [ ] **Step 3: Write the module**

```python
#!/usr/bin/env python3
"""File an unmatched alert as a GitHub issue (docs/agents/devops.md).

Zero dependencies (stdlib only) and argv-driven, so it runs against a live or
backed-up DB with nothing installed:

    python3 scripts/file_alert_issues.py state/fund.sqlite --since 2026-08-21
    python3 scripts/file_alert_issues.py state/fund.sqlite --since 2026-08-21 --apply

DRY RUN IS THE DEFAULT. Filing is the only irreversible act here — an issue can
be closed but not un-filed — so producing issues needs an explicit --apply.

This is not a detector. It never decides whether a condition is true; it reads
alerts the software already raised and asks the tracker what is already open.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling audit_day
import audit_day                                           # noqa: E402

MAX_TITLE = 110


@dataclass(frozen=True)
class Filing:
    action: str                  # "create" | "skip" | "comment"
    labels: tuple[str, ...]
    code: str
    ticker: str | None
    title: str
    body: str
    issue: int | None = None


def _title(code: str, ticker: str | None, text: str) -> str:
    head = f"{code}: " + (f"{ticker} — " if ticker else "")
    room = MAX_TITLE - len(head)
    return head + (text if len(text) <= room else text[:room - 1] + "…")


def _body(code, ticker, occurrences, cleared, db_path) -> str:
    lines = [f"Filed automatically from `{db_path}` by "
             "`scripts/file_alert_issues.py`.", "",
             f"- **code:** `{code}`"]
    if ticker:
        lines.append(f"- **ticker:** `{ticker}`")
    lines += [f"- **occurrences in window:** {len(occurrences)}", ""]
    if cleared:
        lines += ["> A clearing alert arrived for this condition. The symptom"
                  " resolved; whether the underlying defect is fixed is a"
                  " human judgement.", ""]
    lines.append("### Alert text seen")
    for created_at, text in occurrences:
        lines.append(f"- `{created_at}` — {text}")
    return "\n".join(lines)


def plan_filings(conn, since: str, tracker, db_path: str = "") -> tuple[list[Filing], list[str]]:
    start, _ = audit_day.et_day_window(since)
    rows = conn.execute(
        "SELECT payload, created_at FROM events WHERE kind = 'alert'"
        " AND created_at >= ? ORDER BY id", (start,)).fetchall()

    groups: dict[tuple[str, ...], dict] = {}
    malformed: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            malformed.append(f"{row['created_at']}: unparseable payload")
            continue
        if payload.get(audit_day.SELF_ALERT_KEY):
            continue                       # the rollup restates the others
        code = payload.get("code")
        if not code:
            malformed.append(f"{row['created_at']}: no code — "
                             f"{payload.get('text', '')[:80]}")
            continue
        ticker = payload.get("ticker")
        labels = (f"alert:{code}",) + ((f"ticker:{ticker}",) if ticker else ())
        group = groups.setdefault(labels, {"code": code, "ticker": ticker,
                                           "occurrences": [], "cleared": False})
        if payload.get("clears"):
            group["cleared"] = True
        else:
            group["cleared"] = False       # a fresh firing reopens the finding
            group["occurrences"].append((row["created_at"], payload.get("text", "")))

    filings: list[Filing] = []
    for labels, g in groups.items():
        existing = tracker.open_issue(labels)
        body = _body(g["code"], g["ticker"], g["occurrences"], g["cleared"], db_path)
        if not g["occurrences"]:
            # Nothing but clearing alerts. Comment if something is tracking
            # it; never file an issue to announce that a problem went away.
            if existing is not None:
                filings.append(Filing("comment", labels, g["code"], g["ticker"],
                                      "", body, existing))
            continue
        title = _title(g["code"], g["ticker"], g["occurrences"][0][1])
        action = "skip" if existing is not None else "create"
        filings.append(Filing(action, labels, g["code"], g["ticker"], title,
                              body, existing))
    return filings, malformed
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_file_alert_issues.py -v`
Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add scripts/file_alert_issues.py tests/test_file_alert_issues.py
git commit -m "feat: one condition is one issue, however its wording changed"
```

---

### Task 7: The `gh` tracker, and the `--apply` path

**Files:**
- Modify: `scripts/file_alert_issues.py`
- Test: `tests/test_file_alert_issues.py`

**Interfaces:**
- Consumes: `Filing` and `plan_filings` from Task 6.
- Produces: `GhTracker(repo: str, run=subprocess.run)` implementing `open_issue`, plus `ensure_label`, `create_issue`, `comment_issue`; and `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_file_alert_issues.py`:

```python
class RecordingRun:
    """Stands in for subprocess.run. Records argv, answers from a script."""
    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        key = " ".join(argv[:3])
        out = self.replies.get(key, "[]")
        class R: returncode, stdout, stderr = 0, out, ""
        return R()


def test_dry_run_performs_no_mutation(db, db_path, capsys):
    """Seeded with an alert that WOULD file — otherwise 'no mutating calls'
    passes vacuously and proves nothing."""
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    db.close()
    run = RecordingRun()
    rc = _load().main([str(db_path), "--since", "2026-08-24"], run=run)
    assert rc == 0
    assert "would file" in capsys.readouterr().out      # it had work to skip
    mutating = [c for c in run.calls
                if "create" in c or "comment" in c or "label" in c]
    assert mutating == []          # asserted, not assumed


def test_apply_creates_the_label_before_the_issue(db, db_path):
    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    db.close()
    mod = _load()
    run = RecordingRun()
    mod.main([str(db_path), "--since", "2026-08-24", "--apply"], run=run)
    joined = [" ".join(c) for c in run.calls]
    label_at = next(i for i, c in enumerate(joined) if "label create" in c)
    issue_at = next(i for i, c in enumerate(joined) if "issue create" in c)
    assert label_at < issue_at


def test_gh_failure_is_reported_and_never_retried(db, db_path, capsys):
    """A retry with a fresh id is how one condition becomes two issues."""
    class FailingRun(RecordingRun):
        def __call__(self, argv, **kw):
            self.calls.append(argv)
            class R:
                returncode = 0 if "list" in argv else 1
                stdout, stderr = "[]", "gh: HTTP 403"
            return R()

    _alert(db, "2026-08-24T13:37:54+00:00", code="unprotected_position",
           ticker="NVDA", text=NVDA_0824)
    db.close()
    run = FailingRun()
    rc = _load().main([str(db_path), "--since", "2026-08-24", "--apply"], run=run)
    assert rc != 0
    assert "FAILED" in capsys.readouterr().err
    creates = [c for c in run.calls if "create" in c and "issue" in c]
    assert len(creates) == 1          # reported once, never retried


def test_an_unreadable_db_exits_non_zero_having_filed_nothing(tmp_path, capsys):
    run = RecordingRun()
    rc = _load().main([str(tmp_path / "nope.sqlite"), "--since", "2026-08-24",
                       "--apply"], run=run)
    assert rc != 0
    assert run.calls == []
```


- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python3 -m pytest tests/test_file_alert_issues.py -k "dry_run or apply or gh_failure" -v`
Expected: FAIL — `main` does not exist.

- [ ] **Step 3: Implement `GhTracker` and `main`**

Append to `scripts/file_alert_issues.py`. `run` is injected everywhere so the tests never touch the network:

```python
class GhTracker:
    """The tracker, over the `gh` CLI (docs/agents/issue-tracker.md)."""

    def __init__(self, repo: str, run=None):
        import subprocess
        self._run = run or subprocess.run
        self.repo = repo

    def _gh(self, *args: str) -> str:
        r = self._run(["gh", *args, "--repo", self.repo],
                      capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    def open_issue(self, labels):
        argv = ["issue", "list", "--state", "open", "--json", "number"]
        for label in labels:
            argv += ["--label", label]
        found = json.loads(self._gh(*argv) or "[]")
        return found[0]["number"] if found else None

    def ensure_label(self, label: str) -> None:
        self._gh("label", "create", label, "--force")

    def create_issue(self, title, body, labels) -> None:
        argv = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            argv += ["--label", label]
        self._gh(*argv)

    def comment_issue(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)


def main(argv: list[str] | None = None, run=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--since", required=True, help="ET calendar date YYYY-MM-DD")
    ap.add_argument("--repo", default="benjaminematton/fund")
    ap.add_argument("--apply", action="store_true",
                    help="actually file; without it this is a dry run")
    args = ap.parse_args(argv)

    # sqlite3.connect CREATES a missing file rather than raising, so an
    # unreadable DB surfaces as "no such table: events" on the first query,
    # not on connect. Both are caught below and both file nothing.
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    tracker = GhTracker(args.repo, run=run)
    try:
        filings, malformed = plan_filings(conn, args.since, tracker, args.db)
    except RuntimeError as e:
        print(f"tracker unavailable: {e}", file=sys.stderr)
        return 1
    except sqlite3.Error as e:
        print(f"cannot read {args.db}: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    for m in malformed:
        print(f"MALFORMED (not filed): {m}", file=sys.stderr)

    failed = 0
    for f in filings:
        if f.action == "skip":
            print(f"already tracked as #{f.issue}: {' '.join(f.labels)}")
            continue
        verb = "filing" if args.apply else "would file"
        if f.action == "comment":
            print(f"{verb} a comment on #{f.issue}: cleared {f.code}")
        else:
            print(f"{verb}: {f.title}  [{' '.join(f.labels)}]")
        if not args.apply:
            continue
        try:
            if f.action == "comment":
                tracker.comment_issue(f.issue, f.body)
            else:
                for label in f.labels:
                    tracker.ensure_label(label)
                tracker.create_issue(f.title, f.body, f.labels)
        except RuntimeError as e:
            # Reported, never retried: a retry with a fresh id is how you get
            # two issues for one condition.
            print(f"FAILED {f.action} for {' '.join(f.labels)}: {e}",
                  file=sys.stderr)
            failed += 1

    if not filings and not malformed:
        print(f"no alerts needing an issue since {args.since}")
    return 1 if (failed or malformed) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full suite**

Run: `make test`
Expected: all green, lint clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/file_alert_issues.py tests/test_file_alert_issues.py
git commit -m "feat: an alert nobody tracked becomes an issue somebody can see"
```

---

### Task 8: Verification against real data (spec §7)

**Files:** none changed. This produces evidence, not code.

- [ ] **Step 1: Dry run against a backup with known alerts**

```bash
.venv/bin/python3 scripts/file_alert_issues.py \
    backups-from-vm/fund-2026-08-20.sqlite --since 2026-08-18
```

Expected: the 2026-08-18 `pm_timeout` trio resolves to **one** would-file line, the `analyst_turn_failed` / `pm_turn_failed` pair to `seat_turn_failed`, and the `audit 2026-08-18 FAILED` rollup appears **nowhere**. Rows predating the migration carry no `code` and must print as `MALFORMED (not filed)` — that is correct behaviour on historical data, not a bug.

- [ ] **Step 2: Dry run against a copy of the droplet DB**

Copy it down read-only first (production reads are unrestricted; **do not** run this against the droplet's live file):

```bash
scp root@138.197.47.97:/var/lib/fund/fund.sqlite /tmp/droplet-fund.sqlite
.venv/bin/python3 scripts/file_alert_issues.py /tmp/droplet-fund.sqlite --since 2026-08-21
```

Expected: the NVDA condition present on **both** 08-21 and 08-24 resolves to a single would-file line. Same caveat on pre-migration rows.

- [ ] **Step 3: Record the output in the PR body.** A green suite is not evidence the key survives real interpolated text; this output is.

- [ ] **Step 4: Do not run `--apply` yet.** First real filing is Benjamin's call — it writes to the public tracker.

---

## Not in this plan

- **`ops/fund-daily.service`'s heartbeat** (`ExecStopPost` carrying `${EXIT_STATUS}`). Separate branch: it lands by droplet deploy, not by merge, and `tests/test_ops_units.py` is where its test goes.
- **`morning-standup` / `/eod-digest` reading these issues.** Downstream of this plan and specced in `docs/superpowers/specs/2026-08-21-day-bookends-design.md`.
- **Backfilling codes onto historical alert rows.** They stay malformed-and-reported. Rewriting `events` history to make a report look tidy is not worth touching the source of truth for.
