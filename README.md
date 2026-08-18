# fund — an AI hedge fund run by long-lived agents

A multi-agent paper-trading firm on the [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview).
Analyst and PM agents make a real decision each morning from live market data
via their own tool calls. Between every LLM decision and every broker order
sits a **deterministic Python gate** that no agent can talk its way past.

Agents decide inside a deterministic control envelope. That is the whole idea.

> **Paper only.** `ALPACA_PAPER_TRADE=true` everywhere; there is no live-trading
> code path and adding one is invariant #1's whole job to prevent.

---

## Architecture

The following diagram traces one market day, from the scheduled fire to the
Slack projection, with SQLite as the source of truth and Slack as a read-only
projection of it:

```
                       ┌──────────────── one fire per market day ─────────────┐
                       │  launchd 09:35 ET  ->  scripts/run_day.py            │
                       │  (the ONLY place real clock/Slack/Alpaca/LLM meet)   │
                       └──────────────────────┬──────────────────────────────┘
                                              │ market closed? exit 0, trade nothing
                                              v
   orchestrator/  ── stages, sequential, each behind a checkpoint CAS ──────────
   pre_gate -> research -> decision -> gate -> execution -> reconciliation -> close
       │           │          │         │         │            │          │
       │           v          v         │         v            │          v
       │      ┌─────────┐ ┌───────┐     │    ┌────────┐        │      EOD digest
       │      │ analyst │ │  pm   │     │    │  exec  │        │
       │      │  seat   │ │ seat  │     │    │  seat  │        │
       │      └────┬────┘ └───┬───┘     │    └───┬────┘        │
       │           │ MCP tools│         │        │             │
       │           v          v         │        v             │
       │    submit_signal  submit_decision   list_open_tickets  │
       │    (analyst-only) (pm-only)         (exec-only)        │
       │           │          │              │                 │
       │           └──────────┴──────┬───────┘                  │
       │                             v                          │
       │                  ╔══════════════════════╗              │
       └─ allowed actions ║   gate/  (NO LLM)    ║              │
          {buy:n, sell:n} ║  vol/corr tiers      ║              │
                          ║  cash + sector caps  ║              │
                          ║  8-position limit    ║              │
                          ║  -3% circuit breaker ║              │
                          ╚═══════════╤══════════╝              │
                                      │ ticket (id == client_order_id, TTL 45m)
                                      v                         │
                       PreToolUse hook: no valid ticket -> DENY  │
                                      │                         │
                                      v                         v
                        mcp__alpaca__place_* ──────────> Alpaca paper broker
                                      │                         │
                       PostToolUse hook: mirror the fill        │ fill poll
                                      v                         v
                          SQLite (source of truth)  ──outbox──> Slack (projection)
```

Reading it in one line: **agents → MCP tools → deterministic gate → hook → broker**,
with SQLite as the source of truth and Slack as a read-only projection of it.

| Package | Role | LLM code? |
|---|---|---|
| `scripts/run_day.py` | the live composition root — real clock, Slack, Alpaca, seats | wires it |
| `orchestrator/` | stage machine, checkpoints, fill-poll reconciliation | **no** |
| `agents/` | one SDK seat per role, hooks, seat options factory | yes |
| `gate/` | tiered sizing, ticket store, order validation | **no** |
| `market/` | Alpaca reads (`source_alpaca`) + pure feature math (`features`) | **no** |
| `state/` | SQLite DDL, CAS state machines, journals | **no** |
| `slackkit/` | outbox, renderers, real/fake Slack ports | **no** |
| `fundbt/` `stratgate/` `calibration/` | research stack: backtests, strategy gates G1–G4, analyst scoring | **no** |

`gate/`, `stratgate/`, `calibration/`, `orchestrator/` and `state/` are held
LLM-free by an AST lint (`scripts/check_purity.py`) that runs in `make test`.

## What it does

- **Real agentic decisions.** The analyst picks its own tool calls against live
  quotes and news; the PM sizes a buy/sell/hold and must be able to genuinely
  choose HOLD on a boring day. Neither can place an order.
- **A deterministic risk gate.** Volatility tiers, correlation multipliers, a
  cash cap, an 8-position limit, a 60% sector cap with resize, and a −3% daily
  circuit breaker — one code path serves both the advisory pre-gate and the
  enforcement pass, so what the PM was shown is what the gate enforces
  (`tests/test_sim_day.py::test_pm_brief_carries_the_signal_and_the_budget_the_gate_enforces`).
- **A hook that cannot be argued with.** A `PreToolUse` hook denies any order
  without an open, unexpired gate ticket. Hooks run before permission rules;
  the agent's only path to the broker goes through it.
- **Idempotent execution.** `client_order_id` is always the ticket id, so a
  retry is 422-rejected by the broker rather than double-filled.
- **Default HOLD, everywhere.** A missing analyst signal becomes neutral/0; a
  silent PM becomes hold/0 plus an alert; a NaN in the feed rejects the trade;
  an unreadable market clock skips the day. Every one of those is a test.
- **Slack as the firm's UI.** Signals, gate verdicts, fills and the EOD digest
  are projected from an outbox with per-event dead-lettering.
- **Structured output only.** Agents emit data through MCP tool schemas that
  are advisory to the model (the pinned SDK has no `strict=True`); the
  pydantic handler validates every safety-relevant constraint, so no code
  anywhere parses a ticker, an action or a size out of free text.
- **Record/replay test suite.** 518 offline tests including six full simulated
  day shapes that run the real gate, hooks, tools, DB and fill-poll against
  recorded LLM decisions — no network, no API keys, $0 of inference.

## Run it

```bash
make test         # full offline suite + purity lint. No network, no keys.
make sim-day      # six simulated day shapes: real everything, recorded LLM
make live-day     # ONE real day: real Slack + Alpaca paper + real LLM seats
```

Live runs need `.env` (copy `.env.example`) loaded into the shell:

```bash
set -a; source .env; set +a
make live-day
python3 scripts/audit_day.py "$FUND_DB" "$(TZ=America/New_York date +%F)"
```

`scripts/run_day.py` refuses to start unless `ALPACA_PAPER_TRADE=true`, names
every missing env var on line one, and exits 0 without trading when the
broker's clock says the market is closed.

**First supervised live run: follow [`HANDOFF-LIVE.md`](HANDOFF-LIVE.md).** It
has the exact command sequence, the expected output of each step, and the abort
criteria.

### Scheduling

One fire per market day — no daemon. The day is a sequential process that
exits; checkpoint CAS makes a re-fire resume rather than repeat.

**launchd (macOS).** Replace `/ABSOLUTE/PATH/TO/fund` in
`ops/com.fund.daily.plist` with this checkout's path, then:

```bash
cp ops/com.fund.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fund.daily.plist
launchctl list | grep com.fund.daily
# stop with: launchctl unload ~/Library/LaunchAgents/com.fund.daily.plist
```

**cron (alternative).**

```cron
TZ=America/New_York
35 9 * * 1-5 cd /ABSOLUTE/PATH/TO/fund && set -a && . ./.env && set +a && .venv/bin/python3 scripts/run_day.py >> logs/run_day.log 2>&1
```

Both fire at 09:35 ET, Mon–Fri. launchd's `StartCalendarInterval` uses the
machine's **local** time and ignores `TZ`, so the plist is correct only on a
machine set to America/New_York — cron's `TZ=` line does honour it.
Market holidays and early closes need no schedule change: the run exits 0 on
the broker's clock.

**A LaunchAgent cannot wake a sleeping Mac.** `StartCalendarInterval` fires on
the next wake instead, so a laptop asleep at 09:35 ET runs the day whenever you
open it — or not at all, if that is after the close. This is fail-safe, not
fail-silent (the `market_is_open` guard still holds), but it is not a schedule.
On a Mac, pair the agent with a wake:

```bash
sudo pmset repeat wakeorpoweron MTWRF 06:30:00   # 5 min before a 06:35 local fire
pmset -g sched                                    # verify it registered
```

You also stay logged in: a LaunchAgent runs in your user session, not as a
daemon. A laptop is a poor host for this — the first trip with the lid shut is
a day that silently does not happen.

**Run it on exactly ONE host, ever.** `acquire_lock` uses `flock`, which is
machine-local, so a second host does not see the first one's lock. Each would
keep its own SQLite, mint its own tickets, and place its own orders —
`client_order_id` idempotency cannot help, because the ids differ. That is a
real double-trade. Unload the agent on the old machine before enabling a new
one.

## Cost

Runtime is Haiku-tier for the analyst and exec seats, Sonnet-tier for the PM
(a strong tier for the PM is deliberate — `specs/design.md` §2), with per-seat
`max_budget_usd` caps in
`agents/config/*.yaml` — never hardcoded. Three seat turns at MVF's watchlist
size is the whole daily spend, and a HOLD day skips the execution turn
entirely (zero tickets → no LLM call).

The caps are **backstops, not the expectation**, and the two numbers are not
the same number: **caps sum to $2.25 worst-case** (analyst $0.50 + pm $0.75 +
exec $1.00); **expected spend is < $0.50/day**; **measured after the first
live day**. What bounds the expectation is the watchlist size and each seat's
`max_turns`, not the caps — a day that actually hits $2.25 is a runaway to
investigate, not a budget that was spent as designed.

Every turn's cost is recorded to the `costs` table and summed into the EOD
digest. `ResultMessage.total_cost_usd` is a **client-side estimate**, so it is
labelled `est.` everywhere it is surfaced. When the SDK does not populate it,
the fund records no row and raises an alert rather than writing a `0.00` that
would make real spend look free.

All offline testing costs $0: the record/replay architecture replaces the LLM
and nothing else.

## Status

Phase 1 (execution plumbing) and the MVF slice (risk gate, market features,
fund MCP tools, analyst + PM seats, daily loop, fill-poll, digest, audit,
schedule) are built and green offline: **556 tests, purity lint clean**.

**Live since 2026-08-17.** All nine MVF acceptance boxes are ticked
(`docs/superpowers/specs/2026-08-12-mvf-scope.md` §4). That day's clean run:

    analyst  NVDA bullish 72 · MSFT neutral 40      7 turns   $0.0504
    pm       NVDA buy 80 @ stop 215 · MSFT hold     5 turns   $0.1161
    exec     filled 80 @ 227.09, oto + stop leg     3 turns   $0.0332
    AUDIT CLEAN 2026-08-17, zero alerts             total     $0.1997

The first run that morning failed, and the postmortem is worth reading before
trusting anything here: the gate validated a nested stop-leg shape the broker
has never exposed, so every ticket carrying a stop was undeliverable — and the
whole offline suite was green over it, because the fixtures encoded the same
wrong assumption. Fixture and code agreed with each other and both disagreed
with Alpaca. `make schema-pin` now asks the real server before every live day;
no offline test can catch that class of bug. The failed run's red audit is kept
in `state/fund-2026-08-17-incident.sqlite`.

Next: the resolutions/reflection loop (decisions currently produce no scored
outcomes, so `calibration/` has never been fed), then the second analyst seat.

## Map of the repo

- `CLAUDE.md` — the seven invariants. Read first.
- `specs/design.md` — seats, daily cycle, gate math.
- `specs/contracts.md` — DDL, pydantic models, tool schemas, state machines.
- `specs/acceptance.md` — per-phase done criteria.
- `specs/strategy.md`, `specs/strategy-contracts.md`, `specs/calibration.md` —
  the research stack: pre-registration, backtest rules, gates G1–G4,
  allocation and kill rules, analyst scoring → PM weights.
- `charters/` — seat system prompts (`pm.md` and `quant.md` are the quality bar).
- `fixtures/golden-day.md` — one worked day, used as test vectors.

### Research stack

`fundbt/` (backtest engine + trial registry), `stratgate/` (DSR/PSR/MinTRL/WFE
statistics and the G2/G3 evaluators) and `calibration/` (Brier/BSS scoring →
deterministic PM weights) are pre-built and tested. Rules they inherit:

- LLMs hypothesise; code validates. Nothing in those packages imports LLM code.
- Default is REJECT: NaN/missing/malformed anywhere → the strategy does not advance.
- Every backtest logs a trial row; family-wide N feeds the deflated Sharpe.
- The holdout is consumed exactly once per spec, enforced by a PRIMARY KEY.
- Cost floors are constants, not parameters.

Run a backtest only through the `run_backtest` tool — an unlogged trial
corrupts the deflated-Sharpe correction fund-wide.
