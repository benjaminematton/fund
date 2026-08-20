# Critic G1 alignment gate — measured result

**Date:** 2026-08-20
**Branch:** `feat/g1-alignment-gate`
**Plan:** [2026-08-18-critic-seat.md](../plans/2026-08-18-critic-seat.md), Task 7
**Design:** [2026-08-18-g1-alignment-design.md](2026-08-18-g1-alignment-design.md)

## Verdict

**The gate was not measured and does not pass. G1 does not ship on this evidence, and the holdout has not been spent.**

The design's central assumption — that a Critic seat can tell a spec whose coded
rule implements its stated mechanism from one that does not, at ≥8/9 detection
and ≤1/9 false alarm — is **untested**. Not refuted: untested. Three rounds were
run, 37 live trials and $2.20 spent, and none of them measures the shipped
configuration.

Detection under the shipped charter (v3) was never observed at all. **Zero
misaligned cases have ever run against it.**

## Trial ledger

Every trial on disk, and what each one is evidence of. Committed at `9090bf8`.

| Run | Trials | Charter | Fresh | Measures |
|---|---|---|---|---|
| `d169f6d` | 1 | v2 | 1 | smoke — the path works end to end |
| `critic-v2-r1` | 18 | v2 | 17 | a charter that indexed its own eval set |
| `critic-v3-r2` | 18 | v3 | 6 | two aligned cases, `a04` and `h01` |

**The only uncontaminated evidence about the shipped design is 6 trials:**

- `a04` (expect `clear`, "incidental is not load-bearing"): **objected 3/3** — false alarm.
- `h01` (expect `clear`, "not the Critic's job"): **clear 3/3** — correct.

One aligned archetype held, one did not, and detection is unmeasured. That is
the entire yield.

## Why there is no valid measurement

Three independent causes, each sufficient on its own.

### 1. The charter was an index of its own eval set (round 1)

Charter v2's Judgment section listed a six-bullet attack taxonomy, and each
bullet named an archetype the case set tested. The seat was not reasoning from
the spec in front of it; it was matching against a list that had been handed to
it. Anything round 1 measured is an upper bound on a leaked rubric, not an
estimate of the seat.

Even so, round 1 **missed `m01` on all three trials** with the answer key in the
prompt, and false-alarmed on 6 of 9 aligned trials. That is the one number worth
carrying forward, and it points the wrong way — but see cause 4: it cannot be
attributed to a known model, so it is a warning sign rather than a measurement
of anything.

Fixed in `d110b1e` (charter v3: a 5-step method, no archetype list). Three cases
whose bait charter v2 had excluded by name were re-authored in `90832a5`.

### 2. Round 2 re-graded round 1 (rig bug, mine)

`run_trial` derived its work directory from a fixed path, so a second suite over
the same cases reopened the first suite's SQLite database. Consequences, in
order:

1. `get_spec_brief` found the spec already critiqued and served an empty queue.
2. The seat correctly did nothing — **12 of round 2's 18 trials made no
   `submit_spec_critique` call at all.**
3. `_rows` ran an unscoped `SELECT` and reported round 1's verdicts as round 2's
   output.

The chain starts earlier than round 2: `critic-v2-r1`'s `m01/t1` also made no
submission and reported the **smoke** trial's row.

A whole run was graded as fresh when the seat had submitted nothing. Nothing
caught it because every test and the offline dry run pass their own `tmp_path`;
only the real suite uses the shared default.

Fixed bluntly at `3969206` (wipe the trial dir). The intended fix — a
`MAX(rowid)` watermark, mirroring `state.events_watermark`, so "this trial's
rows" is true by construction rather than by directory hygiene — is specified in
the comment at [evals/runner.py:141-159](../../../evals/runner.py#L141-L159) and
**is not done**.

### 3. The gate passed on the numerator alone

`Gate.ok` checked `detection_hit >= MIN_DETECTION` and never looked at
`detection_n`. Traces accumulate under `<label>/<git_sha>/` and `grade_traces`
rglobs the tree, so re-running a label after any commit doubles every count —
and 8/18, a 44% detection rate, reported `GATE PASS`.

Found by a peer review session, reproduced here, fixed at `6e8a5a9` with a
`miscounted` check that requires every case to contribute exactly
`TRIALS_PER_CASE` trials. Pinned by
[tests/test_critic_gate.py](../../../tests/test_critic_gate.py).

### 4. The served model cannot be attributed to the configured one

**All 37 trials emitted `model_fallback_used`**, naming
`claude-haiku-4-5-20251001` as a model that appeared in the turn's
`model_usage` while `agents/config/critic.yaml` configures
`model: claude-sonnet-5` and `fallback_model: claude-sonnet-5`. Haiku is not
even the configured fallback.

The instrumentation at [agents/runtime.py:290-302](../../../agents/runtime.py#L290-L302)
worked exactly as designed — *"model_id is trustworthy precisely when this
event is absent"* — and nobody read the events. Each trace's own `model` field
reads `claude-sonnet-5` because it records what was configured, not what
served.

**What this does and does not establish.** `served` carries only the
*unmatched* keys, so the event cannot distinguish "Haiku served the turn" from
"Sonnet served the turn and Haiku appeared in `model_usage` alongside it" —
`_unmatched_models`' own docstring names the mixed haiku-then-sonnet turn as
the case it exists to catch. Two facts cut against the pure-Haiku reading:
cost per trial is **$0.0801 mean / $0.187 max**, above the PM seat's measured
Sonnet **$0.0453 / $0.1268**, which is not what Haiku 4.5 costs — though
`total_cost_usd` is a client-side estimate and therefore suggestive rather than
decisive.

It is also not ambient: **72 PM trials** across `control`, `primary2`,
`postfix2` and `postfix3`, on identical `model:` / `fallback_model:` lines,
emitted **zero** such events. Something changed between the PM runs
(2026-08-18) and the Critic runs (2026-08-19/20) — credentials, quota, or SDK
resolution.

So the honest statement is not "the seat was Haiku". It is that **the trials
cannot be attributed to the configured model**, which disqualifies them as a
measurement of the shipped seat on its own, before any of causes 1–3.

Correspondingly, round 1's 6/9-with-the-answer-key is **unattributable**, not
merely weak. It should not be read as "Sonnet scored 67% with the rubric in
its prompt", and equally should not be excused as "that was only Haiku" —
neither claim is supported.

**This is not confined to this branch.** Every seat runs the same seam, as
does the droplet's daily run. Raised by the peer review; resolving why Haiku
appears is not this branch's work and has no owner yet. The one clean test is
a single live turn printing `result.model_usage` in full — the traces store
only the unmatched subset.

## The structural problem — this is the one that matters

Causes 2 and 3 are ordinary bugs, now fixed. Cause 1 is not, and re-running does
not resolve it.

The case set is **one archetype per case**: each case exists to test one
recognisable species of hypothesis/rule misalignment. A charter that is
deployable must tell the seat what to look for. Any rubric specific enough to be
useful therefore names the archetypes the case set is built from — and the
coupling regenerates on the next rewrite. Charter v3 still carries five of the
six distinguishing nouns inside its method steps.

An attempt to lint the coupling automatically was written and **dropped**: run
against v2 it caught one incidental span and missed every real leak. Judging
whether a charter indexes its own eval set is a human read, not a check, and it
is recorded as such at `05649fb`.

**A measurement is only as good as the independence of charter and cases, and
that independence does not currently exist.** Spending another dev round buys a
number whose meaning is unknown.

## The gate arithmetic is stricter than the design

Worth checking by hand, because it needs no trust in anything above.

The design's stated target is a **80%** detection rate. The gate's threshold is
**8 of 9 = 88.9%**. For a seat performing exactly at the design's target:

| True detection rate | P(≥8 of 9) |
|---|---|
| 0.70 | **19.6%** |
| 0.80 | **43.6%** |
| 0.90 | **77.5%** |

A seat that exactly meets the design fails this gate more often than it passes —
on a holdout that is spent once and cannot be re-run. Either the threshold or
the design target is wrong, and that is a design decision, not a coding one.
`MIN_DETECTION` was **not** loosened to make anything pass; it stands at 8.

## What ships from this branch regardless

Tasks 1–6 are complete, reviewed, and green at **1,011 tests**. Nothing here
depends on the gate outcome, and none of it touches the trade pipeline
(`insert_default_critiques(..., "no_critic_seat")` is intact; the Critic has no
`submit_critique`).

- `evals/cases.py` generalised from tickers to subjects; 12 Critic cases, dev/holdout split
- `strategy_specs` + `strategy_critiques` DDL, `CREATE TABLE IF NOT EXISTS` throughout
- `get_spec_brief` + `submit_spec_critique`, attribution required not defaulted
- Critic seat config, charter v3, `#research` renderer
- Eval rig subject-generalisation, I3/I4 fixes, `seat_registry`
- `scripts/critic_gate.py` + boundary tests, `scripts/dry_run_critic.py`

## To get a real measurement

In order. Cost is measured, not estimated: **$0.080/trial mean, $0.187 max**
over the 23 fresh trials — about 4× the PM's mean, so budget ~$1.45 per
18-trial round.

1. **Decide the threshold against the design target.** 8/9 or 80% — not both.
2. **Break the charter/case coupling for real.** Either write cases whose
   misalignment is not archetype-shaped, or accept that the charter names
   archetypes and say so in the result rather than pretending to independence.
3. **Land the watermark fix** so a re-run cannot report a previous run's rows.
4. **Re-run the dev half** under a charter written without reference to the
   cases, and only then consider the holdout.
5. Only after all of the above: spend the holdout, once.

## Handed on, not done

- **`submit_spec_critique` lacks `strict=True`**, which
  `specs/strategy-contracts.md:216` declares. Not fixed here: **no tool in
  `agents/tools/fund_server.py` uses `strict=True`** — the schemas do carry
  `additionalProperties` (6 occurrences), so `strict` alone is the gap, and it
  is a server-wide decision rather than a one-liner in my tool. Raised by the
  peer review; left as a substantive change to a tool inside work recommended
  against shipping. The file has no owner on master.
- **`evals/seats/critic.yaml` ceilings stay PROVISIONAL** (10 turns / $0.75).
  Observed over the 23 fresh trials: turns mean 3.74, max 6 (3:13 4:5 5:3 6:2);
  cost mean $0.0801, p95 $0.1247, max $0.1867. Not adopted as I5 ceilings for
  two reasons, and the second is the stronger: they were measured against a
  charter step 2 expects to rewrite, and **against a turn whose served model is
  unknown** (cause 4). "Provisional" here means *possibly the wrong model*, not
  *small sample* — adopting them could under-provision the seat that actually
  ships. I5 re-scores every run on disk.
- **Every `strategy_critiques` row from these runs carries a `model_id` that
  may not name the model that served the turn.** Rows are never retro-edited by
  design; this is the enumeration that
  [agents/runtime.py:301](../../../agents/runtime.py#L301) asks for in place of
  hiding it.
- **Run labels** are `critic-v2-r1` / `critic-v3-r2`, which do not match the
  plan's `critic-v2-*` pooling glob. Anything pooling by that glob silently
  drops the v3 round.
- The `rmtree` at [evals/runner.py:162](../../../evals/runner.py#L162) is the
  blunt fix, not the intended one.

## Decisions this leaves open

These belong to Benjamin, not to this session and not to a reviewer:

1. Spend another dev round, or stop here.
2. Whether the holdout is ever spent.
3. Whether `feat/g1-alignment-gate` merges at all — Tasks 1–6 are green and
   independently useful, but merging them ships a seat whose gate is unmeasured.

**Recommendation: stop, and leave the holdout unspent.** A peer review session
reached the same conclusion independently; both of us have flagged that as a
concurring opinion rather than a replication — we are two instances of the same
model reading the same artifacts.
