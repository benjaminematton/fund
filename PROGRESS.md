# Progress

Running record of where the fund actually stands. Read this first in a new
session; `README.md` explains what the system *is*, this explains what it has
*done* and what is *open*.

Update it when a milestone lands or an open item closes — not per commit.

---

## Status — 2026-08-18

| | |
|---|---|
| **Mode** | Alpaca **paper** only (invariant 1) |
| **Live since** | 2026-08-17 — first clean end-to-end day |
| **Tests** | 769 offline green on arm64 at `92ad9f3`; **768 + 1 known failure on x86_64** — see below |
| **Watchlist** | NVDA, MSFT, AAPL |
| **Open position** | NVDA 80 @ 227.09, live stop at 215 |
| **Scheduled on** | **DigitalOcean droplet `fund-vm` (NYC3, Debian 13, ET clock)** since 2026-08-18 |

### 2026-08-18 — a lost day, and what it bought

The timer fired correctly at 09:35 and both seats failed:
`ExecTurnViolation: required MCP server(s) not connected after 30.0s`. `uv`
installs to `~/.local/bin`, which is not on systemd's default `PATH`, and
`agents/seats.py:49` hardcodes `uvx alpaca-mcp-server`. Fixed by
`Environment=PATH=` on the daily and pnl units.

**The system behaved correctly.** Default HOLD held — zero tickets, zero orders,
the NVDA position untouched. The unit exited 1, `OnFailure` fired, and the alert
reached `#risk` in 73 seconds. One day of opportunity was lost; nothing was
unsafe.

**Validation, not the code, was the failure.** Every manual check used
`su - fund` — a login shell, which sources the profile and finds `uvx`; systemd
does not. And the timer→service rehearsal ran with the market closed, so
`run_day.py` exited on the broker clock before any seat started: it passed
*while concealing the defect*. A rehearsal that exits early is worse than none,
because it produces a green checkmark. Same shape as 2026-08-17, where fixtures
and code agreed with each other and both disagreed with the broker.

Four things landed in response:

- **`make preflight`** — runs a real seat turn through `systemd-run` under the
  unit's exact `PATH`/`HOME`/`EnvironmentFile`. Exercises `uvx` → MCP →
  Anthropic → seat the way the timer will. ~2 min, ~$0.31, places no orders.
  Mandatory after any host, unit, or environment change.
- **A written deploy procedure** (`ops/README.md`). It was wrong twice, and
  *running* it is what exposed both: the pull must run as `fund` (root has
  neither the deploy key nor ownership of the tree), and
  `/etc/systemd/system/fund-*` are **copies**, so a pull updates `ops/` while
  the old unit keeps firing behind a clean `git status` and a matching `HEAD`.
- **Alerts that state the consequence**, plus `FUND_ALERT_MENTION` so a failure
  pings regardless of Slack client settings. The headline deliberately makes no
  claim about positions: a failure *after* an order lands exits identically, so
  the script cannot know, and an alert asserting unverified safety is worse than
  a terse one.
- **A heartbeat** closing the one gap `OnFailure` structurally cannot see — a
  unit that never runs produces no failure to react to, so a disabled timer, a
  reboot across 09:35 with `Persistent=false`, or a powered-off droplet were
  *silent*, indistinguishable from a quiet day.

### 2026-08-18 — the projection made readable, and one channel per job

The fund's Slack posts were written for whoever wrote them. `⛔ AAPL buy —
no_headroom` and `✅ TICKET c37bb3d5 buy AAPL ≤71` are log lines: they name a
fact without saying what happened or whether it mattered. Three commits
replaced them — mrkdwn prose (`02a6848`), Block Kit layout for the six mid-day
posts (`b7bc91e`), and a laid-out digest and P&L (`3ae9073`). Every
`Rejected()` code in `gate/` now has an English gloss, and a static test fails
if a new code is added without one.

The workspace was reorganised to match:

- **`#fund-ops`** now carries host, systemd and deploy failures. A droplet that
  failed to boot and a position that breached a limit are different
  emergencies with different readers; interleaving them in `#risk` trains you
  to skim both.
- **`#new-channel` and `#social` archived.** Both were Slack defaults. The
  first had been the rehearsal dumping ground before `#fund-staging` existed.
- `ops/staging-env.example` still pointed rehearsals at it, so that was fixed
  *before* archiving (`92ad9f3`). Order mattered: Slack answers an archived
  channel with `is_archived`, which `RealSlack` classifies as permanent and
  `drain()` dead-letters — a staging day built from that template would have
  lost its whole projection with no error at post time.

Slack scopes now include `chat:write.customize`, `channels:manage`,
`channels:join` and `channels:history`. **Reinstalling to add a scope does not
rotate the bot token** — confirmed twice; `/etc/fund/env` was untouched both
times.

**Per-agent identity landed** the same day (`f1bbbce`). Each seat posts under
its own name and face — Nora (Analyst), Vic (PM), Dash (Execution), with Kai
(Quant) and Ida (Critic) waiting on their charters. Machinery deliberately has
no persona: the gate, the broker and the orchestrator post as the fund itself,
because invariant 3 exists to keep the gate free of LLM code and a channel
that gives it a face erases the one distinction a reader most needs.

`RemappedSlack` in `scripts/run_day.py` widened in lockstep with the port —
worth noting because it is the failure mode that hides: miss it and staging
loses identity while production keeps it, so the rehearsal is the thing that
breaks.

### 2026-08-18 — the broker server pinned to a version

`agents/seats.py` now launches `alpaca-mcp-server@2.2.1` instead of whatever
`uvx` resolved that morning. Chosen because it is the version the droplet's warm
uvx cache already holds *and* PyPI's latest (released 2026-08-10), so the pin
records today's behaviour rather than changing it — pinning forward would have
put a cold download inside the 09:35 launch path, a new failure mode introduced
by a change whose whole purpose was removing one. Verified by resolving the spec
`--offline` on the droplet: it returns 2.2.1, and a deliberately wrong pin
(`@2.1.1`) fails offline, which is what proves the pin is honoured rather than
ignored.

**The guard was pointing at the wrong server.**
`tests/test_live_smoke.py` — the schema pin, the one defence against the
2026-08-17 outage class — launched its own hardcoded `uvx alpaca-mcp-server`
rather than going through `agents/seats.py`. Pinning only production would have
left `make schema-pin` validating *latest* while the fund ran 2.2.1: the check
and the checked drifting apart silently, on the next upstream release. Both now
import one `ALPACA_MCP_SPEC` constant, so they cannot disagree. Today they
resolve identically (38 tools, flat `stop_loss_*` strings on both), which is why
this was invisible.

**Two more copies of the same drift, caught in review.** `ops/README.md`
pre-warmed the cache with bare `uvx alpaca-mcp-server`, and the cutover check
did too. On the next upstream release those warm a version the seats never
launch — putting the cold download back inside 09:35, the exact failure the pin
exists to remove. Both now derive `ALPACA_MCP_SPEC` from the source rather than
naming a version that rots.

What the pin changes about `make schema-pin`: it no longer warns early that
upstream moved a field, because upstream can no longer move under the fund
unattended. It now gates the *bump* — run it when raising the pin, which is the
only way the version can change.

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

Three systemd timers on the droplet. **Only one host may hold a live schedule**
— `flock` is machine-local and ticket-id namespaces are per-database, so two
hosts means genuine duplicate orders that `client_order_id` cannot dedupe.

| unit | fires | does |
|---|---|---|
| `fund-daily.timer` | 09:35 ET Mon–Fri | full trading day, self-audits |
| `fund-pnl.timer` | 16:35 ET Mon–Fri | posts P&L $ / % vs SPY |
| `fund-backup.timer` | 17:30 ET daily | atomic, integrity-checked snapshot |
| `fund-alert@.service` | on any of the above failing | posts the failure to `#risk`, mentioning the operator |
| healthchecks.io `fund-daily` | **when a 09:35 ping does not arrive** | alerts `#risk` + email at 10:20 ET |

The watchdog is off-box on purpose: a dead droplet cannot run its own. It is fed
by `ExecStartPost` on `fund-daily.service`, so a ping means the day *completed*;
the `-` prefix is fail-safe in the right direction — a failed ping cannot fail
the trading day it reports on, and a ping that never lands makes the watchdog
alert. Errors there can only cause a false alarm, never silence. The check must
be in **cron** mode (`35 9 * * 1-5`); the default simple period mode would make
Friday's ping set Saturday's deadline and page every weekend.

The timezone is pinned **in the `OnCalendar` expression** as well as on the
host, so the schedule survives a host timezone change. `Persistent=false`
reproduces the old plists' `RunAtLoad=false`: a day starts because the market
opened, never because the host booted. No `Restart=` anywhere — invariant 4's
default is HOLD.

16:35 (not 16:15) is measured, not chosen: `close_frame` shifts its end back
`SIP_DELAY` (16 min), so a 16:15 fire asks for 15:59 — before the closing
auction writes the bar.

Full layout, cutover and rollback procedure: `ops/README.md`.

### The Mac after cutover

`com.fund.pull-backups` is the **only** fund launchd job that should exist here.
`com.fund.daily.plist` was moved out of `~/Library/LaunchAgents` to
`~/fund-rollback/`, and `.env` was renamed to `.env.MIGRATED-TO-VM` — two
independent barriers, because `launchctl unload` is session-scoped and
`~/Library/LaunchAgents` reloads at every login. Unloading alone would have let
the fund resurrect here days later while the droplet was live.

**Verified 2026-08-18**, and the logout/login test this was waiting on turned
out to be unnecessary: `launchctl list` shows only `com.fund.pull-backups`, and
`~/Library/LaunchAgents` contains only its plist —
`com.fund.daily.plist` sits in `~/fund-rollback/`. The open question was whether
a session-scoped `unload` would survive a login; it never applied, because the
trading job was **moved out of the auto-load directory** rather than unloaded.
A login has nothing to resurrect. The one-host invariant holds on the Mac side
by construction, not by anyone remembering.

### Alerting

`run_day.py:25-34` documents a pre-Slack window that posts nothing on failure,
justified because "the exit is non-zero with a descriptive stderr message, so
it is a visible failure, just not a Slack one." That premise assumed a human at
the machine. On a droplet it is false, so systemd `OnFailure=` restores it —
verified against a start failure, which is the case the script itself cannot
cover. Known gap: if the Slack token is what broke, the alert cannot post
either.

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
| 2026-08-18 | eval rig for the LLM seats merged (`191fd18`, `5196282`) |
| 2026-08-18 | **moved off the Mac** onto a DigitalOcean droplet on an ET clock; systemd timers, Slack failure alerting, nightly verified snapshots |
| 2026-08-18 | Slack posts rewritten for a person — mrkdwn, then Block Kit (`02a6848`, `b7bc91e`, `3ae9073`) |
| 2026-08-18 | workspace cut to one channel per job; host failures split into `#fund-ops` (`92ad9f3`) |

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

- [ ] **`test_golden` is arm64-only — re-record it portably.** The golden
      `data_snapshot_hash` cannot be reproduced on x86_64, so `make test` is
      708/709 on the droplet. Root cause, measured not guessed: `rng.uniform`
      computes `low + (high-low)*x`, and arm64 and x86_64 contract that into FMA
      differently, so `vol[0]` differs by **1 ULP at the very first draw**
      (`0x1.6974569e58a45p-6` vs `...44p-6`) and 2520 iterations amplify it. The
      RNG integer stream is identical and the `DIP_PCT` branch is *not* the
      culprit — kick counts match exactly (4837) on both.
      **Verified fix:** quantizing the market makes hashes bit-identical at 8dp
      or coarser (10dp and finer still diverge); `close.round(6)` in
      `tests/synthetic.py` is one line. The work is the re-record: 16 pinned
      values across two tests, and the fixture's *meaning* must survive — golden
      params must still pass G2/G3 and `FAIL_PARAMS` must still fail on `wfe`.
      Deferred deliberately on 2026-08-18: `fundbt/` is unwired from the daily
      cycle, so this cannot affect trading.

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

      **Precondition — the `FUND_HOST_ID` guard** (`run_day.py` refuses to run
      when the DB records a different last-writing host). Moved here from "Now"
      on 2026-08-18, deliberately: while the fund is paper, two hosts cost a
      corrupted record, not money, and the Mac is already double-disarmed —
      `com.fund.daily.plist` is out of `~/Library/LaunchAgents` and `.env` is
      renamed, so the failure needs a human to undo two barriers first. Real
      money is what makes duplicate orders expensive, and `client_order_id`
      idempotency cannot catch them: `flock` is machine-local and ticket-id
      namespaces are per-database.

      Two things to settle when it is built, both known now. It would **block
      the documented rollback** (§Rollback restores the plist and `.env` on
      purpose, then starts on a DB the droplet last wrote), so it needs an
      override — and an override used once under outage pressure is procedural
      again. And `state/` has no meta/kv table, so recording the host means a
      `state/schema.sql` change against a live `fund.sqlite` with no migration
      framework; a sidecar file beside `run_day.lock` avoids the DDL but does
      not travel with a copied database. Decide that before coding.

---

## Known limitations — stated plainly

- **No agent judgment is tested.** 574 tests cover plumbing. Edit
  `charters/pm.md` or `charters/analyst.md` right now and every test stays
  green. This is the gap the eval closes.
- **`mcp_servers` is hardcoded** at `agents/seats.py:61` — the Alpaca server is
  always `uvx` at `ALPACA_MCP_SPEC` with real credentials. Market data and news
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
