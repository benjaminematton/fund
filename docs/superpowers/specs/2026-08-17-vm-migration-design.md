# Design — move the daily run to a Linux VM

**Date** 2026-08-17 · **Base** `7aa3c32` · **Mode** Alpaca paper only (invariant 1)
**Status** reviewed (architecture / code quality / tests / performance) — 13 issues folded in

Move the fund's two scheduled jobs off this Mac and onto an always-on Linux
box set to `America/New_York`, and cut over without ever letting two hosts hold
a live schedule at the same time.

---

## 1. Why

Not compute. All three seats are remote Anthropic API calls, so local CPU load
is near zero. The two real wins:

1. **An ET machine makes both schedules literal.** The committed plists are
   ET-machine templates (`Hour 9` / `Hour 16`); this Mac is Pacific, so its
   installed copies use shifted Hours. That arithmetic, its DST re-derivation,
   and the `pmset` sleep/wake dance all disappear on an ET box.
2. **Always-on**, so the laptop can move without the fund missing days.

---

## 2. Decisions

| question | decision |
|---|---|
| provider | DigitalOcean, NYC |
| size | 1 vCPU / 2 GB, $12/mo, plus a 2 GB swap file |
| deploy shape | systemd units directly on the host — **no containers** |
| extras in scope | failure alerting to Slack; nightly off-box DB backup |
| backup destination | dated on-box snapshots, kept indefinitely + opportunistic rsync pull to the Mac |
| cutover | tonight (2026-08-17), VM takes 2026-08-18 |

### Why not containers

`CLAUDE.md` advertises `docker compose up — one service per seat +
orchestrator`. **No Dockerfile and no compose file exist**, and that sentence
describes a *different architecture* from what runs: one sequential daily
script. Building it would mean changing the host and the architecture at once,
making any failure unattributable.

A single timer-invoked container would avoid the re-architecture but buys
little — the box is single-purpose, so a dedicated VM already provides the
isolation an image would, while adding an image build, a uv cache layer, the
node runtime the SDK spawns, and a volume story for SQLite, journals, and
`.env`. Portability is better served by checked-in unit templates and an
install script.

Fixing the stale `CLAUDE.md` line is in scope.

### The one-way door we are *not* opening

Provider choice is close to reversible: this is a git checkout, a venv,
systemd units, and one SQLite file, with no managed services, IAM, or VPC
wiring. Moving hosts again is an rsync and a `systemctl enable`.

What is *not* reversible is **single-host SQLite**. Invariant 6 plus a
machine-local `flock` is exactly why only one host may ever run this. Going
multi-host later (HA, or splitting research off from trading) is an
architecture change, deliberately out of scope here.

---

## 3. Machine, runtime, layout

**Droplet.** DigitalOcean NYC, Debian 13, 1 vCPU / 2 GB, 2 GB swap.
`timedatectl set-timezone America/New_York`.

**User.** A non-root `fund` user. Units are **system** units with `User=fund`,
not user units — user units require `loginctl enable-linger` to fire without a
login session, which is a silent-failure trap this fund cannot afford.

**Layout.** The split is code vs. state:

| path | holds | why |
|---|---|---|
| `/opt/fund` | git checkout + `.venv` | disposable; re-clonable at any time |
| `/var/lib/fund/fund.sqlite` | **the fund** | outside the checkout, so no `git clean -x` or re-clone can reach it |
| `/var/lib/fund/journals/` | agent memory | same reason; set via `FUND_JOURNALS` |
| `/var/lib/fund/backups/` | dated snapshots, kept indefinitely | §5 |
| `/etc/fund/env` | secrets, `0600 fund:fund` | survives a re-clone; loaded by `EnvironmentFile=` |
| `/etc/fund/alert-env` | **only** `SLACK_BOT_TOKEN` + channel, `0600` | §4 — the alert must not share a failure mode with the job |

`/var/lib/fund/run_day.lock` lands there for free — `acquire_lock` derives it
from `FUND_DB`'s parent.

No code change is required for this: `FUND_DB` is a required env var and
`FUND_JOURNALS` is overridable (`run_day.py:446`). Nothing in business logic
hardcodes a repo-relative `state/` or `logs/`.

**No `logs/` directory.** systemd captures stdout/stderr into the journal;
`journalctl -u fund-daily` replaces both plist log paths.

**Python.** `uv python install 3.14` (standalone build; does not touch Debian's
system 3.13), then `make deps` builds the venv via stdlib `venv` exactly as on
the Mac. uv is required anyway for the hardcoded `uvx alpaca-mcp-server`
(`agents/seats.py:49`), so this costs nothing and gives exact interpreter
parity with the green suite.

`make deps` must create the venv, not `uv venv`: `scripts/sync_deps.py` shells
out to `pip`, which stdlib `venv` bundles and `uv venv` does not.

**Pre-warm the uv tool cache at provision time** (§8, P1): on a fresh box the
cache is cold, so the first `uvx alpaca-mcp-server` downloads and builds the
tool environment. Left cold, that download happens inside the 09:35 critical
path, and a slow or unreachable PyPI means the seats get no broker tools at all.

**`.env` → `EnvironmentFile=`.** The file is plain `KEY=value` plus `#`
comments, with no `$`, no quotes, no spaces in values, and no trailing
backslash on any comment line — so systemd parses it directly and no
`set -a; . .env` subshell is needed.

> **`FUND_DB` must be rewritten, not copied.** It is currently a *relative*
> path, working on the Mac only because launchd sets `WorkingDirectory`. Under
> systemd it would resolve against `WorkingDirectory=/opt/fund` and land the
> fund inside the checkout — exactly what this layout prevents. Set it to
> `/var/lib/fund/fund.sqlite`.

**Persistent logs are not free.** Debian ships journald `Storage=auto`, which
means *volatile* — journals live in `/run/log/journal` and are erased on
reboot unless `/var/log/journal` exists. Since the plists' log files are gone,
the install must `mkdir -p /var/log/journal && systemd-tmpfiles --create`, and
set `SystemMaxUse=200M` explicitly rather than rely on the default
(10% of the filesystem, capped at 4G) on a 25 GB disk.

---

## 4. Scheduling and alerting

Three timer/service pairs. Daily and P&L are direct translations of the
plists; backup is new.

```ini
# fund-daily.timer
[Timer]
OnCalendar=Mon..Fri *-*-* 09:35:00 America/New_York
AccuracySec=1s          # else systemd randomizes within a 1-minute window
Persistent=false        # == RunAtLoad=false: a day starts because the market
                        # opened, never because the host did. A missed fire is
                        # skipped, not caught up.
Unit=fund-daily.service
```

```ini
# fund-daily.service
[Unit]
OnFailure=fund-alert@%n.service

[Service]
Type=oneshot
User=fund
WorkingDirectory=/opt/fund
EnvironmentFile=/etc/fund/env
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/run_day.py
TimeoutStartSec=30min   # bound a hung LLM call; kill -> failed -> alert
```

The timezone is pinned **in the expression** as well as on the host, so the
schedule stays correct even if the box's timezone is later changed. DST is a
non-issue at these times: only 02:00–03:00 local events get skipped or
doubled, and 09:35/16:35 exist on every day of the year.

Deliberately **no `Restart=`**. Invariant 4 says the default is HOLD — a failed
day waits for a human, it does not retry itself. A timeout kill is recoverable:
the kernel releases the flock when the process dies, and checkpoint CAS makes
the next run resume rather than repeat.

`fund-pnl` is the same shape at `16:35:00` running `close_pnl.py`. The plists'
reasoning is preserved as a comment: 16:35 not 16:15, because `close_frame`
shifts its end back `SIP_DELAY` (16 min), so an earlier fire asks for a bar the
closing auction has not written.

**The three service files duplicate `Type`/`User`/`EnvironmentFile`/`OnFailure`
deliberately** (review C1). A `fund@.service` template parameterized by `%i`
would be DRYer, but these are declarative files someone reads under pressure on
a failed morning, and `%i`-to-script indirection is the wrong thing to decode
then. The jobs also genuinely differ in timeout, schedule, and failure meaning.

### Failure alerting

```ini
# fund-alert@.service
[Service]
Type=oneshot
User=fund
EnvironmentFile=/etc/fund/alert-env   # NOT /etc/fund/env — see below
ExecStart=/opt/fund/ops/notify_failure.sh %i
```

`notify_failure.sh` is `curl` to Slack `chat.postMessage` and nothing else — no
DB connection, no fund imports. The alerting path must not share failure modes
with the thing it watches. It posts the unit name, the exit status, and the
last ~20 journal lines, which requires adding `fund` to the `systemd-journal`
group.

**Its own credential file** (review A1). The alert reads `/etc/fund/alert-env`,
holding only `SLACK_BOT_TOKEN` and the channel — never `/etc/fund/env`. If the
alert shared the main env file, then the single most likely fresh-VM failure
(a missing or unreadable env file) would break the job *and* its alert
identically, and the coverage claim below would be false.

**Redact before posting** (review A4). The journal tail is filtered for
`sk-ant-`, `xoxb-`, `xapp-`, and `PK` prefixes before it reaches Slack. A
traceback that dumps the environment must not publish broker keys.

**Check Slack's response body** (review C2). `chat.postMessage` returns
**HTTP 200 with `{"ok": false}`** on an auth or scope error, so `curl --fail`
sees success and the alert is silently lost. The script parses `ok` with `jq`,
logs the `error` field, and exits non-zero so the failure is itself visible.
`curl --max-time` bounds a hang.

**Why alerting is required, not optional.** `run_day.py:25-34` documents a
deliberate gap: `paper_guard`, `require_env`, `acquire_lock`, `market_is_open`,
and `RealSlack(...)` all run before the alert-and-drain guard, so a failure
there posts nothing to Slack. The docstring justifies it:

> That is acceptable: no order can have been placed by that point, and the exit
> is non-zero with a descriptive stderr message, so it is a visible failure,
> just not a Slack one.

**The move invalidates that premise.** "Visible" meant visible to a human at
the machine; on a droplet, a non-zero exit and a stderr line go to a journal
nobody reads. That uncovered window is precisely where a fresh VM fails —
missing env var, blocked egress in `market_is_open`, bad Slack token. Because
`OnFailure` also fires on start failures (203/EXEC, unreadable
`EnvironmentFile`) and timeout kills, it covers the whole window. This restores
the docstring's premise in the new environment rather than rewriting its
reasoning.

Note what the docstring's justification does and does not cover: it argues
*safety* (no order was placed), not *notification*. A fund that silently stops
trading for a week is the failure it leaves open.

---

## 5. Backups

`fund-backup.timer`, daily at 17:30 ET, weekends included (cheap and
idempotent):

- `sqlite3 .backup` → `/var/lib/fund/backups/fund-YYYY-MM-DD.sqlite`, plus a
  copy of `journals/`. `.backup` uses the SQLite backup API and is WAL-safe
  with a live writer; a plain file copy is not.
- **Atomic write** (review C4): write to `.tmp`, run `PRAGMA integrity_check`
  on it, then `mv` into place. A snapshot only appears once it is proven
  restorable, so an interrupted backup can never leave a partial file that
  looks valid.
- **No pruning** (review C3). At 86 KB per snapshot a full year costs ~31 MB on
  a 25 GB disk. A retention policy would buy nothing measurable while
  introducing the only destructive operation in the entire design. Revisit only
  if the DB grows by orders of magnitude.
- `OnFailure=fund-alert@%n.service` — a silently failing backup is a classic trap
- adds `sqlite3` and `jq` as package dependencies

Mac side: `ops/pull-backups.sh` (rsync from the droplet) plus a launchd agent
firing daily, which simply does nothing while the Mac is asleep.

This keeps a useful property: the droplet holds credentials only for the APIs
it must reach. A push backup would add a write credential for another system
onto the trading box; a pull uses the SSH key the Mac already needs.

---

## 6. Cutover

**The ordering matters more than the steps.** Everything that validates the VM
can run against a *snapshot* of the DB while the Mac stays authoritative. The
dangerous moment is the *final* state transfer: once the VM holds the current
DB, a Mac fire would write to a stale copy and place orders against the same
Alpaca account under a ticket-id namespace the VM has never seen. So the unload
belongs immediately **before** the final transfer.

| phase | on the Mac | on the VM | safe because |
|---|---|---|---|
| 0 — prep | delete orphan `fund-2026-08-17-rerun.sqlite-{wal,shm}`; `sqlite3 .backup` → snapshot | — | nothing changes |
| 1 — build | still authoritative, timer loaded | provision, TZ, user, packages, clone, `make deps`, `make test`, install `/etc/fund/env` (with `FUND_DB` absolute) and `/etc/fund/alert-env`, journald persistence, pre-warm uv cache, units installed **disabled** | VM does no writes |
| 2 — validate | untouched | the seven checks below | all read-only or market-closed early-exit |
| 3 — cutover | **unload, move plist out of `~/Library/LaunchAgents`, rename `.env`** | final `.backup` transfer + journals rsync; restore assertions; `systemctl enable --now` all three timers | one host holds an enabled timer at every instant |
| 4 — watch | — | first 09:35 run; digest lands; first-ever 16:35 P&L run | see below |

### Two independent barriers on the Mac (reviews T1, A2)

`launchctl unload` is **session-scoped**. `com.fund.daily.plist` currently sits
in `~/Library/LaunchAgents/`, which is launchd's per-user auto-load directory —
launchd loads its contents at every login. Unloading alone means the fund
**resurrects on the Mac at the next reboot or login**, days later, with no
human action, while the VM is also live. That is trap 2 firing automatically,
and `client_order_id` idempotency does not catch it.

So cutover does both:

1. **Move the plist out** of `~/Library/LaunchAgents` (a copy is kept in
   `ops/` for rollback), so launchd cannot find it at login.
2. **Rename the Mac's `.env`**, so any stray `make live-day` exits 1 loudly on
   `require_env` rather than silently placing duplicate orders.

A `FUND_HOST_ID` guard inside `run_day.py` — refusing to run when the DB was
last written by a different host — is the principled fix that makes this
invariant *enforced* rather than procedural. It is deliberately a follow-up
(§9): changing code and host on the same night is what this design argues
against everywhere else.

### Rollback (review A3)

**Only at a clean day boundary. Mid-day rollback is forbidden.** Once the VM
has placed an order, that order exists at Alpaca; rolling back to a pre-cutover
Mac snapshot would discard it from the DB and leave the broker and the source
of truth disagreeing — materially worse than a missed day.

Rollback mirrors the cutover exactly, in reverse: disable the VM timers first,
verify with `systemctl list-timers`, then transfer the DB and journals back,
then restore the plist and `.env` on the Mac and reload. If a VM day fails
mid-flight, **fix forward or accept a missed day** — never roll back until the
day is closed.

### Validation checks (phase 2)

1. `make test` green — **678 passed, 6 deselected** (not the 574 that
   `PROGRESS.md` and the handoff still claim; the eval rig added tests)
2. `make schema-pin` green — introspects the **live** Alpaca MCP tool schema.
   This exists because on 2026-08-17 the whole offline suite was green over a
   total outage: fixtures and gate encoded the same wrong stop-leg assumption.
   Do not skip it.
3. `uvx alpaca-mcp-server` starts and lists tools, from a warm cache
4. `run_day.py` with the market closed → exit 0, writing nothing
5. **Timer→service rehearsal** (review T2): a scratch timer firing ~2 minutes
   out at the real service. Gate 6 below proves only that systemd *parsed* the
   calendar; a manual `systemctl start` proves only that the *service* runs.
   Neither tests the join — timer triggers unit, as `User=fund`, with
   `EnvironmentFile` loaded and output captured — which is the entire
   production path and would otherwise first execute unattended at 09:35.
6. **All four alert triggers** (review T3): start failure (bad main env);
   non-zero exit (the most common real failure — audit fails, exit 1); timeout
   kill (via a temporarily lowered `TimeoutStartSec`); and the alert's own env
   broken, to *observe* that limitation as `systemctl --failed` rather than
   assume it.
7. **Restore test with three assertions** (review T4): `PRAGMA integrity_check`
   passes; `audit_day.py` reports 2026-08-17 clean; and row counts for
   `signals`, `decisions`, `tickets`, `orders` match the source. The row-count
   check is the one that actually catches a truncated copy — a partial file
   opens fine.

### Acceptance gates (phase 3, in order)

1. `systemctl list-timers` shows the expected next fire in ET
2. Slack posts land in the real channels from the VM
3. `launchctl list | grep fund` on the Mac returns nothing
4. `~/Library/LaunchAgents/` contains no fund plist
5. The Mac's `.env` is renamed
6. **Re-verify 3 and 4 after a real logout/login cycle** — the unload alone
   does not survive one

**De-risk the P&L job.** `com.fund.pnl` was never installed, so
`close_pnl.py` has never run in production. It is read-only plus a Slack post,
so prove it manually after a close before it ever sits on a timer. Its first
execution should be supervised, not scheduled.

**Never schedule `make eval`.** It spends real money — measured $0.81 and ~7
minutes per run. Only the three timers above get enabled.

---

## 7. Repo changes

- `ops/` gains seven unit files — three timer/service pairs (`fund-daily`,
  `fund-pnl`, `fund-backup`) plus `fund-alert@.service` — and three scripts
  (`notify_failure.sh`, `backup.sh`, `pull-backups.sh`)
- `ops/README.md` — provisioning runbook, including the rollback procedure
- `HANDOFF-LIVE.md:453-455` — the `launchctl` block becomes `systemctl`
- `CLAUDE.md` — remove the `docker compose up` line describing files that do
  not exist
- `PROGRESS.md` — host, schedule, test count, and open items
- The plists move out of `~/Library/LaunchAgents` but stay in `ops/` for one
  clean week as the rollback source, then are deleted

---

## 8. Risks accepted

- **A broken Slack token silences its own alert.** If Slack auth is what
  fails, `notify_failure.sh` cannot post either. A dead-man's-switch
  (external ping on success) would cover it; it was considered and declined.
  Known accepted risk, not an oversight. Checking the `ok` field at least makes
  the failure visible in `systemctl --failed` and the journal.
- **Off-box backup freshness depends on the Mac being awake.** On-box snapshots
  are now unbounded, so a long Mac absence loses nothing; losing the droplet
  while the Mac has been asleep for a long stretch does. Object storage can be
  added later without redesign.
- **Single-host SQLite** remains the architecture (§2), and the one-host
  invariant remains *procedural* until the `FUND_HOST_ID` follow-up lands.
- **`uvx alpaca-mcp-server` is unpinned** (`agents/seats.py:49`), so it resolves
  latest at run time. An upstream release could move a tool-schema field name
  unattended between one day and the next — the exact 2026-08-17 outage class.
  `make schema-pin` defends only when run. Pre-warming the cache is tonight's
  mitigation; whether `uv tool install <pkg>==X` actually pins what a bare
  `uvx` resolves is **unverified** and must be checked on the box. If it does
  not, pinning in `seats.py` becomes a follow-up.
- **A parallel session merged the eval rig mid-design** (`191fd18`,
  `5196282`). It does not touch `run_day.py`, `close_pnl.py`, `audit_day.py`,
  `gate/`, `state/`, or `agents/seats.py`, and its runner uses a per-trial DB
  (`evals/runner.py:116`), never `FUND_DB`. If another session is active during
  cutover, the state being migrated could still shift — `sqlite3 .backup` is
  WAL-safe, but the cutover assumes no concurrent writer.

## 9. Follow-ups (not tonight)

- **`FUND_HOST_ID` guard** in `run_day.py`: refuse to run when the DB records a
  different last-writing host. Turns the one-host invariant from procedural
  into enforced. Needs tests.
- **Pin the MCP server version** in `agents/seats.py:49`, if the provision-time
  pin attempt proves insufficient.
- **Dead-man's-switch**, if the accepted risk above stops feeling acceptable.

## 10. Out of scope

- Real money. Invariant 1 stays `true`; `scripts/check_purity.py` untouched.
- Re-architecting seats into long-lived per-seat containers.
- Multi-host or HA.
