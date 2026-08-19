CREATE TABLE signals (
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

CREATE TABLE critiques (                       -- Critic's advisory review of the PM's DRAFT
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
CREATE TABLE decisions (
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

CREATE TABLE tickets (
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

CREATE TABLE orders (
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

CREATE TABLE resolutions (
  id            INTEGER PRIMARY KEY,
  decision_id   INTEGER NOT NULL REFERENCES decisions(id) UNIQUE,
  horizon_days  INTEGER NOT NULL,             -- default 5 trading days
  realized_return REAL NOT NULL,
  alpha_vs_spy  REAL NOT NULL,
  invalidated   INTEGER NOT NULL DEFAULT 0,   -- invalidation condition hit early
  reflection    TEXT,                         -- written by the deciding agent
  resolved_at   TEXT NOT NULL
);

CREATE TABLE checkpoints (
  run_date      TEXT NOT NULL,
  stage         TEXT NOT NULL,                -- standup|research|debate|decision|gate|execution|close|reflection
  ticker        TEXT NOT NULL DEFAULT '*',
  status        TEXT NOT NULL DEFAULT 'pending',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (run_date, stage, ticker)
);

CREATE TABLE events (                          -- outbox: SQLite truth -> Slack projection
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,
  payload       TEXT NOT NULL,                -- JSON
  created_at    TEXT NOT NULL,
  posted_at     TEXT                          -- NULL until projected to Slack
);

CREATE TABLE costs (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,
  agent         TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  usd_estimate  REAL NOT NULL,                -- ResultMessage.total_cost_usd (client-side est.)
  recorded_at   TEXT NOT NULL
);
