# ops — the fund's scheduled host

The fund runs on one Linux droplet on an ET clock. Two jobs trade; one backs up.

**Only one host may ever hold a live schedule.** `flock` is machine-local and
ticket-id namespaces are per-database, so two hosts produce genuine duplicate
orders that `client_order_id` idempotency cannot catch. Everything in this
document is arranged around that.

## Layout

| path | holds |
|---|---|
| `/opt/fund` | git checkout + `.venv` — disposable, re-clonable |
| `/var/lib/fund/fund.sqlite` | **the fund** — outside the checkout, so no re-clone or `git clean -x` can reach it |
| `/var/lib/fund/journals/` | agent memory (`FUND_JOURNALS`) |
| `/var/lib/fund/traces/` | one trace per seat turn (`FUND_TRACES`) — the corpus every day-review reads |
| `/var/lib/fund/backups/` | dated snapshots, kept indefinitely |
| `/etc/fund/env` | job secrets, `0600 fund:fund` |
| `/etc/fund/alert-env` | alert secrets only, `0600 fund:fund` |

`/var/lib/fund/run_day.lock` lands there for free — `acquire_lock` derives it
from `FUND_DB`'s parent.

There is no `logs/` directory. systemd captures stdout and stderr into the
journal: `journalctl -u fund-daily`.

## Units

| unit | fires | runs |
|---|---|---|
| `fund-daily.timer` | 09:35 ET Mon–Fri | `scripts/run_day.py` |
| `fund-pnl.timer` | 16:35 ET Mon–Fri | `scripts/close_pnl.py`, then `scripts/resolve_day.py` |
| `fund-backup.timer` | 17:30 ET daily | `ops/backup.sh` |
| `fund-alert@.service` | on any of the above failing | `ops/notify_failure.sh` |

Three things about these are deliberate and should not be "tidied":

- **The timezone is pinned in the `OnCalendar` expression**, not only on the
  host, so the schedule stays correct if the box's timezone is ever changed.
- **`Persistent=false`** reproduces the old plists' `RunAtLoad=false`: a day
  starts because the market opened, never because the host booted. A missed
  fire is skipped, not caught up.
- **No `Restart=` anywhere.** Invariant 4's default is HOLD. A failed day waits
  for a human; it does not retry itself.

`16:35` is measured, not chosen: `close_frame` shifts its end back `SIP_DELAY`
(16 min), so a 16:15 fire asks for a 15:59 bar the closing auction has not
written yet, and correctly posts nothing.

## Provisioning a fresh host

Debian 13, 1 vCPU / 2 GB, NYC. As root:

```bash
timedatectl set-timezone America/New_York
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo "/swapfile none swap sw 0 0" >> /etc/fstab

adduser --disabled-password --gecos "" --home /home/fund --shell /bin/bash fund
usermod -aG systemd-journal fund     # notify_failure.sh reads the journal

# Debian defaults journald to Storage=auto, which is VOLATILE without this
# directory — logs would be erased on every reboot, and they are now the only
# forensic trail this host has.
mkdir -p /var/log/journal /etc/systemd/journald.conf.d
systemd-tmpfiles --create --prefix /var/log/journal
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/fund.conf
systemctl restart systemd-journald

apt-get update && apt-get install -y git curl sqlite3 jq rsync
mkdir -p /var/lib/fund/journals /var/lib/fund/traces /var/lib/fund/backups /etc/fund /opt/fund
chown -R fund:fund /var/lib/fund /opt/fund
chmod 750 /var/lib/fund && chmod 700 /etc/fund
```

As `fund`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH=$HOME/.local/bin:$PATH
uv python install 3.14          # standalone; does not touch Debian's 3.13

git clone https://github.com/benjaminematton/fund.git /opt/fund
cd /opt/fund && make deps       # must be make deps, NOT uv venv:
                                # scripts/sync_deps.py shells out to pip, which
                                # stdlib venv bundles and uv venv does not.
make test                       # offline, no keys needed
```

Pre-warm the tool cache, or the first `uvx` download lands inside the 09:35
critical path. Warm **the pinned spec**, never bare `alpaca-mcp-server`: bare
warms whatever is latest today, which leaves the version the seats actually
launch cold, and puts the download back in the launch path the moment upstream
ships a release. Deriving it from the source keeps this from rotting at the next
bump.

```bash
set -a; . /etc/fund/env; set +a
uvx "$(.venv/bin/python3 -c 'from agents.seats import ALPACA_MCP_SPEC as s; print(s)')" --help
```

### Secrets

`/etc/fund/env` is the Mac's `.env` with **every state path rewritten absolute**.
They are relative in the original and work there only because launchd sets
`WorkingDirectory`; under systemd they would resolve against `/opt/fund` and put
the fund inside the checkout.

```
FUND_DB=/var/lib/fund/fund.sqlite
FUND_JOURNALS=/var/lib/fund/journals
FUND_TRACES=/var/lib/fund/traces
```

**`FUND_TRACES` unset records nothing.** It is deliberately not in
`REQUIRED_ENV` — an older env file runs the day exactly as before rather than
refusing to start over an evidence feature — which also means a missing line
here fails silently. Both units that need it read this file
(`fund-daily.service` writes the traces, `fund-backup.service` archives them),
so one line covers both. A day that recorded nothing is visible in the log:
`run_day` prints `recording seat traces under <path>` when the sink is live.

**Adding this line before the code that reads it is deployed arms a change
that fires later.** It is inert until a pull brings the reading code, and then
recording starts on the next day with no deploy step that mentions it — the
env file changed hours or days earlier and nothing surfaces it at pull time.
That happened on 2026-08-19: the line was staged at 16:45 EDT, 45 minutes
after a deploy, and the next pull would have started recording by inheritance
rather than by choice. If you stage an env line ahead of its code, say so to
whoever deploys next; `/etc/fund/env` is not in git and no diff will show it.
Traces cannot be reconstructed afterwards, so the cost of forgetting is the
corpus itself.

`/etc/fund/env` also carries the heartbeat target, which is why the units can
stay free of any hardcoded monitoring URL:

```
HC_PING_URL=https://hc-ping.com/<uuid>
```

`/etc/fund/alert-env` holds three lines and nothing else:

```
SLACK_BOT_TOKEN=xoxb-...
FUND_ALERT_CHANNEL=#fund-ops
FUND_ALERT_MENTION=<@U...>
```

`#fund-ops`, not `#risk`: a host that failed to boot and a position that
breached a limit are different emergencies with different readers, and mixing
them trains you to skim both.

`FUND_ALERT_MENTION` is optional but wanted. A Slack channel notification
preference is per-device and resets on reinstall; a real `<@U...>` in the
payload pings regardless of client settings. Plain `@name` does **not** work —
Slack does not resolve display names server-side, so it posts literal text and
notifies nobody.

**It is a separate file on purpose.** A missing or unreadable `/etc/fund/env`
is the most likely fresh-host failure; if the alert read the same file it would
die for the identical reason and you would learn nothing.

systemd parses these files itself — it does **not** expand `${VAR}`, and it
continues a comment across a trailing backslash where a shell would not.

### The heartbeat check

`fund-daily.service` pings `HC_PING_URL` from `ExecStartPost`, so a ping means
the day completed. The watchdog alerts on the **absence** of one — the single
failure mode `OnFailure` cannot see, because a timer that never fires produces
nothing for systemd to react to. Provisioning a fresh host is not finished
until this exists; without it the host is silent in exactly the case that
matters, and silence reads as a quiet day.

On healthchecks.io, the check must be in **cron** mode:

| field | value |
|---|---|
| Schedule | `35 9 * * 1-5` |
| Timezone | `America/New_York` |
| Grace | `45 min` (2700s) |
| Integrations | `#risk` Slack **and** email |

**Cron mode, not the default "simple" period mode.** Simple mode expects a ping
every N hours, so Friday's run sets Saturday's deadline and you get a false
alarm every weekend the fund correctly does not run. Cron mode knows about
weekdays. Market holidays are already safe either way: the timer fires,
`run_day.py` exits 0 on the broker clock, and the ping still goes out.

Grace is 45 minutes because `TimeoutStartSec=30min` bounds the run, so a ping
can legitimately arrive as late as ~10:05 — the deadline lands at 10:20, still
early enough to act on the day.

Verify it the way the unit will, expanding the variable through systemd rather
than trusting a shell that has it for other reasons:

```bash
systemd-run --uid=fund --pipe --wait --quiet \
  --property=EnvironmentFile=/etc/fund/env \
  /usr/bin/curl -fsS -m 10 --retry 3 '${HC_PING_URL}'   # expect: OK
```

### Install the units

```bash
sudo cp /opt/fund/ops/fund-*.timer /opt/fund/ops/fund-*.service \
        "/opt/fund/ops/fund-alert@.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/fund-daily.service
```

Do **not** enable the timers until the cutover.

## Cutover

**The ordering is the safety property.** Validation runs against a *snapshot*
while the old host stays authoritative. The old host is silenced immediately
**before** the final state transfer — never after — because once the new host
holds the current database, a fire on the old one writes to a stale copy and
places orders under a ticket-id namespace the new host has never seen.

### 1. Validate (old host still live)

```bash
make test                       # offline suite
make schema-pin                 # the REAL tool schema, at the pinned version
# broker tools reachable — the same pinned spec the seats launch, never bare
uvx "$(.venv/bin/python3 -c 'from agents.seats import ALPACA_MCP_SPEC as s; print(s)')" --help
python scripts/run_day.py       # market closed -> exit 0, writes nothing
```

> None of the four prove what systemd will do. Run interactively — or under
> `su - fund`, which is a **login shell** — they source the profile and get
> `~/.local/bin` on `PATH`. systemd's default `PATH` does not include it.
> Validating with `su - fund` is not a check of the launch path.

Then rehearse the timer→service join, which neither `list-timers` nor a manual
`systemctl start` proves on its own:

```bash
cat > /etc/systemd/system/fund-rehearsal.timer <<EOF
[Timer]
OnActiveSec=90s
AccuracySec=1s
Unit=fund-daily.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload && systemctl start fund-rehearsal.timer
sleep 120 && journalctl -u fund-daily.service -n 20 --no-pager
systemctl stop fund-rehearsal.timer && rm /etc/systemd/system/fund-rehearsal.timer
systemctl daemon-reload
```

**The rehearsal above is not sufficient on its own.** Run with the market
closed — which is when a cutover happens — `run_day.py` exits on the broker
clock before any seat starts. It proves the timer fires the unit and nothing
past that: no `uvx`, no MCP connect, no seat turn. On 2026-08-18 it passed and
concealed a missing `PATH` that cost a full trading day. A rehearsal that exits
early is worse than no rehearsal, because it produces a green checkmark.

So finish with the preflight, which is **mandatory after any host, unit, or
environment change**:

```bash
make preflight    # ~2 min, ~$0.31, places no orders
```

It runs a real seat turn via `systemd-run` under the unit's exact `PATH`,
`HOME` and `EnvironmentFile` — the only check here that exercises `uvx` → MCP →
Anthropic → a seat the way the timer will. Expect `a01 3/3 OK`. Note
`/opt/fund/.env` must be a symlink to `/etc/fund/env`: `scripts/eval_suite.py`
loads `.env` itself via `scripts/eval_one.py:load_env`.

Then prove the alert fires — break `/etc/fund/env`, start the unit, confirm the
Slack message. Then restore it.

### 2. Silence the old Mac — BOTH barriers

> `launchctl unload` is **session-scoped**. `~/Library/LaunchAgents` is
> launchd's per-user auto-load directory — it reloads its contents at every
> login. Unloading alone means the fund resurrects on the Mac at the next
> reboot or login while the droplet is live. Two hosts means two ticket-id
> namespaces, so `client_order_id` idempotency will **not** dedupe the
> duplicate orders. Move the plist out; do not merely unload it.

```bash
launchctl unload ~/Library/LaunchAgents/com.fund.daily.plist
mkdir -p ~/fund-rollback && mv ~/Library/LaunchAgents/com.fund.daily.plist ~/fund-rollback/
mv .env .env.MIGRATED-TO-VM          # second barrier: a stray run exits 1 loudly

launchctl list | grep -i fund || echo "PASS: no fund job loaded"
ls ~/Library/LaunchAgents/ | grep -i fund || echo "PASS: no fund plist in auto-load dir"
```

Re-check both **after a real logout/login** — the unload alone does not survive one.

### 3. Transfer and enable

```bash
sqlite3 state/fund.sqlite ".backup '/tmp/fund-final.sqlite'"
sqlite3 /tmp/fund-final.sqlite 'PRAGMA integrity_check'
scp /tmp/fund-final.sqlite root@HOST:/var/lib/fund/fund.sqlite
rsync -az journals/ root@HOST:/var/lib/fund/journals/
ssh root@HOST 'chown -R fund:fund /var/lib/fund'
```

Assert the copy survived — three checks, because "the file opened" is not "the
fund survived" and a truncated copy opens fine:

```bash
sqlite3 /var/lib/fund/fund.sqlite 'PRAGMA integrity_check'
.venv/bin/python3 scripts/audit_day.py /var/lib/fund/fund.sqlite 2026-08-17
for t in signals decisions tickets orders; do
  printf "%s=%s " "$t" "$(sqlite3 /var/lib/fund/fund.sqlite "SELECT count(*) FROM $t")"
done; echo
```

Row counts must match the source. Then:

```bash
systemctl enable --now fund-daily.timer fund-pnl.timer fund-backup.timer
systemctl list-timers 'fund-*' --no-pager
```

## Deploy a code change

`/opt/fund` is a plain git checkout and the units run straight out of it, so a
deploy is a pull. Nothing needs restarting for a *code* change: every unit is
`Type=oneshot` and reads the working tree fresh at each invocation. That also
means the tree is read *while the next run starts* — hence the guard below.

**The unit files are the exception, and they are the easy thing to miss.**
`/etc/systemd/system/fund-*.{service,timer}` are **copies**, not symlinks into
the repo. A pull updates `ops/` in the checkout and changes nothing systemd
runs. Edit a unit, pull, and the old one still fires — with a clean `git
status` and a matching `HEAD` to reassure you.

```bash
systemctl is-active fund-daily.service        # MUST be `inactive`
su - fund -c 'cd /opt/fund && git pull --ff-only'
cd /opt/fund && git diff --stat <old-sha>..HEAD -- pyproject.toml state/schema.sql ops/
make preflight                                 # as root; ~2 min, ~$0.31
```

If `ops/` shows a changed `.service` or `.timer`, reinstall the copies and
reload, then re-verify — `daemon-reload` alone re-reads `/etc/systemd/system`,
which is not where you just edited:

```bash
cp /opt/fund/ops/fund-*.timer /opt/fund/ops/fund-*.service /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/fund-daily.service
systemctl list-timers fund-daily.timer --no-pager   # confirm still armed
```

**The pull runs as `fund`, not root.** The read-only GitHub deploy key lives in
`/home/fund/.ssh/id_ed25519` and `/opt/fund` is `fund:fund`; as root the pull
fails on `Permission denied (publickey)` and, with a key present, would leave
root-owned objects in `.git` that the units can no longer write. `make
preflight` is the opposite — run it as root, since `systemd-run --uid=fund`
needs the privilege to set the unit properties.

This is the one place `su - fund` is right. Its login shell is the *point* here
— git needs `$HOME` for the key and `known_hosts`. The warning against it under
Cutover is about using it to *validate the launch path*, where the profile it
sources hides systemd's real `PATH`.

**1. Never deploy while a day is running.** `is-active` is the check;
`fund-daily.service` is `oneshot`, so "active" means a trading day is in
flight. A pull mid-run swaps files under a live process that has already
imported some modules and not others — it would run half the old code and half
the new. `acquire_lock()`'s flock protects against a *second* run, not against
the tree changing beneath the first one. Deploy after the close or before the
open.

**2. `--ff-only` on purpose.** A merge commit created on the droplet exists
nowhere else and makes the next pull conflict. If it refuses to fast-forward,
someone edited the tree in place — find out what before forcing anything.

**3. Check the two files that a pull alone does not handle.** If
`pyproject.toml` changed, the venv is stale — resync it **as the `fund` user**,
never as root, or the venv ends up owned by root and the units can no longer
write it:

```bash
su - fund -c 'cd /opt/fund && .venv/bin/python3 scripts/sync_deps.py'
```

If `state/schema.sql` changed, read `state/migrations.py` before pulling.

There is now a migration framework, added 2026-08-19: `state/db.py:connect()`
applies every pending migration, so an existing `fund.sqlite` does pick up the
change. Before that, `schema.sql` was applied only at creation and an existing
DB silently disagreed with the code — which is why this step used to say
**stop** outright.

It is still the only step in a deploy that **writes to the live database**, so
it is the one to slow down on:

```bash
cd /opt/fund && git diff <old-sha>..origin/master -- state/migrations.py
```

Read what it does before you run it. Additive `ALTER TABLE ADD COLUMN` with a
default is safe and idempotent; anything that drops, renames, rewrites or
backfills a column is not a deploy step and needs planning on its own.
Take a fresh backup immediately before the pull.

**`make preflight` does NOT run the migration, and a green preflight is not
evidence that it ran.** Preflight drives `eval_suite.py`, which builds a fresh
per-trial DB under `evals/traces/` and never opens `$FUND_DB`. Measured on
2026-08-19: after a clean pull and a green preflight, the live DB still had
zero of the six new columns. Left there, the first `connect()` against it would
have been the next 09:35 `run_day` — the schema changing unattended, mid-day,
with a green preflight standing as false reassurance that it already had.

So fire it deliberately, while you are watching and the backup is fresh:

```bash
su - fund -c 'cd /opt/fund && set -a && . /etc/fund/env && set +a && \
  .venv/bin/python3 -c "from state.db import connect; import os; \
  connect(os.environ[\"FUND_DB\"])"'
```

Then assert against the source of truth rather than trusting the exit code —
the columns exist AND the row counts are unchanged:

```bash
sqlite3 "$FUND_DB" 'PRAGMA table_info(signals);'   # charter_version, model_id
sqlite3 "$FUND_DB" 'SELECT count(*) FROM signals;' # vs the pre-pull number
sqlite3 "$FUND_DB" 'PRAGMA integrity_check;'
```

`ADD COLUMN` cannot lose rows, so this is meant to be boring. Run it anyway:
every incident on this deployment so far has been the system reporting success
for something nobody compared against the database.

**4. Finish with `make preflight`, not with a green `git pull`.** A pull that
succeeded proves files moved. Preflight proves the seats still start under
systemd — the exact thing that looked fine and was not on 2026-08-18.

To undo a bad deploy, `git checkout <previous-sha>` and run `make preflight`
again. That is a *code* rollback and is always safe; rolling back **data** is a
different operation, governed by the section below.

## Staging: a full day on demand

`make preflight` proves the launch path and a seat turn. It stops short of the
gate and the exec seat, so until 2026-08-18 the only thing that exercised
**order placement** was a live 09:35 fire — a 24-hour iteration loop, and one
that had already cost a trading day.

`make staging-day` runs a complete day — analyst, PM, gate, a real broker order,
reconciliation, audit — against a **second Alpaca paper account**, through the
same `systemd-run` launch path the timer uses. About 4 minutes, about $0.23,
any time the market is open.

```bash
make staging-reset     # flatten the scratch account, wipe the scratch DB
make staging-day       # the full day
```

### Setup, once

Copy `ops/staging-env.example` to `/etc/fund/staging-env`, fill it in, then
`chown fund:fund` and `chmod 600`. It needs a **second Alpaca paper account** —
its own key pair, not production's.

### Why a second account is not optional

A rehearsal sharing production's account would place orders that change
production's positions and buying power. The next real day would then size
against state its own database never recorded, and no audit or reconciliation
downstream can detect that — the broker and the source of truth simply disagree,
with nothing marking the moment they diverged.

So `ops/staging-day.sh` refuses to start unless it can prove, by querying both
key pairs, that staging and production are **different account numbers** and
**different `FUND_DB` paths**, and that `ALPACA_PAPER_TRADE=true` in staging
too. `ops/staging-reset.sh` liquidates positions, so it reuses that same guard
rather than carrying a copy — a divergent copy of a safety check is worse than
no copy. Both refusals are pinned in `tests/test_ops_staging_day.py`.

### What it does and does not prove

Proves: `uvx` → MCP connect → analyst with live market data and news → PM →
deterministic gate → exec seat placing a real order → reconciliation → audit,
all under the unit's real `PATH`, `HOME` and `EnvironmentFile`.

Does not prove: anything about *this* account's positions or cash, which differ
from production's — so gate sizing and `allowed_actions` will differ too. A
clean staging day means the machinery works, not that today's production
decision would have been identical.

Also bounded by market hours: `run_day.py` exits on the broker clock when the
market is shut, so this is a 09:30–16:00 ET tool.

### Housekeeping

Positions accumulate across runs, which changes what the gate allows on the next
one. Run `make staging-reset` first when you want a comparable baseline; skip it
deliberately when you want to exercise the sell path.

Staging posts every channel to a harmless one via `SLACK_CHANNEL_OVERRIDES`.
`run_day.py` hard-stops on a malformed entry rather than risk posting a
rehearsal to `#pnl`.

## Rollback

**Only at a clean day boundary. Mid-day rollback is forbidden.** Once a day has
placed an order, that order exists at Alpaca. Rolling back to a pre-cutover
snapshot would discard it from the database and leave the broker and the source
of truth disagreeing — materially worse than a missed day.

If a day fails mid-flight: **fix forward, or accept a missed day.**

At a clean boundary, mirror the cutover in reverse:

```bash
ssh root@HOST 'systemctl disable --now fund-daily.timer fund-pnl.timer fund-backup.timer'
ssh root@HOST 'systemctl list-timers "fund-*" --no-pager'    # verify gone FIRST

ssh root@HOST "sqlite3 /var/lib/fund/fund.sqlite \".backup '/tmp/back.sqlite'\""
scp root@HOST:/tmp/back.sqlite state/fund.sqlite
rsync -az root@HOST:/var/lib/fund/journals/ journals/
rsync -az root@HOST:/var/lib/fund/traces/ traces/

mv .env.MIGRATED-TO-VM .env
cp ~/fund-rollback/com.fund.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fund.daily.plist
```

## Daily operations

```bash
journalctl -u fund-daily -n 100 --no-pager     # the day's log
journalctl -u fund-daily --since "09:30"       # this morning
systemctl list-timers 'fund-*' --no-pager      # when does it next fire
systemctl --failed                             # anything broken
systemctl start fund-pnl.service               # P&L digest + resolutions by hand
ls -la /var/lib/fund/backups/                  # snapshots
```

## Deliberately not scheduled

`make eval` runs real LLM turns against the real charters — measured **$0.81
and ~7 minutes** per run. It is never put on a timer. Run it by hand.
