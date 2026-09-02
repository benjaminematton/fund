---
paths:
  - orchestrator/
  - tests/test_orchestrator*.py
---
# orchestrator — standing

No LLM: clock, stage scheduler, turn assignment, checkpoints. The orchestrator
assigns every workflow-critical turn — a Slack event never produces a decision,
an order, or a state transition (invariant 6). Time comes from an injected
`Clock` protocol; never `datetime.now()` or `time.sleep()` in business logic —
that is what makes `make sim-day` possible. Never import from `agents/` here.
Workflow tables are state machines: transitions only through
`state/transition()`, allowed transitions in `specs/contracts.md`.

# Journal

## 2026-08-31 · #205 · fund-e2
- `coverage` joins `signals`↔`offered` on `(run_date, ticker)`, not date alone
  — date-only, it could read up to 20.0 since `signals` predates `offered` by
  years; both tables' UNIQUE/PK bound the pair-join ≤1 structurally.
  `handle_submit_signal` still never checks a ticker against `offered` — the
  join, not a write-time guard, is what excludes an off-menu signal.
- `write_weights`' `conn.rollback()` is only exercised by a raise *inside*
  the write loop (after ≥1 INSERT); a compute-phase raise can't tell a real
  rollback from none at all. Pinned in `tests/test_improve.py` via a
  connection subclass that fails the second INSERT.
- A rostered-but-silent seat still gains `n_graded`: `run_research`'s
  placeholder rows (`charter_version='none'`) get graded exactly like real
  ones — `grade_rows` doesn't filter on it. Only de-rostering freezes it;
  don't treat `n_graded` alone as a "seat gone quiet" signal.
