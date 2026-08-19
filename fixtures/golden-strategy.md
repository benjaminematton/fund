# Golden Strategy — `fixtures/golden-strategy.md`

Worked example of one strategy through the full lifecycle (companion to
`fixtures/golden-day.md`). Every number below is exact and asserted by
`tests/test_golden.py` — this file IS the Phase-5 test vector for `stratgate/`.
The pipeline is deterministic: if any number drifts, behavior changed and the
fixture must be deliberately re-recorded (same policy as `recordings/`).

## The market

Synthetic, seeded (`tests/synthetic.py`, seed=42): 20 names, 2,520 daily bars
(2016-01-04 onward), geometric random walk with a **planted** mean-reversion
edge — after a 5-day drop of ≥5%, next-day expected return gets a +50 bps kick.
We planted the edge, so we know the ground truth the gate should find.

Data snapshot hash: `dat_adfbd511dae060bd` (hashed at 6 significant digits, so
it is identical on macOS and Linux — see `snapshot_hash`)

## G1 — the spec

```
spec_id:          spec_golden000000f1
family:           F1 (short-term mean reversion, liquid)
hypothesis:       liquidity provision — buyers of 5d dips above trend are
                  compensated for absorbing short-term selling pressure
liquidity_bucket: mega_large  (cost floor: 5 bps/side, agent cannot lower)
signal_rule:      dip_buyer
param_ranges:     dip_days [3,8,1] · dip_pct [0.03,0.08,0.01] · trend_days [150,250,50]
search_budget:    20 configs
```

## The PASS path — params {dip_days: 5, dip_pct: 0.05, trend_days: 200}

`config_hash cfg_2ad6bd632a066999 · run_key run_7b4a168abfd09ced`

`run_backtest` (holdout = last 18 months quarantined; 8.448 visible years):

| metric | value | G2 threshold | ok |
|---|---|---|---|
| n_trades | 740 | ≥ 100 | ✓ |
| span_years | 8.448 | ≥ 8 | ✓ |
| net_sharpe (5 bps/side) | 1.827113 | ≥ 0.5 | ✓ |
| net_sharpe_2x | 1.594162 | ≥ 0.3 | ✓ |
| net_sharpe_3x | 1.360801 | > 0 | ✓ |
| per_period_sharpe | 0.115097 | (feeds DSR) | — |
| deflated_sharpe (N=1) | 1.000000 | ≥ 0.95 | ✓ |
| wfe | 0.639459 | ≥ 0.5 | ✓ |
| max_drawdown | 0.204060 | ≤ 0.25 | ✓ |
| cost_share | 0.113073 | ≤ 0.50 | ✓ |
| param cliff (worst neighbor loss) | 32.1% (dip_days↑: 1.2413) | ≤ 40% | ✓ |

**G2: PASS.** → bull/bear debate → G3.

G3 one-shot holdout (391 days, 160 trades — consumed forever, pass or fail):

| check | value | threshold | ok |
|---|---|---|---|
| holdout_sharpe | 3.092703 | > 0 | ✓ |
| holdout_vs_wf ratio | 1.6927 | ≥ 0.5 | ✓ |
| regimes non-negative | 3 of 3 | ≥ 2 of 3 | ✓ |

**G3: PASS → VALIDATED.** PM plans the sleeve at **half** the gate Sharpe
(≈0.91) → 5% initial sleeve → INCUBATING (≥60 trading days AND ≥30 closed
positions on shadow P&L before G4).

A second identical `run_backtest` call returns `cached: true`, same `run_key`,
and family N stays unchanged — idempotency is the test.

## The FAIL path — params {dip_days: 3, dip_pct: 0.03, trend_days: 150}

Same spec, same data, shallower/faster dip trigger:

| metric | value | verdict |
|---|---|---|
| n_trades | 1,473 | noise churn — 2× the trades |
| net_sharpe | 1.5917 | **looks great** |
| deflated_sharpe | 0.9983 | passes |
| wfe | **0.307** | **G2: REJECT (wfe)** |

**The lesson the fixture exists to teach:** a Sharpe-1.59 backtest with 1,473
trades was rejected because its out-of-sample-to-in-sample ratio collapsed —
it trades noise, not the planted edge. Headline Sharpe is the last number to
trust and the first number an agent will be tempted by. The gate doesn't argue;
it counts.

## Invariants demonstrated

1. Cost floors bite: PASS-config Sharpe degrades monotonically 1.83 → 1.59 → 1.36
   across 1×/2×/3× stress — real edges survive cost stress; the FAIL config's
   problem wasn't costs, it was stability.
2. Holdout single-touch: the second `evaluate_holdout` call raises
   `holdout_already_consumed` (PRIMARY KEY, not policy) and alerts `#risk`.
3. Budget: config #21 against this spec raises `budget_exhausted` — and the
   rejected attempt still increments family N (a spent trial is a spent trial).
4. Determinism: same seed → same market → these exact numbers, on any machine.
