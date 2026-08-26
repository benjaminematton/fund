# Devops: the loop that keeps the fund running

**This is not the fund's own feedback loop.** Two loops exist and conflating them wastes sessions.

| | **Devops** (this doc) | **The fund's loop** |
|---|---|---|
| Who runs it | Benjamin + dev agents | the seats, at 09:35 and 16:35 |
| Input | alerts raised by the run | signals, decisions, resolutions |
| Output | issues, commits, deploys | PM weights, reflections, scoreboard |
| Specced in | this doc | `specs/calibration.md`, phased in `specs/acceptance.md` |

They touch at one point: **the fund's alerts are an input to devops.** Nothing devops does changes how
a seat decides — charters and gate thresholds change only by human commit (`specs/design.md`
Non-goals). If your work starts to look like calibration, scoreboards or strategy gates, you are in
the other loop.

## The loop

```
alert fires  →  becomes a GitHub issue  →  standup shows it  →  work  →  closed
     ↑                                                                    │
     └──────────────── EOD sweeps what is still open ────────────────────┘
```

An alert that does not become an issue dies in Slack. On 2026-08-21 an unprotected-position alert
fired, posted, and sat eight hours; separately, four sessions each independently re-discovered the
same four untracked problems.

## Detection is done — do not add a detector

The fund already implements the practices that matter. Verify before extending; do not build a second
checker that can disagree with the first.

| Layer | Where |
|---|---|
| In-run assertions, and the day's exit code | `scripts/audit_day.py::audit()` |
| Failure alerting | `OnFailure=fund-alert@%n.service` → `ops/notify_failure.sh` |
| Liveness — the run that never started | `ExecStopPost` heartbeat to an off-box watchdog (`HC_PING_URL/${EXIT_STATUS}`), fired pass or fail so silence means only "never ran" |
| Pre-trade control | `gate/` |

**Alert on what happened; watchdog what didn't.** A signal that stops arriving is invisible to any
threshold check — absence reads as health — so liveness is inverted into a heartbeat whose *silence*
is the failure, watched from outside the box. An in-system check can never report that system's own
failure.

Audits derive from `specs/contracts.md` §6 (failure semantics), never from `CLAUDE.md`'s invariants —
those are design rules, already enforced by `scripts/check_purity.py` and
`tests/test_exec_seat_tool_surface.py` in `make test`. An audit justified by a single past incident
expires with it.

## Issues

Conventions: `docs/agents/issue-tracker.md` (`gh` CLI). Label an issue that tracks a recurring alert
so it is never filed twice.

## Runbook

Deploy, cutover, rollback, and the preflight: `ops/README.md`. **Read the runbook shipped in the
version you are deploying**, not the one you remember — a deploy delta that includes `ops/README.md`
changes the procedure you are following.

## Authorization

Droplet mutations, broker actions and gate-threshold changes need Benjamin's word **in your own
session**. `~/.claude/align/fund/decisions.md` is the record — read it yourself; a relayed go is not
authorization.
