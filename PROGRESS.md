# Progress

Running record of where the fund actually stands. Read this first in a new
session. `README.md` explains what the system *is*; this file explains what the system has
*done* and what is *open*.

Update it when a milestone lands or an open item closes — not per commit.

> **⚠ STALE — a snapshot, not current state.** Despite "read this first" above, this file
> has not been updated since 2026-08-20 (`git log -1 -- PROGRESS.md`). Nothing below is
> current state, and this header deliberately quotes no commit, count, or phase number —
> a number that depends on state outside this file will eventually be wrong inside it.
>
> For what is actually true right now: board issue **#49** and its children for what to work
> on, open issues and PRs for what is in flight, `git log` on `master` for what shipped, and
> `specs/acceptance.md` for the build order. See `specs/INDEX.md`.

---

## Status — 2026-08-19

The following table summarizes where the fund stands:

| Item | Value |
|---|---|
| **Mode** | Alpaca **paper** only (invariant 1) |
| **Live since** | 2026-08-17 — first clean end-to-end day |
| **Tests** | 955 offline green at `51bc7eb`, identical on macOS arm64, linux/amd64 and linux/arm64 |
| **CI** | runs all 909 (was 36); **first green run in the repo's history on 2026-08-19** |
| **Seats** | PM, exec, and **two** analysts — `analyst` (price/fundamentals) and `news` (news/sentiment, read-only, blind to the book). The `news` seat is deployed but **defective**, see #6 |
| **Watchlist** | NVDA, MSFT, AAPL |
| **Open position** | NVDA 80 @ 227.09, stop 215 gtc (expires 2026-11-17) — **hand-placed 2026-08-19 11:46 PDT** after two sessions unprotected (see below) |
| **Scheduled on** | **DigitalOcean droplet `fund-vm` (NYC3, Debian 13, ET clock)** since 2026-08-18 |

### 2026-08-19 — the stop that expired at the bell

The 09:35 run was clean: three signals, three `hold 0` decisions, no tickets, no
exec turn, `AUDIT CLEAN`, $0.194. **The defect was in the position it was
holding, not in the run.**

The 2026-08-17 NVDA entry went in as an OTO whose stop leg inherited the
parent's `time_in_force: DAY`. Confirmed at the broker by resolving the fund's
own gate ticket id:

```
get_order_by_client_id("a14aa36b-…")  →  98ce80e8-…     ← matches orders.alpaca_order_id
  PARENT  NVDA buy  80  MARKET  OTO   FILLED    tif DAY
  LEG     NVDA sell 80  STOP 215      EXPIRED   tif DAY
                                      expired_at 2026-08-17T20:00:06Z
```

20:00:06Z is 16:00 ET — the close of the day it was placed. **The position was
unprotected for two full sessions** while `decisions.stop_price` and this file
both asserted a live stop at 215. No loss was taken and that is luck, not
design: lows since the leg died were 218.69 and 216.76 against a 215 stop.

Three layers each had a reason not to look:

- `validate_order` never checked `time_in_force`. A `DAY` stop passed every
  other check hardened on 08-17.
- `reconcile_orders` polls only `status IN ('submitted','partially_filled')`.
  Once the parent filled, its row went terminal; the exit leg is invisible to
  it by construction.
- Nothing compared open positions against live protective orders, so
  `AUDIT CLEAN` was clean by not asking. It audits the *run*, not the *account*.

**None of the 08-18 work could have caught this.** Every fix that day —
`PATH=`, `OnFailure`, the heartbeat, `make preflight`, the version pin — targets
a run that *fails*. This one succeeds, audits clean, and posts a digest. It has
also been dormant by luck: every day since 08-17 has been holds, so no second
stopped order has been placed to trip it again.

Same family as the two before it: **the system asserted something nobody
compared to the source of truth.** Fixtures agreed with the gate and both
disagreed with Alpaca (08-17). A rehearsal passed by exiting early (08-18). The
database says "stop at 215" and the broker says no open orders (08-19).

#### What closes it

Two changes, and one thing deliberately left open.

- **The gate requires `gtc` on any stop-carrying order** (`gate/tickets.py`).
  The root cause sits one layer deeper than the broker: the MCP place tool's
  `time_in_force` **default is `day`** and it omits the field unless the seat
  passes it — both now schema-pinned in `tests/test_live_smoke.py`. The gate is
  the enforcement and its denial message carries the reason, but a seat that
  only ever learns the rule by being denied burns a turn on every stopped
  ticket, so `charters/exec.md` (v4) states it as well. The contract also went
  into `specs/design.md` and `specs/acceptance.md` — it was living only in the
  plan file, which nothing treats as canonical. The recorded 08-17 turn (`tests/recordings/oto.jsonl`) is now the
  hook-level regression test proving a `day` stop never reaches the broker —
  the recording was not edited, and the healthy path got a new `oto_gtc.jsonl`.
- **`orchestrator/protection.py` asserts, after every run, that a promised stop
  is still live at the broker.** Which source owns which fact is the whole
  design: the **broker** owns what protection *exists*, the fund's **record**
  owns what was *promised*, and comparing them is the comparison nobody
  performed on 08-17. The database is never allowed to assert a stop exists —
  only what was intended. It runs on a full-HOLD day too, which is the shape
  this incident had, and it fails closed on every ambiguity: broker
  unreachable, a number that will not parse, a truncated order page, a position
  with no provenance in our own records.

A position the PM opened *without* a stop on purpose (`charters/pm.md:25` — a
non-price invalidation) is **standing exposure, not a fault**, and stays silent
here. Alerting on it would red the audit every day on a correct day, and a
channel that cries wolf daily protects nothing. Making that exposure visible is
a follow-up: a protection line in the EOD digest, where state belongs
(invariant 6). Two smaller follow-ups go with it — a second trigger outside
`run_day` (a run that dies early performs no check, as 08-18 would have), and
unifying the three near-copies of whole-share coercion.

**Both broker claims are now confirmed against the live paper account**
(2026-08-20 10:53 ET, market open, 4/4 live tests, zero skips). Alpaca accepts
`gtc` on an OTO market parent and hands that lifetime down to the stop leg —
the assumption the whole branch rests on — and once the parent fills, the
activated leg IS visible to `open_orders()`, which is what the protection
assertion actually reads.

The second one had to be measured rather than reasoned about. A `held` OTO
child is genuinely absent from `QueryOrderStatus.OPEN`, so the assertion can
only run against a filled parent during market hours; the test skips loudly
when the market is closed rather than passing on a path that proves nothing.
It had never once passed before today. A green offline suite cannot settle a
question about the broker — on 08-17 the fixtures and the code agreed with each
other and both disagreed with the broker, and a fixture written from the same
assumption would have agreed a third time.

#### The position itself

**Closed by hand, not by code.** The NVDA 80 was naked from 2026-08-17 16:00 ET
until **2026-08-19 11:46 PDT**, two full sessions. Benjamin then had a stop
placed directly through the REST API — `NVDA sell stop 80 @ 215, tif gtc`,
`client_order_id manual-protective-stop-nvda-2026-08-19`, broker order
`5abc139f-4817-4a34-aedd-f2ca28203c5c`, `submitted_at 2026-08-19T18:46:22.978Z`
— deliberately outside the gate. It is risk-reducing only, and the database
already asserted a stop at 215, so this makes the broker agree with the source
of truth rather than editing the belief down to match a diminished reality.

**That `gtc` is not permanent: the order carries `expires_at
2026-11-17T21:00:00Z`.** Alpaca caps good-till-canceled at roughly 90 days, so
this stop dies in November unless it is replaced. Nothing watches for that.
Reading "gtc" as "forever" is the same class of mistake as reading a `day` leg
as protection that lasts — the incident above is what happens when an order's
lifetime is assumed rather than checked.

No code path places a stop, and none was added. A missing stop alerts a human;
placing a replacement is order placement, which belongs to the gate and the exec
seat (invariant 4).

That order has **no row in `orders` and no gate ticket**, which is the correct
outcome and is pinned by a test: the assertion asks one question in one
direction — did a promised stop survive at the broker — and never the converse.
Asserting that every live broker order maps back to a DB row would alert on
exactly the human intervention these alerts ask for.

**The next hole in the same class is now live, not theoretical.** `reconcile_orders`
polls only rows in `orders`, so if that hand-placed stop fills, the fund's
database will go on believing it holds NVDA 80 while the broker is flat. The
protection assertion does not catch it either: it iterates broker *positions*,
and a closed position produces none. Not solved on this branch.

### 2026-08-19 — the check that had never passed

`ci` was red on `tests/test_golden.py::test_golden_pass_path`. The prior
investigation read the run history as a green baseline on `990a9ae` followed by
41 red runs, concluded the environment had drifted under a frozen hash, and
recommended pinning `numpy` and `pandas`.

**Filtering the run list by workflow says something else: 55 runs, zero
successes. `ci` had never passed once.** The "green baseline" was a
`Dependency Graph` run that fired on the same commit one second earlier and was
picked up by a `--branch master` query with no `--workflow` filter. That single
misread is what produced the rest: born-red and regressed-on-a-date are
different diagnoses, and only the second makes dependency drift the obvious
story. A check that had never worked would have pointed straight at the
platform.

**Root cause, and it is not architecture.** `snapshot_hash` hashed the decimal
*text* of the float matrix, so it asserted bit-identical floats across machines —
something numpy does not provide. numpy's macOS wheel FMA-contracts the
multiply-add inside `Generator.uniform` (`low + (high-low)*u`, one rounding);
the manylinux wheel rounds twice. Confirmed by exact rational arithmetic:
macOS returns `0x1.6974569e58a45p-6`, Linux `...44p-6`. The 1 ULP lands on
`vol[0]` at the very first draw and 2520 iterations of `rng.normal` + `cumprod`
amplify it into 30,151 of 50,400 cells differing at ~5.4e-15 relative, which
survives `round(10)` and changes the CSV bytes.

The axis is **macOS vs Linux, not arm64 vs x86_64** — this file previously said
otherwise. linux/amd64 and linux/arm64 are byte-identical at every layer; a
fresh Python 3.12 on arm64 macOS reproduces the macOS value. It is the wheel's
compiler, not the ISA. Likewise `db738d7`'s message blamed numpy 1.26 vs 2.x;
numpy 2.5.2 on Linux still produces its "numpy 1.26" value today. The old
constant was simply the Linux value, and re-recording it from a Mac is what
turned CI red.

**The fix quantizes the hash, not the market.** Hashing at 6 significant digits
(`to_csv(float_format="%.6g")`) changed 2 of 19 pinned values rather than the 16
this file anticipated, because rounding `tests/synthetic.py` would have moved
every economic number while quantizing at the hash moves only the hash and its
derived `run_key`. The precision was chosen by measuring distance to the nearest
rounding boundary: **1289x** the observed drift at `%.6g`, but only **1.6x** at
`%.6f` and already straddling at `%.8f`. The previously suggested "8 decimal
places or coarser" sits in that thin band — it would have passed on the day and
re-broken later. The regression test asserts stability under perturbation rather
than a new frozen constant, so a third re-record cannot arrive quietly.

**CI covered 36 of 909 tests.** It installed `numpy`+`pandas` only and ran
`tests/run_tests.py`. That gap is why five real failures in `test_fund_tools.py`
sat invisible while master read green, and it is the same blind spot that let
the golden failure be misdiagnosed for weeks: a check covering a fraction of the
suite still reports one green tick. CI now installs from `pyproject.toml` — not
a second hardcoded list, since restating a version range in the workflow is
exactly the drift vector at issue — and runs `pytest tests/`.

Two defects surfaced immediately, both in tests that reached past their subject:

- **`mcp` 2.0.** `claude-agent-sdk~=0.2.116` admits `<0.3.0`; a fresh resolve
  gets `0.2.141`, which widened *its own* cap from `mcp<2.0.0` to `mcp<3.0.0`,
  so `mcp` 2.0.0 lands and `Server.request_handlers` is gone. Only tests broke,
  because only tests reached into that internal — production hands the instance
  to the SDK and was verified working end-to-end under the new versions. No pin,
  nothing touching the droplet.
- **The renderer guard scanned other branches.** `test_every_written_kind_has_a_renderer`
  walked `root.rglob("*.py")` skipping only `.venv` and `tests`, so it descended
  into `.claude/worktrees/` and reported sibling checkouts' `append_event` kinds
  as defects in whatever branch you stood on. CI never saw it (`actions/checkout`
  has no nested worktrees), so it fell entirely on developers — and since
  `make test` must pass before every commit, it made that gate unpassable for
  anyone with a worktree open.

**The shape all three share.** Each was a check that looked authoritative while
measuring something else: a dependency-graph workflow read as `ci`, 36 tests read
as the suite, another branch's code read as this one's. None was a wrong answer;
each was a right answer to a question nobody had checked. Two were fixed without
touching production code at all.

### 2026-08-19 — a second analyst, and what it got wrong on day one

The research stage runs two analysts, not one (`d21b755`, `2c119c7`, PR #2). The
new `news` seat reads news and sentiment, is **read-only and blind to the book**
— `alpaca_toolsets: "news,stock-data"`, no account access, `place_*` denied at
both the toolset and `disallowed_tools` layers, `setting_sources: []`. Per-seat
neutral/0 defaulting landed with it: a seat that files no report defaults per
seat, not per ticker. Staging day green on the scratch account, real order
filled, 7/7 checkpoints.

**It has never yet produced a usable signal.** On 2026-08-19 it called
`get_news` with `start == end == "2026-08-19"`. Both date-only bounds resolve to
`T00:00:00Z`, so the interval is zero-width and Alpaca returns an empty list with
a clean 200 and no error. The seat wrote "No news published 2026-08-19 … zero
reported catalyst. Down move without headline is noise, not signal" for all three
tickers, on a day with **30 articles** across them and four directly on NVDA.
The analyst seat, same run, same tool, same minute, cited five.

Two defects, and the second is the one that did the damage. The query is wrong.
But `charters/news.md:59` also said "if the feed is **empty**" where
`charters/analyst.md:52` says "if data is **missing**", and `:57` elevated
"absence of news is information" to a principle — so the charter instructed the
seat to treat silence as a measured fact. **Fixing the query alone leaves a seat
that still asserts absence from an empty response.** Both tracked as #6.

The lesson generalises past this seat: **an empty result is unmeasured, never
measured-as-nothing.** The same rule `3ff004e` already encodes for resolutions
("Unmeasurable is no row, never a zero"), and what invariant 4 means applied to
evidence rather than to actions. Note also what makes this the dangerous shape —
a hallucinated headline is visibly suspect, while a confident false negative
reads as diligence and passes into the PM's evidence unchallenged.

`max_turns: 16` for this seat remains **unmeasured**: the staging figures
(8 of 16 turns, $0.0301/day) measured a seat doing nothing useful.

### 2026-08-18 — the feedback loop closed, and the crossing that nearly emptied it

`orchestrator/resolve.py` + `scripts/resolve_day.py` write `resolutions`
nightly (`3ff004e`), riding `fund-pnl.service`'s 16:35 fire for the same
SIP_DELAY reason `close_pnl` has.

`calibration/rows.py` is the crossing, and it is where this quietly fails:
resolutions are **per-decision** but `grade_rows` wants **per-analyst**, and
`signals.direction` (bullish/bearish) is not `signal_probability`'s vocabulary
(long/short). Untranslated, three rows in yields one out — and the board renders
near-empty **without erroring**, which is the failure mode that hides. Verified
over 90 resolutions: 360 signal rows in, 360 graded out, 0 dropped.

**The loop is not fully closed.** `resolutions.reflection` is still never
written, because the daily cycle has no reflection stage to write it, and it
cannot live in the 09:35 run — resolutions are written at 16:35. Tracked as #4.
`orchestrator/reflect.py` computes the factual frame a seat would reflect on
(call, per-seat confidence, realized return and alpha) and stores it ahead of any
prose; what consumes it does not exist yet.

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

The workspace was reorganized to match:

- **`#fund-ops`** now carries host, systemd, and deploy failures. A droplet that
  failed to boot and a position that breached a limit are different
  emergencies with different readers; interleaving them in `#risk` trains you
  to skim both.
- **`#new-channel` and `#social` archived.** Both were Slack defaults. The
  first had been the rehearsal dumping ground before `#fund-staging` existed.
- `ops/staging-env.example` still pointed rehearsals at `#new-channel`, so that was fixed
  *before* archiving (`92ad9f3`). Order mattered: Slack answers an archived
  channel with `is_archived`, which `RealSlack` classifies as permanent and
  `drain()` dead-letters — a staging day built from that template would have
  lost its whole projection with no error at post time.

Slack scopes now include `chat:write.customize`, `channels:manage`,
`channels:join`, and `channels:history`. **Reinstalling to add a scope does not
rotate the bot token** — confirmed twice; `/etc/fund/env` was untouched both
times.

**Per-agent identity landed** the same day (`f1bbbce`). Each seat posts under
its own name and face — Nora (Analyst), Vic (PM), Dash (Execution), with Kai
(Quant) and Ida (Critic) waiting on their charters. Machinery deliberately has
no persona: the gate, the broker, and the orchestrator post as the fund itself,
because invariant 3 exists to keep the gate free of LLM code. A channel
that gives it a face erases the one distinction a reader most needs.

`RemappedSlack` in `scripts/run_day.py` widened in lockstep with the port —
this is the failure mode that hides: miss the widening and staging
loses identity while production keeps it, so the rehearsal is the thing that
breaks.

### 2026-08-18 — the broker server pinned to a version

`agents/seats.py` now launches `alpaca-mcp-server@2.2.1` instead of whatever
`uvx` resolved that morning. Chosen because it is the version the droplet's warm
`uvx` cache already holds *and* PyPI's latest (released 2026-08-10), so the pin
records today's behavior rather than changing it — pinning forward would have
put a cold download inside the 09:35 launch path, a new failure mode introduced
by a change whose whole purpose was removing one. Verified by resolving the spec
`--offline` on the droplet: it returns 2.2.1, and a deliberately wrong pin
(`@2.1.1`) fails offline, which is what proves the pin is honored rather than
ignored.

**The guard was pointing at the wrong server.**
`tests/test_live_smoke.py` — the schema pin, the one defense against the
2026-08-17 outage class — launched its own hardcoded `uvx alpaca-mcp-server`
rather than going through `agents/seats.py`. Pinning only production would have
left `make schema-pin` validating *latest* while the fund ran 2.2.1: the check
and the checked drifting apart silently, on the next upstream release. Both now
import one `ALPACA_MCP_SPEC` constant, so they cannot disagree. Today they
resolve identically (38 tools, flat `stop_loss_*` strings on both), which is why
this was invisible.

**Two more copies of the same drift, caught in review.** `ops/README.md`
pre-warmed the cache with bare `uvx alpaca-mcp-server`, and the cutover check
did too. On the next upstream release, those two steps warm a version the seats never
launch — putting the cold download back inside 09:35, the exact failure the pin
exists to remove. Both now derive `ALPACA_MCP_SPEC` from the source rather than
naming a version that rots.

What the pin changes about `make schema-pin`: it no longer warns early that
upstream moved a field, because upstream can no longer move under the fund
unattended. It now gates the *bump* — run it when raising the pin, which is the
only way the version can change.

### The live day, as recorded

All seven checkpoints reached `done` and the audit was clean. The stages produced the following:

- **analyst** → 2 signals (NVDA bullish 72%, MSFT bearish 40%)
- **pm** → 2 decisions: NVDA buy 80 with stop 215; MSFT hold 0
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
| `fund-alert@.service` | on any of the preceding three timers failing | posts the failure to `#risk`, mentioning the operator |
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

Full layout, cutover, and rollback procedure: `ops/README.md`.

### The Mac after cutover

`com.fund.pull-backups` must be the **only** fund launchd job on the Mac.
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
the machine. On a droplet that premise is false, so systemd `OnFailure=` restores the missing alert —
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
| 2026-08-19 | golden snapshot hash made platform-independent — **first green `ci` run in the repo's history** (`557d1cd`) |
| 2026-08-19 | CI widened from 36 to 909 tests; `mcp` 2.0 compatibility in tests (`e072f73`) |
| 2026-08-19 | renderer guard stopped scanning sibling worktrees (`c0ad2d4`) |
| 2026-08-18 | **the feedback loop closed** — `resolutions` written nightly (`3ff004e`); reflection stage still absent (#4) |
| 2026-08-19 | **second analyst seat** (`news`, read-only, blind to the book) merged and deployed; per-seat neutral/0 defaults; staging day green on the scratch account |
| 2026-08-19 | clean run; **found the NVDA stop had expired at the 08-17 bell** — two sessions unprotected, never breached; stop re-placed by hand at 11:46 PDT |
| 2026-08-19 | the `news` seat's first live signals asserted "No news published" on a day with 30 articles — zero-width query, and a charter that licensed it (#6) |
| 2026-08-20 | missing-stop class closed: the gate requires `gtc`, and the day asserts every position is protected (`51bc7eb`) — leg inheritance and leg visibility both measured live |

### The stop-leg shape the broker never accepted

`gate/tickets.py` validated a nested `stop_loss: {stop_price}`; the real
`place_stock_order` takes a **flat** `stop_loss_stop_price` string. Every
stopped ticket was undeliverable in both directions — the gate denied the real
shape and the broker rejected the assumed one.

The entire offline suite was green over a total outage, because
`tests/fake_alpaca.py` and the recordings encoded the *same* wrong assumption as
the code. Fixture and code agreed with each other and both disagreed with
Alpaca. That is why `make schema-pin` exists and why it is step §0b of the
`HANDOFF-LIVE.md` runbook: it introspects the live server's schema and fails if the field names
move.

---

## Open items

**Now**

- [ ] **Decide whether to pin `claude-agent-sdk` exactly.** CI now resolves the
      SDK and `mcp` fresh on every run, so a future release can break the build.
      That is deliberate — it surfaces where it previously hid — but it is a
      standing exposure, and `~=0.2.116` is what let `mcp` 2.0 in via a cap the
      SDK widened underneath it. Pinning reaches the droplet, so it needs a human
      commit either way. Not urgent while the break is test-only.

**Next branches** — each belongs in a **new chat**, per the standing rule that
new implementation branches get fresh context.

- [ ] **Agent eval.** Scoped: see the "Eval scoping" section. Start with the
      injection case at the PM boundary.
- [ ] **Union asymmetry** in the price-history exclusion (ledgered ruling,
      2026-08-17)
- [x] ~~**Resolutions and reflection loop**~~ — **half done.** `resolutions` is
      written nightly (`3ff004e`) and `calibration/rows.py` grades it. What
      remains is the reflection stage that would write `resolutions.reflection`;
      it cannot live in the 09:35 run because resolutions land at 16:35. Tracked
      as **#4**, no longer a blank cycle.
- [x] ~~**Second analyst seat**~~ — **shipped and defective.** The `news` seat
      is merged and deployed; it has never produced a usable signal, and both
      defects are prompt-level. Tracked as **#6**. Its `max_turns` is still
      unmeasured. Do not treat the debate mechanic as having two voices until #6
      closes — it currently has one voice and one confident silence.
- [ ] README demo recording

**Last, after everything above** — decided 2026-08-17

- [ ] **Fund it with real money.** The goal is not returns; it is the claim
      *"I built an agentic system I trusted enough to put my own money behind."*
      Judge this milestone on whether that sentence is defensible, not on P&L.

      **Fund ~$1,200, not less.** The gate approves only when
      `equity × vol_tier × corr_mult ≥ price` (`gate/risk.py:87-91`). At $100
      the budget is $20–27, so the current watchlist rejects `no_headroom`
      every single day, and fitting the money would mean swapping to sub-$10
      high-vol names — that is, modifying the system to demonstrate it.
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

- **No agent judgment is tested.** The whole suite (see the Tests row above)
  covers plumbing. Edit `charters/pm.md` or `charters/analyst.md` right now and
  every test stays green. This is the gap the eval closes — and #6 is what it
  costs: two prompt-level defects shipped to production and went two live days
  unnoticed. `evals/seats/` still holds only `pm.yaml` (#18).
- **`mcp_servers` is hardcoded** at `agents/seats.py:61` — the Alpaca server is
  always `uvx` at `ALPACA_MCP_SPEC` with real credentials. Market data and news
  cannot be faked without adding a seam.
- **No NAV history, and no fund-level P&L baseline.** The broker exposes only
  today and yesterday; the digest reports daily P&L, not a since-inception
  curve. `resolutions` does not fill the gap — it is **per-decision**, so it can
  measure whether a call was right but not what the fund is worth. Fund-level
  "P&L $ and %" per `specs/contracts.md:285` needs a stored daily equity
  baseline. Deliberate — it would need storage, and whether that is justified is
  undecided.
- **Research side is unwired.** `fundbt/`, `stratgate/`, `calibration/` are
  built and tested but the daily cycle does not call them.
- **One decision-maker.** No debate, no second opinion, no dissent.
- **A stopped position cannot be trimmed — only exited whole.** A full-size GTC
  stop reserves the entire position at the broker, which reports
  `qty_available` 0, so a partial sell is refused. The one size that works is a
  full exit, because it is the only one the reservation does not block.

  **The 08-19 hardening is what makes this systematic rather than incidental.**
  The gate now requires `gtc` on stop-carrying orders and
  `assert_positions_protected` checks the stop is still live, so full-size GTC
  protection is the *intended* output of the fix — and full-size GTC protection
  is exactly what makes a position unsellable in part. Deploying the fix for the
  first problem created the second. Both are correct; they are in tension, and
  neither is a bug.

  The resolution is ruled and **unmerged**: amend the stop down to the
  post-sale size first, then sell — never cancel-then-sell, which leaves the
  position naked in between and reintroduces the 08-17 class. `ADR-0003
  reducing-a-stopped-position`, on `docs/adr-stop-amend`, not on master. **No
  code exists**; the branch is docs-only and its plan has not passed review.

  Until that lands, treat "reduce a position" as unavailable rather than
  untested. A partial sell does not fail gracefully — the broker refuses it.
- **An unprotected position does not stop the day — it alerts.** Ruled
  2026-08-20, after the 08-17 stop expiry. `orchestrator/protection.py` asserts
  after every run that a promised stop is still live at the broker, and raises
  an alert when it is not; nothing blocks trading on the strength of it.

  The considered alternative, recorded so it is not re-derived from scratch:
  **alert normally, but deny tickets that would INCREASE exposure to the
  unprotected symbol.** It is the better-reasoned policy on the merits — an
  unprotected NVDA does not make a properly-stopped MSFT ticket unsafe, so a
  blanket halt over-reads invariant 4, while adding to the naked leg is the case
  that actually bites. **It lost on cost, not on logic.** `GateInputs`
  (`gate/risk.py`) is `ConfigDict(strict=True, extra="forbid", frozen=True)` with
  twelve fields and no protection or coverage field, so expressing "is this
  symbol covered" means an explicit schema change to a frozen gate model plus
  feeding the gate broker coverage data it has never received — gate-threshold
  class under invariant 3, structural rather than a policy tweak. Against that,
  the window it would have bought is now detected rather than silent.

  Revisit if `GateInputs` gains coverage data for another reason. Do not revisit
  by widening the schema for this alone.

---

## Eval scoping

The following table lists the three seats, what each can reach, and what each writes:

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
bars, quotes. For more information, see the "Known limitations" section.

That split sets the order of work:

- **PM evals need zero new plumbing** — the PM's entire decision input is the brief,
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
