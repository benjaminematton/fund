# Improvement loops — the buildable half

Date: 2026-08-18. Source design: `research/improvement-loops.md` (amended).
Outside evidence: `research/field-brief-agent-improvement-loops.md`.

## Problem Statement

The fund runs a full trading day, every day, and then nobody can say whether it
ran *well*. `scripts/audit_day.py` answers one binary question — did every stage
complete and is the outbox drained — and `scripts/close_pnl.py` answers a second
— did the book make money. Neither says anything about the quality of the
judgment in between: which seat defaulted to a timeout, which decision the gate
rejected and why, which analyst's confidence was wildly out of line with its own
recent history, which turn was worth reading.

Worse, the raw material for answering that is being destroyed daily. Every seat
turn is a prompt, a set of tool calls, and a response; none of it is written
anywhere. The eval harness has a trace format and a grader designed so that a
new invariant retroactively covers every trace ever recorded — and there is no
corpus for it to cover, because production writes none. A month from now the
operator will want to look at how the News/Sentiment analyst behaved in the
first week of live running, and that week will not exist.

The same gap blocks every tuning loop downstream. `signals` and `decisions`
carry no `charter_version` and no `model_id`, so even once the scoreboard has
data, a score delta cannot be attributed to the charter that produced it.
Charters are already versioned in their headers — Portfolio Manager is at v6 —
and none of that history is recoverable from the database.

## Solution

Four pieces, none of which give an agent any new authority.

Live runs persist one trace per seat turn, so the corpus exists from the day
this lands. Those traces are backed up alongside the database and the journals.

A new daily scorecard reads the day's rows and produces a severity-ordered list
of the turns worth reading, posted to Slack through the outbox like everything
else. Its job is not to produce a number that goes green — it is to rank what
the operator opens first.

`signals` and `decisions` gain `charter_version` and `model_id`, so every graded
call is attributable from that point forward.

The deciding seat's reflection stops being a free narration of its own record. A
deterministic factual frame — what the seat predicted, at what confidence, and
what actually happened — is computed from `resolutions` and `signals` and given
to the seat, which writes only interpretation on top. Both halves are stored, so
the numbers survive next to the claim even when the claim is wrong.

Alongside these, a process rather than code: a live failure that is reproducible
from its recorded trace gets promoted by hand into a case in the eval suite,
where it runs offline in `make test` forever after.

## User Stories

1. As the CEO, I want every seat turn recorded as a trace, so that I can read
   what an agent actually did weeks after it did it.
2. As the CEO, I want the Execution Trader's turns traced too, so that the one
   seat where a bad decision becomes a real order is not the only blind spot.
3. As the CEO, I want traces written to the same storage zone as the database
   and journals, so that they inherit the deployment's existing permissions
   rather than inventing a new trust boundary.
4. As the CEO, I want traces included in the nightly backup, so that a lost
   droplet does not destroy the corpus the whole loop depends on.
5. As the CEO, I want no retention or pruning policy for traces, so that the
   deployment continues to contain no destructive operation.
6. As a developer running a simulated day, I want traces produced by the sim as
   well as by live runs, so that the trace path is exercised offline with no
   network and no keys.
7. As a developer writing tests, I want the trace sink injectable, so that tests
   collect traces in memory and never touch the filesystem.
8. As the CEO, I want a daily scorecard listing the turns worth reading, so that
   reviewing a day means opening five things rather than reading everything.
9. As the CEO, I want the scorecard ordered by a fixed severity rule, so that
   there is no weight for me to quietly tune until the day looks good.
10. As the CEO, I want seats that defaulted — a critic timeout, a PM timeout
    resolving to hold — ranked at the top, so that silent degradation is the
    first thing I see.
11. As the CEO, I want gate rejections listed with their reasons, so that I can
    tell a correctly-blocked bad decision from a gate that is mis-tuned.
12. As the CEO, I want decisions that failed or expired surfaced, so that
    execution problems are visible without reading the orders table.
13. As the CEO, I want a seat's confidence flagged when it departs sharply from
    that seat's own recent mean, so that a miscalibrated turn stands out.
14. As the CEO, I want per-seat cost outliers flagged, so that a seat burning
    budget is visible the same day rather than at the monthly review.
15. As the CEO, I want stage latency outliers flagged, so that a slow stage is
    caught before it starts missing its window.
16. As the CEO, I want coverage reported — tickers researched versus decisions
    produced — so that a silently narrowed day is visible.
17. As the CEO, I want the scorecard to run with no dependencies installed, so
    that it works against a live database on a host with nothing but Python.
18. As the CEO, I want the scorecard to never fail the day, so that the
    exit-code contract that drives the alert path stays owned by the audit
    alone.
19. As the CEO, I want the scorecard posted through the outbox as its own event,
    so that it arrives even on days when the P&L job correctly posts nothing.
20. As the CEO, I want the scorecard's Slack post to be a projection of rows the
    database already holds, so that invariant 6 is not weakened.
21. As the CEO, I want no LLM narrative over the scorecard yet, so that nothing
    invents a failure taxonomy before I have written one.
22. As the CEO, I want `charter_version` recorded on every signal and decision,
    so that a score delta can be attributed to the charter that produced it.
23. As the CEO, I want `model_id` recorded on every signal and decision, so that
    a model change is never mistaken for a charter effect.
24. As the CEO, I want historical rows to read `unknown` rather than a guessed
    version, so that the attribution gap is visible instead of fabricated.
25. As the CEO, I want a row the orchestrator wrote because a seat was silent
    to say so explicitly rather than borrow a charter version, so that silence
    is never counted as judgment.
25a. As the CEO, I want attribution to be non-nullable with three distinct
    values — a real version, `none`, and `unknown` — so that excluding
    defaulted rows from a charter comparison is a deliberate clause rather
    than an accident of NULL handling.
26. As a deciding seat, I want to receive a computed factual frame of my own
    call and its outcome, so that I am interpreting evidence rather than
    recalling it.
27. As the CEO, I want the frame to state what was predicted, at what
    confidence, and what actually happened, so that the seat cannot get the
    facts wrong even when it gets the lesson wrong.
28. As the CEO, I want the frame to omit invalidation entirely while
    `invalidated` is a constant zero, so that no reflection asserts a fact the
    system cannot know.
29. As the CEO, I want the frame and the seat's prose stored together, so that
    a later reader sees the numbers beside the claim without trusting the seat
    to have cited them.
30. As the CEO, I want a seat that writes nothing to still leave the frame
    behind, so that a silent turn produces a record rather than a blank.
31. As the CEO, I want the seat unable to write the factual half, so that the
    audit trail is a property of storage rather than of the model's compliance.
32. As the CEO, I want a live failure that is reproducible from its trace to
    become a permanent eval case, so that a regression caught once is caught
    forever.
33. As the CEO, I want promotion into the eval suite to be a human writing a
    case, so that no agent invents the failure taxonomy.
34. As the CEO, I want only failures fully determined by their recorded inputs
    to be eligible, so that no case fails for reasons the grader cannot see.
35. As the CEO, I want the first instance of a failure class to be eligible, so
    that a recurrence requirement does not let every new failure through once by
    design.
36. As a developer, I want promoted cases to run in the existing offline suite,
    so that the ratchet costs no new machinery.
37. As the CEO, I want none of this to give any seat a new tool or a new
    toolset, so that the seat table in the design doc stays accurate.
38. As the CEO, I want none of this to touch gate thresholds, so that invariant
    3 holds without an exception.

## Implementation Decisions

**One new seam, and only one.** The per-seat wrapper in the composition root is
the single point every seat's turn passes through, and it is the lowest point
that holds everything a trace needs — the seat's config (model, charter text),
the snapshot, and the brief tickers. It gains an optional trace-sink callable,
and calls a pure trace-building function that has no SDK import, no database,
and no filesystem. The live root passes a writer; tests pass an in-memory
collector; a sim day passes whichever the harness wants. Every seat is covered
by construction, including the Execution Trader, and no per-seat wiring exists
to drift.

Corrected during planning: the seat-turn helper one level down was the obvious
candidate and is the wrong one. It receives only the client, the prompt, and
the required servers — none of the seat, model, charter text, snapshot, or
brief tickers the trace type requires — so a sink there could emit only what
its caller already holds.

**Trace format is the existing one.** The eval harness's trace type and its
on-disk layout are reused unchanged, so the existing grader — a pure function of
a trace and a case, which runs nothing — scores live traces with no
modification. This is what makes a future invariant retroactive over the whole
corpus.

That type carries two fields that exist for the eval rig: a case name and a
trial number, meaning a named scenario run N times. A live turn has neither, so
a live trace names its case with a `live-` prefix on the run date and uses the
trial number as a per-day turn sequence. The prefix is deliberate: it keeps the
overload self-documenting and makes the live corpus separable by a prefix test.

**Deferred, deliberately.** The clean long-term shape is not this one. The
trace type conflates *what happened in a turn* — seat, model, charter, snapshot,
tool calls, cost, error — with *where it came from*, which is eval-rig
provenance. Splitting it into a turn payload plus a provenance discriminator is
what one would design today. It is not done here for two reasons: it collides
with in-flight work generalizing the eval rig for a second seat, and the shape
of the split is precisely what the first error-analysis pass over a real corpus
should decide. Designing that schema before reading a single live trace is the
same error as writing evaluators before error analysis, which the source
design's invariant 8 forbids. Revisit after that pass; the `live-` prefix makes
the migration a filter rather than a refactor.

**Trace storage.** A new environment variable names the trace root, defaulting
beside the database and journals inside the deployment's state directory, which
is group-restricted and owned by the service user. Secrets live in a separate
directory the traces never enter. The backup script enumerates its targets
explicitly, so traces are added to its tarball; measured trace size is ~6.5 KB,
which puts a year at tens of megabytes — the same order as the database
snapshots the no-prune argument was written for, so no retention policy is
added and the deployment keeps its property of containing no destructive
operation.

**The scorecard is a new sibling to the audit, not an extension of it.** The
audit script is a tight argument about one thing — what a clean day means — and
its non-zero exit is wired into the day runner and the systemd failure path. A
scorecard that never fails the day does not belong behind that contract. It
follows the same rules: standard library only, argv-driven, importable with
nothing on the path, so it runs against a live database on a bare host. It is
not placed in `calibration/`, which is analyst scoring feeding PM weights;
widening that package to process metrics blurs a boundary CI enforces.

**Ranking is a fixed severity order, not a weighted score.** In order: seats
that defaulted and malformed submissions; then gate rejections with reason;
then statistical outliers (confidence far from the seat's own recent mean, cost
outliers, stage latency outliers). Invalidation is deliberately not a ranking
input: the resolution job writes `invalidated` as a constant zero because
neither invalidation signal the fund has is readable from it, so ranking on that
column would silently rank on nothing. A weighted score would be a number the
operator starts tuning — a scoreboard that can be p-hacked with no LLM
involved.

**Scorecard inputs are all existing rows.** Critique notes, decision statuses,
ticket rejection reasons, gate-rejected events, timeout alerts, per-seat costs,
checkpoint timestamps for stage latency, and the count of researched tickers
against produced decisions. No new table.

**Scorecard projection.** Its own outbox event, appended at the end of the run
and drained on the normal path. Not attached to the post-close P&L job: that job
has three paths that log and exit zero posting nothing, so piggybacking would
make the scorecard vanish on exactly the abnormal days worth reading, and its
absence would read as a quiet day rather than a skipped job.

**Attribution columns.** `signals` and `decisions` each gain a non-null
`charter_version` and `model_id`. Charters already carry a version in their
header with a changelog, so the value is real going forward. Historical rows
backfill to `unknown` — they span versions with no record of which, and a
constant would be a fabrication. This migration lands on its own, with its own
tests, and the contracts spec changes in the same commit since it is canonical
for the DDL.

**The factual frame.** A pure function joining `resolutions` and `signals`
renders what the seat predicted, at what confidence, and what actually happened
(realized return, alpha versus SPY). It omits invalidation for the reason above,
and the field re-enters only when something actually writes it. The frame is
rendered into the seat's reflection prompt; the reflection column stores frame
and prose concatenated, so the numbers survive next to the claim. The seat
cannot write the factual half — that is a property of how the row is assembled,
not an instruction the model is asked to follow.

**The nightly resolution job already exists** and deliberately leaves the
reflection column null, naming the agent write as a follow-on. This spec is that
follow-on; it adds nothing to that job.

**The ratchet is a process, not a module.** Promotion is a human writing a case
in the existing eval suite against a recorded trace. Eligibility: the failure is
reproducible from recorded inputs alone. No automation, no recurrence
requirement.

**Sequencing.** Trace persistence first — every day without it is unrecoverable,
while attribution is at least correct from its migration onward. Then the
attribution migration, then the scorecard, then the ratchet, then the frame.

**Seat surface unchanged.** No seat gains a tool, a toolset, or a settings
source. The Execution Trader's locked tool surface is untouched.

## Testing Decisions

A good test here asserts external behavior only: what a trace contains, what
order the scorecard ranks in, what the database rejects, what a reflection row
holds. None of them assert how a function is structured internally, and none
reach into private state. Tests are the spec — a failing test means the
implementation is wrong, and no golden fixture or expected value is ever updated
to make one pass.

**Trace persistence** is tested through the injected sink, with no filesystem
involved: a simulated day produces one trace per seat turn, every seat present,
and the existing grader scores the resulting corpus unchanged. A separate test
covers the writer itself against a temporary root. Prior art: the recorded/
replayed day-shape simulations, which already drive real tools, gate, and
database against recorded decisions.

**The scorecard** is tested exactly as the audit is — loaded by module path,
called as a function, run against a doctored database. Prior art is the existing
audit test, which builds the golden-day simulation database and then damages it
one way at a time to assert each named violation appears. The scorecard's
version asserts ordering: a day carrying a critic timeout, a gate rejection, and
a cost outlier ranks them in that order. Two negative tests carry weight: the
scorecard never exits non-zero on a low score, and a day where the P&L job posts
nothing still produces a scorecard event.

**The attribution migration** is tested by rejection: an insert missing either
column fails, a simulated day records both end to end, and historical rows read
`unknown`.

**The factual frame** is tested as a pure function over fixture rows — the
rendered frame contains the prediction, the confidence, and the outcome, and
contains no invalidation claim. A reflection row written by a seat that produced
no prose still contains the frame. The golden-day fixture's T+5 vector is the
anchor, since the resolution job already passes against it.

**The ratchet** is tested by exercising it once: a promoted case fails against
the trace that motivated it and passes after the fix, offline, in the standard
suite.

Purity holds throughout: the lint that forbids LLM imports and wall-clock calls
in the deterministic packages must stay clean, and the scorecard and frame
introduce no clock call — time comes from the injected clock or from the row.

## Out of Scope

The three human-gated loops — charter tuning, harness hill-climbing, and
model/budget allocation — stay blocked in the research document behind one
trigger: a failure taxonomy derived by hand from at least a hundred live
traces. Nothing here builds an analysis agent, a charter trial registry, a
paired charter eval, or an incubation comparison.

Also out: any LLM narrative layer over the scorecard; automated promotion of
failures into the eval suite; weekly lesson distillation, which waits until the
frame has produced reflections worth distilling; lesson grading; and any change
to gate thresholds, seat toolsets, or the Execution Trader's tool surface.

The decision that live incubation remains promotion evidence for charter
changes — compared difference-in-differences against seats whose charters did
not change — is recorded in the research document and is not implemented here.

## Further Notes

The seat naming in any code written against this should follow the accepted
ADR: the second analyst is News/Sentiment, not Fundamentals.

This project tracks issues as GitHub issues rather than in Linear, so this spec
is not published through the delegation skill.

Two claims in the source design are the author's judgment rather than sourced
practice, and are flagged as such there: that live incubation can carry
promotion evidence given a difference-in-differences comparison, and the regime
objection it answers. Neither is implemented by this spec.

One finding worth carrying to whoever owns the strategy-alignment gate: an
80% single-accuracy threshold on a hand-authored eval set is the wrong
instrument for a reviewer whose value is catching a minority class. The
practice the research supports is reporting true-positive and true-negative
rates separately against human labels, on a held-out split that never informs
the charter being tested — otherwise iterating until the number clears is the
documented overfitting pattern.
