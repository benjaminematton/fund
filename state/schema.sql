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
-- UPDATE ever; supersede via lineage. `strategies` (lifecycle state) is
-- deliberately NOT here: nothing in this phase reads it, and the G1 gate
-- plan adds it with the transitions that need it.
CREATE TABLE IF NOT EXISTS strategy_specs (
  spec_id          TEXT PRIMARY KEY,
  family           TEXT NOT NULL,              -- 'F1'..'F5' | 'petition:<name>'
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
