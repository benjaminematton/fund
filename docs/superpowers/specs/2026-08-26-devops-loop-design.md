# The devops loop: one tracked condition, two producers

Design spec. Supersedes nothing; **extends** `docs/agents/devops.md`, which stays the
canonical statement of the loop. Where this file and that one disagree after the doc PR
lands, the doc wins.

Derived from a measurement of the loop as implemented on `origin/master` at `116242d`
(2026-08-26) and a grilling session that settled thirteen decisions. Every measured claim
below is marked **[M]** (observed — command run, output read) or **[R]** (reasoned from
code that has never executed).

## 1. What was measured

The detection layer devops.md describes is real, deployed, and exercised: `audit_day`,
`OnFailure=fund-alert@`, the `ExecStopPost` heartbeat, and `gate/` all ran today
(`run_day: AUDIT CLEAN 2026-08-26`, `Result=success`, `curl: OK`) **[M]**.

The loop that hangs off it does not turn:

| Edge | State |
|---|---|
| alert fires → GitHub issue | Two implementations. Neither is what the doc describes **[M]** |
| issue → standup shows it | Filed issues land in `morning-standup`'s "unattached" bucket — reported, never dispatched **[R]** |
| work → closed | Manual; nothing links a close back to the condition **[M]** |
| EOD sweeps what is still open | Not implemented. `eod-digest` sweeps sessions, not the tracker — zero `gh issue` calls **[M]** |

**The duplicate edge is the finding that reshaped this design.** Two files implement "an
untracked condition dies," two days apart, in different namespaces:

- `scripts/file_alert_issues.py` — source: `events` rows `kind='alert'` from the trading
  run. Labels `alert:<code>`. Files automatically. **Has never run**: no `alert:*` label
  has ever existed on the repo, and the script calls `ensure_label` before every create
  **[M]**. Nothing invokes it — not `ops/`, the Makefile, a unit, or CI; its only
  references in production code are comments **[M]**.
- `devcheck.check_issue_coverage` — source: on-demand production checks. Labels
  `check:<name>`. Nags a human to file. **In use**: #102 carries
  `check:db_broker_agreement` **[M]**.

Both articulate the same rule in nearly the same words ("an alert nobody files disappears
when the window closes" / "an alert that does not become an issue dies in Slack"). The
wired one is not the one the doc describes.

Two further measurements shaped the answers: **`gh` is not installed on the droplet**
(`command -v gh` empty; `su - fund -c "gh auth status"` fails) **[M]**, and
`ops/pull-backups.sh` already rsyncs `/var/lib/fund/backups/` to this machine on a launchd
timer at 19:00, where `gh` is authenticated **[M]**.

## 2. Decisions

Thirteen, in the order settled. Each is binding on the implementation; a change needs a
new round, not a judgement call at the keyboard.

**D1 — Both producers survive.** They are not duplicates. Run alerts fire when nobody is
looking; devcheck findings exist only because a human ran `dev-status`. That asymmetry is
precisely why the run-alert side must not depend on someone looking — that dependency is
the eight-hour failure. Keep both, share one tracking rule, and name both edges in the doc.

**D2 — Filed issues are reported, never dispatched.** An auto-filed issue is a *receipt*
that a condition exists, not a scoped work item: its title is alert text truncated at 110
chars. Boarding stays a human ordering decision — `morning-standup` is right, and the one
time a lane boarded itself the attach was reverted in 68 seconds. The filer stamps no
`Part of #<map>`. It **does** stamp a `severity:` label so the standup's report can rank.

**D3 — Recurrence: comment and reopen.** A re-fire after close means the fix did not work,
and it is the one case the current design guarantees a reader sees as a brand-new problem.
One comment **per filer run-window**, carrying the occurrence count — never one per firing,
which trades "30 firings look like 1" for a thread nobody reads. The body already
aggregates "occurrences in window", so this is wiring.

**D4 — The loop's input widens.** devops.md starts at "alert fires" and describes 13 alerts
in 8 days, while the board runs on ~50 issues dominated by `audit:*` and `severity:*`
findings **[M]**. The edges are identical for every source; only the producer differs. The
doc covers run alerts, devcheck findings, audit findings, and peer discoveries.

**D5 — The ratchet edge, quoted not promised.** Nothing in the loop compounds. The
compounding mechanism exists — `docs/agents/regression-ratchet.md` — and devops.md never
mentions it **[M]**. Add a `closed → permanent eval case` edge **conditionally**, quoting
the ratchet's own eligibility rule (only failures fully determined by a recorded trace,
which excludes most droplet/systemd/broker alerts), plus one line saying devops is a loop,
not a flywheel, and that the fund's flywheels are the ratchet and calibration.

**D6 — "Do not add a detector" gets the boundary it never stated, in two clauses.**
devcheck landed two days after that doctrine and re-derives invariants `audit_day` also
asserts **[M]**. It is not a violation, but the line has to be written down:

1. **Authority.** `audit_day` asserts in-run and fails the day. devcheck renders findings
   for a human and has no authority over the run. Two things that can disagree is fine when
   exactly one of them can stop a trade.
2. **Source of truth.** Both derive from `specs/contracts.md` §6, never from `CLAUDE.md`'s
   invariants. Two checkers reading different rulebooks can disagree about *what the rule
   is*, not merely about state — which is the disagreement the doctrine actually guards
   against.

**D7 — The automated filer runs locally, chained to the backup pull.** Not on the droplet:
that would put a repo-write token on the box that also holds broker keys — the box whose
entire alert path is built around not leaking credentials off it. Not on a second launchd
timer either: a 5-minute offset is a race against a slow rsync. Invoke it from the **end of
`ops/pull-backups.sh`**, so "backup pulled" implies "filer ran" and there is one fewer clock
to drift.

The dry-run default is a CLI safety for humans, **not a policy against automation** — the
doc must say so, or a future session reads the docstring as forbidding the chained run.

*Stated cost, precisely:* launchd has no `OnFailure`, so a filer that stops filing is
silent. `issue_coverage` is a **partial** backstop, and D14 covers the rest. Two limits,
both measured:

- It only fires when `dev-status` runs, and `dev-status` runs when standup does. **That much
  of filer silence is covered by the standup cadence, not by machinery** — worst case a
  one-standup delay rather than eight hours of nobody-owns-it, and if standup lapses both
  producers go silent together.
- It can only nag about conditions devcheck **independently re-derives**. devcheck reads
  persistent state — paper mode, position coverage, outbox, services, checkpoints — and
  never reads `events.kind='alert'` rows **[M]**. A run-alert-only condition, transient and
  visible solely in the events table, has no coverage here at all: devcheck cannot see it,
  so there is nothing for it to report as untracked.

devcheck's one attempt to read that table is itself broken, which is why the gap is total
rather than partial: `_scorecard_codes()` is docstringed "Alert codes raised on the
droplet's most recent run date" but selects `events.kind`, not the payload's `code`.
Production's kinds are `signal, decision, gate_approved, fill, digest, alert, pnl,
model_fallback_used, scorecard`; `check_degradations` matches them against `("gate_error",
"pm_timeout", "critic_timeout", "missing_signal")`. The intersection is empty by
construction, so that check is structurally dead-green — and `health.md` suppresses it, so
the deadness is unobservable **[M]**. Out of scope here (§6), tracked separately.

**D8 — Rolling 7-day window; reopen only on a firing after the close.** A today-only window
misses any day the pull did not run (a sleeping laptop). Rolling makes missed runs
self-healing, since label dedupe makes re-scans free. But rolling alone plus D3 is a nightly
reopen bot: a 5-day-old firing would reopen an issue a human closed yesterday. The
close-timestamp rule is what makes the window safe.

**Compare in UTC explicitly.** The droplet writes ET-clocked rows; GitHub's `closed_at` is
UTC. A naive comparison reopens or skips wrongly for firings inside the offset.

**D9 — One canonical `condition:<id>` label, emitted by both producers.** `alert:` and
`check:` are demoted to *source* labels, kept so you can still tell who filed it. A mapping
table was rejected as a second place to forget to update; per-namespace lookup was rejected
as the disagreement D1 exists to design out.

**Migration is part of the change, not a follow-up.** #102 already carries
`check:db_broker_agreement`, and any other open issue carries an old namespace. Until they
are relabelled the shared lookup cannot see them, and the first automated run double-files
every condition that is already tracked.

**D10 — Suppression is shared; `accepted` suppresses the reopen, not the record.** Both
producers read `suppress:` from `.claude/health.md` front matter, which `dev_status`
already parses with hand-rolled stdlib **[M]** — so the filer can import it without
breaking its zero-dependency rule. A close carrying `accepted` stops the reopen
permanently, distinct from an ordinary close, which stays reopen-on-refire.

The filer **still appends its occurrence-count comment to an accepted issue.** An accepted
condition that recurs 100× worse would be invisible under a pure opt-out; a silently
accruing count costs nothing, nags nobody, and leaves a re-promotion trail.

**D11 — The EOD sweep is a devcheck check.** Open `condition:` issues, oldest first, folded
into `make dev-status`. In-repo, testable, no cross-repo skill edit — and both
`morning-standup` and `eod-digest` already fold `health_command` output in, so one check
lights up both ends of the day. It sits inside D6's boundary exactly: renders findings for a
human, no authority over the run.

**D12 — The root checkout is out of scope.** `Developer/fund` sits on a detached HEAD
predating devops.md, the filer, and devcheck, and three live sessions (`fund-28`,
`fund-34`, `fund-e5`) have it as their cwd **[M]**. File an issue with the evidence and
raise it at the next standup. Do **not** reconcile it here: moving HEAD under three working
sessions is the `git branch -f` failure one level up.

**D13 — Doc PR first, with per-edge status markers.** The doc is correct whether or not the
timer ships, and every session started before it lands operates without it. But round 1's
core finding was *a doc describing an edge that was not wired* — and a doc-only PR
describing `condition:` labels, reopen semantics and a chained filer recreates that failure
for the window between the two PRs, in a repo whose new rule is explicitly hostile to it
("a dated filename is a snapshot of the moment it was written, never current state").

So every edge in the revised doc carries its status: **exists today** (devcheck nag, manual
filer) versus **specced, landing in the code PR** (chained run, reopen, `condition:`
labels). The code PR's diff includes flipping those markers. The doc is then true on both
sides of the gap instead of aspirational in the middle of it.

**D14 — The filer stamps its own liveness; a devcheck check reports staleness.** D7's
backstop does not reach run-alert-only conditions, so without this the filer is a signal
that can stop arriving with nothing watching — the exact shape devops.md names: *"a signal
that stops arriving is invisible to any threshold check — absence reads as health."*
Building a second alerter would violate D6, so the cheap form stays inside its boundary:

- On a successful run the filer writes a stamp to `$FUND_LOCAL_BACKUPS/.filer-last-run`
  (the env var `pull-backups.sh` already requires), containing the window it covered.
- A devcheck check reads it and reports `warn` when the stamp is older than **3 days** —
  long enough to tolerate a laptop off over a long weekend, short enough to catch a stopped
  filer inside one work week. An absent stamp or unset env var reports `unknown`, never a
  crash, per devcheck's house rule that a descriptor problem cannot stop the checks.

Pure function over `Snapshot`, renders for a human, no authority over the run. It converts
filer silence from invisible to a finding, and it is what makes §5's criterion 8 concrete:
the stamp not advancing *is* the report.

## 3. The design

### 3.1 Label taxonomy

| Label | Meaning | Who sets it |
|---|---|---|
| `condition:<id>` | The canonical condition. **The only key any lookup uses.** | Both producers |
| `alert:<code>` | Source: a run alert, `events.kind='alert'` | The filer |
| `check:<name>` | Source: a devcheck finding | The human, prompted by `issue_coverage` |
| `severity:<tier>` | Existing taxonomy, so the standup can rank a report | The filer (D2) |
| `accepted` | Declared known; suppresses reopen, not the comment (D10) | Human only |

### 3.2 The tracking rule, stated once

> A condition is tracked **iff** an open issue carries its `condition:<id>` label.

One helper, imported by both producers. Any `gh` failure resolves to "nothing is tracked",
which over-reports — the safe direction, and the rule `_tracked_checks` already follows.

### 3.3 Filer behavior

Invoked from the end of `ops/pull-backups.sh`, after the rsync succeeds, against the
freshly pulled backup DB. The window start is computed **in the shell** and passed as
`--since`, keeping the script argv-driven and free of a wall clock — which is what makes it
testable.

**Precedence, stated once because the rows below overlap:** suppression is evaluated first
and wins outright — a suppressed condition produces nothing, whatever the tracker holds.
Then `accepted`, which downgrades reopen to comment-only. Then the open/closed state
machine. An implementer resolving these in a different order is the failure this line
exists to prevent.

Per condition in the window:

| Tracker state | Action |
|---|---|
| No issue | Create: `condition:` + source + `severity:`; body aggregates occurrences |
| Open issue | Comment once per run-window with the occurrence count (D3) |
| Closed, no firing after `closed_at` | Nothing |
| Closed, firing after `closed_at` (UTC) | Reopen + comment (D3, D8) |
| Closed with `accepted` | Comment only, never reopen (D10) |
| Suppressed in `health.md` | Nothing |
| Clearing alert only | Existing `comment` action; never file to announce a fix |

### 3.4 Sweep behavior

A devcheck check reporting open `condition:` issues oldest-first. Severity `warn`, not
`alert` — an open tracked issue is the system working, and a check that nags daily about
correctly-tracked work is what `check_issue_coverage`'s own docstring warns against.

Beside it, D14's staleness check: `warn` when `.filer-last-run` is older than 3 days,
`unknown` when the stamp or `FUND_LOCAL_BACKUPS` is absent. Two checks, one fold-in — the
sweep says what is still open, the stamp says whether anything is still filing.

## 4. Test seams

Named here because the spec gate exists to check them before code is written.

1. **`plan_filings(conn, since, tracker, db_path)`** — already the seam; `tracker` is
   injected, and `tests/test_file_alert_issues.py` (376 lines) already drives it with a
   fake. It extends: the tracker protocol grows `issue_state(condition_id) -> (number,
   state, closed_at, labels)`. Two adapters exist (`GhTracker`, the test fake), so this
   clears the real-seam bar.
2. **devcheck checks are pure functions over `Snapshot`** — the sweep check takes tracker
   rows through `Snapshot`, and D14's staleness check takes the stamp's age the same way.
   `dev_status.py` stays the only place doing I/O. Existing pattern; `tracked_checks` is the
   precedent. Both new checks are testable without a tracker, a clock, or a filesystem.
3. **Totality test for D9** — every alert code (collected the way `check_alert_codes.py`
   already collects them, by AST) and every devcheck check name resolves to exactly one
   `condition:` id. Total by construction, not by discipline.
4. **UTC comparison (D8)** — table-driven cases at the ET/UTC boundary: a firing inside the
   offset on either side of `closed_at`.
5. **Migration (D9)** — a dry-run assertion that every currently-open issue carrying an old
   namespace maps to a `condition:` id before the first `--apply`.

## 5. Acceptance

Per edge, in the shape `specs/acceptance.md` uses. Tests first, then code until green.

1. A run alert with no tracked condition produces exactly one issue carrying
   `condition:`, a source label, and `severity:`.
2. The same condition firing again the next day produces **no second issue** and exactly
   one new comment.
3. A condition whose issue was closed, firing again after `closed_at`, reopens that issue.
   Firing again *before* `closed_at` does not.
4. A condition suppressed in `health.md` produces nothing — **including when an open issue
   already tracks it**, which is the precedence case implementers will otherwise split on.
5. A closed issue labelled `accepted`, firing again, gains a comment and stays closed.
6. `make dev-status` lists open `condition:` issues oldest-first at severity `warn`.
7. Both producers, given the same condition, resolve to the same `condition:` id — asserted
   by the totality test, not by inspection.
8. `ops/pull-backups.sh` invokes the filer only after a successful rsync, and a filer
   failure does not fail the pull. **The report surface is D14's stamp**: a failed run does
   not advance `.filer-last-run`, so the staleness check fires at the next `dev-status`.
   Stderr into a launchd log is not a report — nobody reads it.
9. A stamp older than 3 days makes `make dev-status` report filer staleness at `warn`; an
   absent stamp reports `unknown` rather than crashing or reading as healthy.

**Runtime evidence, not a green suite (this ships unattended).** Before the code PR is
called done: one real dry run against a pulled backup DB, output pasted, showing what it
would file against the live tracker.

## 6. Out of scope

- Reconciling the root checkout (D12) — issue + standup.
- Installing `gh` on the droplet. D7 exists so this never has to happen.
- Any change to what an alert *is*, or to `check_alert_codes.py`'s literal-code rule. This
  spec changes tracking, never detection — D6's whole point.
- Auto-attaching anything to the board (D2).

## 7. Landing order

1. **Doc PR** against `master`: `docs/agents/devops.md` revised per D1, D4, D5, D6, D7,
   D13, with per-edge status markers.
2. **Code PR**: `condition:` namespace + migration, shared lookup, reopen/comment/suppress
   behavior, sweep check, `pull-backups.sh` chaining — and the marker flips from step 1.

Both against `master` via PR, never a direct edit to a shared checkout.
