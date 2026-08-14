# HANDOFF — the first supervised live day

Everything in this repo is built and green offline. What is left is the one
thing an agent must not do for you: run real money-shaped commands against real
keys, watching. This is that runbook.

Do it **at the terminal, in order, during market hours** (09:35–15:30 ET on a
weekday). Do not skip a step because the previous one "looked fine". Every step
prints something specific; if it prints something else, stop and read §6.

Ten minutes if it goes well.

---

## 0. Before anything: prove this is the paper account

```bash
cd ~/Developer/fund          # this checkout
set -a; source .env; set +a
```

Now run the sanity check. **Do not proceed until every line reads OK.**

```bash
echo "PAPER=${ALPACA_PAPER_TRADE}"                    # must print exactly: PAPER=true
echo "DB=${FUND_DB}"                                  # must be a path, e.g. state/fund.sqlite
echo "SLACK=${SLACK_BOT_TOKEN:0:5}"                   # must print: SLACK=xoxb-
echo "ANTHROPIC=${ANTHROPIC_API_KEY:0:7}"             # must print: ANTHROPIC=sk-ant-
echo "ALPACA=${ALPACA_API_KEY:0:2}"                   # must print: ALPACA=PK
```

Then confirm the keys point at the **paper** endpoint and it is funded:

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        https://paper-api.alpaca.markets/v2/account | python3 -m json.tool | head -20
```

Expect `"status": "ACTIVE"`, a non-zero `"equity"`, and — the one that matters —
the account id should be the paper one you expect. If these credentials are
rejected by the paper endpoint, they are not paper credentials. **Stop.**

And confirm the market is actually open right now:

```bash
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        https://paper-api.alpaca.markets/v2/clock
```

Expect `"is_open":true`. If it is false, everything below will correctly refuse
to trade — come back when it is open.

### Known trap: the Slack token

The bot token must start with `xoxb-`. An `xapp-` app-level token cannot call
`chat.postMessage`; the outbox will dead-letter every event and the audit will
fail on `dead-lettered outbox events`. Fix the token, not the audit.

The bot must also be **invited to all five channels** the renderers post to:

```
#research  #trading-floor  #risk  #trade-log  #pnl
```

In each one: `/invite @<your-bot>`. A `not_in_channel` error dead-letters the
event exactly like a bad token does.

> Rehearsing somewhere harmless? Set
> `SLACK_CHANNEL_OVERRIDES=#pnl=#test-pnl,#risk=#test-risk,#trade-log=#test-trade-log,#research=#test-research,#trading-floor=#test-floor`
> and every post is remapped. Unset it for the real run.

---

## 1. The `@live` smoke — one share, end to end

```bash
.venv/bin/pytest -m live tests/test_live_smoke.py -v
```

**What it does:** seeds one gate ticket, runs a REAL Execution Trader turn
through the SDK, the seat calls `mcp__alpaca__place_stock_order` with the
ticket id as `client_order_id`, the `PreToolUse` hook validates it against the
ticket, the order round-trips on the paper account, and the `PostToolUse`
recorder mirrors it into SQLite. Then it posts to Slack.

**Expect:**

```
tests/test_live_smoke.py::test_one_share_paper_round_trip PASSED
tests/test_live_smoke.py::test_alpaca_source_account_state_and_close_frame PASSED

2 passed in ...
```

Takes up to ~2 minutes: the first `uvx alpaca-mcp-server` start is slow, and
the test polls the order for up to 90s.

**Capture now — evidence for acceptance box §4.1:**

```bash
# the fill JSON (substitute the client_order_id the test printed / the newest order)
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=5" \
  | python3 -m json.tool | tee /tmp/live-smoke-fill.json
```

Then open the `#trade-log` message the test posted and **copy its permalink**
(message ⋯ menu → Copy link).

| Evidence | Ticks |
|---|---|
| `/tmp/live-smoke-fill.json` showing status `filled` (or `canceled`) with your `client_order_id` | §4.1 "1-share paper order round-trips" |
| Slack permalink to the `#trade-log` post | §4.1 "fill in real Slack" |

A `canceled` status is acceptable ONLY if the market closed mid-test. During
market hours, expect `filled`.

### Then flatten it — do not skip this

The smoke **buys** 1 share of AAPL and only cancels the order if it stays
unfilled. During market hours it fills, and nothing in the test liquidates it.
§2's universe is `watchlist ∪ positions`, so a leftover AAPL share makes the
live day run **3 active tickers, not the 2** the reduced config specifies (§7)
— on the exact run whose purpose is measuring per-seat turn consumption. Close
it before §2:

```bash
curl -s -X DELETE -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        "https://paper-api.alpaca.markets/v2/positions/AAPL" | python3 -m json.tool
sleep 5
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        "https://paper-api.alpaca.markets/v2/positions" | python3 -m json.tool
```

The second call must print `[]`. (The DELETE submits a market sell, so give it
a moment to fill.) If you deliberately leave the share on, say so in the
acceptance notes and expect **3** active tickers in §2 — read `num_turns` per
seat accordingly, because the analyst's budget is per ticker.

---

## 2. The live day

```bash
make live-day
```

**What it does:** `scripts/run_day.py` — the one composition root. Real
`WallClock`, real Slack, real Alpaca paper, three real LLM seats built through
`build_seat_options`. Checks the broker's clock first; runs pre_gate →
research → decision → gate → execution → reconciliation → close; records every
turn's cost; then runs the audit itself.

**Expect, roughly in this order:**

```
run_day: 2026-MM-DD: watchlist ['NVDA', 'MSFT'] -> active ['MSFT', 'NVDA']
... (SDK seat output)
run_day: AUDIT CLEAN 2026-MM-DD
```

and `echo $?` → `0`.

Other legitimate outcomes:

- `run_day: market is closed — no stages run, nothing traded (exit 0)` — you
  ran it outside market hours. Not a failure; not a completed day either.
- `run_day: another run_day holds … — exiting 0 rather than racing it` — a
  scheduled run is already in flight. Not a failure: two overlapping processes
  would both re-run a stage sitting at `running`, meaning two seat turns' spend
  and two drains' Slack posts. Wait for the first one and read its log.
- `run_day: no active tickers — running the day with zero seat turns` — the
  pre-gate found nothing possible today (no cash headroom, nothing held). The
  day still completes, still posts a digest, and still prints `AUDIT CLEAN`:
  with no active tickers no seat turn is ever scheduled, so zero `costs` rows
  is that day's correct shape, not a violation. (A day that DID schedule turns
  and recorded no cost still fails — see §6.2.)
- A HOLD day. **This is a success.** The PM deciding HOLD on a boring day is
  the designed behaviour, not a broken run.

In Slack you should see: signals in `#research`, the PM's verdict in
`#trading-floor`, a gate ticket or rejection in `#risk`, a fill in
`#trade-log` if it traded, and the EOD digest in `#pnl`.

---

## 3. The audit

`make live-day` runs this itself, but run it again explicitly — it is the
written evidence:

```bash
python3 scripts/audit_day.py "$FUND_DB" "$(TZ=America/New_York date +%F)" > /tmp/audit-day.txt 2>&1
echo "exit=$?"          # no pipe: $? is the audit's own status, not tee's
cat /tmp/audit-day.txt
```

**Expect:**

```
exit=0
AUDIT CLEAN 2026-MM-DD
```

Any other output is a list of named violations, one per line, and exit 1. See §6.

---

## 4. Cost evidence

```bash
sqlite3 "$FUND_DB" \
  "SELECT agent, session_id, usd_estimate FROM costs WHERE run_date = '$(TZ=America/New_York date +%F)';"
sqlite3 "$FUND_DB" \
  "SELECT ROUND(SUM(usd_estimate), 4) FROM costs WHERE run_date = '$(TZ=America/New_York date +%F)';"
```

Expect one row per seat turn that ran (analyst + pm, plus exec only if there
was an open ticket) and a total **≤ $0.50**. This is a client-side estimate —
it is labelled `est.` in the digest for exactly that reason.

---

## 5. What ticks what

| Evidence | Where it lands | Acceptance box (`docs/superpowers/specs/2026-08-12-mvf-scope.md` §4) |
|---|---|---|
| Fill JSON with your `client_order_id` | `/tmp/live-smoke-fill.json` | §4.1 — `@live` smoke round-trips |
| Slack permalink to the `#trade-log` post | Slack | §4.1 — fill visible in real Slack |
| `AUDIT CLEAN <date>` + exit 0 | `/tmp/audit-day.txt` | §4.7 — ≥1 full market day completes |
| `decisions` row for the day, `digest` event posted to `#pnl` | SQLite + Slack | §4.7 — decision row + (if traded) fill + digest |
| `costs` sum ≤ $0.50 | SQLite | §4.9 — live day ≤ $0.50 est. |

Paste all five into the commit message (or PR body) that ticks the boxes.

---

## 6. Abort criteria — read this before you need it

**STOP, capture, report. Do not retry blind.** Re-running a live day after an
unexplained failure is how you turn one bad order into two.

Stop immediately on any of:

1. **A hook deny on a live order.** The seat's `place_*` call came back denied
   by the `PreToolUse` gate. That means an order was attempted that no valid
   ticket authorised — the gate did its job, and the reason it had to is the
   bug. Capture the deny reason.
2. **Any audit violation.** Any line other than `AUDIT CLEAN`. Each names its
   own shape: a stuck checkpoint, a decision stranded at `submitted`/
   `approved`, an order stuck `submitted`, an undrained outbox, a dead-lettered
   event *raised today*, an alert *raised today*, or no cost rows on a day that
   scheduled seat turns. Every count is scoped to the audited ET day, so
   yesterday's alert never reds today — and the audit's own failure alert is
   marked and excluded, so a re-fire cannot compound it.
3. **`run_day_failed — …` in `#risk`.** Something raised between the DB
   connection and the audit: a stage body, the watchlist/sectors load, or the
   market-data fetch. The day stopped there and **the audit did not run**, so
   the DB is mid-flight — capture it below before anything else. Note this can
   land after a ticket was minted and an order placed.
4. **An order you did not expect** — wrong symbol, wrong side, or a quantity
   above the ticket's `max_qty`.
5. **The paper guard firing** (`ALPACA_PAPER_TRADE must be 'true'`). Do not
   "just export it" — find out why it was wrong.

Capture the state before touching anything:

```bash
DATE="$(TZ=America/New_York date +%F)"
cp "$FUND_DB" "/tmp/fund-$DATE-postmortem.sqlite"
python3 scripts/audit_day.py "$FUND_DB" "$DATE" > /tmp/audit-fail.txt 2>&1
sqlite3 "$FUND_DB" "SELECT stage,status FROM checkpoints WHERE run_date='$DATE';"
sqlite3 "$FUND_DB" "SELECT id,ticker,action,qty,status FROM decisions WHERE run_date='$DATE';"
sqlite3 "$FUND_DB" "SELECT client_order_id,symbol,side,qty,status,filled_qty FROM orders;"
sqlite3 "$FUND_DB" "SELECT id,kind,posted_at,payload FROM events ORDER BY id DESC LIMIT 20;"
curl -s -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
        -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=20" > /tmp/broker-orders.json
```

Then report with those artefacts attached.

**Safe to re-run:** if — and only if — the failure was a *crash* partway
through (process killed, laptop slept, network dropped) and the audit shows
stages stuck at `running`/`pending` rather than a wrong-looking order. Every
stage is behind a checkpoint CAS, so `make live-day` resumes rather than
repeats, and `client_order_id` idempotency means a re-placed order is
422-rejected by the broker, not double-filled. When in doubt, do not.

**One known non-safety violation to recognise:** if the ONLY audit line is
`alert events raised: 1` and the alert text starts with `cost_unavailable`,
the SDK simply did not populate `total_cost_usd` for that turn. The fund
records no cost row rather than a fake `0.00`. It is still a STOP-and-report —
capture the `ResultMessage` (see §7) — but it is an accounting gap, not a
trading fault.

---

## 7. The reduced config — what to capture, and why

The first live day deliberately runs **smaller than the design allows**:

| Setting | Value today | Where |
|---|---|---|
| Watchlist | **2 tickers** (NVDA, MSFT) | `config/watchlist.yaml` |
| Analyst `max_turns` | **16** | `agents/config/analyst.yaml` |

Both carry the comment `provisional — right-size from first live
ResultMessage`. The reason: the analyst charter budgets ≤4 tool calls per
ticker, and how the SDK counts a tool call against `max_turns` cannot be
determined offline. Exhausting the budget degrades safely (the ticker defaults
to neutral/0), but demo day deserves a real signal — so there is headroom now
and it gets tightened on evidence, not on a guess.

**Capture from the first run, per seat:**

- `ResultMessage.num_turns` — how many turns the seat actually consumed
- `ResultMessage.total_cost_usd` — the est. spend for that turn

`num_turns` is not persisted in the DB, so `run_day` logs it explicitly after
every seat turn — one line per seat, independent of the SDK's own stdout
formatting:

```bash
grep "turn done:" logs/run_day.out.log
# run_day: analyst turn done: num_turns=11 est_cost_usd=0.0182
# run_day: pm turn done: num_turns=6 est_cost_usd=0.0094
```

Running interactively, the same lines are on the console. `total_cost_usd` is
also in the `costs` table (§4).

Then, in a **follow-up commit**, right-size both values and drop the
`provisional` comments:

```
chore: right-size analyst max_turns and watchlist from first live day

num_turns observed: analyst N, pm M.
```

If `num_turns` came in at or above `max_turns`, the seat was truncated — raise
the ceiling before widening the watchlist, not after.

---

## 8. After a clean day

```bash
# schedule it (see README §Scheduling — replace the placeholder path first)
cp ops/com.fund.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fund.daily.plist
launchctl list | grep com.fund.daily
```

Then check `logs/run_day.out.log` each morning, and the `#pnl` digest each
afternoon.
