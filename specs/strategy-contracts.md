# Strategy Contracts — `specs/strategy-contracts.md`

Schemas, state machines, and failure semantics for the strategy-development pipeline defined in `specs/strategy.md`. Extends `specs/contracts.md`; same conventions: SQLite is the source of truth, Slack is a projection, all transitions via the compare-and-swap `transition()` helper, `stratgate/` imports no LLM code (CI-enforced, same rule as `gate/`).

---

## 1. Identity and hashing

Everything is content-addressed so results are reproducible and idempotent.

```
spec_id        = "spec_"  + sha256(canonical_json(spec_fields))[:16]      # immutable spec
config_hash    = "cfg_"   + sha256(canonical_json(spec_id, params))[:16]   # one parameter assignment
run_key        = "run_"   + sha256(config_hash + data_snapshot_hash
                                   + engine_version + str(seed))[:16]      # one backtest run
strategy_id    = spec_id                                                    # a strategy IS its spec
```

- `canonical_json`: sorted keys, no whitespace, floats as shortest round-trip repr. One canonicalizer in `fundbt/hashing.py`; nothing else may hash.
- Identical `run_key` → return cached result, **do not** insert a new trial row (idempotent retries, same rule as `client_order_id`).
- Any change to a spec field creates a *new* `spec_id` with `lineage_parent` set. Family trial counts survive lineage (see §4).

## 2. DDL

> **Unification status:** the shipped `fundbt/registry.py` maintains its own minimal DDL for `trial_registry` + `holdout_evaluations` (standalone, `:memory:` default, no FKs) so the starter kit runs without the fund DB. The schema below is canonical; migrating the registry to write to the fund DB (FKs intact, single source of truth) is the Phase-5 acceptance item "Trial registry unified." `strategy_specs`, `strategies`, `sleeves`, and `shadow_fills` have **no implementing code yet** — they are Phase-5 integration work.

```sql
-- Immutable pre-registration (Gate G1). No UPDATE ever; supersede via lineage.
CREATE TABLE strategy_specs (
  spec_id          TEXT PRIMARY KEY,
  family           TEXT NOT NULL,              -- 'F1'..'F5' | 'petition:<name>'
  seat             TEXT NOT NULL,              -- proposing seat (charter name)
  hypothesis       TEXT NOT NULL CHECK(length(hypothesis) <= 500),
  mechanism_class  TEXT NOT NULL CHECK(mechanism_class IN
                     ('behavioral','institutional','risk_premium','liquidity_provision')),
  universe         TEXT NOT NULL,              -- JSON: {index, pit_constituents: true, filters[]}
  liquidity_bucket TEXT NOT NULL CHECK(liquidity_bucket IN ('mega_large','mid','small','micro')),
  signal_rule      TEXT NOT NULL,              -- JSON: coded rule + params w/ declared ranges
  param_ranges     TEXT NOT NULL,              -- JSON: {param: [lo, hi, step]}
  search_budget    INTEGER NOT NULL CHECK(search_budget > 0),
  holding_period_d INTEGER NOT NULL,
  rebalance        TEXT NOT NULL,
  expected_turnover REAL NOT NULL,
  exit_rule        TEXT NOT NULL,
  invalidation     TEXT NOT NULL,              -- falsifying observation, ≤500 chars
  capacity_usd     REAL NOT NULL,
  predicted        TEXT NOT NULL,              -- JSON: {net_sharpe, max_dd, hit_rate}
  llm_in_loop      INTEGER NOT NULL DEFAULT 0, -- invariant 5 applies if 1
  lineage_parent   TEXT REFERENCES strategy_specs(spec_id),
  created_at       TEXT NOT NULL               -- injected Clock, ISO-8601 UTC
);

-- Lifecycle state (the only mutable strategy row).
CREATE TABLE strategies (
  strategy_id      TEXT PRIMARY KEY REFERENCES strategy_specs(spec_id),
  state            TEXT NOT NULL CHECK(state IN
                     ('SPEC','BACKTEST','VALIDATED','INCUBATING',
                      'ALLOCATED','SCALED','PROBATION','RETIRED','REJECTED')),
  state_version    INTEGER NOT NULL DEFAULT 0, -- CAS token for transition()
  reject_reason    TEXT,                       -- required when state='REJECTED'
  gate_results     TEXT,                       -- JSON: latest G2/G3/G4 verdict blobs
  updated_at       TEXT NOT NULL
);

-- Append-only. EVERY backtest by ANY seat. The DSR's N comes from here.
CREATE TABLE trial_registry (
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
CREATE INDEX idx_trials_family ON trial_registry(family);
CREATE INDEX idx_trials_spec   ON trial_registry(spec_id);

-- One row per strategy, ever. Enforces invariant 6 (holdout touched once).
CREATE TABLE holdout_evaluations (
  spec_id     TEXT PRIMARY KEY REFERENCES strategy_specs(spec_id),
  run_key     TEXT NOT NULL REFERENCES trial_registry(run_key),
  passed      INTEGER NOT NULL,
  detail      TEXT NOT NULL,                   -- JSON: per-check results
  created_at  TEXT NOT NULL
);

-- Capital sleeves (Gate G4 onward).
CREATE TABLE sleeves (
  sleeve_id     TEXT PRIMARY KEY,              -- "slv_" + spec_id[5:]
  spec_id       TEXT NOT NULL UNIQUE REFERENCES strategy_specs(spec_id),
  pct_equity    REAL NOT NULL CHECK(pct_equity >= 0 AND pct_equity <= 0.20),
  high_water    REAL NOT NULL DEFAULT 0,       -- shadow P&L high-water mark, USD
  corr_group    TEXT,                          -- sleeves with ρ>0.6 share a combined cap
  ramp_month    INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL
);

-- Shadow P&L: paper fills re-costed at quoted spreads (Alpaca paper flatters fills).
CREATE TABLE shadow_fills (
  fill_id          TEXT PRIMARY KEY,           -- Alpaca fill id
  sleeve_id        TEXT NOT NULL REFERENCES sleeves(sleeve_id),
  ticket_id        TEXT NOT NULL,              -- risk-gate ticket (joins to trade pipeline)
  symbol           TEXT NOT NULL,
  side             TEXT NOT NULL,
  qty              REAL NOT NULL,
  paper_price      REAL NOT NULL,
  quoted_bid       REAL,                       -- NBBO at order submit (from data feed)
  quoted_ask       REAL,
  est_true_price   REAL NOT NULL,              -- paper_price adjusted to floor-cost model
  est_cost_bps     REAL NOT NULL,
  created_at       TEXT NOT NULL
);
-- G4 evidence = SUM over shadow_fills, never raw paper P&L.
```

## 3. Tool contracts (strict MCP, same style as `submit_signal`)

### 3.1 `submit_strategy_spec` (any analyst/researcher seat)

Input: all `strategy_specs` fields except ids/timestamps. Handler validates (pydantic, `extra="forbid"`), computes `spec_id`, INSERTs spec + `strategies` row in state `SPEC`, projects a summary to `#research`. Duplicate `spec_id` → return existing id (idempotent). Malformed → tool error, nothing written, **no partial specs**.

### 3.2 `run_backtest` (seats listed in the spec's family config)

```python
class BacktestRequest(BaseModel, extra="forbid"):
    spec_id: str
    params:  dict[str, float | int | str]      # must lie inside spec.param_ranges
    seed:    int = 0                            # accepted but results keyed by it

class BacktestResult(BaseModel):
    # matches the dict returned by fundbt/run_backtest.py::run_backtest exactly
    run_key: str
    spec_id: str
    config_hash: str
    data_snapshot_hash: str
    n_trades: int
    span_years: float
    net_sharpe: float                # annualized, at floor costs
    net_sharpe_2x: float
    net_sharpe_3x: float
    per_period_sharpe: float         # daily SR fed to the DSR computation
    deflated_sharpe: float           # N = family-wide trial count AT RUN TIME
    n_trials_family: int             # the N used (this trial included)
    wfe: float                       # walk-forward efficiency, OOS/IS (NaN if < 40 bars/fold)
    max_drawdown: float
    turnover_annual: float
    cost_share: float                # costs / gross P&L
    param_neighbors: dict            # {param: {up: sharpe, down: sharpe}}
    regime_sharpe: dict              # {bull, bear, chop}
    cached: bool
```

Implementation notes (behavior of the shipped `fundbt/`, normative):
- **DSR cold-start prior:** with fewer than 2 prior family trials, `family_sharpe_variance` falls back to `0.5 · max(SR, 0.01)²`. This prior is load-bearing — it sets the deflation on the *first* trial of any family. Changing it is a gate-threshold change (human commit only).
- **Snapshot hashing:** the wrapper *computes and records* `data_snapshot_hash` from the provided slice. Verification against a pinned manifest (step 6 below) is integration work — until the manifest exists, the hash provides reproducibility, not tamper-evidence.
- **State check (step 1's `strategies.state` clause) lives in the MCP handler**, not in `run_backtest` itself — the pure function receives an already-validated spec dict.
- A trade-ledger artifact (`ledger_uri`) is **not** produced in v1; add it as a new optional field if/when the artifact store exists — do not require it retroactively.

Wrapper enforcement order (fail → tool error, no trial row except where noted):
1. spec exists and `strategies.state ∈ {SPEC, BACKTEST}`
2. params within `param_ranges`
3. `COUNT(trial_registry WHERE spec_id=?) < search_budget` — **exceeded → REJECT and a `budget_exhausted` event; this rejection IS logged**
4. holdout window excluded from data slice (last 18 months, boundary pinned per data snapshot)
5. cost floors applied by `liquidity_bucket` (5/15/40/100 bps per side); 2×/3× computed always
6. data snapshot SHA256 verified; mismatch → `snapshot_corrupt`, run refused
7. run; INSERT trial row; UPSERT `strategies.state → BACKTEST`

### 3.3 `stratgate.evaluate(spec_id)` — not an MCP tool; orchestrator-invoked pure function

G2 runs automatically when a seat requests promotion; G3 runs only on G2 pass + PM sponsorship; both write verdict JSON to `strategies.gate_results` and project to `#risk`. G3's holdout run inserts the single `holdout_evaluations` row inside the same transaction as the verdict — **pass or fail** (invariant 6). A second G3 attempt for the same spec_id hits the PRIMARY KEY and resolves to REJECT `holdout_already_consumed`.

## 4. State machine

```
SPEC ──run_backtest──► BACKTEST ──G2 pass──► (debate) ──G3 pass──► VALIDATED
 │                        │                                            │
 │                        └──G2 fail / budget_exhausted──► REJECTED    │ PM activates
 │                                                                     ▼
 └──30d idle──► REJECTED(stale)                                  INCUBATING
                                                                     │ G4 pass (60 trading days
        RETIRED ◄──kill rule──── PROBATION ◄──kill rule──┐           │  AND 30 closed positions)
           ▲                        │ recovers            │           ▼
           │                        ▼                     ├────── ALLOCATED ──ramp──► SCALED
           └──2nd probation──── (back to prior state) ────┘
```

Allowed transitions (everything else is a bug; `transition()` rejects):

| from | to | trigger | actor |
|---|---|---|---|
| SPEC | BACKTEST | first `run_backtest` | proposing seat |
| SPEC/BACKTEST | REJECTED | G2 fail, budget exhausted, 30d idle | stratgate / orchestrator |
| BACKTEST | VALIDATED | G2 ∧ debate ∧ G3 pass | stratgate |
| VALIDATED | INCUBATING | PM `activate_strategy` | PM (within sleeve rules) |
| INCUBATING | ALLOCATED | G4 pass | stratgate |
| INCUBATING | REJECTED | G4 fail | stratgate |
| ALLOCATED | SCALED | ramp complete | stratgate (monthly job) |
| ALLOCATED/SCALED | PROBATION | −10% sleeve DD ∨ 60d shadow-Sharpe < 0 ∨ invalidation hit | stratgate (nightly job) |
| PROBATION | prior state | 60d recovery criteria | stratgate |
| PROBATION | RETIRED | −15% DD ∨ 2nd probation ∨ 60 more days Sharpe < 0 | stratgate |
| RETIRED / REJECTED | — | terminal (revival = new spec + lineage) | — |

CAS: every transition passes `expected_state_version`; mismatch → no-op + `stale_transition` event (idempotent under orchestrator retry, same as trade pipeline).

**Lineage and N:** family-wide N for DSR = `COUNT(*) FROM trial_registry WHERE family = ?` — across all specs, all seats, all time, including REJECTED lineage ancestors. This is deliberate (group-level snooping, Arnott–Harvey–Markowitz).

## 5. Failure semantics

Unifying rule (invariant 7): anything unexpected resolves to *not advancing*, alert `#risk`, keep the day moving. Never block the trading pipeline on strategy-pipeline failures.

| failure | resolution |
|---|---|
| data snapshot hash mismatch | refuse run, `snapshot_corrupt` event, page CEO (data integrity) |
| vectorbt crash / timeout mid-run | no trial row, tool error to seat; 3rd consecutive → `engine_degraded`, disable tool until human ack |
| trial INSERT succeeds, stats write fails | impossible by construction: single transaction |
| duplicate `run_key` | return cached result, `cached=true`, no new row |
| G2/G3 evaluator exception | verdict REJECT `gate_error`; spec stays evaluable after fix (except consumed holdout) |
| holdout re-evaluation attempt | REJECT `holdout_already_consumed`, alert `#risk` (someone/something is p-hacking) |
| shadow-fill quote missing (feed gap) | assume worst-case: cost floor × 2 booked to shadow P&L, `quote_gap` event |
| sleeve kill-rule job crashes | on restart, recompute from ledger (pure function of DB); kills are never lost, only late |
| PM tries to activate non-VALIDATED spec | `transition()` rejects; `invalid_activation` event |
| seat calls `run_backtest` on another seat's spec in a gated family | allowed (research is collaborative) but logged under calling seat; N accrues to family either way |
| clock skew / non-monotonic timestamps | injected Clock is the only time source (design.md §4); violation is a test failure, not a runtime branch |

## 6. Projection to Slack

Same outbox pattern as `events` (contracts §2): `#research` gets spec registrations and G2 verdicts (thread per spec); `#debate` hosts strategy debates (same mechanics as ticker debates); `#risk` gets G3/G4 verdicts, probations, kills; `#pnl` weekly sleeve scoreboard (shadow P&L, live-vs-expected Sharpe, decay flags); `#ceo-office` waiver requests and `stratgate/` threshold change proposals. Slack is never a trigger: gates run from orchestrator schedule + DB state only.

## 7. Acceptance hooks (extends `specs/acceptance.md`)

- **Phase 5 (see `specs/acceptance.md`):** `make test` covers: spec immutability, run_key idempotency, budget exhaustion logging, holdout single-touch (PRIMARY KEY test), CAS transitions under concurrent retry, DSR N counting across lineage, shadow-fill worst-case costing on quote gaps.
- **Golden fixture:** one F1 spec through SPEC→ALLOCATED with exact expected numbers at every gate (companion to `fixtures/golden-day.md`; build when `stratgate/` lands).
- **CI:** `stratgate/` and the `run_backtest` wrapper import no LLM code; no `datetime.now()`; cost-floor constants referenced from one module.
