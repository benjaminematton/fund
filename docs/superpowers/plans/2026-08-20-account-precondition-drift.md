# Account Precondition Drift Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert when the broker's account settings drift from a checked-in baseline, so a changed precondition is named rather than surfacing as an unexplained `gate_error`.

**Architecture:** A broker read (`AlpacaSource.account_config()`) and a checked-in baseline (`config/account_config_baseline.yaml`) are diffed by a new pure-ish module (`orchestrator/preconditions.py`), which appends one `alert` event per difference and returns the count. `scripts/run_day.py` loads the baseline and calls the assertion before `run_day(...)`. Nothing is added to `gate/`.

**Tech Stack:** Python 3.12, pydantic v2, pytest, PyYAML, alpaca-py 0.44.0, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-20-account-precondition-drift-design.md`

## Global Constraints

- **Paper only.** `ALPACA_PAPER_TRADE=true`. Never add a live-trading path, flag, or TODO.
- **`gate/` gains nothing.** No imports, no new files, no network call. `scripts/check_purity.py` must not be edited.
- **Default is HOLD / alert.** Every error, unreachable broker, or malformed payload alerts. No branch may pass silently.
- **Alert-only.** Nothing in this plan may block, halt, or abort a trading day.
- **Never weaken a test or update a golden fixture to go green.** Stop and ask.
- **Conventional commits.** Never write `Co-Authored-By` or any AI attribution in a commit or PR body.
- **Baseline before every commit:** `make test` → 1105 passed, 1 skipped, 7 deselected at `894e1b8`. The count only grows.
- Events are appended via `slackkit.outbox.append_event(conn, kind, payload, now_iso)` using the existing `"alert"` kind. Do not add a new event kind.

---

## File Structure

| File | Responsibility |
|---|---|
| `orchestrator/preconditions.py` | **Create.** Diff baseline vs. broker payload, append alerts, return count. The only new logic. |
| `tests/test_preconditions.py` | **Create.** Drift classes, silence on match, broker failure. |
| `market/source_alpaca.py` | **Modify.** Add `account_config()` — the live broker read. |
| `tests/fake_alpaca.py` | **Modify.** Add `account_config()` so offline runs have the surface. |
| `config/account_config_baseline.yaml` | **Create.** The pinned payload. Populated from a real read in Task 5. |
| `scripts/run_day.py` | **Modify.** Load the baseline, call the assertion before `run_day(...)`. |

Task order is dependency order: the assertion (1) is testable with a plain dict before either broker side exists; the fake (2) and the live read (3) are independent of each other; wiring (4) needs 1–3; the real capture (5) is the only non-offline step and lands last.

---

### Task 1: The drift assertion

**Files:**
- Create: `orchestrator/preconditions.py`
- Test: `tests/test_preconditions.py`

**Interfaces:**
- Consumes: `slackkit.outbox.append_event(conn, kind: str, payload: dict, now_iso: str) -> int`
- Produces: `assert_account_config_unchanged(conn, *, broker, baseline: dict, now_iso: str) -> int` — returns the number of alerts appended. `broker` is any object with `account_config() -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preconditions.py`:

```python
"""Account precondition drift (2026-08-20 design)."""
import json
import sqlite3

import pytest

from orchestrator.preconditions import assert_account_config_unchanged
from state.db import connect

NOW = "2026-08-20T09:00:00-04:00"
BASE = {"no_shorting": True, "suspend_trade": False, "max_margin_multiplier": "1"}


class _Broker:
    def __init__(self, payload):
        self._payload = payload

    def account_config(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _alerts(conn) -> list[str]:
    rows = conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id").fetchall()
    return [json.loads(r["payload"])["text"] for r in rows]


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "t.sqlite"))
    yield c
    c.close()


def test_exact_match_is_silent(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker(dict(BASE)), baseline=BASE, now_iso=NOW)
    assert n == 0
    assert _alerts(conn) == []


def test_changed_field_alerts_naming_old_and_new(conn):
    drifted = dict(BASE, no_shorting=False)
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    text = _alerts(conn)[0]
    assert "no_shorting" in text and "True" in text and "False" in text


def test_one_alert_per_drifted_field(conn):
    drifted = dict(BASE, no_shorting=False, suspend_trade=True)
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 2
    assert len(_alerts(conn)) == 2


def test_field_missing_from_payload_alerts(conn):
    """Alpaca removed a setting the baseline pins."""
    drifted = {k: v for k, v in BASE.items() if k != "suspend_trade"}
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "suspend_trade" in _alerts(conn)[0]


def test_field_new_in_payload_alerts(conn):
    """Alpaca added a setting the baseline does not pin — the case a
    hand-written enumeration would miss."""
    drifted = dict(BASE, dtbp_check="entry")
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "dtbp_check" in _alerts(conn)[0]


def test_broker_failure_alerts_and_does_not_raise(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker(RuntimeError("boom")), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "RuntimeError" in _alerts(conn)[0]


def test_unparseable_payload_alerts(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker("not-a-dict"), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert _alerts(conn)


def test_empty_baseline_alerts_rather_than_passing(conn):
    """A baseline that failed to load must never read as 'nothing drifted'."""
    n = assert_account_config_unchanged(
        conn, broker=_Broker(dict(BASE)), baseline={}, now_iso=NOW)
    assert n == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preconditions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.preconditions'`

- [ ] **Step 3: Write the implementation**

Create `orchestrator/preconditions.py`:

```python
"""Assertion: the broker's account settings still match the pinned baseline
(2026-08-20 design).

Invariant 3 locks the gate's thresholds, but they stand on account settings
`gate/risk.py` never reads. No LLM seat can move them — agents/runtime.py's
_broker_verb_policy denies update_account_config — but a human at the Alpaca
dashboard or Alpaca itself still can.

This detects, it does not control. Every limb of the envelope already fails
closed: sizing is bounded by min(dollar, cash) rather than buying power, and
the sell branch caps at held_qty so the gate cannot open a short. What was
missing is ATTRIBUTION — a cleared no_shorting surfaces as `gate_error`, which
names nothing. So drift alerts and the day continues.

The whole payload is diffed, not a list of interesting settings. Enumeration is
the failure mode this repo keeps hitting: the exec verb surface was counted
4 -> 5 -> 7 -> 8 by four sessions in one afternoon, and the hand-written
setting list in config/broker_tool_surface.yaml is already wrong against pinned
alpaca-py 0.44.0. A field Alpaca adds later must redden a check, not pass
unseen.

Not a stage: an assertion must re-check on a resumed day rather than be skipped
as 'done', and a duplicate alert is the safe direction.

Every ambiguity alerts (invariant 4) — an unreachable broker, an unreadable
payload, an empty baseline. A check that can pass while lying is worse than no
check at all."""
from __future__ import annotations

import sqlite3

from slackkit.outbox import append_event


def assert_account_config_unchanged(conn: sqlite3.Connection, *, broker,
                                    baseline: dict, now_iso: str) -> int:
    """Alert on every difference between `baseline` and the broker's account
    configuration. Returns the number of alerts appended.

    Never raises and never blocks: this runs on the critical path of a live
    trading day, and a precondition that is merely *different* is not grounds
    to refuse to trade."""

    def alert(text: str, drift: dict) -> int:
        append_event(conn, "alert", {"text": text, "drift": drift}, now_iso)
        return 1

    if not baseline:
        # Distinct from "nothing drifted". An unloadable or empty baseline
        # would otherwise make this function silently unable to fail.
        return alert(
            "account config: baseline is empty — the drift check could not"
            " run, so today's account settings are unverified",
            {"reason": "empty_baseline"})

    try:
        actual = broker.account_config()
    except Exception as e:                       # noqa: BLE001 — every failure alerts
        return alert(
            f"account config: could not read from the broker —"
            f" {type(e).__name__}: {str(e)[:120]}",
            {"reason": "unreadable"})

    if not isinstance(actual, dict):
        return alert(
            f"account config: broker returned {type(actual).__name__},"
            " expected a mapping — settings are unverified",
            {"reason": "malformed"})

    n = 0
    for field in sorted(set(baseline) | set(actual)):
        if field not in actual:
            n += alert(
                f"account config: `{field}` is pinned to"
                f" {baseline[field]!r} but the broker no longer reports it",
                {"field": field, "expected": baseline[field], "actual": None})
        elif field not in baseline:
            n += alert(
                f"account config: broker reports `{field}` ="
                f" {actual[field]!r}, which the baseline does not pin",
                {"field": field, "expected": None, "actual": actual[field]})
        elif actual[field] != baseline[field]:
            n += alert(
                f"account config: `{field}` changed —"
                f" expected {baseline[field]!r}, broker reports"
                f" {actual[field]!r}",
                {"field": field, "expected": baseline[field],
                 "actual": actual[field]})
    return n
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_preconditions.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Run the purity lint and the full suite**

Run: `python scripts/check_purity.py && make test`
Expected: purity lint clean (this task adds nothing to `gate/`), suite 1113 passed, 1 skipped, 7 deselected

- [ ] **Step 6: Commit**

```bash
git add orchestrator/preconditions.py tests/test_preconditions.py
git commit -m "feat: account settings drift from their baseline without anyone noticing"
```

---

### Task 2: The fake broker's account surface

**Files:**
- Modify: `tests/fake_alpaca.py`
- Test: `tests/test_preconditions.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `FakeAlpaca.account_config() -> dict`, and a constructor keyword `account_config: dict | None = None`. When not supplied it returns `DEFAULT_ACCOUNT_CONFIG`, a module-level dict in `tests/fake_alpaca.py` holding the nine alpaca-py 0.44.0 fields.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preconditions.py`:

```python
def test_fake_alpaca_reports_a_default_account_config(conn):
    """sim-day and the offline suite need the surface, and its default must
    match its own baseline so a plain fake is silent."""
    from tests.fake_alpaca import DEFAULT_ACCOUNT_CONFIG, FakeAlpaca

    fake = FakeAlpaca(prices={"NVDA": 100.0})
    assert fake.account_config() == DEFAULT_ACCOUNT_CONFIG
    n = assert_account_config_unchanged(
        conn, broker=fake, baseline=DEFAULT_ACCOUNT_CONFIG, now_iso=NOW)
    assert n == 0


def test_fake_alpaca_account_config_is_overridable(conn):
    from tests.fake_alpaca import DEFAULT_ACCOUNT_CONFIG, FakeAlpaca

    # Must differ from DEFAULT_ACCOUNT_CONFIG's own value (False), or there is
    # no drift to detect and the test asserts nothing.
    fake = FakeAlpaca(prices={"NVDA": 100.0},
                      account_config=dict(DEFAULT_ACCOUNT_CONFIG,
                                          no_shorting=True))
    n = assert_account_config_unchanged(
        conn, broker=fake, baseline=DEFAULT_ACCOUNT_CONFIG, now_iso=NOW)
    assert n == 1
    assert "no_shorting" in _alerts(conn)[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preconditions.py -k fake_alpaca -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_ACCOUNT_CONFIG'`

- [ ] **Step 3: Write the implementation**

In `tests/fake_alpaca.py`, add above `class FakeAlpaca`:

```python
# The nine fields alpaca.trading.models.AccountConfiguration carries in pinned
# alpaca-py 0.44.0. Values are paper-account defaults; a test that cares about
# a specific setting overrides it rather than editing this dict.
DEFAULT_ACCOUNT_CONFIG = {
    "dtbp_check": "entry",
    "fractional_trading": True,
    "max_margin_multiplier": "4",
    "max_options_trading_level": 0,
    "no_shorting": False,
    "pdt_check": "entry",
    "ptp_no_exception_entry": False,
    "suspend_trade": False,
    "trade_confirm_email": "all",
}
```

Change `FakeAlpaca.__init__` to accept and store the override:

```python
    def __init__(self, prices: dict[str, float],
                 fill_prices: dict[str, float] | None = None,
                 mode: str = "fill",
                 account_config: dict | None = None) -> None:
        self.prices = dict(prices)
        self.fill_prices = dict(fill_prices or {})
        self.mode = mode
        self.orders: dict[str, dict] = {}
        self.place_attempts: list[dict] = []
        self.cancel_attempts: list[str] = []
        self._account_config = dict(
            DEFAULT_ACCOUNT_CONFIG if account_config is None else account_config)
```

Add the method to `FakeAlpaca`:

```python
    def account_config(self) -> dict:
        """Twin of market/source_alpaca.py:AlpacaSource.account_config."""
        return dict(self._account_config)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_preconditions.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: 1115 passed, 1 skipped, 7 deselected. The `__init__` change is keyword-only with a default, so no existing `FakeAlpaca(...)` call site changes.

- [ ] **Step 6: Commit**

```bash
git add tests/fake_alpaca.py tests/test_preconditions.py
git commit -m "test: the fake broker has no account surface to drift from"
```

---

### Task 3: The live broker read

**Files:**
- Modify: `market/source_alpaca.py`
- Test: `tests/test_source_alpaca_helpers.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces: `AlpacaSource.account_config() -> dict` — every field of `TradingClient.get_account_configurations()` as plain values, no filtering.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_source_alpaca_helpers.py`:

```python
def test_account_config_returns_every_field_as_plain_values():
    """No filtering: a setting Alpaca adds must reach the drift check rather
    than be dropped by a hand-written field list here."""
    from market.source_alpaca import AlpacaSource

    class _Config:
        def __init__(self):
            self.no_shorting = False
            self.suspend_trade = False
            self.max_margin_multiplier = "4"

    class _Trading:
        def get_account_configurations(self):
            return _Config()

    src = AlpacaSource.__new__(AlpacaSource)
    src._trading = _Trading()
    assert src.account_config() == {
        "no_shorting": False,
        "suspend_trade": False,
        "max_margin_multiplier": "4",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_source_alpaca_helpers.py -k account_config -v`
Expected: FAIL — `AttributeError: 'AlpacaSource' object has no attribute 'account_config'`

- [ ] **Step 3: Write the implementation**

In `market/source_alpaca.py`, add to `AlpacaSource` directly after `account_state`:

```python
    def account_config(self) -> dict:
        """Every account setting the broker reports, as plain values.

        Deliberately unfiltered. orchestrator/preconditions.py diffs the whole
        payload against a pinned baseline precisely so a setting nobody
        classified — or one Alpaca adds in a later release — still reddens the
        check. A field list here would reintroduce the enumeration that
        config/broker_tool_surface.yaml's comment already got wrong."""
        c = self._trading.get_account_configurations()
        return {k: v for k, v in vars(c).items() if not k.startswith("_")}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_source_alpaca_helpers.py -k account_config -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: 1116 passed, 1 skipped, 7 deselected

- [ ] **Step 6: Commit**

```bash
git add market/source_alpaca.py tests/test_source_alpaca_helpers.py
git commit -m "feat: nothing reads back the account settings the gate stands on"
```

---

### Task 4: Wire it into the trading day

**Files:**
- Create: `config/account_config_baseline.yaml` (placeholder values; Task 5 replaces them)
- Modify: `scripts/run_day.py`
- Test: `tests/test_run_day.py`

**Interfaces:**
- Consumes: `assert_account_config_unchanged(conn, *, broker, baseline, now_iso) -> int` (Task 1); `AlpacaSource.account_config()` (Task 3); `FakeAlpaca.account_config()` (Task 2).
- Produces: `scripts/run_day.py` module constant `ACCOUNT_BASELINE_YAML = ROOT / "config" / "account_config_baseline.yaml"`.

- [ ] **Step 1: Create the baseline file with placeholder values**

Create `config/account_config_baseline.yaml`:

```yaml
# The paper account's settings, PINNED.
#
# orchestrator/preconditions.py diffs this against the broker before every
# trading day and alerts on ANY difference — a changed value, a field Alpaca
# drops, or a field Alpaca adds. The whole payload is pinned on purpose: the
# hand-written setting list in config/broker_tool_surface.yaml was already
# wrong against pinned alpaca-py 0.44.0, which is what enumeration does.
#
# Invariant 3 says gate thresholds change only by human commit. These are the
# settings those thresholds stand on, so they change the same way: when a drift
# alert fires, verify the change was intended, then commit the new values here.
# Editing this file to silence an alert you have not explained is the one
# misuse that makes the check worthless.
#
# PLACEHOLDER — captured values land in Task 5. Until then this file is
# structurally valid but does not describe the real account.
dtbp_check: entry
fractional_trading: true
max_margin_multiplier: "4"
max_options_trading_level: 0
no_shorting: false
pdt_check: entry
ptp_no_exception_entry: false
suspend_trade: false
trade_confirm_email: all
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_run_day.py`:

```python
def test_account_baseline_yaml_parses_and_is_not_empty():
    """An empty or unparseable baseline makes the drift check unable to fail,
    so it is checked here rather than discovered at 09:00."""
    import yaml
    from scripts.run_day import ACCOUNT_BASELINE_YAML

    baseline = yaml.safe_load(ACCOUNT_BASELINE_YAML.read_text())
    assert isinstance(baseline, dict) and baseline


def test_drift_alerts_without_raising(tmp_path):
    """Alert-only: drift is recorded and returns a count rather than raising.

    That the DAY still completes with the check wired in is proved by
    `make sim-day` in this task's verification, not here — this exercises the
    assertion alone and is named for what it actually does."""
    import json

    from orchestrator.preconditions import assert_account_config_unchanged
    from state.db import connect
    from tests.fake_alpaca import DEFAULT_ACCOUNT_CONFIG, FakeAlpaca

    conn = connect(str(tmp_path / "t.sqlite"))
    fake = FakeAlpaca(prices={"NVDA": 100.0},
                      account_config=dict(DEFAULT_ACCOUNT_CONFIG,
                                          suspend_trade=True))
    n = assert_account_config_unchanged(
        conn, broker=fake, baseline=DEFAULT_ACCOUNT_CONFIG,
        now_iso="2026-08-20T09:00:00-04:00")
    assert n == 1
    row = conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert'").fetchone()
    assert "suspend_trade" in json.loads(row["payload"])["text"]
    conn.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_run_day.py -k "account_baseline or drift_alerts_without_raising" -v`
Expected: FAIL — `ImportError: cannot import name 'ACCOUNT_BASELINE_YAML' from 'scripts.run_day'`

- [ ] **Step 4: Add the constant and the call**

In `scripts/run_day.py`, beside `SECTORS_YAML` at :83:

```python
ACCOUNT_BASELINE_YAML = ROOT / "config" / "account_config_baseline.yaml"
```

Add to the `orchestrator` imports near :73:

```python
from orchestrator.preconditions import assert_account_config_unchanged  # noqa: E402
```

Replace the `run_day(...)` call at :607 with:

```python
    # Before any stage: the gate's thresholds stand on broker-side account
    # settings nothing else reads back. Alert-only — a changed precondition is
    # not grounds to refuse to trade, but it must not go unnamed (2026-08-20
    # design). Drained explicitly because run_day drains per stage and this
    # runs before the first one.
    if assert_account_config_unchanged(
            conn, broker=source,
            baseline=yaml.safe_load(ACCOUNT_BASELINE_YAML.read_text()) or {},
            now_iso=iso(clock.now())):
        drain(conn, slack, iso(clock.now()))

    run_day(ctx, execution_turn=execution_turn, broker=source, sleep=time.sleep)
```

- [ ] **Step 5: Confirm the names the new call depends on are already in scope**

Run: `sed -n '72p;75p' scripts/run_day.py`
Expected: `iso` imported from `orchestrator.clock` at :72 and `drain` from `slackkit.outbox` at :75 — both verified present at `894e1b8`, so no import is added beyond `assert_account_config_unchanged`. `conn`, `slack`, `clock` and `source` are all in scope at the call site.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_run_day.py -k "account_baseline or drift_alerts_without_raising" -v`
Expected: PASS, 2 passed

- [ ] **Step 7: Run the full suite and a simulated day**

Run: `make test && make sim-day`
Expected: suite 1118 passed, 1 skipped, 7 deselected. `sim-day` completes — the fake's default config matches the placeholder baseline, so the day is silent.

- [ ] **Step 8: Commit**

```bash
git add config/account_config_baseline.yaml scripts/run_day.py tests/test_run_day.py
git commit -m "feat: the day runs without ever checking the settings it assumes"
```

---

### Task 5: Capture the real baseline

**Files:**
- Modify: `config/account_config_baseline.yaml`

**Interfaces:**
- Consumes: `AlpacaSource.account_config()` (Task 3).
- Produces: nothing. Terminal task.

**This is the one step that is not offline.** Everything above is green without it. The values must come from a real read — inventing them ships a baseline that alerts on day one and trains everyone to ignore the alert.

- [ ] **Step 1: Confirm authorization before reading production**

The handoff grants unrestricted **reads** of the droplet (`root@138.197.47.97`, `/opt/fund`). This task performs a read and mutates nothing there. If the read cannot be performed, **stop and report** — do not populate the file from memory, from the fake's defaults, or from the placeholder. A wrong baseline is worse than an absent feature.

- [ ] **Step 2: Read the live paper account's configuration**

Run on the droplet, from `/opt/fund` with its `.env` loaded:

```bash
python -c "
import yaml
from market.source_alpaca import AlpacaSource
print(yaml.safe_dump(AlpacaSource().account_config(), sort_keys=True))
"
```

Expected: nine keys, matching the field names in `tests/fake_alpaca.py:DEFAULT_ACCOUNT_CONFIG`.

- [ ] **Step 3: Replace the placeholder values**

Keep the header comment in `config/account_config_baseline.yaml` verbatim, delete the two `PLACEHOLDER` lines, and replace the nine values with the captured output.

- [ ] **Step 4: Verify the check is silent against the real account**

Re-run Step 2's command on the droplet with the committed baseline in place, confirming zero drift. Expected: no alert.

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: 1118 passed, 1 skipped, 7 deselected. Real values may differ from `DEFAULT_ACCOUNT_CONFIG`; no test compares the two, and if one does, fix the test to stop coupling them rather than editing the captured values.

- [ ] **Step 6: Commit**

```bash
git add config/account_config_baseline.yaml
git commit -m "feat: pin the paper account's settings as the drift baseline"
```

Record in the commit body **which account and when** the values were captured.

---

## Verification

- `make test` — the count only grows; the baseline is 1105 at `894e1b8`.
- `make sim-day` — a full simulated day completes with the check wired in.
- `python scripts/check_purity.py` — `gate/` untouched.
- **A green suite is not evidence.** It was green through all three of this week's incidents. The evidence this branch owes is the check *firing*: `test_changed_field_alerts_naming_old_and_new` and `test_field_new_in_payload_alerts` are that demonstration, and Task 5 Step 4 is its production counterpart.

## Out of scope

`market/source_alpaca.py:170` truncates fractional position quantities — `int(float(p.qty))` turns 10.5 shares into `held_qty = 10` silently, while `orchestrator/protection.py`'s `_qty` treats the same broker field as unreadable and alerts. A real defect, a different one, deliberately not in this diff.
