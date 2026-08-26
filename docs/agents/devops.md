# Devops: the loop that keeps the fund running

**This is not the fund's own feedback loop.** Two loops exist and conflating them wastes sessions.

| | **Devops** (this doc) | **The fund's loop** |
|---|---|---|
| Who runs it | Benjamin + dev agents | the seats, at 09:35 and 16:35 |
| Input | findings from four producers (below) | signals, decisions, resolutions |
| Output | issues, commits, deploys | PM weights, reflections, scoreboard |
| Specced in | this doc | `specs/calibration.md`, phased in `specs/acceptance.md` |

They touch at one point: **the fund's alerts are an input to devops.** Nothing devops does changes how
a seat decides — charters and gate thresholds change only by human commit (`specs/design.md`
Non-goals). If your work starts to look like calibration, scoreboards or strategy gates, you are in
the other loop.

## The loop

```
finding surfaces  →  GitHub issue  →  standup reports it  →  work  →  closed
                                                                        │
                                          (nothing sweeps what is still open — see Gaps)
```

A finding that does not become an issue dies where it appeared. On 2026-08-21 an
unprotected-position alert fired, posted, and sat eight hours; separately, four sessions each
independently re-discovered the same four untracked problems.

**Every edge below carries its status.** `[live]` means it works today. `[specced]` means it is
designed and not yet built — the design is
`docs/superpowers/specs/2026-08-26-devops-loop-design.md`, and the PR that builds it flips the
marker. A doc that describes machinery as though it existed is the failure this convention prevents;
it is the failure this doc itself shipped with.

## Producers — who surfaces a finding, and how it reaches the tracker

| Producer | Source | Reaches the tracker by | Status |
|---|---|---|---|
| Run alert | `events` rows `kind='alert'`, raised during the trading day | `scripts/file_alert_issues.py`, chained to the end of `ops/pull-backups.sh` | `[live]`, once `FUND_FILER` is set in the launchd plist |
| devcheck finding | `make dev-status`, read against the running host | `check_issue_coverage` names untracked findings; a human files them | `[live]` |
| Audit finding | a human or agent auditing the repo | a human files it | `[live, manual]` |
| Peer discovery | another session working this repo | a human files it | `[live, manual]` |

The first two are the machinery; the last two are most of the volume. In the eight days to
2026-08-26 the run raised 13 alerts while the board carried ~50 open issues, dominated by audit
findings. **A reader whose work enters through the manual producers is in this loop too** — the
edges are the same, only the producer differs.

**The run-alert producer is the one that cannot wait for someone to look.** devcheck findings exist
only because a human ran `dev-status`; a run alert fires at 09:38 whether or not anyone is at a
keyboard. That asymmetry is why it gets automation and the others do not.

Two notes on the chained filer, both of which have been misread before:

- **It runs on the Mac, not the droplet.** `gh` is not installed there, and putting a repo-write
  token on the box that holds broker keys works against everything `ops/notify_failure.sh` does to
  keep credentials from leaving it. It runs off the back of the nightly backup pull, so "backup
  pulled" implies "filer ran".
- **The filer's dry-run default is a CLI safety for humans, not a policy against automation.** Its
  docstring says filing is the only irreversible act it performs, which is a reason to make a human
  type `--apply`, not a reason the chained run may not.

## Detection is done — do not add a detector

The fund already implements the practices that matter. Verify before extending; do not build a second
checker that can disagree with the first.

| Layer | Where |
|---|---|
| In-run assertions, and the day's exit code | `scripts/audit_day.py::audit()` |
| Failure alerting | `OnFailure=fund-alert@%n.service` → `ops/notify_failure.sh` |
| Liveness — the run that never started | `ExecStopPost` heartbeat to an off-box watchdog (`HC_PING_URL/${EXIT_STATUS}`), fired pass or fail so silence means only "never ran" |
| Pre-trade control | `gate/` |
| Production state, on demand | `devcheck/` via `make dev-status` — read-only against the running host |

**Alert on what happened; watchdog what didn't.** A signal that stops arriving is invisible to any
threshold check — absence reads as health — so liveness is inverted into a heartbeat whose *silence*
is the failure, watched from outside the box. An in-system check can never report that system's own
failure.

**Two checkers, one authority.** `audit_day` asserts in-run and fails the day. `devcheck` renders
findings for a human and has no authority over the run — it cannot stop a trade, fail a day, or
change what a seat does. Two instruments that can disagree is tolerable precisely because exactly one
of them can act on the disagreement. Keep it that way: a devcheck finding is an input to a human, and
that is the whole of its power.

**Open question — what each checker derives from (#112).** This doc used to say audits derive from
`specs/contracts.md` §6 and never from `CLAUDE.md`'s invariants. That is true of `audit_day` and was
written for it. It does not describe `devcheck`: eleven of its thirteen checks cite CLAUDE.md
invariants or `acceptance.md` Phase 2 by name, and §6 is a stage×failure table with no rows that
could ground them. So the two checkers read different rulebooks today. Whether that is a defect, a
boundary worth writing down, or a rule that needs narrowing is open at **#112** — do not resolve it
by picking one and editing this line. When it settles, the ruling replaces this paragraph.

An audit justified by a single past incident expires with it.

## Gaps — what is missing, and which kind of missing

Two states, and the difference decides whether you may rely on an edge:

**Deferred for scope** — the mechanism would work; nobody has built it, because the design chose to
ship a minimum first and let two weeks of observation decide.

- The EOD sweep. The loop diagram has no return edge: nothing lists what is still open at the end of
  a day. A `dev-status` check is the intended home.
- Unifying the two label namespaces (`alert:<code>` and `check:<name>`) behind one condition id. Held
  because no condition is yet known that both producers emit.

**Deferred because the mechanism does not work** — do not build these as specified; the blocker
comes first.

- Reopen-and-comment on a recurring condition. The comment path has no dedupe key, so a second run
  over one window comments twice.
- Shared suppression between the two producers. `read_suppressed` returns devcheck check ids, not
  alert codes; mapping the live `degradations` entry across would silently stop `pm_timeout` and
  `gate_error` from ever being filed.
- A filer liveness stamp. `FUND_LOCAL_BACKUPS` is set nowhere but inside the launchd plist, so the
  check would report `unknown` in every shell a human runs it from — and `pull-backups.sh` cannot
  currently tell a stale mirror from a fresh one (**#110**), so the stamp would advance daily against
  a dead backup.

**The consequence of shipping the minimum, stated plainly:** the chained filer has no failure
alerting. launchd has no `OnFailure`, and `check_issue_coverage` does not cover it — devcheck never
reads `events` alert rows, and its one attempt to is broken (**#107**). A filer that silently stops
filing is caught by a human reading the output, or not at all. This is accepted for the observation
window, not solved.

## This is a loop, not a flywheel

Nothing here compounds. No turn of it makes the next turn cheaper — it is a work queue with a human
at every decision point, and that is the right shape for it.

The fund's flywheels are elsewhere: `specs/calibration.md` (scoring feeds weights) and
`docs/agents/regression-ratchet.md` (a real failure becomes a permanent eval case, graded on every
run forever). There is an edge from this loop into the ratchet — a closed devops issue whose failure
is fully determined by a recorded trace can be promoted to a case — but **expect it almost never to
fire.** The ratchet grades seat-turn traces; droplet, systemd and broker failures are not narrowly
excluded from it, they are categorically outside its domain. The edge is real and nearly always
inapplicable, and a reader should not plan around it.

## Issues

Conventions: `docs/agents/issue-tracker.md` (`gh` CLI). Label an issue that tracks a recurring
condition so it is never filed twice — that label is what the filer and `check_issue_coverage` both
key on, and it is the only thing standing between a recurring condition and one issue per occurrence.

An auto-filed issue is a **receipt that a condition exists**, not a scoped work item: its title is
alert text truncated to fit. It carries no `Part of #<map>` line and is never attached to the board
by the thing that filed it. Boarding is a human ordering decision — promote a receipt to a lane when
you have decided it is worth one.

## Runbook

Deploy, cutover, rollback, and the preflight: `ops/README.md`. **Read the runbook shipped in the
version you are deploying**, not the one you remember — a deploy delta that includes `ops/README.md`
changes the procedure you are following.

## Authorization

Droplet mutations, broker actions and gate-threshold changes need Benjamin's word **in your own
session**. `~/.claude/align/fund/decisions.md` is the record — read it yourself; a relayed go is not
authorization.
