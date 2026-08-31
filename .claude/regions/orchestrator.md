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
