# Kickoff prompts — fund repo and superpowers

Your doc pack IS the brainstorming output: a validated spec. So you enter the superpowers pipeline at step 2 (`writing-plans`), never re-brainstorm, and repeat plan → execute → review per phase. Prompts below are ready to paste.

## Session 0 — repo assembly and orientation

Run this session once, before any planning. Paste the following prompt:

```
Read CLAUDE.md, then specs/design.md, specs/contracts.md, specs/acceptance.md,
and skim specs/strategy.md, charters/, fixtures/. Do not write any code.

Then do two things:
1. Report back: the five biggest risks or ambiguities you see in implementing
   Phase 1, and any contradictions between the spec files. Questions, not fixes.
2. Verify the starter kit (merged at fundbt/ + stratgate/ + calibration/):
   run its tests (32, offline), confirm no LLM imports, confirm the holdout
   one-touch is PK-enforced, and check its DSR against the Bailey–López de
   Prado 2014 worked example it claims to reproduce (tests/test_stats.py).
   Report discrepancies; change nothing.
```

Why: forces full-context reading before any plan exists, surfaces spec bugs while they're cheap, and audits the one pre-built component before anything depends on it.

## Session 1 — plan Phase 1

Paste the following prompt to plan Phase 1 with the `writing-plans` skill:

```
Planning context: brainstorming is already done. The validated design lives in
specs/ — treat it as decided. If any part of the spec seems wrong or ambiguous
while planning, raise it as a question; never silently redesign.

Use your writing-plans skill to produce plans/phase-1.md for exactly this scope:
specs/acceptance.md §0 (test infrastructure) + Phase 1 (Execution Trader plumbing).
Nothing from later phases.

Requirements for the plan:
- Small tasks, each independently verifiable, each naming the files it touches
  and the acceptance.md checklist items it satisfies. Tests are written before
  the code they verify (your test-driven-development skill applies per task).
- The canonical schemas are specs/contracts.md — plan zero schema invention.
- Constraints that bind every task: the 7 invariants at the top of CLAUDE.md;
  offline-by-default tests; no datetime.now()/time.sleep() in business logic;
  minimal implementation — no abstractions or flexibility the spec doesn't require.
- Done for the whole plan = every Phase 1 checkbox in specs/acceptance.md passes
  via `make test`, plus the one @live smoke test run manually.

Stop after writing the plan. I will review it before execution.
```

## Session 2 — execute

Paste the following prompt to execute the plan with the `executing-plans` skill:

```
Execute plans/phase-1.md with your executing-plans skill. Review checkpoint after
each task. Per task: test-driven-development (RED-GREEN-REFACTOR), then
verification-before-completion — "done" means the named acceptance.md checkboxes
pass with `make test` output shown, not that code compiles. If a task can't meet
its acceptance criteria without deviating from specs/, stop and ask.
When the plan is complete: requesting-code-review, with special attention to
invariant violations (gate purity, idempotency, paper-only, tool-call-only outputs).
```

## Later phases

Later phases run the same loop, with two adjustments:

- **Phases 2–3** (desk, firm): tasks parallelize well (independent seats, charters, gate math vs. debate mechanics), so use `subagent-driven-development` instead of `executing-plans` at Session 2. Keep the same plan-first prompt shape, swapping the phase number and scope line.
- **Phase 5** (the lab): scope line becomes "integration only — fundbt/, stratgate/, and calibration/ are pre-built and tested; extend, don't rewrite" and the plan must start from the Session-0 starter-kit audit findings.

## Worktrees and branches

Fresh repo: work on `main` until Phase 1 lands, then one branch per phase with `finishing-a-development-branch` closing each. `using-git-worktrees` becomes worth it from Phase 2 on, when you may want a charter-tuning branch running alongside phase work.

## Principles behind these prompts

These prompts share the following principles:

- Exact files are named, and the reading order is specified.
- Scope is fenced to one phase, with an explicit "nothing from later phases".
- Done-criteria are checkable and external (`acceptance.md`), never "when it works".
- Constraints are stated as invariants that cannot change.
- The plan is externalized to `plans/phase-N.md`, so any future session can resume it.
- An anti-over-engineering clause keeps the implementation spec-minimal.
- A review gate sits between plan and execution.
- Deviations surface as questions, not silent fixes.
