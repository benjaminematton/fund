# G1 alignment gate — design

**Date:** 2026-08-18 · **Status:** approved, not implemented
**Evidence:** `research/strategy-research-addendum-2026-08.md` §1

## Problem

`specs/strategy.md` §2 requires a registered spec to state a **hypothesis** (the economic mechanism) and a **signal rule** (deterministic, coded). Nothing verifies the rule implements the hypothesis.

G2 is purely statistical. The first and only mechanism review is the Risk Officer's hostile review at G3 — the same gate that spends the one-shot holdout. So a spec whose code silently drifted from its stated mechanism burns its holdout before anyone checks, and invariant 6 forbids a re-roll. The holdout evidence is destroyed permanently for a spec that was invalid by construction.

This is the failure AlphaAgent's hypothesis-alignment term targets: an agent states a mechanism, codes something else, and the backtest still looks fine.

A second, smaller defect: `specs/design.md` §2 assigns the Critic to "review strategy specs at G1," but `specs/strategy.md` §2 and §7 never mention it, and the Critic is defined as advisory and never blocking. The one seat nominally doing this job has no authority and appears in no strategy-pipeline table.

## Decisions

### Deterministic gate, LLM verdict as input

The check is semantic, but invariant 3 forbids LLM code in `stratgate/` (CI-enforced by `scripts/check_purity.py`). Resolution: the Critic (in `agents/`) reviews and writes a verdict row; `stratgate.evaluate_g1()` is pure code that reads the row. The LLM's judgment is an input recorded in SQLite, never a branch inside gate code.

Rejected alternatives:
- **Structural checks only** (declared parameters, invalidation references an observable). Cannot catch a rule that cleanly implements the *wrong* mechanism, which is the defect found. Also partly redundant — §4.1 already rejects configs outside pre-declared ranges.
- **Both layered.** Structural checks carry the redundancy above for a gate with zero implementation today. Revisit once we have seen what gets past the semantic check.

### G1 failure → REJECTED, not bounce-for-revision

At G1 no backtest has run, so rejection costs nothing in trial budget — family N is untouched. Re-registering is one tool call with a lineage link, so "keep good ideas alive" does not argue for bouncing. Bouncing would break spec immutability (a stated invariant) and require mutable specs or a revision counter. It would also create an iterate-until-the-Critic-clears-you loop — p-hacking the alignment check instead of the backtest.

### The timeout default inverts

In the trade pipeline a missing critique defaults to `clear` (`specs/contracts.md` §6) — advisory, fails open, because a silent Critic must never stall the trading day. **At G1 the default is the opposite: no verdict means the spec does not advance**, per `specs/strategy.md` invariant 7. Same tool, opposite default, chosen by which pipeline it is in.

This inversion is the feature. Without it a Critic timeout silently waves through exactly the specs nobody reviewed, and the change is decorative.

## Dependency — the Critic seat does not exist yet

`charters/critic.md` is written, but there is no `agents/config/critic.yaml`, no runtime for the seat, and `evals/` supports PM only (`evals/seats/pm.yaml`, `evals/cases/pm/`). `orchestrator/daily.py:166` currently inserts default `clear` critiques with the note `no_critic_seat`, and `state/critiques.py` documents why: without them the critique-row guard in `submit_decision` would refuse every PM call.

So this design has a hard prerequisite. **Standing up the Critic seat is a separate plan and must land first**, carrying the alignment eval set (§ "The unvalidated assumption") as its acceptance criteria. The 80% gate is evaluated there, before any G1 code is written.

Note the same fail-open shape already exists in the trade pipeline: its critique guard is currently satisfied by rows the orchestrator writes to itself. That is a deliberate, labelled MVF placeholder rather than a bug — but it is the exact pattern G1's inverted default exists to avoid, and the Critic-seat plan should replace it with real critiques.

## Prerequisite result — 2026-08-20: NOT MET

**This design does not ship.** The prerequisite gate above was run and did not
produce a measurement: the charter under test was written as an index of its own
eval set, so the seat was matching a list rather than reasoning from the spec,
and detection under the corrected charter was never observed on a single
misaligned case. 37 live trials across three rounds yielded 6 uncontaminated
ones, all on aligned specs, of which one archetype held and one false-alarmed.
The holdout is unspent. Full ledger, the two rig bugs that cost the other 31
trials, and the reason a re-run does not fix the charter/case coupling:
[2026-08-18-critic-g1-alignment-result.md](2026-08-18-critic-g1-alignment-result.md).

A fourth cause was raised and **retracted**: the `model_fallback_used` event on
all 37 trials is a false positive, and the seat did run on the configured
Sonnet 5. What it exposed instead is an instrumentation bug that belongs to
`agents/runtime.py`, not to G1: the event now fires on every turn of every
Sonnet-configured seat, which in production is the PM.

Separately, the 80% target above and the plan's 8-of-9 threshold are not the
same number — 8/9 is 88.9%, and a seat performing exactly at 80% clears it only
43.6% of the time on a one-shot holdout. Reconciling those is a prerequisite of
the prerequisite.

## Architecture

No new lifecycle state and no new transition. `SPEC → BACKTEST` gains a precondition, and the existing `SPEC → REJECTED` transition gains two new triggers (`g1_no_review`, `g1_misaligned`) alongside G2 fail / budget exhausted / 30d idle.

**Enforcement point:** the `run_backtest` wrapper, which already owns the "agent cannot override" checks in `specs/strategy.md` §4 (registered spec required, cost floors, holdout quarantine, budget). G1 clearance joins that list as check 0. `transition()` remains a pure CAS helper and is not the enforcement point.

### Components

| Component | Location | Responsibility |
|---|---|---|
| `strategy_critiques` table | `state/` DDL | `spec_id`, `verdict` (`clear` \| `objections`), `objections` JSON (≤3, one sentence each), `seat`, `created_at` |
| `submit_spec_critique` | `agents/tools/` | Strict MCP tool, Critic seat only. Same contract style as `submit_critique` |
| `stratgate.evaluate_g1(critique) -> Verdict` | `stratgate/gate.py` | Pure. `None` → REJECT `g1_no_review`; `objections` → REJECT `g1_misaligned`; `clear` → PASS |
| G1 review stage | `orchestrator/` | Assigns the Critic a turn when a spec enters SPEC. **On deadline, inserts nothing.** |

### Data flow

```
Quant: submit_strategy_spec  →  state SPEC
orchestrator                 →  assigns Critic a G1 turn
Critic: submit_spec_critique →  strategy_critiques row
run_backtest wrapper         →  evaluate_g1(row)   [check 0, before spec/budget checks]
                                 PASS   → transition SPEC → BACKTEST
                                 REJECT → transition SPEC → REJECTED(reason)
```

### Error handling

Every failure resolves to *not advancing*: missing row, malformed payload, Critic crash, orchestrator restart mid-stage. The orchestrator must not write a default row under any circumstance.

## The unvalidated assumption

**No published source establishes that an LLM reviewer reliably catches mechanism-vs-rule misalignment** (addendum, gap 3). The entire gate rests on it. If the Critic cannot do this job, the gate adds latency and a false sense of rigour while blocking nothing real.

Therefore the eval set is built and run **before** the gate code, not after. Hand-written cases from the F1–F5 families, including deliberately misaligned specs where the code is correct but implements a different mechanism than stated. Below `prompt-engineer`'s declared 80% threshold, find failure patterns before iterating — and if it cannot be brought above threshold, this design does not ship.

## Testing

Red first, per `specs/acceptance.md`.

1. `evaluate_g1(None)` → REJECT `g1_no_review` — the named case that carries the whole change
2. `evaluate_g1(objections)` → REJECT `g1_misaligned`
3. `evaluate_g1(clear)` → PASS
4. `run_backtest` refuses a spec without a clear G1
5. `SPEC → BACKTEST` blocked without a clear G1; `SPEC → REJECTED` recorded with reason
6. Orchestrator deadline path writes no row
7. `scripts/check_purity.py` still clean
8. `make sim-day` — a deliberately misaligned spec is blocked end to end

## Spec changes (same commit — specs are canonical)

- `specs/strategy-contracts.md` §2 DDL, §3 tool contract, §4 state machine (new triggers on `SPEC → REJECTED` + precondition on `SPEC → BACKTEST`), §5 failure semantics
- `specs/strategy.md` §4 — G1 clearance added to the wrapper's enforced list
- `specs/strategy.md` §2 (G1 requirements), §7 (division of labor — add Critic)
- `specs/design.md` §2 seat table — Critic blocking in the strategy pipeline, advisory in the trade pipeline

## Out of scope

Structural G1 checks; changes to G2/G3/G4; any change to the trade pipeline's advisory-Critic behavior; alpha-wealth (addendum §2) — tracked separately and blocked on defining a backtest p-value.
