# VM Migration Implementation Plan

<!-- plan-status -->
> **Status: DELIVERED — 2026-08-25.** `ops/fund-daily.service` and the rest of the unit set are on `master`; the droplet runs the schedule.
>
> Follow-up on the board: #17 (`make preflight` never opens the live DB, so a pending migration gets a false green).
>
> **Checkbox state is not a progress signal and nothing reads it.** Measured 2026-08-24 across
> every plan file in this directory: 359 unchecked boxes, zero checked, including plans whose work
> demonstrably shipped. Ticking them is friction for the ticker and invisible to everyone else.
> Work in flight lives on the board — the `wayfinder:map` issue and its children. This plan is the
> *how*, referenced from an issue; it is never read as state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the fund's two scheduled jobs from launchd on a Pacific Mac to systemd on a DigitalOcean Debian 13 droplet set to `America/New_York`, without ever letting two hosts hold a live schedule.

**Architecture:** Three timer/service pairs (`fund-daily`, `fund-pnl`, `fund-backup`) plus a templated alert unit, running as a non-root `fund` user. Code lives in `/opt/fund`; live state lives outside the checkout in `/var/lib/fund`. Failures reach Slack through `OnFailure=`, using a credential file separate from the job's own, so the alert path shares no failure mode with the thing it watches.

**Tech Stack:** Debian 13, systemd, POSIX shell, `sqlite3`, `jq`, `uv`-managed Python 3.14, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-vm-migration-design.md`

## Global Constraints

- **Paper only.** `ALPACA_PAPER_TRADE=true` everywhere. Never add a live-trading path.
- **Exactly one host may hold a live schedule at any instant.** `flock` is machine-local and ticket-id namespaces are per-DB, so two hosts produce duplicate orders that `client_order_id` idempotency cannot catch.
- **Default is HOLD.** No `Restart=` on any unit. A failed day waits for a human.
- **Never weaken a test, update a golden fixture, or change an expected value to go green.** Stop and ask.
- **No `Co-Authored-By` or AI attribution** in any commit message or PR body.
- Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`, `docs:`.
- `make test` must pass before every commit. Current baseline: **678 passed, 6 deselected.**
- Tasks 1–2 add `jq` as a **`make test` dependency** (the ops scripts parse JSON with it; the tests fake `curl` and `journalctl` but use the real `jq`). Present on the Mac at `/opt/homebrew/bin/jq`; installed on the droplet in Task 7 Step 5.
- `scripts/audit_day.py` is **positional**: `audit_day.py <db_path> <run_date>`. Prints `AUDIT CLEAN <date>` and exits 0 when clean.
- Never run a backtest except through the `run_backtest` tool.
- `make eval` is never scheduled — it spends real money ($0.81, ~7 min per run).
- Secrets move out of band. Never print, commit, or paste a key.

## Paths (exact, used throughout)

| purpose | path |
|---|---|
| checkout | `/opt/fund` |
| venv python | `/opt/fund/.venv/bin/python3` |
| live DB | `/var/lib/fund/fund.sqlite` |
| journals | `/var/lib/fund/journals` |
| backups | `/var/lib/fund/backups` |
| job secrets | `/etc/fund/env` (`0600 fund:fund`) |
| alert secrets | `/etc/fund/alert-env` (`0600 fund:fund`) |
| units | `/etc/systemd/system/` |

---

## File Structure

**Created in this repo:**

| file | responsibility |
|---|---|
| `ops/notify_failure.sh` | post a failed unit's name, status, redacted journal tail to Slack; verify Slack's `ok` |
| `ops/backup.sh` | atomic, integrity-checked SQLite snapshot + journals tarball |
| `ops/pull-backups.sh` | Mac-side rsync pull of the droplet's backups |
| `ops/com.fund.pull-backups.plist` | Mac launchd agent driving the pull |
| `ops/fund-daily.{timer,service}` | 09:35 ET trading day |
| `ops/fund-pnl.{timer,service}` | 16:35 ET P&L digest |
| `ops/fund-backup.{timer,service}` | 17:30 ET snapshot |
| `ops/fund-alert@.service` | templated failure alert |
| `ops/README.md` | provisioning, cutover, rollback runbook |
| `tests/test_ops_notify.py` | redaction, payload shape, Slack `ok` handling |
| `tests/test_ops_backup.py` | atomicity, integrity, row counts, no-prune |

**Modified:** `CLAUDE.md` (stale docker line), `PROGRESS.md` (host, schedule, test count, open items), `HANDOFF-LIVE.md:453-455` (launchctl → systemctl).

---

## Task 1: `ops/notify_failure.sh` — the alert path

**Files:**
- Create: `ops/notify_failure.sh`
- Test: `tests/test_ops_notify.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ops/notify_failure.sh <unit-name>`, invoked by `fund-alert@.service` (Task 4). Reads `SLACK_BOT_TOKEN` and `FUND_ALERT_CHANNEL` from the environment. Honors `FUND_ALERT_CURL` (curl binary override, for tests) and `FUND_ALERT_JOURNALCTL` (journalctl override, for tests). Exits 0 only when Slack returns `ok:true`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ops_notify.py`:

```python
"""ops/notify_failure.sh — the alert path must not leak secrets or lie about success."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "notify_failure.sh"


def _fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return p


def _run(tmp_path, journal_text, curl_body, unit="fund-daily.service"):
    """Run the script with fake journalctl and fake curl; return (proc, payload)."""
    journalctl = _fake_bin(tmp_path, "journalctl", f"cat <<'EOF'\n{journal_text}\nEOF")
    # fake curl writes the request body it was handed to payload.json, then answers
    payload = tmp_path / "payload.json"
    curl = _fake_bin(
        tmp_path,
        "curl",
        f'for a in "$@"; do prev=$last; last=$a; '
        f'if [ "$prev" = "-d" ] || [ "$prev" = "--data" ]; then printf %s "$a" > {payload}; fi; done\n'
        f"cat <<'EOF'\n{curl_body}\nEOF",
    )
    env = {
        **os.environ,
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "FUND_ALERT_CHANNEL": "#risk",
        "FUND_ALERT_CURL": str(curl),
        "FUND_ALERT_JOURNALCTL": str(journalctl),
    }
    proc = subprocess.run([str(SCRIPT), unit], capture_output=True, text=True, env=env)
    body = json.loads(payload.read_text()) if payload.exists() else None
    return proc, body


def test_posts_unit_name_and_channel(tmp_path):
    proc, body = _run(tmp_path, "all fine", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert body["channel"] == "#risk"
    assert "fund-daily.service" in body["text"]


def test_redacts_every_known_secret_prefix(tmp_path):
    leaky = (
        "Traceback: ANTHROPIC_API_KEY=sk-ant-api03-DEADBEEFdeadbeef\n"
        "SLACK_BOT_TOKEN=xoxb-9999-8888-abcdefgh\n"
        "SLACK_APP_TOKEN_EXEC=xapp-1-A099-77-cafebabe\n"
        "ALPACA_API_KEY=PKABCDEFGHIJKLMNOP01\n"
    )
    proc, body = _run(tmp_path, leaky, '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    for secret in ("sk-ant-api03-DEADBEEFdeadbeef", "xoxb-9999-8888-abcdefgh",
                   "xapp-1-A099-77-cafebabe", "PKABCDEFGHIJKLMNOP01"):
        assert secret not in text, f"leaked {secret}"
    assert "REDACTED" in text


def test_nonzero_exit_when_slack_says_not_ok(tmp_path):
    """Slack returns HTTP 200 with ok:false on auth errors — curl --fail cannot see it."""
    proc, _ = _run(tmp_path, "boom", '{"ok":false,"error":"invalid_auth"}')
    assert proc.returncode != 0
    assert "invalid_auth" in (proc.stderr + proc.stdout)


def test_payload_is_valid_json_despite_quotes_in_journal(tmp_path):
    proc, body = _run(tmp_path, 'he said "hi" and \\ backslashed', '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert 'he said "hi"' in body["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_ops_notify.py -v`
Expected: FAIL — all four error because `ops/notify_failure.sh` does not exist.

- [ ] **Step 3: Write the script**

Create `ops/notify_failure.sh`:

```sh
#!/bin/sh
# Post a failed unit's status to Slack. Invoked by fund-alert@.service via
# OnFailure=. Deliberately dependency-free of the fund itself: no DB
# connection, no python, no fund imports — the alert path must not share a
# failure mode with the thing it is watching.
#
# Reads SLACK_BOT_TOKEN and FUND_ALERT_CHANNEL from /etc/fund/alert-env, NOT
# from /etc/fund/env. A missing or unreadable job env file is the most likely
# fresh-host failure; if the alert read the same file it would die identically.
set -eu

UNIT="${1:?usage: notify_failure.sh <unit-name>}"
: "${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN not set (is /etc/fund/alert-env loaded?)}"
: "${FUND_ALERT_CHANNEL:?FUND_ALERT_CHANNEL not set}"

CURL="${FUND_ALERT_CURL:-curl}"
JOURNALCTL="${FUND_ALERT_JOURNALCTL:-journalctl}"

# Redact anything shaped like a credential before it leaves the box. A
# traceback that dumps os.environ must not publish broker keys to Slack.
redact() {
    sed -E \
        -e 's/sk-ant-[A-Za-z0-9_-]+/sk-ant-REDACTED/g' \
        -e 's/xoxb-[A-Za-z0-9-]+/xoxb-REDACTED/g' \
        -e 's/xapp-[A-Za-z0-9-]+/xapp-REDACTED/g' \
        -e 's/PK[A-Z0-9]{16,}/PK-REDACTED/g'
}

STATUS="$(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)"
CODE="$(systemctl show -p ExecMainStatus --value "$UNIT" 2>/dev/null || echo '?')"
TAIL="$("$JOURNALCTL" -u "$UNIT" -n 20 --no-pager -o cat 2>/dev/null | redact || echo '(journal unavailable)')"

TEXT="$(printf ':rotating_light: *%s* failed\nresult=%s exit=%s\n```\n%s\n```' \
        "$UNIT" "$STATUS" "$CODE" "$TAIL")"

# jq builds the payload so quotes, backslashes and newlines in the journal
# tail cannot produce malformed JSON.
PAYLOAD="$(jq -n --arg channel "$FUND_ALERT_CHANNEL" --arg text "$TEXT" \
           '{channel: $channel, text: $text}')"

RESPONSE="$("$CURL" --silent --show-error --max-time 20 \
    -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H 'Content-Type: application/json; charset=utf-8' \
    -d "$PAYLOAD")"

# chat.postMessage returns HTTP 200 with {"ok":false} on auth/scope errors, so
# curl --fail sees success. Check the body or the alert is silently lost.
if [ "$(printf %s "$RESPONSE" | jq -r '.ok')" != "true" ]; then
    echo "notify_failure: slack rejected the post: $(printf %s "$RESPONSE" | jq -r '.error // .')" >&2
    exit 1
fi
```

- [ ] **Step 4: Make it executable and run the tests**

```bash
chmod +x ops/notify_failure.sh
.venv/bin/python3 -m pytest tests/test_ops_notify.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: `682 passed, 6 deselected` (678 baseline + 4 new).

- [ ] **Step 6: Commit**

```bash
git add ops/notify_failure.sh tests/test_ops_notify.py
git commit -m "feat: failure alerts reach Slack without leaking keys or lying about success

Slack's chat.postMessage answers HTTP 200 with ok:false on an auth error, so
curl --fail reports success and the alert is silently lost. The script parses
the body instead. The journal tail is redacted for sk-ant-, xoxb-, xapp- and
PK prefixes first, because a traceback that dumps the environment would
otherwise publish broker keys."
```

---

## Task 2: `ops/backup.sh` — atomic, verified snapshots

**Files:**
- Create: `ops/backup.sh`
- Test: `tests/test_ops_backup.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ops/backup.sh`, invoked by `fund-backup.service` (Task 4). Reads `FUND_DB` and `FUND_BACKUP_DIR` from the environment; optional `FUND_JOURNALS`. Writes `${FUND_BACKUP_DIR}/fund-YYYY-MM-DD.sqlite` and `journals-YYYY-MM-DD.tar.gz`. Never deletes anything.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ops_backup.py`:

```python
"""ops/backup.sh — a snapshot appears only once it is proven restorable."""
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "backup.sh"


def _seed_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, note TEXT)")
    conn.executemany("INSERT INTO signals (note) VALUES (?)",
                     [(f"n{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def _run(db: Path, backups: Path, journals: Path | None = None):
    env = {**os.environ, "FUND_DB": str(db), "FUND_BACKUP_DIR": str(backups)}
    if journals is not None:
        env["FUND_JOURNALS"] = str(journals)
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)


def test_snapshot_is_valid_and_row_counts_match(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db, rows=5)
    proc = _run(db, backups)
    assert proc.returncode == 0, proc.stderr

    snaps = list(backups.glob("fund-*.sqlite"))
    assert len(snaps) == 1, snaps
    conn = sqlite3.connect(snaps[0])
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 5
    conn.close()


def test_leaves_no_tmp_file_behind(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db)
    assert _run(db, backups).returncode == 0
    assert list(backups.glob("*.tmp")) == []


def test_a_corrupt_source_produces_no_snapshot(tmp_path):
    """The dated file must never appear unless it passed integrity_check."""
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    db.write_bytes(b"this is not a database")
    proc = _run(db, backups)
    assert proc.returncode != 0
    assert list(backups.glob("fund-*.sqlite")) == []


def test_never_deletes_existing_snapshots(tmp_path):
    """No prune step: 86 KB a day does not justify the only destructive op."""
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    _seed_db(db)
    backups.mkdir()
    ancient = backups / "fund-1999-01-01.sqlite"
    ancient.write_text("keep me")
    assert _run(db, backups).returncode == 0
    assert ancient.exists(), "backup.sh must never delete anything"


def test_tars_the_journals_when_present(tmp_path):
    db, backups = tmp_path / "fund.sqlite", tmp_path / "backups"
    journals = tmp_path / "journals"
    journals.mkdir()
    (journals / "pm.md").write_text("# pm journal")
    _seed_db(db)
    assert _run(db, backups, journals).returncode == 0
    assert len(list(backups.glob("journals-*.tar.gz"))) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_ops_backup.py -v`
Expected: FAIL — 5 errors, `ops/backup.sh` does not exist.

- [ ] **Step 3: Write the script**

Create `ops/backup.sh`:

```sh
#!/bin/sh
# Nightly snapshot of the fund. Invoked by fund-backup.service at 17:30 ET.
#
# `sqlite3 .backup` uses the SQLite backup API, which is WAL-safe with a live
# writer; a plain `cp` of a WAL-mode database is not.
#
# NO PRUNE. At ~86 KB a snapshot, a full year costs ~31 MB. A retention policy
# would buy nothing measurable while introducing the only destructive
# operation in the whole deployment. Revisit only if the DB grows by orders of
# magnitude.
#
# `date` here is fine: this is ops, outside the injected-Clock rule that governs
# business logic (CLAUDE.md conventions / scripts/check_purity.py scope).
set -eu

: "${FUND_DB:?FUND_DB not set}"
: "${FUND_BACKUP_DIR:?FUND_BACKUP_DIR not set}"

STAMP="$(date +%Y-%m-%d)"
mkdir -p "$FUND_BACKUP_DIR"

TMP="${FUND_BACKUP_DIR}/fund-${STAMP}.sqlite.tmp"
FINAL="${FUND_BACKUP_DIR}/fund-${STAMP}.sqlite"

rm -f "$TMP"
sqlite3 "$FUND_DB" ".backup '${TMP}'"

# Verify BEFORE the rename, so an interrupted or corrupt backup can never
# leave a partial file that looks like a valid snapshot.
CHECK="$(sqlite3 "$TMP" 'PRAGMA integrity_check' 2>/dev/null || echo failed)"
if [ "$CHECK" != "ok" ]; then
    rm -f "$TMP"
    echo "backup: integrity_check failed for ${FUND_DB} (got: ${CHECK})" >&2
    exit 1
fi

mv "$TMP" "$FINAL"
echo "backup: wrote ${FINAL}"

if [ -n "${FUND_JOURNALS:-}" ] && [ -d "$FUND_JOURNALS" ]; then
    JTMP="${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz.tmp"
    tar -czf "$JTMP" -C "$(dirname "$FUND_JOURNALS")" "$(basename "$FUND_JOURNALS")"
    mv "$JTMP" "${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz"
    echo "backup: wrote ${FUND_BACKUP_DIR}/journals-${STAMP}.tar.gz"
fi
```

- [ ] **Step 4: Make it executable and run the tests**

```bash
chmod +x ops/backup.sh
.venv/bin/python3 -m pytest tests/test_ops_backup.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: `687 passed, 6 deselected`.

- [ ] **Step 6: Commit**

```bash
git add ops/backup.sh tests/test_ops_backup.py
git commit -m "feat: nightly snapshot that is verified before it is trusted

sqlite3 .backup is WAL-safe where cp is not. The snapshot is written to a temp
name and integrity-checked before the rename, so an interrupted backup cannot
leave a partial file that looks valid — the restore test samples one snapshot
and would not notice.

No prune step. At 86 KB a day a year costs 31 MB, which does not justify
giving this deployment a destructive operation."
```

---

## Task 3: Mac-side pull

**Files:**
- Create: `ops/pull-backups.sh`, `ops/com.fund.pull-backups.plist`

**Interfaces:**
- Consumes: `ops/backup.sh`'s output layout (`fund-*.sqlite`, `journals-*.tar.gz`).
- Produces: `ops/pull-backups.sh`, reading `FUND_DROPLET` (e.g. `fund@203.0.113.10`) and `FUND_LOCAL_BACKUPS`.

- [ ] **Step 1: Write the pull script**

Create `ops/pull-backups.sh`:

```sh
#!/bin/sh
# Mac-side: pull the droplet's snapshots down. Opportunistic — the launchd
# agent fires daily and this simply fails fast when the Mac is asleep or off
# the network, which is not an error worth alerting on.
#
# Pull, not push: the droplet then needs no credential for any other system.
# It holds keys only for the APIs it must reach.
set -eu

: "${FUND_DROPLET:?FUND_DROPLET not set (e.g. fund@203.0.113.10)}"
LOCAL="${FUND_LOCAL_BACKUPS:?FUND_LOCAL_BACKUPS not set}"

mkdir -p "$LOCAL"
rsync -az --ignore-existing \
      -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
      "${FUND_DROPLET}:/var/lib/fund/backups/" "$LOCAL/"
echo "pull-backups: $(ls -1 "$LOCAL" | wc -l | tr -d ' ') files in $LOCAL"
```

- [ ] **Step 2: Verify it fails fast with no config**

```bash
chmod +x ops/pull-backups.sh
env -u FUND_DROPLET -u FUND_LOCAL_BACKUPS ops/pull-backups.sh; echo "exit=$?"
```

Expected: `FUND_DROPLET not set` on stderr, `exit=1`. Fail fast, descriptive.

- [ ] **Step 3: Write the launchd agent**

Create `ops/com.fund.pull-backups.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!--
  Mac-side backup pull. Fires daily at 19:00 local and does nothing when the
  Mac is asleep or off the network. Replace /ABSOLUTE/PATH/TO/fund and the
  FUND_DROPLET host before loading.

  This is the ONLY fund launchd job that should exist on this Mac after the
  VM cutover. If com.fund.daily ever reappears in ~/Library/LaunchAgents,
  two hosts hold a live schedule and duplicate orders follow.
-->
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.fund.pull-backups</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-lc</string>
    <string>FUND_DROPLET=fund@DROPLET_IP FUND_LOCAL_BACKUPS=/ABSOLUTE/PATH/TO/fund/backups-from-vm exec /ABSOLUTE/PATH/TO/fund/ops/pull-backups.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/tmp/fund-pull-backups.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/fund-pull-backups.err.log</string>
</dict>
</plist>
```

- [ ] **Step 4: Confirm the suite is unaffected**

Run: `make test`
Expected: `687 passed, 6 deselected`.

- [ ] **Step 5: Commit**

```bash
git add ops/pull-backups.sh ops/com.fund.pull-backups.plist
git commit -m "feat: Mac-side backup pull, so the droplet holds no foreign credential

Pull rather than push: the droplet keeps keys only for the APIs it must reach,
and the SSH key the Mac already needs to administer it does the rest. The
agent fires daily and does nothing while the Mac is asleep."
```

---

## Task 4: The seven systemd units

**Files:**
- Create: `ops/fund-daily.timer`, `ops/fund-daily.service`, `ops/fund-pnl.timer`, `ops/fund-pnl.service`, `ops/fund-backup.timer`, `ops/fund-backup.service`, `ops/fund-alert@.service`

**Interfaces:**
- Consumes: `ops/notify_failure.sh` (Task 1), `ops/backup.sh` (Task 2).
- Produces: unit files installed to `/etc/systemd/system/` in Task 8. Validated with `systemd-analyze verify` there — **it cannot run on macOS.**

The three service files repeat `Type`/`User`/`EnvironmentFile`/`OnFailure` **deliberately**. A `fund@.service` template parameterized by `%i` would be DRYer, but these are read under pressure on a failed morning and the jobs genuinely differ in timeout, schedule and failure meaning.

- [ ] **Step 1: Write the daily pair**

`ops/fund-daily.timer`:

```ini
[Unit]
Description=fund — one trading day (09:35 ET, Mon-Fri)
Documentation=file:///opt/fund/ops/README.md

[Timer]
# Timezone pinned in the expression AS WELL AS on the host, so the schedule
# survives someone changing the box's timezone later.
# DST is a non-issue here: only 02:00-03:00 local events get skipped or
# doubled, and 09:35 exists on every day of the year.
OnCalendar=Mon..Fri *-*-* 09:35:00 America/New_York
# Without this systemd places the fire at a randomized point in a 1-minute
# window. 09:35 should mean 09:35.
AccuracySec=1s
# == the plists' RunAtLoad=false. A day starts because the market opened,
# never because the host did. A missed fire is skipped, not caught up.
Persistent=false
Unit=fund-daily.service

[Install]
WantedBy=timers.target
```

`ops/fund-daily.service`:

```ini
[Unit]
Description=fund — run_day.py (full trading day, self-auditing)
Documentation=file:///opt/fund/ops/README.md
# run_day.py's pre-Slack window (paper_guard, require_env, acquire_lock,
# market_is_open, RealSlack construction) posts nothing to Slack on failure.
# Its docstring calls that acceptable because the exit is "a visible failure,
# just not a Slack one" — true at a Mac someone sits in front of, false on a
# droplet. OnFailure restores that premise. It fires on start failures
# (203/EXEC, unreadable EnvironmentFile) and timeout kills too.
OnFailure=fund-alert@%n.service

[Service]
Type=oneshot
User=fund
Group=fund
WorkingDirectory=/opt/fund
EnvironmentFile=/etc/fund/env
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/run_day.py
# Bound a hung LLM call. On kill the kernel releases the flock and checkpoint
# CAS makes the next run resume rather than repeat.
TimeoutStartSec=30min
# NO Restart=. Invariant 4: the default is HOLD. A failed day waits for a
# human; it does not retry itself.
```

- [ ] **Step 2: Write the P&L pair**

`ops/fund-pnl.timer`:

```ini
[Unit]
Description=fund — EOD P&L digest (16:35 ET, Mon-Fri)
Documentation=file:///opt/fund/ops/README.md

[Timer]
# 16:35, NOT 16:15. close_frame shifts its end back SIP_DELAY (16 min,
# measured — free-plan SIP blackout), so a 16:15 fire asks for 15:59, before
# the closing auction writes the bar, and correctly posts nothing.
OnCalendar=Mon..Fri *-*-* 16:35:00 America/New_York
AccuracySec=1s
Persistent=false
Unit=fund-pnl.service

[Install]
WantedBy=timers.target
```

`ops/fund-pnl.service`:

```ini
[Unit]
Description=fund — close_pnl.py (P&L $ and %% vs SPY)
Documentation=file:///opt/fund/ops/README.md
OnFailure=fund-alert@%n.service

[Service]
Type=oneshot
User=fund
Group=fund
WorkingDirectory=/opt/fund
EnvironmentFile=/etc/fund/env
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/close_pnl.py
TimeoutStartSec=10min
```

Note: `%` is escaped as `%%` in the Description — systemd treats `%` as a specifier prefix.

- [ ] **Step 3: Write the backup pair**

`ops/fund-backup.timer`:

```ini
[Unit]
Description=fund — nightly DB + journals snapshot (17:30 ET)
Documentation=file:///opt/fund/ops/README.md

[Timer]
# Daily including weekends: cheap, idempotent, and a weekend snapshot costs
# 86 KB.
OnCalendar=*-*-* 17:30:00 America/New_York
AccuracySec=1s
Persistent=false
Unit=fund-backup.service

[Install]
WantedBy=timers.target
```

`ops/fund-backup.service`:

```ini
[Unit]
Description=fund — atomic, integrity-checked snapshot
Documentation=file:///opt/fund/ops/README.md
# A silently failing backup is a classic trap.
OnFailure=fund-alert@%n.service

[Service]
Type=oneshot
User=fund
Group=fund
WorkingDirectory=/opt/fund
EnvironmentFile=/etc/fund/env
Environment=FUND_BACKUP_DIR=/var/lib/fund/backups
ExecStart=/opt/fund/ops/backup.sh
TimeoutStartSec=10min
```

- [ ] **Step 4: Write the alert template**

`ops/fund-alert@.service`:

```ini
[Unit]
Description=fund — Slack alert for failed unit %i
Documentation=file:///opt/fund/ops/README.md

[Service]
Type=oneshot
User=fund
Group=fund
# NOT /etc/fund/env. The alert must not share a failure mode with the job it
# watches, and a missing or unreadable job env file is the single most likely
# fresh-host failure — it would break both identically.
EnvironmentFile=/etc/fund/alert-env
ExecStart=/opt/fund/ops/notify_failure.sh %i
TimeoutStartSec=2min
```

- [ ] **Step 5: Sanity-check the files locally**

`systemd-analyze` does not exist on macOS, so only structural checks are possible here:

```bash
for f in ops/fund-*.timer ops/fund-*.service ops/fund-alert@.service; do
  printf "%-32s " "$f"
  grep -q '^\[Unit\]' "$f" && grep -qE '^\[(Timer|Service)\]' "$f" \
    && echo OK || echo "MALFORMED"
done
grep -L 'OnFailure=' ops/fund-daily.service ops/fund-pnl.service ops/fund-backup.service
```

Expected: seven `OK` lines, and the `grep -L` prints nothing (every job service has an `OnFailure`).

- [ ] **Step 6: Commit**

```bash
git add ops/fund-daily.timer ops/fund-daily.service ops/fund-pnl.timer \
        ops/fund-pnl.service ops/fund-backup.timer ops/fund-backup.service \
        ops/fund-alert@.service
git commit -m "feat: systemd units replacing the launchd plists

Timezone pinned in the OnCalendar expression as well as on the host, so the
schedule survives a host timezone change. AccuracySec=1s because systemd
otherwise randomizes the fire inside a one-minute window. Persistent=false
reproduces RunAtLoad=false: a day starts because the market opened.

No Restart= anywhere — invariant 4's default is HOLD. The alert template
reads its own credential file, so the most likely fresh-host failure cannot
break the job and its alert identically."
```

---

## Task 5: `ops/README.md` — the runbook

**Files:**
- Create: `ops/README.md`

**Interfaces:**
- Consumes: every file from Tasks 1–4.
- Produces: the procedure Tasks 7–12 execute. Referenced by every unit's `Documentation=`.

- [ ] **Step 1: Write the runbook**

Create `ops/README.md` covering, in this order:

1. **Layout table** — the exact paths table from this plan's header.
2. **Provisioning**, as numbered shell blocks (the commands from Tasks 7–9).
3. **Cutover**, with the ordering rule stated first: *validation runs against a snapshot while the Mac stays authoritative; the Mac is silenced immediately before the final transfer, never after.*
4. **The two Mac barriers**, with this warning verbatim:

   > `launchctl unload` is **session-scoped**. `~/Library/LaunchAgents` is
   > launchd's per-user auto-load directory — it reloads its contents at every
   > login. Unloading alone means the fund resurrects on the Mac at the next
   > reboot or login, while the VM is live. Two hosts means two ticket-id
   > namespaces, so `client_order_id` idempotency will **not** dedupe the
   > duplicate orders. Move the plist out; do not merely unload it.

5. **Rollback** — day-boundary only, mirroring cutover in reverse: disable VM timers → verify with `systemctl list-timers` → transfer DB and journals back → restore plist and `.env` on the Mac → reload. With: *if a VM day fails mid-flight, fix forward or accept a missed day. Never roll back mid-day — the order exists at Alpaca and rolling back desyncs the broker from the source of truth.*
6. **Daily operations** — `journalctl -u fund-daily -n 100`, `systemctl list-timers`, `systemctl --failed`, manual `systemctl start fund-pnl.service`.
7. **What is deliberately not scheduled** — `make eval` ($0.81, ~7 min per run).

- [ ] **Step 2: Verify every referenced file exists**

```bash
grep -oE 'ops/[a-zA-Z0-9@._-]+' ops/README.md | sort -u | while read -r f; do
  test -e "$f" && echo "OK   $f" || echo "MISSING $f"
done
```

Expected: every line `OK`.

- [ ] **Step 3: Commit**

```bash
git add ops/README.md
git commit -m "docs: provisioning, cutover and rollback runbook

States the ordering rule the cutover turns on — the Mac is silenced
immediately before the final state transfer, never after validation — and why
unloading the plist is not enough to silence it."
```

---

## Task 6: Documentation corrections

**Files:**
- Modify: `CLAUDE.md` (the `docker compose up` line), `PROGRESS.md`, `HANDOFF-LIVE.md:453-455`

- [ ] **Step 1: Fix the stale docker claim in `CLAUDE.md`**

Find the line under **Commands** reading:

```
- `docker compose up` — one service per seat + orchestrator.
```

Replace with:

```
- `systemctl start fund-daily.service` — one trading day on the VM host. See `ops/README.md`.
```

There is no Dockerfile and no compose file in this repo; that line described files that do not exist and an architecture (long-lived per-seat containers) that is not what runs.

- [ ] **Step 2: Update `PROGRESS.md`**

Three corrections:
- **Tests:** `574 offline, green` → `678 offline, green` (the eval rig added tests; verify with `make test` and use the real number)
- **Scheduled on:** `this Mac (Pacific) — com.fund.daily loaded` → the droplet, with the three timers
- **Open items:** close "Push 2 unpushed commits", close "Install ops/com.fund.pnl.plist", close "Move the run to a VM", drop the `pmset` item (no longer applicable), and add the two follow-ups from spec §9 — the `FUND_HOST_ID` guard and the MCP version pin.

- [ ] **Step 3: Update `HANDOFF-LIVE.md`**

Replace the launchctl block at lines 453–455:

```bash
cp ops/com.fund.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fund.daily.plist
launchctl list | grep com.fund.daily
```

with:

```bash
sudo cp ops/fund-*.timer ops/fund-*.service ops/fund-alert@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fund-daily.timer fund-pnl.timer fund-backup.timer
systemctl list-timers 'fund-*'
```

- [ ] **Step 4: Verify no stale docker reference survives**

```bash
grep -rn "docker compose\|Dockerfile" CLAUDE.md README.md PROGRESS.md HANDOFF-LIVE.md || echo "clean"
```

Expected: `clean`.

- [ ] **Step 5: Run the suite and commit**

```bash
make test
git add CLAUDE.md PROGRESS.md HANDOFF-LIVE.md
git commit -m "docs: the repo stops describing a container story it does not have

CLAUDE.md advertised 'docker compose up — one service per seat + orchestrator'.
There is no Dockerfile and no compose file, and that sentence described a
different architecture from the sequential daily script that actually runs.

Also corrects the test count (574 -> 678, the eval rig added tests) and moves
the schedule from launchd on this Mac to systemd on the droplet."
```

- [ ] **Step 6: Push**

```bash
git push origin master
```

---

## Part B — provisioning (requires the droplet)

> **Human gate.** `doctl` is not installed and the droplet does not exist.
> Benjamin creates it in the DigitalOcean console and supplies SSH access
> before Task 7 can start. Secrets move out of band — never printed, committed,
> or pasted into a transcript.

## Task 7: Create the droplet and base OS config

- [ ] **Step 1 (Benjamin):** Create a droplet — Debian 13, **Basic / Regular, 1 vCPU / 2 GB / 25 GB**, region **NYC1 or NYC3**, SSH key auth. Note the IP.

- [ ] **Step 2: Verify access and identity**

```bash
ssh root@DROPLET_IP 'cat /etc/debian_version; uname -m; nproc; free -m | head -2'
```

Expected: Debian 13.x, 1 CPU, ~2 GB RAM.

- [ ] **Step 3: Timezone, swap, user**

```bash
ssh root@DROPLET_IP 'set -eux
timedatectl set-timezone America/New_York
date

fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo "/swapfile none swap sw 0 0" >> /etc/fstab
free -m | tail -2

adduser --system --group --home /home/fund --shell /bin/bash fund
usermod -aG systemd-journal fund   # so notify_failure.sh can read the journal
id fund'
```

Expected: `date` prints EDT; swap shows ~2 GB; `id fund` lists `systemd-journal`.

- [ ] **Step 4: Persistent journald**

```bash
ssh root@DROPLET_IP 'set -eux
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
printf "[Journal]\nStorage=persistent\nSystemMaxUse=200M\n" > /etc/systemd/journald.conf.d/fund.conf 2>/dev/null \
  || { mkdir -p /etc/systemd/journald.conf.d && printf "[Journal]\nStorage=persistent\nSystemMaxUse=200M\n" > /etc/systemd/journald.conf.d/fund.conf; }
systemctl restart systemd-journald
journalctl --disk-usage'
```

Expected: `journalctl --disk-usage` reports archived journals **on disk**. Debian's default is `Storage=auto`, which is *volatile* without `/var/log/journal` — logs would vanish on every reboot, and they are now the only forensic trail.

- [ ] **Step 5: Packages**

```bash
ssh root@DROPLET_IP 'set -eux
apt-get update
apt-get install -y git curl sqlite3 jq rsync
sqlite3 --version; jq --version; git --version'
```

- [ ] **Step 6: Confirm unattended-upgrades will not reboot mid-day**

```bash
ssh root@DROPLET_IP 'grep -rn "Automatic-Reboot" /etc/apt/apt.conf.d/ || echo "not configured (default false)"'
```

Expected: either absent or `"false"`. Debian's default is off, but DO images can differ — a box that reboots itself at 09:37 is a silent missed day.

---

## Task 8: Runtime, checkout, green suite

- [ ] **Step 1: Install uv and Python 3.14 as the `fund` user**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'set -eux
curl -LsSf https://astral.sh/uv/install.sh | sh
. \$HOME/.local/bin/env 2>/dev/null || export PATH=\$HOME/.local/bin:\$PATH
uv --version
uv python install 3.14
uv python list
command -v python3.14 || ls \$HOME/.local/bin'"
```

Expected: `uv` reports a version; `uv python list` shows a 3.14 build.

**If `python3.14` is not on PATH**, the Makefile's `BOOT_PY` probe
(`command -v python3.14 || python3.13 || python3.12 || python3`) will fall
through to Debian's 3.13. Symlink the managed interpreter into
`~/.local/bin/python3.14` so the venv matches the Mac's 3.14.5.

- [ ] **Step 2: Clone and build the venv**

`make deps` must create the venv — **not** `uv venv`. `scripts/sync_deps.py`
shells out to `pip`, which stdlib `venv` bundles and `uv venv` does not.

```bash
ssh root@DROPLET_IP 'set -eux
mkdir -p /opt/fund && chown fund:fund /opt/fund'
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'set -eux
export PATH=\$HOME/.local/bin:\$PATH
git clone https://github.com/benjaminematton/fund.git /opt/fund
cd /opt/fund && make deps && .venv/bin/python3 --version'"
```

Expected: `Python 3.14.x`.

- [ ] **Step 3: Green suite — acceptance gate 1**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'cd /opt/fund && make test'"
```

Expected: **`687 passed, 6 deselected`** (678 baseline + 9 added in Tasks 1–2), purity lint clean. No network and no keys are needed for this.

- [ ] **Step 4: Validate the unit files — the check macOS could not run**

```bash
ssh root@DROPLET_IP 'set -eux
cp /opt/fund/ops/fund-*.timer /opt/fund/ops/fund-*.service /opt/fund/ops/fund-alert@.service /etc/systemd/system/
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/fund-daily.timer /etc/systemd/system/fund-daily.service \
                       /etc/systemd/system/fund-pnl.timer /etc/systemd/system/fund-pnl.service \
                       /etc/systemd/system/fund-backup.timer /etc/systemd/system/fund-backup.service \
                       "/etc/systemd/system/fund-alert@.service"
systemd-analyze calendar "Mon..Fri *-*-* 09:35:00 America/New_York"
systemd-analyze calendar "Mon..Fri *-*-* 16:35:00 America/New_York"'
```

Expected: `systemd-analyze verify` prints nothing (success). Each `calendar`
call prints a **Next elapse** in EDT. Timers are installed but **not enabled** —
do not enable them until Task 11.

---

## Task 9: Secrets, state layout, cache pre-warm

- [ ] **Step 1: Create the state and secret directories**

```bash
ssh root@DROPLET_IP 'set -eux
mkdir -p /var/lib/fund/journals /var/lib/fund/backups /etc/fund
chown -R fund:fund /var/lib/fund
chmod 750 /var/lib/fund
chmod 700 /etc/fund'
```

- [ ] **Step 2 (Benjamin, out of band): install `/etc/fund/env`**

Copy the Mac's `.env` content, **with `FUND_DB` rewritten**:

```
FUND_DB=/var/lib/fund/fund.sqlite
FUND_JOURNALS=/var/lib/fund/journals
```

`FUND_DB` is currently a *relative* path, working on the Mac only because
launchd sets `WorkingDirectory`. Under systemd it would resolve against
`WorkingDirectory=/opt/fund` and put the fund inside the checkout, where a
re-clone or `git clean -x` could destroy it.

Then:

```bash
ssh root@DROPLET_IP 'chown fund:fund /etc/fund/env && chmod 600 /etc/fund/env && wc -l /etc/fund/env'
```

- [ ] **Step 3 (Benjamin, out of band): install `/etc/fund/alert-env`**

Two lines only:

```
SLACK_BOT_TOKEN=<the same xoxb- bot token>
FUND_ALERT_CHANNEL=#risk
```

```bash
ssh root@DROPLET_IP 'chown fund:fund /etc/fund/alert-env && chmod 600 /etc/fund/alert-env && wc -l /etc/fund/alert-env'
```

- [ ] **Step 4: Verify systemd parses both env files**

```bash
ssh root@DROPLET_IP 'systemd-analyze verify /etc/systemd/system/fund-daily.service && echo "env parses"'
ssh root@DROPLET_IP 'grep -c "\\\\$" /etc/fund/env || true'
```

Expected: `env parses`; the `$` count is `0`. systemd does **not** expand
`${VAR}` in an `EnvironmentFile`, and it continues comments across a trailing
backslash where a shell would not.

- [ ] **Step 5: Pre-warm the uv tool cache — acceptance gate 4**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'set -eux
export PATH=\$HOME/.local/bin:\$PATH
set -a; . /etc/fund/env; set +a
timeout 120 uvx alpaca-mcp-server --help || true
uv cache dir && du -sh \$(uv cache dir)'"
```

Expected: a populated cache. Cold, this download happens **inside** the 09:35
critical path, and a slow or unreachable PyPI means the seats get no broker
tools at all.

- [ ] **Step 6: Try to pin the MCP server version (spec §8, P1)**

`agents/seats.py:49` hardcodes `uvx alpaca-mcp-server` with **no version**, so
it resolves *latest* at run time — an upstream release could move a tool-schema
field name unattended between one day and the next, which is exactly the
2026-08-17 outage class.

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'set -eux
export PATH=\$HOME/.local/bin:\$PATH
uvx alpaca-mcp-server --version 2>/dev/null || uv tool list
uv tool install alpaca-mcp-server
uv tool list'"
```

**Record the resolved version.** Then determine whether a bare
`uvx alpaca-mcp-server` uses the installed (pinned) environment or re-resolves
latest. **This is unverified.** If it re-resolves, do **not** patch
`agents/seats.py` tonight — file it as the follow-up in spec §9. Changing code
and host on the same night is what this design argues against everywhere else.

---

## Task 10: Phase-2 validation (Mac still authoritative)

Nothing in this task writes to the live fund. `run_day.py` exits before any
write when the market is closed; every other check is read-only.

- [ ] **Step 1: Provisional state copy for validation**

```bash
cd /Users/benjaminmatton/Developer/fund
rm -f state/fund-2026-08-17-rerun.sqlite-shm state/fund-2026-08-17-rerun.sqlite-wal
sqlite3 state/fund.sqlite ".backup '/tmp/fund-snapshot.sqlite'"
sqlite3 /tmp/fund-snapshot.sqlite 'PRAGMA integrity_check'
scp /tmp/fund-snapshot.sqlite root@DROPLET_IP:/var/lib/fund/fund.sqlite
rsync -az journals/ root@DROPLET_IP:/var/lib/fund/journals/
ssh root@DROPLET_IP 'chown -R fund:fund /var/lib/fund'
```

The orphan `-shm`/`-wal` files belong to a database that was renamed to
`fund.sqlite`; both are 0 bytes, so nothing is lost by deleting them.

- [ ] **Step 2: Restore assertions — acceptance gate 7**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'set -eu
sqlite3 /var/lib/fund/fund.sqlite \"PRAGMA integrity_check\"
cd /opt/fund && .venv/bin/python3 scripts/audit_day.py /var/lib/fund/fund.sqlite 2026-08-17
for t in signals decisions tickets orders; do
  printf \"%s=%s \" \"\$t\" \"\$(sqlite3 /var/lib/fund/fund.sqlite \"SELECT count(*) FROM \$t\")\"
done; echo'"
```

Compare the four counts against the Mac:

```bash
cd /Users/benjaminmatton/Developer/fund
for t in signals decisions tickets orders; do
  printf "%s=%s " "$t" "$(sqlite3 state/fund.sqlite "SELECT count(*) FROM $t")"
done; echo
```

Expected: `integrity_check` = `ok`; audit reports 2026-08-17 **clean**; all four
counts identical. Row counts are the assertion that catches a truncated copy —
a partial file opens fine.

- [ ] **Step 3: Live schema pin — acceptance gate 2**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'cd /opt/fund
export PATH=\$HOME/.local/bin:\$PATH
set -a; . /etc/fund/env; set +a
make schema-pin'"
```

Expected: PASS. Do not skip this. On 2026-08-17 the entire offline suite was
green over a total outage because the fixtures and the gate encoded the same
wrong stop-leg assumption; this is the only check that introspects the broker's
real schema.

- [ ] **Step 4: Market-closed dry run**

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'cd /opt/fund
export PATH=\$HOME/.local/bin:\$PATH
set -a; . /etc/fund/env; set +a
.venv/bin/python3 scripts/run_day.py; echo \"exit=\$?\"'"
```

Expected: `market is closed — no stages run, nothing traded (exit 0)`, `exit=0`.
This exercises env loading, egress to Alpaca and the venv while writing nothing.

- [ ] **Step 5: Timer→service rehearsal**

Gate 6 proves systemd *parsed* the calendar; a manual `systemctl start` proves
the *service* runs. Neither tests the join — timer triggers unit, as
`User=fund`, with `EnvironmentFile` loaded and output captured — which is the
entire production path and would otherwise first execute unattended at 09:35.

```bash
ssh root@DROPLET_IP 'set -eux
cat > /etc/systemd/system/fund-rehearsal.timer <<EOF
[Timer]
OnActiveSec=90s
AccuracySec=1s
Unit=fund-daily.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload && systemctl start fund-rehearsal.timer
sleep 120
journalctl -u fund-daily.service -n 20 --no-pager
systemctl stop fund-rehearsal.timer && rm /etc/systemd/system/fund-rehearsal.timer
systemctl daemon-reload'
```

Expected: the journal shows `run_day: market is closed`, proving the whole
chain fired unattended. **Safe** — the market is closed, so `run_day.py` exits
before writing.

- [ ] **Step 6: All four alert triggers — acceptance gate 5**

```bash
# (a) start failure: unreadable job env
ssh root@DROPLET_IP 'mv /etc/fund/env /etc/fund/env.bak
systemctl start fund-daily.service || true
sleep 5; systemctl status fund-alert@fund-daily.service --no-pager | tail -5
mv /etc/fund/env.bak /etc/fund/env'

# (b) non-zero exit — the most common real failure
ssh root@DROPLET_IP 'systemd-run --unit=fund-faketest --property=OnFailure=fund-alert@%n.service /bin/false
sleep 5; journalctl -u "fund-alert@fund-faketest.service" -n 10 --no-pager'

# (c) timeout kill
ssh root@DROPLET_IP 'systemd-run --unit=fund-faketimeout --property=OnFailure=fund-alert@%n.service \
    --property=TimeoutStartSec=5s --property=Type=oneshot /bin/sleep 60
sleep 15; journalctl -u "fund-alert@fund-faketimeout.service" -n 10 --no-pager'

# (d) the alert's OWN env broken — observe the known limitation
ssh root@DROPLET_IP 'mv /etc/fund/alert-env /etc/fund/alert-env.bak
systemd-run --unit=fund-fakenoalert --property=OnFailure=fund-alert@%n.service /bin/false
sleep 5; systemctl --failed --no-pager
mv /etc/fund/alert-env.bak /etc/fund/alert-env'
```

Expected: (a), (b), (c) each deliver a Slack message to `#risk` naming the
failed unit. (d) delivers **nothing**, and the alert unit itself appears in
`systemctl --failed` — this is the accepted risk in spec §8, now *observed*
rather than assumed.

- [ ] **Step 7: Prove `close_pnl.py` manually — before it is ever scheduled**

`com.fund.pnl` was never installed on the Mac, so this has **never run in
production**. It is read-only plus a Slack post. It is after 16:16 ET now, so
today's bar exists.

```bash
ssh root@DROPLET_IP "su - fund -s /bin/bash -c 'cd /opt/fund
export PATH=\$HOME/.local/bin:\$PATH
set -a; . /etc/fund/env; set +a
.venv/bin/python3 scripts/close_pnl.py; echo \"exit=\$?\"'"
```

Expected: `exit=0` and a P&L digest in `#pnl`. Its first execution should be
supervised, not scheduled.

---

## Part C — cutover

## Task 11: Silence the Mac, transfer, enable

**Do not begin until every check in Task 10 passed.** From here the ordering is
load-bearing.

- [ ] **Step 1: Silence the Mac — BOTH barriers, before any transfer**

```bash
launchctl unload ~/Library/LaunchAgents/com.fund.daily.plist
mkdir -p ~/fund-rollback
mv ~/Library/LaunchAgents/com.fund.daily.plist ~/fund-rollback/
cd /Users/benjaminmatton/Developer/fund && mv .env .env.MIGRATED-TO-VM

launchctl list | grep -i fund || echo "GATE 3 PASS: no fund job loaded"
ls ~/Library/LaunchAgents/ | grep -i fund || echo "GATE 4 PASS: no fund plist in auto-load dir"
test -f .env && echo "GATE 5 FAIL" || echo "GATE 5 PASS: .env renamed"
```

`launchctl unload` alone is **session-scoped**; `~/Library/LaunchAgents` is
launchd's per-user auto-load directory and reloads at every login. Moving the
plist out is what actually stops it. The renamed `.env` is the second barrier:
a stray `make live-day` now exits 1 on `require_env` instead of silently
placing duplicate orders.

- [ ] **Step 2: Final state transfer**

```bash
cd /Users/benjaminmatton/Developer/fund
sqlite3 state/fund.sqlite ".backup '/tmp/fund-final.sqlite'"
sqlite3 /tmp/fund-final.sqlite 'PRAGMA integrity_check'
scp /tmp/fund-final.sqlite root@DROPLET_IP:/var/lib/fund/fund.sqlite
rsync -az journals/ root@DROPLET_IP:/var/lib/fund/journals/
ssh root@DROPLET_IP 'chown -R fund:fund /var/lib/fund'
```

- [ ] **Step 3: Re-run the restore assertions against the final copy**

Repeat Task 10 Step 2. Expected: `ok`, audit clean, four row counts matching.

- [ ] **Step 4: Enable the timers — acceptance gate 6**

```bash
ssh root@DROPLET_IP 'set -eux
systemctl enable --now fund-daily.timer fund-pnl.timer fund-backup.timer
systemctl list-timers "fund-*" --no-pager'
```

Expected: three timers, next elapse **09:35 EDT tomorrow**, **16:35 EDT
tomorrow**, and **17:30 EDT today or tomorrow**.

- [ ] **Step 5: Prove the backup path end to end**

```bash
ssh root@DROPLET_IP 'systemctl start fund-backup.service && ls -la /var/lib/fund/backups/'
```

Expected: one `fund-YYYY-MM-DD.sqlite` and one `journals-YYYY-MM-DD.tar.gz`, no `.tmp`.

- [ ] **Step 6: Install the Mac-side pull**

```bash
cd /Users/benjaminmatton/Developer/fund
sed -e "s|/ABSOLUTE/PATH/TO/fund|$PWD|g" -e "s|DROPLET_IP|<the IP>|" \
    ops/com.fund.pull-backups.plist > ~/Library/LaunchAgents/com.fund.pull-backups.plist
launchctl load ~/Library/LaunchAgents/com.fund.pull-backups.plist
launchctl list | grep pull-backups
FUND_DROPLET=fund@<the IP> FUND_LOCAL_BACKUPS=$PWD/backups-from-vm ops/pull-backups.sh
```

Expected: the pull reports files copied. Add `backups-from-vm/` to `.gitignore`.

---

## Task 12: Watch, and close out

- [ ] **Step 1: Gate 6 re-verification after a real logout**

The single most important post-cutover check. Log out of macOS and back in (or
reboot), then:

```bash
launchctl list | grep -i fund
ls ~/Library/LaunchAgents/ | grep -i fund
```

Expected: only `com.fund.pull-backups`. If `com.fund.daily` reappears, **stop
and disable the VM timers immediately** — two hosts are live and duplicate
orders are one market open away.

- [ ] **Step 2: Watch the first VM day (tomorrow, 09:35 ET)**

```bash
ssh root@DROPLET_IP 'journalctl -u fund-daily.service --since "09:30" --no-pager'
ssh root@DROPLET_IP 'systemctl --failed --no-pager'
```

Expected: `AUDIT CLEAN 2026-08-18`, digest in Slack, nothing failed.

- [ ] **Step 3: Watch the first scheduled P&L (16:35 ET)**

```bash
ssh root@DROPLET_IP 'journalctl -u fund-pnl.service --since "16:30" --no-pager'
```

- [ ] **Step 4: Update `PROGRESS.md` with the outcome and commit**

Record the cutover date, the droplet as host, the three timers, and move the
two follow-ups from spec §9 into Open items.

```bash
make test
git add PROGRESS.md
git commit -m "docs: the fund runs on the VM"
git push origin master
```

- [ ] **Step 5: After one clean week — delete the plists**

```bash
git rm ops/com.fund.daily.plist ops/com.fund.pnl.plist
rm -rf ~/fund-rollback
git commit -m "chore: drop the launchd plists after a clean week on the VM"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: §3 layout → Tasks 7/9; §4
units and alerting → Tasks 1/4; §5 backups → Tasks 2/3; §6 cutover, both Mac
barriers, rollback, validation checks 1–7, acceptance gates 1–6 → Tasks 10/11/12
and the runbook in Task 5; §7 repo changes → Tasks 4/5/6; §8 risks → surfaced at
Task 9 Step 6 and Task 10 Step 6; §9 follow-ups → Task 6 Step 2.

**Known gaps, stated rather than hidden:**
- `systemd-analyze verify` cannot run on macOS, so Task 4's units are only
  structurally checked locally and truly validated in Task 8 Step 4.
- Whether `uv tool install` pins what a bare `uvx` resolves is **unverified**;
  Task 9 Step 6 determines it on the box and defers the code change either way.
- Task 12 Step 1 requires a real logout, which cannot be automated from here.
