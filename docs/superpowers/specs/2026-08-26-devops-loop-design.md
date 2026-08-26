# The devops loop: invoke the filer, observe, then decide

Design spec, **cut back after adversarial review**. Extends `docs/agents/devops.md`, which
stays the canonical statement of the loop. Where this file and that one disagree after the
doc PR lands, the doc wins.

Claims are marked **[M]** (observed — a command was run and its output read), **[R]**
(reasoned from code that has never executed), or **[X]** (asserted in review and since
falsified — kept only where the correction is instructive).

**Revision history.** The first draft carried thirteen binding decisions. An adversarial
review in a fresh context found six resting on premises the repo contradicts, two of them
false outright, and made the scope argument below. Five decisions are deferred, three are
amended, three defects were split out as issues (#110, #111, #112). What that review
falsified is recorded here rather than deleted — a spec that hides its own corrections
teaches nothing.

## 1. Scope, and why it shrank

**The measured defect is one line: nothing invokes `scripts/file_alert_issues.py` [M].**

It is 213 lines with a 376-line suite. It already dedupes by label, refuses to retry, and
handles a missing `gh`, an unreadable DB, malformed payloads, clearing alerts, and the
audit rollup. It has executed zero times against production.

The first draft answered that by designing a unified label namespace, reopen semantics,
shared suppression, a sweep check, and a liveness stamp — five mechanisms, none of them
informed by a single observed run. The review's scope finding stands: that adds a failure
surface plausibly larger than the eight-hour incident it removes. Two of its examples are
enough to make the point — a suppression namespace that could silently swallow real
`gate_error` alerts, and a staleness check inert in every shell anyone would run it from.

**So: ship the smallest thing that produces data. Run it two weeks. Let observed behavior
adjudicate the rest.** Decisions D3, D8, D9, D10, D11 and D14 are deferred to §4, each with
the evidence that would settle it.

## 2. What was measured

The detection layer devops.md describes is real, deployed, and exercised: `audit_day`,
`OnFailure=fund-alert@`, the `ExecStopPost` heartbeat and `gate/` all ran today
(`run_day: AUDIT CLEAN 2026-08-26`, `Result=success`, `curl: OK`) **[M]**.

The loop hanging off it does not turn. Two implementations of "an untracked condition dies"
exist, two days apart, in different namespaces:

- `scripts/file_alert_issues.py` — source: `events` rows `kind='alert'`. Labels
  `alert:<code>` **and `ticker:<t>`**, so a condition is keyed per `(code, ticker)` —
  pinned by `test_two_tickers_of_one_code_are_two_issues` **[M]**. Files automatically.
  Never run: no `alert:*` label has ever existed, and it calls `ensure_label` before every
  create **[M]**.
- `devcheck.check_issue_coverage` — source: on-demand production checks. Labels
  `check:<name>`. Nags a human. In use: #102 carries `check:db_broker_agreement` **[M]**.

Both state the same rule in nearly the same words. The wired one is not the one the doc
describes.

Two facts shaped the answers: **`gh` is not installed on the droplet** **[M]**, and
`ops/pull-backups.sh` already rsyncs `/var/lib/fund/backups/` to this machine on a launchd
timer at 19:00, where `gh` is authenticated **[M]**.

**A correction to the first draft.** It claimed filed issues land in `morning-standup`'s
"unattached" bucket **[X]**. That bucket is defined by a `Part of #<n>` line in the issue
body — which D2 forbids the filer from writing. Filed issues land in the general
open-issues report instead. The conclusion (reported, never dispatched) is unchanged; the
mechanism given for it was wrong and was checkable in one file read.

## 3. Decisions that survive

**D1 — Both producers survive.** Run alerts fire when nobody is looking; devcheck findings
exist only because a human ran `dev-status`. That asymmetry is why the run-alert side must
not depend on someone looking. Keep both. **Amended:** the first draft added a unified
`condition:` namespace to stop them disagreeing. The review found the two producers' subject
sets are disjoint by construction — devcheck never reads `events.kind='alert'` rows **[M]**
— and the spec named no condition both emit. Unification is deferred (§4, D9) until an
actual overlap is observed.

**D2 — Filed issues are receipts, reported and never dispatched.** An auto-filed issue's
title is alert text truncated at 110 chars; boarding stays a human ordering decision. The
filer stamps no `Part of #<map>`. **Amended: it stamps no `severity:` label either.** The
first draft required one; `append_alert`'s payload has no severity field **[M]**, so the
filer would need a code→tier table — the same "second place to forget" the draft rejected
elsewhere — and stamping an existing label would trip #111. Filed issues carry
`alert:<code>` and `ticker:<t>` only.

**D4 — The doc's stated input widens.** devops.md starts at "alert fires" and describes 13
alerts in 8 days, while the board runs on ~50 issues dominated by `audit:*` and `severity:*`
findings **[M]**. The edges are identical for every source; only the producer differs. The
doc covers run alerts, devcheck findings, audit findings and peer discoveries.

**D5 — The ratchet edge, named as near-empty.** Nothing in the loop compounds;
`docs/agents/regression-ratchet.md` is the compounding mechanism and devops.md never
mentions it **[M]**. **Amended after review:** the draft added `closed → permanent eval
case` as a *conditional* edge quoting the eligibility rule. A quoted condition still implies
a live edge. The ratchet grades seat-turn traces through `evals/grade.py`; droplet, systemd
and broker alerts are not narrowly excluded but categorically outside its domain **[M]**.
So the doc says the edge exists and almost never fires, and keeps the line that matters:
devops is a loop, not a flywheel — the fund's flywheels are the ratchet and calibration.

(The review argued the ratchet's arrow points the other way, since its own text says an
ineligible failure means "write an issue instead." That gates *ineligible* failures to
issues; it does not forbid an eligible one the edge. The reframe above is adopted; the
reversal is not.)

**D6 — The detector doctrine gets its authority clause only.** `audit_day` asserts in-run
and fails the day; devcheck renders findings for a human and has no authority over the run.
Two instruments that can disagree is fine when exactly one can stop a trade. **The draft's
second clause — "both derive from `specs/contracts.md` §6" — is dropped as false [X]:**
eleven of devcheck's thirteen checks derive from CLAUDE.md invariants or `acceptance.md` by
name, §6 is a stage×failure table that could not ground them, and the doctrine's own
rationale (invariants are "already enforced in `make test`") argues the opposite way for an
instrument whose stated purpose is asserting against the running host. Filed as **#112**;
the doc PR says only what is true.

**D7 — The filer runs locally, chained to the backup pull.** Not on the droplet: that puts a
repo-write token on the box holding broker keys — the box whose entire alert path is built
around not leaking credentials off it, and which has no `gh` **[M]**. Not on a second
launchd timer: a clock offset is a race against a slow rsync. Invoke it from the **end of
`ops/pull-backups.sh`**, so "backup pulled" implies "filer ran".

The dry-run default is a CLI safety for humans, **not a policy against automation** — the
doc must say so, or a future session reads the docstring as forbidding the chained run.

*Stated cost:* launchd has no `OnFailure`, so a filer that stops filing is silent.
`issue_coverage` does not cover it — devcheck cannot see run-alert-only conditions **[M]**,
and its one attempt to read that table is broken (#107). **This is the observation window's
main risk and is accepted deliberately for two weeks**, not solved: §5's output line is what
makes it detectable by a human reading the log, and D14 is the machinery that would close
it, deferred until the window shows whether it is needed.

**D12 — The root checkout is out of scope.** `Developer/fund` sits on a detached HEAD
predating devops.md, the filer and devcheck, and live sessions have it as their cwd **[M]**.
File an issue, raise it at standup. Moving HEAD under working sessions is the `git branch -f`
failure one level up.

**D13 — Doc PR first, with per-edge status markers.** The doc is correct whether or not the
chained run ships, and every session started before it lands operates without it. But the
core finding was *a doc describing an edge that was not wired*, and a doc-only PR describing
machinery that does not exist recreates that for the window between PRs — in a repo whose
new rule is explicitly hostile to it. So every edge carries its status: **exists today**
versus **specced, landing in the code PR**, and the code PR flips the markers.

*Unenforced, and known:* the flip is discipline across two PRs. If it drifts, the doc lies in
the other direction. A grep-based test pinning the markers to the presence of
`file_alert_issues` in `pull-backups.sh` would cost almost nothing and is worth doing if the
markers survive the observation window.

## 4. Deferred — decided by observation, not by argument

Each names the evidence that would settle it. None is a rejection.

| # | Deferred | What would settle it |
|---|---|---|
| D3 | Comment-and-reopen on recurrence | Whether any condition actually recurs after a close in the window. Blocked anyway: the comment path has no dedupe key, so a second run over one window double-comments |
| D8 | Rolling window + close-timestamp rule | Whether missed runs actually happen. **Its stated premise was false [X]** — `events.created_at` is ISO-8601 **UTC** (`clock.iso()`), not ET; only `--since` is ET, and `audit_day` already converts it. An implementer following the draft would have shifted every firing by the offset. The real fragility is smaller: `+00:00` versus GitHub's `Z`, so parse both rather than string-compare |
| D9 | Unified `condition:` namespace | An observed condition both producers emit. None is known. Note any such id must carry the ticker, or an MSFT exposure dedupes against an open NVDA issue |
| D10 | Shared suppression, `accepted` opt-out | Whether a filed condition is ever declared noise. Blocked anyway: `read_suppressed` returns devcheck **check ids**, and the live entry is `degradations` — mapping it to its codes would silently stop `pm_timeout` and `gate_error` from ever being filed |
| D11 | The EOD sweep as a devcheck check | Whether open receipts actually go unread. `_tracked_checks`'s `--limit 100` against a growing board needs solving first |
| D14 | Filer liveness stamp | Whether the filer ever silently stops. Blocked anyway: `FUND_LOCAL_BACKUPS` is set nowhere but inside the launchd plist **[M]**, so the check reports `unknown` in every shell a human would run it from — and with `rsync --ignore-existing` the stamp would advance daily against a dead backup (#110) |

## 5. The design that ships

One line added to `ops/pull-backups.sh`, plus three fixes to the filer it invokes.

**Invocation.** After the rsync succeeds, run the filer with `--apply` against the newest
snapshot. `set -eu` is in force and `main()` returns 1 on a *malformed payload* — a routine
data condition, not a failure **[M]** — so a bare call fails the pull job and `|| true`
would swallow the tracker-unavailable case too. Capture the return code, report it, exit 0
regardless.

**Which snapshot.** Newest `fund-<date>.sqlite`, explicitly **excluding `fund-predeploy-*`**,
which sorts lexically after the dated snapshots and would otherwise win a naive `tail -1`.

**Print the snapshot's date.** The filer takes the DB path; it prints which file and what
date it read. `pull-backups.sh` reports an inventory count that only grows, so a stalled
droplet backup is indistinguishable from a healthy one (#110) — and a two-week observation
run against a silently stale snapshot observes nothing. This is not D14's machinery; it is
making the human-read output falsifiable, which is the whole theory of a minimal ship.

**Read-only DB open.** `sqlite3.connect(args.db)` opens read-write **[M]**. `dev_status.py`
documents at length why every production read uses `file:{db}?mode=ro` — a read-write open
applies a pending migration as a side effect. The local mirror already carries `-wal`/`-shm`
sidecars from past read-write opens; chaining a nightly one makes it routine. Use
`sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.

**Labels.** `alert:<code>` + `ticker:<t>`, as the code already does. Nothing new, so #111 is
not triggered by this change — but it should be fixed before any future decision has the
filer stamp a human-curated label.

## 6. Test seams

1. **`plan_filings(conn, since, tracker, db_path)`** — already the seam; `tracker` is
   injected and the suite drives it with a fake. Two adapters (`GhTracker`, the fake), so
   this clears the real-seam bar, and `test_gh_tracker_queries_only_open_issues` already
   pins the real argv.
2. **`ops/pull-backups.sh`** — `tests/test_ops_pull_backups.py` already stubs `rsync` via
   `FUND_RSYNC`. The filer gets the same treatment: an injectable command, so the chaining,
   the return-code handling and the snapshot-selection rule are testable without a network,
   a database or `gh`.
3. **Read-only enforcement** — assert the connect string, not the behavior. A test that
   writes to the DB to prove it cannot is a test that corrupts a snapshot when it fails.

## 7. Acceptance

1. A run alert with no open issue for its `(code, ticker)` produces exactly one issue.
2. The same condition firing again the next day produces no second issue.
3. `ops/pull-backups.sh` invokes the filer only after a successful rsync; a filer return
   code of 1 does not fail the pull, and is printed.
4. The snapshot selection picks the newest dated snapshot and never a `fund-predeploy-*`
   file, asserted against a fixture directory containing both.
5. The filer's output names the snapshot file and its date.
6. The filer opens the database read-only.

**Runtime evidence, not a green suite — this ships unattended.** Before the code PR is
called done: one real dry run against a pulled backup, output pasted. **Run it twice and
paste both** — identical output is the only cheap evidence the run is idempotent, and
non-idempotency is what killed D3.

## 8. Out of scope

- #107 (`check_degradations` cannot fire), #110 (pull freshness), #111 (label vandalism),
  #112 (the doctrine question). Each is true on `master` independent of this spec.
- Reconciling the root checkout (D12).
- Installing `gh` on the droplet. D7 exists so this never has to happen.
- Any change to what an alert *is*. This spec changes invocation, never detection.

## 9. Landing order

1. **Doc PR** against `master`: `docs/agents/devops.md` per D4, D5, D6 (authority clause
   only), D7, D13, with per-edge status markers.
2. **Code PR**: the chained invocation and the three filer fixes, plus the marker flips.
3. **Two weeks of observation**, then revisit §4 against what actually happened.

Both PRs against `master`, never a direct edit to a shared checkout.

## 10. On "binding"

The first draft said its decisions were binding and that changing one needed a new round.
Six of thirteen did not survive one careful reading of `clock.py`, `contracts.md` §6 and
`checks.py`. Thirteen decisions settled in a single session, about a script that had never
run, were not a set anything should have been bound to. The decisions here are the ones that
survived review; §4's are open questions with named evidence. Treat both as revisable by the
next person who reads the code more carefully than we did.
