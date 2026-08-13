# Contracts — canonical schemas, state machines, failure semantics

Everything downstream (code, tests, fixtures) conforms to this file. Change here first.

## 1. State machines

Apply transitions only via `state.transition(table, id, from_status, to_status)` — it asserts the edge is legal and the row is currently in `from_status` (compare-and-swap), making handlers idempotent under retry.

**decision**: submitted → approved | rejected | held (held: gate-settled hold, terminal) · approved → executed | failed | expired
**ticket**: `open → consumed | expired`
**order**: `submitted → filled | partially_filled | canceled | rejected`; `partially_filled → filled | canceled`
**checkpoint stage**: `pending → running → done | failed`

## 2. SQLite DDL

```sql
CREATE TABLE signals (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL,                -- YYYY-MM-DD (ET)
  agent         TEXT NOT NULL,                -- seat name, e.g. 'fundamentals'
  ticker        TEXT NOT NULL,
  direction     TEXT NOT NULL CHECK (direction IN ('bullish','bearish','neutral')),
  confidence    INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  summary       TEXT NOT NULL,                -- <= 500 chars
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
  slack_ts      TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (run_date, ticker)
);
-- Advisory only: no FK to decisions (the critique precedes the decision row) and
-- nothing downstream branches on it. Joined by (run_date, ticker) for the
-- scoreboard's objection hit-rate and for weekly reviews.

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
```

## 3. Pydantic models (pydantic v2 — mirror the DDL exactly)

```python
Direction = Literal["bullish", "bearish", "neutral"]
Action    = Literal["buy", "sell", "hold"]
Side      = Literal["buy", "sell"]

class Signal(BaseModel):
    run_date: date; agent: str; ticker: str
    direction: Direction
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(max_length=500)

class Critique(BaseModel):
    run_date: date; ticker: str
    verdict: Literal["clear", "objections"]
    objections: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def objections_iff_verdict(self):
        assert (self.verdict == "objections") == (len(self.objections) > 0)
        assert all(len(o) <= 200 for o in self.objections); return self

class Decision(BaseModel):
    run_date: date; ticker: str
    action: Action
    qty: int = Field(ge=0)
    thesis: str; invalidation: str
    stop_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def hold_means_zero(self):
        assert (self.action == "hold") == (self.qty == 0)
        assert self.stop_price is None or self.action == "buy"   # stops guard new/added longs only
        return self

class Ticket(BaseModel):
    id: str; decision_id: int; ticker: str; side: Side
    max_qty: int = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    expires_at: datetime

class GateResult(BaseModel):
    approved: bool
    ticket: Ticket | None = None
    reason: str | None = None                # required when approved=False
```

## 4. Agent tool schemas (in-process MCP, `create_sdk_mcp_server`)

All schemas declare `"strict": true`. Handlers validate with the pydantic models above, UPSERT to SQLite, append a projection event, and return a one-line confirmation. **These tools are the only path from agent output to workflow state.**

```python
@tool("submit_signal",
      "Record your final daily signal for one ticker. Call exactly once per ticker.",
      {"type": "object",
       "properties": {
         "ticker":     {"type": "string"},
         "direction":  {"type": "string", "enum": ["bullish","bearish","neutral"]},
         "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
         "summary":    {"type": "string", "maxLength": 500}},
       "required": ["ticker","direction","confidence","summary"],
       "additionalProperties": False},
      strict=True)

@tool("submit_critique",
      "Critic only. Record your advisory review of the PM's draft for one ticker. Call exactly once per assigned ticker.",
      {"type": "object",
       "properties": {
         "ticker":     {"type": "string"},
         "verdict":    {"type": "string", "enum": ["clear","objections"]},
         "objections": {"type": "array", "items": {"type": "string", "maxLength": 200},
                        "maxItems": 3,
                        "description": "Required non-empty iff verdict='objections'."}},
       "required": ["ticker","verdict"],
       "additionalProperties": False},
      strict=True)

@tool("submit_decision",
      "PM only. Record the final decision for one ticker. Irrevocable for the day.",
      {"type": "object",
       "properties": {
         "ticker":       {"type": "string"},
         "action":       {"type": "string", "enum": ["buy","sell","hold"]},
         "qty":          {"type": "integer", "minimum": 0},
         "thesis":       {"type": "string"},
         "invalidation": {"type": "string"},
         "stop_price":   {"type": "number", "exclusiveMinimum": 0,
                          "description": "Optional. Set iff the invalidation is a hard price level (buy only); the trader will attach it as a broker-side stop leg (oto order)."}},
       "required": ["ticker","action","qty","thesis","invalidation"],
       "additionalProperties": False},
      strict=True)
```

Availability: `submit_signal` → analyst seats only; `submit_critique` → Critic only; `submit_decision` → PM only. A tool called by the wrong seat returns an error (checked against the seat name baked into the server at construction).

Ordering within the Decision stage: PM draft (Slack only) → `submit_critique` → PM acknowledgment (Slack) → `submit_decision`. The handler for `submit_decision` refuses (tool error, PM retries) if no critique row exists yet for `(run_date, ticker)` — this enforces the draft→critique→final ordering without making the critique blocking: on critic timeout the orchestrator inserts the `clear`/`critic_timeout` row itself, and the PM proceeds. When no critic seat is configured (phases 1–2), the orchestrator inserts `clear`/`no_critic_seat` rows at stage start and the Decision stage runs as a single turn.

## 5. Idempotency & retry rules

1. Order placement: `client_order_id = ticket.id`. On network error, retry the SAME id; Alpaca returns 422 `client_order_id must be unique` if the first attempt landed → treat 422-after-retry as success and reconcile via GET order-by-client_order_id.
2. Stage re-runs (crash resume): every stage handler first reads its checkpoint; `done` → skip. Handlers must be re-runnable: UPSERTs keyed on natural keys (`run_date, agent, ticker` etc.), transitions via compare-and-swap.
3. Slack projection: the poster marks `events.posted_at`; a crash between post and mark can duplicate a Slack message — acceptable (Slack is a projection), never retry into a second DB write.

## 6. Failure semantics (stage × failure → behavior)

| Failure | Behavior |
|---|---|
| Analyst never calls `submit_signal` by stage deadline | Missing signal recorded as `neutral/0` with summary "no report"; pipeline continues |
| Critic never calls `submit_critique` by stage deadline | Orchestrator inserts `verdict='clear', note='critic_timeout'`; PM proceeds — the critique is advisory and never stalls the day |
| PM tool call invalid / never arrives | Decision defaults to HOLD for that ticker; event `pm_timeout` posted to `#risk` |
| Gate error/timeout/malformed input | REJECT with reason `gate_error` → HOLD (invariant 4) |
| Alpaca MCP down at execution | Retry 3× w/ backoff within ticket expiry; then ticket `expired`, decision `failed`, alert `#risk` |
| Slack down | Workflow proceeds (DB-driven); outbox drains when Slack returns |
| Agent process crash mid-day | Supervisor restarts container; runtime resumes via stored `session_id` + `resume=`; orchestrator re-assigns from last checkpoint |
| Orchestrator crash | On restart: today's checkpoints define restart point; stages `done` never re-run |
| Market half-day / holiday | Alpaca `get_clock`/calendar drives stage times; never hardcode 16:00 |
| Duplicate stage trigger | Checkpoint CAS makes second trigger a no-op |

## 7. Strategy platform contracts — see `specs/strategy-contracts.md`

The canonical schemas, content-addressed ids (`spec_id`/`config_hash`/`run_key`), state machine, tool contracts, and failure semantics for the strategy pipeline live in `specs/strategy-contracts.md`. That file was written alongside the starter kit's tested implementation (`fundbt/registry.py`, `stratgate/`) and is authoritative — an earlier draft of those schemas in this file has been removed in its favor. Shared conventions carry over unchanged: SQLite is the source of truth, Slack projection via the `events` outbox, transitions via compare-and-swap, default is REJECT. Analyst-scoring contracts (Brier/BSS → PM weights): `specs/calibration.md`, implemented in `calibration/`.

## 8. Slack message formats (projection only)

- Signal: `[<agent>] <TICKER> — <DIRECTION> (<confidence>/100): <summary>` in `#research` thread.
- Critique: `CRITIQUE <TICKER>: CLEAR` or `CRITIQUE <TICKER>: <n> OBJECTION(S)` + numbered one-sentence objections, as a reply in the ticker's debate thread.
- Gate approval: `✅ TICKET <id[:8]> <side> <TICKER> ≤<max_qty> expires <HH:MM>` in `#risk`; rejection: `⛔ <TICKER> <side> — <reason>`.
- Fill: `🧾 <TICKER> <side> <filled_qty>@<avg_price> (ticket <id[:8]>)` in `#trade-log`, threaded to the decision message.
- EOD digest fields: P&L $ and % vs SPY, positions table, decisions + outcomes, est. inference cost.
