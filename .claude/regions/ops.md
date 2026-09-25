---
paths:
  - ops/
  - tests/test_ops*.py
---
# ops — standing

Keeping the fund running on the VM host: `systemctl start fund-daily.service`
runs one trading day; schedule, cutover, and rollback live in `ops/README.md`.
Devops is a separate loop from the fund's own feedback loop — conflating them
wastes sessions; detection is already built, do not add a second checker
(`docs/agents/devops.md`). Findings reach the tracker per
`docs/agents/issue-tracker.md`. Broker mutations, droplet deploys, and gate
thresholds are Benjamin's, in his own window.

# Journal

## 2026-08-31 · #205 · fund-e2
- Composition-root exit-code contracts must be tested through `main()`
  itself: `weights_day` was pinned only through its inner `write_and_log`
  until a reviewer found 4 `main()` perturbations pass green, incl. exiting
  1 on a scoring failure (would stop `Type=oneshot`, killing reflect_day's
  perishable reflection). Fixed in 6e0b7af — second occurrence of this exact
  gap (first: `test_critic_g1_job.py`).
- The nightly unit's `ExecStart` leg list is restated in prose across many
  files (`ops/README.md`, `Makefile`, `PROGRESS.md`, several
  `scripts/*.py`/tests) and goes stale on every leg addition — this lane was
  the second consecutive occurrence (first: critic_g1). See #218 for the
  exact site list; not yet fixed structurally.
- Sweep trap: `register_spec.py:27`'s "fifth" (daily seat turns, pinned by
  `test_run_day.py`'s `turns_per_day==4`) reads just like the "fifth
  ExecStart leg" wording this lane removed elsewhere (61c2d00) — that line
  was deliberately left alone. Don't let a leg-list sweep touch it; see #218.

## 2026-09-25 · #240 #183 #146 #218 #220 #185 #184 · fund-fe (overseer, 15-PR fix wave)
- **The nightly alert filer had never filed anything** (#240, PR #241). Under launchd
  `#!/usr/bin/env python3` is Xcode's python (SQLite 3.51.0), which refuses a `mode=ro`
  open of a WAL snapshot with no `-shm` sidecar; the venv's 3.53.2 silently creates the
  sidecar, which is why it worked by hand. The open is now `mode=ro&immutable=1` — a bare
  `immutable=1` CREATES a missing file (pinned). `scripts/preflight_schema.py` rightly does
  NOT use immutable on the live DB (its `-wal` holds real commits). The plist and the
  `fund-ops` checkout still point at the pre-fix copy until Benjamin updates them.
- `ops/notify_failure.sh` (PR #244): the `fund-pnl` headline no longer asserts P&L was not
  posted (five legs, only the first posts it). `HC_PING_URL` is redacted by host
  (`hc-ping.com/<anything>`) and by name (`PING_URL` joins the name rule) in both twins;
  the twin pointer in `slackkit/redact.py` is now `:27-35`, six rules.
- **Leg list pin** (#218, PRs #251 + the direction-3 follow-up): the count and positions are
  derived from `fund-pnl.service`'s ExecStart lines; `ops/README.md`'s units table is the
  ONE prose list and `PROGRESS.md`/`Makefile` point at it. The pin is strict — any
  `third|fourth|fifth…` word anywhere in README or Makefile trips it, and the message names
  the word. `register_spec.py:27` "fifth daily seat" counts seat turns, not legs; fenced.
- `make dev-status` (PR #255): `units_installed` hashes `/opt/fund/ops/fund-*` against
  `/etc/systemd/system/` in one ssh round-trip (`sha256sum …; true` — the `; true` keeps a
  never-copied unit from turning the whole read into "not read"). Demonstrated on the
  droplet 2026-09-25: 7 units byte-for-byte. `g1_backlog` ages pending specs in run-days
  (distinct `checkpoints.run_date`), threshold `G1_BACKLOG_RUN_DAYS = 3`, advisory only.
  Any NEW droplet read breaks `tests/test_status_replay.py` (KeyError by design) until
  `make record-status` is re-run live; two readers are stubbed there pending that.
- **No region file covers `scripts/`, `slackkit/`, `evals/`, `devcheck/`**; recorded here:
  - `scripts/run_day.py` (PR #247): alert text is redacted BEFORE `log()` (#150); a held
    lock exits **2** and names the lock path so `OnFailure` fires (#129 — before, exit 0 read
    as a market holiday and the watchdog registered success); `_build_slack` lives once in
    run_day and the three nightly scripts import it (#200). `critic_g1`/`run_dispatcher`
    still exit 0 on their own held locks by documented design.
  - `scripts/critic_g1.py` (PR #253): the Critic turn records a live trace under
    `$FUND_TRACES/critic_g1/`, NOT `$FUND_TRACES` — `Trace.write` is a plain write and every
    composition root starts `turn_seq` at 0, so a sink rooted at FUND_TRACES would overwrite
    the day's `<sha>/live-<date>/0.json`. `emit_trace_guarded` needs `turn_seq` as well as
    the sink; a sink alone fails silently inside the guard. `reflect_day`/`register_spec`
    still pass no sink.
  - `slackkit` (PR #246): `render()` clips the `text` fallback at 40,000 (blocks were already
    clipped at 3,000); `msg_too_long`/`invalid_arguments` are `PermanentPostError` in
    `slackkit/real.py` (classification lives there, not in `outbox.py`).
  - `evals/live.py` (PR #245): `strategy_critiques` is scanned by seat AND ET run-day via
    `created_at` (a seat-only scan returns every critique ever written on the live DB).
  - `make replay REC=<file>` (PR #250) exists: `scripts/replay_day.py` imports
    `tests.test_sim_day.sim_day` (no second harness); a single-seat recording replays as a
    whole day with the golden day filling the other seats. Only hand-authored fixtures can be
    replayed — `make_decision_recorder` is still wired nowhere in production.
