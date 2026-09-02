CREATE TABLE IF NOT EXISTS signals (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,                -- YYYY-MM-DD (ET)
  agent         TEXT NOT NULL,                -- seat name, e.g. 'fundamentals'
  ticker        TEXT NOT NULL,
  direction     TEXT NOT NULL CHECK (direction IN ('bullish','bearish','neutral')),
  confidence    INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  summary       TEXT NOT NULL,                -- <= 500 chars
  charter_version TEXT NOT NULL DEFAULT 'unknown',
                                              -- attribution. Three values, never
                                              -- NULL: a real version (a seat wrote
                                              -- it under that charter), 'none' (the
                                              -- orchestrator wrote it because the
                                              -- seat was silent), 'unknown' (predates
                                              -- attribution). A NULL would drop out
                                              -- of GROUP BY silently, making the
                                              -- exclusion an accident.
  model_id      TEXT NOT NULL DEFAULT 'unknown',
                                              -- the seat's CONFIGURED model at write
                                              -- time. A fallback that served the turn
                                              -- instead raises model_fallback_used.
  slack_ts      TEXT,                         -- projection pointer, may be NULL
  created_at    TEXT NOT NULL,
  UNIQUE (run_date, agent, ticker)            -- re-submission overwrites via UPSERT
);

CREATE TABLE IF NOT EXISTS critiques (                       -- Critic's advisory review of the PM's DRAFT
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  verdict       TEXT NOT NULL CHECK (verdict IN ('clear','objections')),
  objections    TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings, <=3, each <=200 chars
                                              -- (empty iff verdict='clear')
  note          TEXT,                         -- e.g. 'critic_timeout' when defaulted
  charter_version TEXT NOT NULL DEFAULT 'unknown',   -- see signals
  model_id      TEXT NOT NULL DEFAULT 'unknown',     -- see signals
  slack_ts      TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (run_date, ticker)
);
-- Advisory only: no FK to decisions (the critique precedes the decision row) and
-- nothing downstream branches on it. Joined by (run_date, ticker) for the
-- scoreboard's objection hit-rate and for weekly reviews.

-- decision: submitted -> approved | rejected | held (held: gate-settled hold,
-- terminal) . approved -> executed | failed | expired (contracts.md §1)
CREATE TABLE IF NOT EXISTS decisions (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  action        TEXT NOT NULL CHECK (action IN ('buy','sell','hold')),
  qty           INTEGER NOT NULL CHECK (qty >= 0),   -- 0 iff action='hold'
  thesis        TEXT NOT NULL,
  invalidation  TEXT NOT NULL,                -- condition that voids the thesis
  stop_price    REAL CHECK (stop_price IS NULL OR stop_price > 0),
                                              -- set iff invalidation is a hard price level
                                              -- (buy only); NULL = Ops watches the text condition
  charter_version TEXT NOT NULL DEFAULT 'unknown',   -- see signals
  model_id      TEXT NOT NULL DEFAULT 'unknown',      -- see signals
  status        TEXT NOT NULL DEFAULT 'submitted',
  debate_ts     TEXT,                         -- Slack thread of the debate, if any
  created_at    TEXT NOT NULL,
  UNIQUE (run_date, ticker)
);

CREATE TABLE IF NOT EXISTS tickets (
  id            TEXT PRIMARY KEY,             -- uuid4; ALSO the Alpaca client_order_id
  decision_id   INTEGER NOT NULL REFERENCES decisions(id),
  ticker        TEXT NOT NULL,
  side          TEXT NOT NULL CHECK (side IN ('buy','sell')),
  max_qty       INTEGER NOT NULL CHECK (max_qty > 0),
  stop_price    REAL CHECK (stop_price IS NULL OR stop_price > 0),
                                              -- copied from the decision; trader submits an
                                              -- oto order (stop leg) when present
  expires_at    TEXT NOT NULL,                -- ISO8601; gate default: +45 min
  status        TEXT NOT NULL DEFAULT 'open',
  reason        TEXT,                         -- set when gate REJECTS (no row) — see note
  created_at    TEXT NOT NULL,
  UNIQUE (decision_id)                        -- one ticket per decision, ever
);
-- Rejections do NOT create tickets; they set decisions.status='rejected' and
-- append an event(kind='gate_rejected', payload={decision_id, reason}).

CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY REFERENCES tickets(id),  -- idempotency key
  alpaca_order_id TEXT UNIQUE,
  symbol        TEXT NOT NULL,
  side          TEXT NOT NULL,
  qty           INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'submitted',
  filled_qty    INTEGER NOT NULL DEFAULT 0,
  filled_avg_price REAL,
  submitted_at  TEXT NOT NULL,
  closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS resolutions (
  id            INTEGER PRIMARY KEY,
  decision_id   INTEGER NOT NULL REFERENCES decisions(id) UNIQUE,
  horizon_days  INTEGER NOT NULL,             -- default 5 trading days
  realized_return REAL NOT NULL,
  alpha_vs_spy  REAL NOT NULL,
  invalidated   INTEGER NOT NULL DEFAULT 0,   -- invalidation condition hit early
  reflection    TEXT,                         -- written by the deciding agent
  resolved_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
  run_date      TEXT NOT NULL,
  stage         TEXT NOT NULL,                -- standup|research|debate|decision|gate|execution|close|reflection
  ticker        TEXT NOT NULL DEFAULT '*',
  status        TEXT NOT NULL DEFAULT 'pending',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (run_date, stage, ticker)
);

CREATE TABLE IF NOT EXISTS events (                          -- outbox: SQLite truth -> Slack projection
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  payload       TEXT NOT NULL,                -- JSON
  created_at    TEXT NOT NULL,
  posted_at     TEXT                          -- NULL until projected to Slack
);

CREATE TABLE IF NOT EXISTS costs (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,
  agent         TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  usd_estimate  REAL NOT NULL,                -- ResultMessage.total_cost_usd (client-side est.)
  recorded_at   TEXT NOT NULL
);

-- Immutable pre-registration (Gate G1), verbatim from
-- specs/strategy-contracts.md §2 — canonical, do not add fields here. No
-- UPDATE ever; supersede via lineage. Mutable lifecycle state is the
-- `strategies` table below (issue #197), one row per spec_id. That is the
-- TABLE only: it has no state/transition.py machine, so no row moves between
-- §4's states yet.
CREATE TABLE IF NOT EXISTS strategy_specs (
  spec_id          TEXT PRIMARY KEY,
  family           TEXT NOT NULL,              -- 'F1'..'F5' | 'petition:<name>'; enforced by StrategySpec.Family
  seat             TEXT NOT NULL,              -- proposing seat (charter name)
  hypothesis       TEXT NOT NULL CHECK(length(hypothesis) <= 500),
  mechanism_class  TEXT NOT NULL CHECK(mechanism_class IN
                     ('behavioral','institutional','risk_premium','liquidity_provision')),
  universe         TEXT NOT NULL,              -- JSON: {index, pit_constituents, filters[]}
  liquidity_bucket TEXT NOT NULL CHECK(liquidity_bucket IN ('mega_large','mid','small','micro')),
  signal_rule      TEXT NOT NULL,              -- JSON: coded rule + params w/ declared ranges
  param_ranges     TEXT NOT NULL,              -- JSON: {param: [lo, hi, step]}
  search_budget    INTEGER NOT NULL CHECK(search_budget > 0),
  holding_period_d INTEGER NOT NULL,
  rebalance        TEXT NOT NULL,
  expected_turnover REAL NOT NULL,
  exit_rule        TEXT NOT NULL,
  invalidation     TEXT NOT NULL,              -- falsifying observation, <=500 chars
  capacity_usd     REAL NOT NULL,
  predicted        TEXT NOT NULL,              -- JSON: {net_sharpe, max_dd, hit_rate}
  llm_in_loop      INTEGER NOT NULL DEFAULT 0, -- invariant 5 applies if 1
  lineage_parent   TEXT REFERENCES strategy_specs(spec_id),
  created_at       TEXT NOT NULL               -- injected Clock, ISO-8601 UTC
);

-- The Critic's G1 mechanism-alignment verdict. One row per spec, ever.
-- Written ONLY by submit_spec_critique (agents/tools/fund_server.py). The
-- orchestrator must never insert a default row here: at G1 a missing verdict
-- means the spec does not advance, the exact inverse of the trade pipeline's
-- advisory `critiques` table above. Nothing reads this table yet —
-- stratgate.evaluate_g1() is the G1 gate plan.
--
-- charter_version/model_id follow contracts.md §2's attribution contract. The
-- CHECKs NARROW §2's three values to the one this table can hold; they are not
-- a fourth rule. §2 allows 'none' for orchestrator-written rows and 'unknown'
-- as a fallback, and neither can legally occur here: nothing but
-- submit_spec_critique writes this table, and defaulting a G1 verdict is
-- forbidden outright.
CREATE TABLE IF NOT EXISTS strategy_critiques (
  spec_id         TEXT PRIMARY KEY REFERENCES strategy_specs(spec_id),
  verdict         TEXT NOT NULL CHECK (verdict IN ('clear','objections')),
  objections      TEXT NOT NULL DEFAULT '[]',  -- JSON array, <=3, each <=200 chars
                                               -- (empty iff verdict='clear')
  seat            TEXT NOT NULL,
  charter_version TEXT NOT NULL CHECK (charter_version NOT IN ('none','unknown')),
  model_id        TEXT NOT NULL CHECK (model_id NOT IN ('none','unknown')),
  slack_ts        TEXT,
  created_at      TEXT NOT NULL
);

-- Lifecycle state (the only mutable strategy row). Verbatim from
-- specs/strategy-contracts.md §2 — canonical, do not add fields here. The FK
-- to strategy_specs is §1's `strategy_id = spec_id` written into the schema:
-- with state/db.py:22's PRAGMA foreign_keys = ON, a lifecycle row cannot exist
-- without the immutable pre-registration it names.
--
-- Registration WRITES this row (issue #197): state/specs.py's
-- insert_strategy_spec INSERTs the spec and its lifecycle row in state SPEC
-- in one transaction, which is §3.1's "INSERTs spec + `strategies` row in
-- state SPEC". Nothing TRANSITIONS a row, though — this table has no
-- state/transition.py machine, so try_transition() raises IllegalTransition
-- for a table absent from EDGES, which is the right behaviour until §4's
-- edges are implemented. state_version is declared because §2 declares it,
-- not because anything reads it.
--
-- IF NOT EXISTS is load-bearing here and not style: state/db.py:12 matches
-- that exact string to build _TABLES. §2 spells it CREATE TABLE, per its own
-- convention; the two are the same table.
CREATE TABLE IF NOT EXISTS strategies (
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

-- protection: an append-only OBSERVATION LOG, verbatim from specs/contracts.md
-- §2 — canonical, do not add fields here. One row each time the fund sees a
-- protective order at the broker. Deliberately NO status column — ADR-0004
-- records why. What the fund KNOWS it saw and when; never what EXISTS now
-- (_covering_qty reads the broker for that). Like events/costs this is a log,
-- not a workflow table, so contracts.md §1 has no machine for it.
--
-- IF NOT EXISTS is load-bearing here and not style: state/db.py:12 matches
-- that exact string to build _TABLES. §2 spells it CREATE TABLE, per its own
-- convention; the two are the same table.
CREATE TABLE IF NOT EXISTS protection (
  id                TEXT PRIMARY KEY,          -- "<alpaca_order_id>@<observed_at>"
  symbol            TEXT NOT NULL,
  qty               INTEGER NOT NULL CHECK (qty > 0),
  stop_price        REAL CHECK (stop_price IS NULL OR stop_price > 0),
  alpaca_order_id   TEXT NOT NULL,
  client_order_id   TEXT,
  provenance_kind   TEXT NOT NULL
                    CHECK (provenance_kind IN ('observed','adopted')),
  broker_expires_at TEXT,
  observed_at       TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  UNIQUE (alpaca_order_id, observed_at)
);

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
--
-- The `weight` CHECK below is safe only because this table is new: CREATE
-- TABLE IF NOT EXISTS is a no-op against a table that already exists, and
-- state/migrations.py can only express ALTER TABLE ... ADD COLUMN, not the
-- rebuild SQLite needs to add a CHECK to an existing table — see issue #154.
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
  coverage      REAL NOT NULL,                -- seat-written signals on offered (run_date, ticker) pairs / n_offered, over the window
  cost_usd      REAL NOT NULL,                -- costs.usd_estimate summed over the window (est.)
  weight        REAL NOT NULL CHECK (weight >= 0 AND weight <= 1),
  narrowed      INTEGER NOT NULL DEFAULT 0,   -- §2.3: floor released
  inputs_hash   TEXT NOT NULL,                -- hash of the graded rows that produced this row
  created_at    TEXT NOT NULL,
  UNIQUE (as_of_date, agent)
);
