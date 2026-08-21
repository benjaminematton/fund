# Final whole-branch review — fixes applied (2026-08-21)

Branch: `gate-account-preconditions` worktree. Applied all six fixes from the
final review. Verification: `make test` -> 1119 passed, 1 skipped, 7
deselected; `make sim-day` -> 8 passed; `python scripts/check_purity.py` ->
clean.

## Fix 1 — the wiring was unpinned

Added `tests/test_run_day.py::test_a_drifted_account_setting_alerts_but_does_not_abort_the_day`.
It drives `_trading_day` directly (the same entry point
`test_a_zero_ticker_day_runs_the_whole_composition_and_audits_clean` uses)
with a `_DriftingSource(_QuietSource)` whose `account_config()` flips
`suspend_trade` away from the baseline, then asserts:

- an alert naming `suspend_trade` reached `#risk` in the outbox
  (`_risk_texts(slack)`),
- every stage's checkpoint still reached `done`,
- the return code is `1`.

On the `code` assertion: the task brief assumed "not aborted" meant the same
`code == 0` the zero-ticker test asserts. That is not what actually happens.
`scripts/audit_day.py` treats "no alert events TODAY" as part of a clean day
by design — a drift alert genuinely reddens the audit and `report_audit`
correctly returns `1`. That is the audit doing its job, not the day
aborting: "alert-only, not blocking" (the design's own words) means every
stage still runs to completion, not that the exit code stays clean. I wrote
the test to assert what is actually true (`code == 1`, all checkpoints
`done`) rather than force a `code == 0` assertion that contradicts the
documented audit behaviour — asserting the wrong thing would have made this
a second unpinned test, just in the other direction. This is documented in
the test's own docstring.

**Verification — before/after removing the call site:**

Removed the `if assert_account_config_unchanged(...): drain(...)` block at
`scripts/run_day.py:614-618` (commented out), reran the new test alone:

```
FAILED tests/test_run_day.py::test_a_drifted_account_setting_alerts_but_does_not_abort_the_day
    assert code == 1
E   assert 0 == 1
...
run_day: AUDIT CLEAN 2026-07-06
```

Restored the block (`git diff scripts/run_day.py` shows no diff, confirming
an exact restore), reran:

```
tests/test_run_day.py .                                                  [100%]
1 passed, 60 deselected
```

Also removed `test_drift_alerts_without_raising` — it duplicated
`test_fake_alpaca_account_config_is_overridable` in
`tests/test_preconditions.py` (same fake, same baseline, a different
drifted field) and never imported anything from `run_day.py`, so its
placement implied wiring coverage it did not provide.

## Fix 2 — the false claim about a field Alpaca adds later

Verified empirically that `alpaca.trading.models.AccountConfiguration`
defaults to pydantic's `extra='ignore'` (no override in the class or its
`ValidateBaseModel` base): constructing it with an unknown kwarg silently
drops that kwarg. So `TradingClient.get_account_configurations()` cannot
hand `account_config()` a field alpaca-py's model does not declare — the
"field new in payload" branch is unreachable on the live path.

Corrected the wording (kept the code and the branch/test, per the ruling) in:

1. `config/account_config_baseline.yaml` header comment.
2. `orchestrator/preconditions.py` module docstring.
3. `docs/superpowers/specs/2026-08-20-account-precondition-drift-design.md`
   — the "Pin the whole payload" decision paragraph, and the
   failure-semantics table row for "Payload field absent from baseline".
4. `docs/superpowers/plans/2026-08-20-account-precondition-drift.md` — the
   embedded Task 1/Task 4 code-block copies of the same docstring/header
   text (the plan is a historical record that mirrors these files' content
   verbatim, so both copies carried the same overclaim).

All four now say: the pin is against alpaca-py's `AccountConfiguration`
model, not Alpaca's raw API; a field the fund cannot see cannot affect the
fund; the alpaca-py version bump that makes a new field visible is itself a
human commit (invariant 3's own mechanism); the check reddens the first day
the fund can actually observe the field.

**Beyond the four listed places:** found and fixed the identical false claim
in a fifth, live location not named in the task —
`market/source_alpaca.py`'s `account_config()` docstring ("or one Alpaca
adds in a later release — still reddens the check"). Same defect, shipped
code, so I corrected it the same way rather than leave a live docstring
making the disproven claim. Flagging this explicitly since it was outside
the enumerated scope.

The `field not in baseline` branch and `test_field_new_in_payload_alerts`
were left untouched — still correct, still reachable on a version bump.

## Fix 3 — the broker error was discarded

`orchestrator/preconditions.py`, broker-failure branch: the alert payload
now carries `{"reason": "unreadable", "error": str(e)}` (untruncated). The
Slack-facing `text` keeps its existing 120-char cap. Added a comment
explaining the payload keeps the full error because the text is capped.
Existing test `test_broker_failure_alerts_and_does_not_raise` still passes
unmodified (it only checks the text, not the payload).

## Fix 4 — the baseline's value types were unpinned

Added `tests/test_preconditions.py::test_baseline_value_types_match_the_installed_alpaca_pys_model`.
It walks `AccountConfiguration.model_fields` (not a second hand-written
list) and asserts each baseline value's type matches the field's annotation
— unwrapping `Optional[X]`, and treating alpaca-py's `(str, Enum)` fields as
`str` since `account_config()` coerces them via `_enum_str` before this ever
runs. `dtbp_check`, `pdt_check`, `max_options_trading_level` are allowed to
be `None`.

Verified it actually catches the regression the fix describes: manually set
`max_margin_multiplier` to unquoted `4` (int) and reran the check logic —
it failed with
`max_margin_multiplier: baseline holds a int (4), but AccountConfiguration expects str`,
naming the field and both types as required.

## Fix 5 — the false "paper-account defaults" comment

`tests/fake_alpaca.py`, `DEFAULT_ACCOUNT_CONFIG`'s comment now says the
values are arbitrary fixture placeholders, deliberately not the real
account's (which reports `null` for `dtbp_check`, `pdt_check`,
`max_options_trading_level`), and points to
`config/account_config_baseline.yaml` for the real captured values.

## Fix 6 — the false "two lines above" adjacency claim

Reworded both occurrences (spec's Failure semantics section, plan's Global
Constraints) to describe the shared *behaviour* — both `SECTORS_YAML` and
the account baseline raise inside `guarded()` on an unreadable file — rather
than claiming physical adjacency, which is false (`SECTORS_YAML` load is at
`scripts/run_day.py:520`, the baseline load is at `:616`, ~96 lines apart;
only the module-level constants at `:84-85` are adjacent).

## Verification

```
make test          -> 1119 passed, 1 skipped, 7 deselected
make sim-day        -> 8 passed
python scripts/check_purity.py -> PURITY LINT: clean
```

## Files touched

- `orchestrator/preconditions.py`
- `market/source_alpaca.py`
- `config/account_config_baseline.yaml`
- `tests/fake_alpaca.py`
- `tests/test_preconditions.py`
- `tests/test_run_day.py`
- `docs/superpowers/specs/2026-08-20-account-precondition-drift-design.md`
- `docs/superpowers/plans/2026-08-20-account-precondition-drift.md`
