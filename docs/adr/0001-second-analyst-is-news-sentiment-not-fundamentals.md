# The second analyst is News/Sentiment, not Fundamentals

Status: accepted (2026-08-18)

Phase 2 canon named the two analyst seats **fundamentals + technical**
(`specs/design.md` §7 item 2; `docs/superpowers/specs/2026-07-12-phase-2-desk-design.md`
§1, validated 2026-07-12; `specs/contracts.md` §1 DDL uses `'fundamentals'` as the
example `signals.agent` value). We are instead building **technical + news/sentiment**,
because the Fundamentals Analyst as specified cannot be built on the firm's data surface
and would not fit the daily cycle even if it could.

## Why

**No fundamentals data exists.** The `specs/design.md` §2 seat table gives the Fundamentals Analyst
the job "financials, valuation, filings" on toolset `stock-data,news,account`. Alpaca's
market-data surface is price/quotes/trades, options, crypto, news, and corporate actions —
and no financial-statement data (`research/agent-alpha-discovery-brief.md`, data-surface
pass, 2026-08-18). All three of that seat's stated inputs are absent. It would fall back to
news plus price, which is the generalist seat it was meant to replace.

**Cadence mismatch, which is the deeper problem.** Fundamentals update quarterly; the cycle
is daily. On a three-name watchlist of large caps, each ticker yields roughly four fresh
datapoints a year, so the seat would emit ~63 near-identical signals per new fact. Those are
not independent graded calls, and `specs/calibration.md` §4's `N_eff ≈ N/5` correction is for
overlapping *horizons*, not repeated identical *evidence* — so the scoreboard would silently
overstate that seat's sample. A fundamentals lens wants a weekly or earnings-triggered stage,
which does not exist.

**News/sentiment is a real daily lens.** It is already a row in the `specs/design.md` §2 seat
table (toolset `news,stock-data`), it has a live feed, and it produces genuinely new
evidence every day. Splitting analysts by data modality and routing them upward to a manager
with no peer chat is also the dominant pattern in the field
(`research/agent-roles-survey.md` §1: FinCon, TradingGroup), and matches Phase 2's
no-debate shape.

## Considered options

- **Hold to canon; build Fundamentals with no fundamentals data.** Rejected: produces a seat
  whose charter promises evidence it cannot obtain. Every analyst charter binds the seat
  to treat unavailable inputs as missing evidence rather than licence to guess, so an honest
  seat submits neutral/low-confidence almost daily — the near-zero-resolution seat
  `research/improvement-loops.md` says not to upgrade and to review for retirement.
- **Source fundamentals externally (SEC EDGAR XBRL, Form 4, FINRA short interest).** Deferred,
  not rejected. The data is free and bulk-downloadable, but analyst seats hold
  `tools: ["mcp__fund__*", "mcp__alpaca__*"]` only, and `mcp_servers` is a hardcoded literal in
  `agents/seats.py` — so this needs the MCP seam that `PROGRESS.md` ("Eval scoping") lists as
  unbuilt. That is a data-source branch of its own, and it still leaves the cadence problem
  unsolved.
- **Keep the existing generalist and add one specialist.** Rejected: no generalist analyst
  appears in any of the twenty projects surveyed in `research/agent-roles-survey.md`, and the
  generalist has no row in the `specs/design.md` §2 seat table.

## Consequences

- `specs/design.md` line 63's Fundamentals Analyst row is **deferred**, not deleted. Reopening
  it requires two things, not one: an external financial-statement source, and a non-daily
  research stage. Phase 3+.
- **The technical seat keeps the id `analyst`; only `news` is new.** An earlier draft of this
  ADR said both seats would take new ids (`technical`, `news`). That rename was scoped, then
  deferred on measurement: `analyst` appears 154 times across 36 files, including six PM eval
  cases that carry `agent: analyst` and four seat-keyed maps in the eval rig
  (`WRITE_TABLES`, `PROMPT_TEMPLATES`, `PRECONDITIONS`, and the invariant schema map). A
  concurrent branch is making `evals/` seat-agnostic, which collapses those four into one
  entry per seat — so the rename is several times cheaper after that lands, and before it
  lands it also touches the PM eval baseline for no functional gain.
- Consequence accepted for now: the seat performing technical analysis is still called
  `analyst`, which reads oddly beside `news`. The existing `signals` history under
  `agent = 'analyst'` is preserved rather than orphaned, which is the one upside.
- **The deferral is not free, and this is the part that grows.** `signals.agent` is the
  grouping key for `calibration/rows.py` (analyst scoring → PM weights) and for the per-seat
  day scorecard. A rename landing *mid-corpus* does not orphan one day's history — it
  **splits one seat's history across two names**, so both consumers see two short samples
  instead of one long one, and `specs/calibration.md` §4 already puts a seat under 50 graded
  calls at provisional. Renaming today costs one live day (2026-08-17). Renaming after a
  quarter of live running costs a split in exactly the number this branch exists to produce.
  So: revisit as soon as the eval rig is seat-agnostic, not "eventually", and treat it as a
  data migration (rewrite `signals.agent` in place) rather than a code rename — the two
  consumers above must be told either way.
- Both seats cover the full active set rather than splitting it. `specs/calibration.md` §4
  puts a seat below 50 graded calls at provisional; at three tickers with `N_eff ≈ N/5`, full
  overlap reaches an `N_eff` of 50 in roughly 83 trading days, and splitting the watchlist
  would double that. The per-seat scoreboard is therefore **provisional for about four
  months** after this ships — two analysts is the right build, but the payoff is not available
  at merge.
