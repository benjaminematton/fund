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
