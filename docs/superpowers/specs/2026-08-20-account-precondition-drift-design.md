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
                                 ├─→ assert_account_config_unchanged() ─→ findings ─→ outbox ─→ Slack
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
   New module. `assert_account_config_unchanged(conn, *, broker, now_iso) -> list`.
   Diffs baseline against broker, appends one event per drift, returns findings — the
   same contract the `protection.py` assertions use.

   *Why a new module rather than growing `protection.py`:* that module has one subject —
   promised stops versus broker positions — and its docstring is written entirely around
   that comparison. Account config is a different subject on a different data source.

4. **`account_config_drift` event kind + its `RENDERERS` entry in `slackkit/render.py`,
   in the same commit.** A kind with no renderer is dead-lettered by `drain()`, appends
   `projection_error`, and reddens the day one step from the cause.

5. **`FakeAlpaca.account_config()`** (`tests/fake_alpaca.py`)
   So `make sim-day` and the offline suite have the surface. Returns the baseline payload
   by default; tests override fields to force drift.

### Wiring

Top of `run_day` in `orchestrator/daily.py`, **before the `pre_gate` stage** — a
precondition should be known before sizing, not after it.

Not a stage, for the reason already written at `daily.py:478`: an assertion must re-check
on a resumed day rather than be skipped as done, and a duplicate alert is the safe
direction. Drained explicitly, since `run_stage` returns before draining.

## Failure semantics

Invariant 4 and `protection.py`'s doctrine — every ambiguity alerts, because a check that
can pass while lying is worse than no check:

| Condition | Behaviour |
|---|---|
| Field differs from baseline | Alert, one finding per field, naming old and new |
| Broker unreachable / raises | Alert |
| Payload unparseable | Alert |
| Baseline field absent from payload | Alert — Alpaca removed a setting |
| Payload field absent from baseline | Alert — Alpaca added a setting |
| Exact match | Silent |

Nothing here blocks. Every branch either alerts or passes.

## Testing

TDD, per `specs/acceptance.md`'s red-first rule. A green suite is not evidence — it was
green through all three of this week's incidents. The tests that matter show the check
**firing**:

- each drift class in the table above produces exactly one finding, with the field named
- an exact match is silent
- a broker that raises alerts rather than propagating
- the `account_config_drift` kind has a `RENDERERS` entry (the existing
  `test_every_written_kind_has_a_renderer` covers this once the kind exists)
- `run_day` still completes when drift is present — proving alert-only, not blocking

Baseline before any change: **1105 passed, 1 skipped, 7 deselected** at `894e1b8`.

## Out of scope

**`market/source_alpaca.py:170` truncates fractional position quantities.**
`int(float(p.qty))` turns a 10.5-share position into `held_qty = 10`, silently, so a sell
would leave dust. `protection.py`'s `_qty` treats the same broker field as unreadable and
alerts — two readers of one field, one failing closed and one truncating. Found while
verifying this design; genuinely a defect, but a different one. Filed separately rather
than widened into this diff.
