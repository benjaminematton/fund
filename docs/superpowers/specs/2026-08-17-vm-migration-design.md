# Design — move the daily run to a Linux VM

**Date** 2026-08-17 · **Base** `7aa3c32` · **Mode** Alpaca paper only (invariant 1)

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
| backup destination | 14 days of dated on-box snapshots + opportunistic rsync pull to the Mac |
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
| `/var/lib/fund/backups/` | 14 dated snapshots | §5 |
| `/etc/fund/env` | secrets, `0600 fund:fund` | survives a re-clone; loaded by `EnvironmentFile=` |

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

### Failure alerting

```ini
# fund-alert@.service
[Service]
Type=oneshot
User=fund
EnvironmentFile=/etc/fund/env
ExecStart=/opt/fund/ops/notify_failure.sh %i
```

`notify_failure.sh` is `curl` to Slack `chat.postMessage` and nothing else — no
DB connection, no fund imports. The alerting path must not share failure modes
with the thing it watches. It posts the unit name, the exit status, and the
last ~20 journal lines, which requires adding `fund` to the `systemd-journal`
group.

**Why this is required, not optional.** `run_day.py:25-34` documents a
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
- prune snapshots older than 14 days
- `OnFailure=fund-alert@%n.service` — a silently failing backup is a classic trap
- adds the `sqlite3` package as a dependency

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
| 1 — build | still authoritative, timer loaded | provision, TZ, user, packages, clone, `make deps`, `make test`, install `.env` with `FUND_DB` absolute, journald persistence, units installed **disabled** | VM does no writes |
| 2 — validate | untouched | `make schema-pin`; `uvx alpaca-mcp-server` lists tools; `run_day.py` market-closed → exit 0; break `EnvironmentFile` → confirm Slack alert; restore-test a backup | all read-only or market-closed early-exit |
| 3 — cutover | **`launchctl unload` first**, verify gone | final `.backup` transfer + journals rsync; `audit_day.py` → 2026-08-17 still clean; `systemctl enable --now` both timers; `list-timers` shows ET | one host holds an enabled timer at every instant |
| 4 — watch | — | first 09:35 run; digest lands; first-ever 16:35 P&L run | see below |

**De-risk the P&L job.** `com.fund.pnl` was never installed, so
`close_pnl.py` has never run in production. It is read-only plus a Slack post,
so prove it manually after a close before it ever sits on a timer. Its first
execution should be supervised, not scheduled.

### Acceptance gates

1. `make test` green on the VM — **678 passed, 6 deselected** (not the 574 that
   `PROGRESS.md` and the handoff still claim; the eval rig added tests)
2. `make schema-pin` green — introspects the **live** Alpaca MCP tool schema.
   This exists because on 2026-08-17 the whole offline suite was green over a
   total outage: fixtures and gate encoded the same wrong stop-leg assumption.
   Do not skip it.
3. `audit_day.py` reads the migrated DB and still reports 2026-08-17 clean
4. `uvx alpaca-mcp-server` starts and lists tools
5. Slack posts land in the real channels from the VM
6. `systemctl list-timers` shows the expected next fire in ET
7. `launchctl list | grep fund` on the Mac returns nothing

**Never schedule `make eval`.** It spends real money — measured $0.81 and ~7
minutes per run. Only the three timers above get enabled.

---

## 7. Repo changes

- `ops/` gains seven unit files — three timer/service pairs (`fund-daily`,
  `fund-pnl`, `fund-backup`) plus `fund-alert@.service` — and three scripts
  (`notify_failure.sh`, `backup.sh`, `pull-backups.sh`)
- `ops/README.md` — provisioning runbook
- `HANDOFF-LIVE.md:453-455` — the `launchctl` block becomes `systemctl`
- `CLAUDE.md` — remove the `docker compose up` line describing files that do
  not exist
- `PROGRESS.md` — host, schedule, test count, and open items
- The two plists stay for one clean week as a rollback path, then are deleted

---

## 8. Risks accepted

- **A broken Slack token silences its own alert.** If Slack auth is what
  fails, `notify_failure.sh` cannot post either. A dead-man's-switch
  (external ping on success) would cover it; it was considered and declined.
  This is a known accepted risk, not an oversight.
- **Off-box backup freshness depends on the Mac being awake.** On-box dated
  snapshots retain 14 days, so a week of Mac sleep loses nothing; losing the
  droplet *and* having an asleep Mac for 14+ days does. Object storage can be
  added later without redesign.
- **Single-host SQLite** remains the architecture (§2).
- **A parallel session merged the eval rig mid-design** (`191fd18`,
  `5196282`). It does not touch `run_day.py`, `close_pnl.py`, `audit_day.py`,
  `gate/`, `state/`, or `agents/seats.py`, and its runner uses a per-trial DB
  (`evals/runner.py:116`), never `FUND_DB`. If another session is active during
  cutover, the state being migrated could still shift — `sqlite3 .backup` is
  WAL-safe, but the cutover assumes no concurrent writer.

## 9. Out of scope

- Real money. Invariant 1 stays `true`; `scripts/check_purity.py` untouched.
- Re-architecting seats into long-lived per-seat containers.
- Multi-host or HA.
