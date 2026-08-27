---
health_command: make dev-status
suppress:
  - degradations
---

# What healthy means in this repo

`make dev-status` runs `scripts/dev_status.py` — read-only over the droplet,
the broker and the database. It never writes, places, cancels, amends or
deploys.

## Interpreting the output

- **`degradations` is suppressed.** `model_fallback_used` fires at severity 3
  every day: it is an SDK auxiliary Haiku call on Sonnet-configured seats, not
  a real fallback. Suppressed findings still appear, marked `[suppressed]` —
  read them if something else looks wrong.
- **`reflection` ok with an empty `resolutions` table is correct** until a
  decision passes its 5-day horizon. The check knows the difference; a human
  reading the table directly does not.
- **`deploy_state` behind is normal** between a merge and a deploy. It matters
  when the gap contains a gate or seat-surface change.
- **`db_broker_agreement` currently reports itself unwired**, and that is
  accurate rather than broken: `AlpacaSource` exposes no fill-history method,
  so the comparison has no source. It warns instead of printing a green row
  for a comparison nobody performed. It also diverges legitimately after any
  manual out-of-gate order, which is the expected consequence of one, not a
  bug in the check.
- **`position_coverage` alerts when the broker could not be read.** An
  unreachable broker yields an empty book, and "0 positions, every share
  covered" is indistinguishable from a genuinely flat account. Unknown
  exposure is never rendered as safety.
- **`issue_coverage` is the loop from finding to tracked work.** An alert with
  no open issue labelled `check:<id>` will be re-derived by the next session
  and lost again. It fires on every alert that has no such issue yet, which on
  a repo that has not adopted the `check:` labels means all of them — that is
  the intended first run, not a bug. Read the current state from
  `gh issue list`, never from this file.

## Filing what this finds

`docs/agents/issue-tracker.md` is the convention. Label a new issue
`check:<check_id>` so the finding stops being re-derived:

    gh issue create --label "check:position_coverage" --title "..." --body "..."

An issue that describes production behaviour should say which check would go
green when it is fixed.

## Escalate, never act

Broker mutations, droplet deploys, and gate thresholds are Benjamin's, in his
own window. `~/.claude/align/fund/decisions.md` is the record; read it there
rather than taking a peer's account of it.
