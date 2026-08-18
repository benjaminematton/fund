# Progress

Running record of where the fund actually stands. Read this first in a new
session; `README.md` explains what the system *is*, this explains what it has
*done* and what is *open*.

Update it when a milestone lands or an open item closes — not per commit.

---

## Status — 2026-08-17

| | |
|---|---|
| **Mode** | Alpaca **paper** only (invariant 1) |
| **Live since** | 2026-08-17 — first clean end-to-end day |
| **HEAD** | `352165d` |
| **Tests** | 574 offline, green; purity lint clean |
| **Watchlist** | NVDA, MSFT, AAPL |
| **Open position** | NVDA 80 @ 227.09, live stop at 215 |
| **Scheduled on** | this Mac (Pacific) — `com.fund.daily` loaded |

### The live day, as recorded

All 7 checkpoints `done`, audit clean.

- **analyst** → 2 signals (NVDA bullish 72%, MSFT bearish 40%)
- **pm** → 2 decisions: NVDA buy 80 w/ stop 215; MSFT hold 0
- **gate** → 1 ticket, `max_qty` 80, stop 215
- **exec** → 1 order, filled 80 @ 227.09, `oto` with a flat stop leg

The 2026-08-17 *incident* DB is preserved unmodified at
`state/fund-2026-08-17-incident.sqlite` — the failed first attempt, kept as the
honest record. No DB surgery was performed on it.

---

## What runs unattended

Two launchd jobs. **Both must live on the same host as `state/fund.sqlite`, and
only one host may run either** — `flock` is machine-local, and two hosts means
two ticket-id namespaces, so idempotency will not dedupe duplicate orders.

| job | fires | does | installed? |
|---|---|---|---|
| `com.fund.daily` | 09:35 ET Mon–Fri | full trading day, self-audits | ✅ loaded |
| `com.fund.pnl` | 16:35 ET Mon–Fri | posts P&L $ / % vs SPY | ❌ **not installed** |

`StartCalendarInterval` uses **machine local time**. The committed plists in
`ops/` are the ET-machine templates (Hour 9 / Hour 16); this Mac is Pacific, so
its installed copies use Hour 6 / Hour 13. Re-derive on any host move, and
re-check after every DST change.

16:35 (not 16:15) is measured, not chosen: `close_frame` shifts its end back
`SIP_DELAY` (16 min), so a 16:15 fire asks for 15:59 — before the closing
auction writes the bar.

The Mac must be awake at fire time. `sudo pmset repeat wakeorpoweron MTWRF
06:30:00` wakes from sleep on AC power; it does not reliably power on from a
full shutdown on Apple Silicon. Sleep it, don't shut it down.

---

## Milestones

| date | what |
|---|---|
| — | `fundbt/` + `stratgate/` + `calibration/` built and tested (research side, not yet wired to the daily cycle) |
| — | orchestrator, gate, three agent seats, Slack projection |
| 2026-08-17 | first live attempt — **failed**: gate validated a stop-leg shape the broker never accepted |
| 2026-08-17 | root-caused by introspecting the live MCP tool schema; fixed + pinned (`7cbf2f1`, `d3320cd`) |
| 2026-08-17 | four observability defects closed (`f94c4d7`, `a26581d`, `9d909e9`) |
| 2026-08-17 | **first clean live day**; MVF acceptance met (`8ee168f`) |
| 2026-08-17 | EOD P&L vs SPY added as a second digest message (`92600bc`, `352165d`) |

### The one that mattered

`gate/tickets.py` validated a nested `stop_loss: {stop_price}`; the real
`place_stock_order` takes a **flat** `stop_loss_stop_price` string. Every
stopped ticket was undeliverable in both directions — the gate denied the real
shape and the broker rejected the assumed one.

The entire offline suite was green over a total outage, because
`tests/fake_alpaca.py` and the recordings encoded the *same* wrong assumption as
the code. Fixture and code agreed with each other and both disagreed with
Alpaca. That is why `make schema-pin` exists and why it is step §0b of the
runbook: it introspects the live server's schema and fails if the field names
move.

---

## Open items

**Now**

- [ ] Push 2 unpushed commits (`92600bc`, `352165d`)
- [ ] Install `ops/com.fund.pnl.plist` — without it there is no P&L digest
- [ ] `sudo pmset repeat wakeorpoweron MTWRF 06:30:00` (needs sudo — Benjamin)
- [ ] **Move the run to a VM** (decided 2026-08-17). Debian box,
      `TZ=America/New_York` — which permanently kills the plist-Hour, DST, and
      sleep/wake traps. Note `CLAUDE.md`'s `docker compose up` describes files
      that **do not exist**; there is no Dockerfile and no compose file, so this
      is a fresh deploy, not a lift-and-shift. Size it small: all three seats are
      remote API calls, local compute is ~zero. Cutover rule — unload
      `com.fund.daily` here **first**; two hosts means two ticket-id namespaces
      and idempotency will not catch the duplicate orders.

**Next branches** — each belongs in a **new chat**, per the standing rule that
new implementation branches get fresh context.

- [ ] **Agent eval.** Scoped: see "Eval scoping" below. Start with the
      injection case at the PM boundary.
- [ ] **Union asymmetry** in the price-history exclusion (ledgered ruling,
      2026-08-17)
- [ ] **Resolutions / reflection loop** — nothing currently closes the feedback
      cycle from outcome back into analyst calibration
- [ ] **Second analyst seat** — the debate mechanic in the design has one voice
- [ ] README demo recording

**Last, after everything above** — decided 2026-08-17

- [ ] **Fund it with real money.** The goal is not returns; it is the claim
      *"I built an agentic system I trusted enough to put my own money behind."*
      Judge this milestone on whether that sentence is defensible, not on P&L.

      **Fund ~$1,200, not less.** The gate approves only when
      `equity × vol_tier × corr_mult ≥ price` (`gate/risk.py:87-91`). At $100
      the budget is $20–27, so the current watchlist rejects `no_headroom`
      every single day, and fitting the money would mean swapping to sub-$10
      high-vol names — i.e. modifying the system in order to demonstrate it.
      At ~$1,200 NVDA and AAPL clear, and the live system is **identical** to
      the one with a clean-day record. Downside is bounded by the balance.

      Blocked on the agent eval: the charters are currently unverified, so
      "trusted" has no evidence behind it. Requires a deliberate human commit
      changing **invariant 1** (CI-enforced by `scripts/check_purity.py`) that
      records the size, the date, and the evidence justifying the flip.

---

## Known limitations — stated plainly

- **No agent judgment is tested.** 574 tests cover plumbing. Edit
  `charters/pm.md` or `charters/analyst.md` right now and every test stays
  green. This is the gap the eval closes.
- **`mcp_servers` is hardcoded** at `agents/seats.py:49` — the Alpaca server is
  always `uvx alpaca-mcp-server` with real credentials. Market data and news
  cannot be faked without adding a seam.
- **No NAV history.** The broker exposes only today and yesterday; the digest
  reports daily P&L, not a since-inception curve. Deliberate — it would need
  storage.
- **Research side is unwired.** `fundbt/`, `stratgate/`, `calibration/` are
  built and tested but the daily cycle does not call them.
- **One decision-maker.** No debate, no second opinion, no dissent.

---

## Eval scoping

The three seats, what each can reach, what each writes:

| | analyst | pm | exec |
|---|---|---|---|
| charter | `charters/analyst.md` | `charters/pm.md` | `charters/exec.md` |
| model | Haiku 4.5 | Sonnet 5 | Haiku 4.5 |
| fund tools | `get_stage_brief`, `submit_signal` | `get_stage_brief`, `submit_decision` | `list_open_tickets` |
| alpaca toolsets | `stock-data, news, account` | `account, stock-data` | `account, trading, stock-data` |
| writes | `signals` | `decisions` | `orders` |
| judgment? | yes | yes | none — the ticket fixes every field |
| measured | 7 turns, ~$0.05 | 5 turns, ~$0.12 | 3 turns, ~$0.03 |

**Injectable today**, all already parameters of `build_seat_options`:
`db_path` (so the PM's input signals are writable rows), `snapshot`
(`{cash, positions, allowed_actions}`), `journals_root`, `clock`.

**Not injectable**: anything reached through the Alpaca MCP server — news,
bars, quotes. See the hardcoded `mcp_servers` limitation above.

That split sets the order of work:

- **PM evals need zero new plumbing** — its entire decision input is the brief,
  and you own all of it.
- **Analyst evals need the MCP seam built first**, because its evidence comes
  off the network.

So the first injection test should target the **PM boundary**, not the news
feed: the analyst's `summary` is free text that flows straight into the PM's
brief. Write a signal row whose summary contains an instruction override, run
the real PM, assert it decides on the numbers and flags `#risk`. Same promise,
no new code, and it is the realistic path — a compromised upstream agent
talking to a downstream one.

Assert **properties, not text**, and run k-of-n (3 runs, require 2) or it will
flake. Mark it `@eval` and exclude it from `make test` like `@live` — it costs
money and hits the network.
