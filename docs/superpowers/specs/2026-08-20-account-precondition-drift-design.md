# Account precondition drift detection

**Date** 2026-08-20 · **Status** approved, not yet implemented · **Base** `origin/master` @ `894e1b8`

## Problem

Invariant 3 makes gate thresholds human-commit-only, and that is genuinely enforced:
`SECTOR_CAP`, `MAX_POSITIONS` and `CIRCUIT_BREAKER` are module constants in
`gate/risk.py`, linted by `scripts/check_purity.py` in CI.

But those thresholds stand on broker-side account settings the fund never reads back.
`update_account_config` carries `no_shorting`, `suspend_trade`, `max_margin_multiplier`
and six more. No LLM seat can reach it any more — `agents/runtime.py`'s
`_broker_verb_policy` is deny-by-default and deployed — but a human at the Alpaca
dashboard, or Alpaca itself, can still move them, and nothing in the fund would notice.

### What the exposure is NOT

The envelope does not move. This was checked limb by limb before designing:

| Setting | Claim | Finding |
|---|---|---|
| `max_margin_multiplier` | more buying power ⇒ bigger positions | **False.** `gate/risk.py:89` sizes on `min(dollar, i.cash)`, and `cash` is `a.cash`, not `buying_power` (`market/source_alpaca.py:163`). |
| `no_shorting` | clearing it lets the fund go short | **False.** The sell branch caps at `held_qty` and requires `held_qty > 0`. An externally-created short arrives as a negative qty and hits `held_qty < 0` → `gate_error` (`gate/risk.py:73`). `orchestrator/protection.py`'s `_CLOSING_SIDE` fails closed on the same case. |
| `suspend_trade` | silent kill switch | **Partly.** It stops trading, but as a broker-side rejection. Loud. |

So every limb already fails closed. **The gap is attribution, not safety:** flip
`no_shorting` and the fund rejects with `gate_error`, which says nothing about the cause.
The fix is therefore a detector, not a new control.

## Decisions

**Alert-only, never blocking.** A precondition change is not per se unsafe — the code
above bounds it. Halting a trading day over a possibly-benign setting costs more than it
buys. Drift alerts; the day proceeds.

**Pin the whole payload, not the settings we think matter.** Enumeration is the failure
mode this repo hit twice today: the exec verb surface was counted 4 → 5 → 7 → 8 by four
sessions, and the hand-written setting list in `config/broker_tool_surface.yaml:41-43` is
*already* wrong against pinned alpaca-py 0.44.0 — it omits `dtbp_check` and `pdt_check`
and lists `disable_overnight_trading`, which that version's model does not have. A
whole-payload diff has no list to keep current, and a field Alpaca adds later reddens a
check instead of passing unseen.

**The baseline is a checked-in file.** Drift is resolved by a human committing a new
baseline. That is invariant 3's mechanism — human commit — applied to preconditions
rather than thresholds.

## Architecture

The broker read happens **outside `gate/`** and arrives as an input, the same shape
`GateInputs.equity` already uses. `gate/` gains nothing and imports nothing; invariant 3
and `scripts/check_purity.py` are untouched.

```
AlpacaSource.account_config()  ──┐
                                 ├─→ assert_account_config_unchanged() ─→ `alert` events ─→ outbox ─→ Slack
config/account_config_baseline.yaml ─┘
```

### Components

1. **`AlpacaSource.account_config() -> dict`** (`market/source_alpaca.py`)
   Wraps `TradingClient.get_account_configurations()` (confirmed present in pinned
   alpaca-py 0.44.0). Returns every field as a plain value, mirroring `account_state()`'s
   discipline. No filtering — filtering here would reintroduce the enumeration.

2. **`config/account_config_baseline.yaml`**
   The pinned payload, sibling to `config/broker_tool_surface.yaml`, with a header stating
   that a drift alert is resolved by verifying the change was intended and committing the
   new baseline.

   **Populating it needs a real read**, and that is the one step in this design that is
   not offline. The values are whatever the paper account currently holds; inventing them
   would ship a baseline that alerts on day one. Read it from the droplet
   (`root@138.197.47.97`, reads unrestricted per the handoff) rather than from a local
   `.env`, and record in the commit message which account and when. Until it is captured,
   the offline suite is unaffected — `FakeAlpaca` supplies its own payload — so
   implementation can proceed and the capture lands last.

3. **`orchestrator/preconditions.py`**
   New module. `assert_account_config_unchanged(conn, *, broker, now_iso) -> int`,
   returning the number of alerts appended — the exact contract
   `assert_positions_protected` and `assert_positions_accounted` already use.

   *Why a new module rather than growing `protection.py`:* that module has one subject —
   promised stops versus broker positions — and its docstring is written entirely around
   that comparison. Account config is a different subject on a different data source.

4. **No new event kind.** Drift is appended as the existing `alert` kind, which already
   has a renderer (`slackkit/render.py:222`) and already fails the day in
   `scripts/audit_day.py:151`. That is the right severity: drift means the fund traded
   under preconditions nobody verified, and it is rare enough not to cry wolf.

   The `model_fallback_used` precedent — "deliberately NOT an `alert`" — points the other
   way and does not apply. That event fires every seat turn and is not a fault; this one
   is rare and is. Using `alert` also removes the dead-letter hazard a new kind would have
   introduced, so the guard that hazard needed disappears with it.

5. **`FakeAlpaca.account_config()`** (`tests/fake_alpaca.py`)
   So `make sim-day` and the offline suite have the surface. Returns the baseline payload
   by default; tests override fields to force drift.

### Wiring

Called from `scripts/run_day.py`, immediately before it calls `run_day(...)` at :607 —
so it runs before any stage, and a precondition is known before sizing rather than after.

Not a stage, for the reason already written at `daily.py:478`: an assertion must re-check
on a resumed day rather than be skipped as done, and a duplicate alert is the safe
direction. `scripts/run_day.py` is the entry point for every run including resumes, so it
re-checks. Drained explicitly.

*Why here rather than inside `orchestrator/daily.run_day`:* the check needs config, and
config loading in this codebase lives in `scripts/run_day.py` (`SECTORS_YAML` at :83,
loaded at :518) while `orchestrator/` and `market/` read no files and receive data.
Putting it inside `run_day` would mean a new `StageCtx` field with a default, and that
default is a trap — `None` either silently skips the check, which is the
passes-while-lying failure this branch exists to prevent, or alerts and churns the eight
existing `run_day` call sites in the tests. Loading at the `scripts/` seam removes the
fork: a missing baseline file fails loudly at load, exactly as a missing `sectors.yaml`
would.

## Failure semantics

Invariant 4 and `protection.py`'s doctrine — every ambiguity alerts, because a check that
can pass while lying is worse than no check:

| Condition | Behaviour |
|---|---|
| Field differs from baseline | Alert, one per field, naming old and new |
| Broker unreachable / raises | Alert |
| Payload unparseable | Alert |
| Baseline field absent from payload | Alert — Alpaca removed a setting |
| Payload field absent from baseline | Alert — Alpaca added a setting |
| Exact match | Silent |
| **Baseline file missing or unparseable** | **Aborts the day** — see below |

Every branch above the last one alerts and the day proceeds.

**The one exception, ruled 2026-08-21.** The baseline is loaded at the call site, so a
missing or unparseable file raises before the assertion is entered and `guarded()` stops
the day. That is deliberate and was chosen over wrapping the load: it is not drift, it
means *no* precondition can be verified, and invariant 4's default is HOLD. It also
matches how `SECTORS_YAML` already behaves two lines above, so unreadable config has one
rule rather than two.

## Testing

TDD, per `specs/acceptance.md`'s red-first rule. A green suite is not evidence — it was
green through all three of this week's incidents. The tests that matter show the check
**firing**:

- each drift class in the table above produces exactly one alert, with the field named
- an exact match is silent and returns 0
- a broker that raises alerts rather than propagating
- the day still completes when drift is present — proving alert-only, not blocking

Baseline before any change: **1105 passed, 1 skipped, 7 deselected** at `894e1b8`.

## Out of scope

**`market/source_alpaca.py:170` truncates fractional position quantities.**
`int(float(p.qty))` turns a 10.5-share position into `held_qty = 10`, silently, so a sell
would leave dust. `protection.py`'s `_qty` treats the same broker field as unreadable and
alerts — two readers of one field, one failing closed and one truncating. Found while
verifying this design; genuinely a defect, but a different one. Filed separately rather
than widened into this diff.
