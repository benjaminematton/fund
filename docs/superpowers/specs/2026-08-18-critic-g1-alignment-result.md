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
carrying forward, and it points the wrong way. (It is a Sonnet 5 number — see
cause 4, which briefly suggested otherwise and is retracted.)

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

### 4. RETRACTED — the model was fine; the instrumentation is not

Raised in peer review and briefly recorded here as a fourth cause: all 37
trials emitted `model_fallback_used` naming `claude-haiku-4-5-20251001`, while
`agents/config/critic.yaml` sets `claude-sonnet-5` for both `model` and
`fallback_model`. Read as "every trial ran on Haiku", that would have
disqualified the runs on its own.

**It is a false positive. The seat ran on the configured Sonnet 5.** A live
probe printing the whole `model_usage` dict — the traces store only
`_unmatched_models`' output, the keys that *failed* to match, so no re-reading
of them could settle it:

```
claude-sonnet-5            2 in /   4 out / 5,347 cache-creation   $0.02012
claude-haiku-4-5-20251001  527 in /  13 out                        $0.00059
```

Sonnet carries the turn — the 5,347 cache-creation tokens are the charter as
system prompt — and is **97% of the cost**. Haiku handles one small auxiliary
call the SDK makes on its own.

**The real finding is a production bug, and it is not on this branch.**
`_unmatched_models` flags when *any* key fails to match, deliberately: its
docstring calls the quantifier "the point", because `any()` would stay silent
on a genuine mid-turn haiku-then-sonnet fallback. That was right when written.
It is now wrong for the world it runs in — the SDK routes an auxiliary Haiku
call on **every turn of every seat**, so:

- `model_fallback_used` fires on every seat turn, every day, on the droplet.
- [agents/runtime.py:301](../../../agents/runtime.py#L301)'s contract —
  *"model_id is trustworthy precisely when this event is absent"* — is now
  vacuous, because the event is never absent.
- `scripts/score_day.py` ranks it severity 3 on the daily scorecard,
  permanently.

Verified fleet-wide, not seat-specific: the same probe on the **PM** seat, whose
72 archived trials across `control`, `primary2`, `postfix2` and `postfix3`
emitted **zero** such events, now emits one identically. So this began between
2026-08-18 and 2026-08-20 and is an SDK/backend change, not a config drift.

Consequence for the record: round 1's 6/9-with-the-answer-key **is** a Sonnet 5
number, and the cost and turn observations below are Sonnet numbers. Causes 1–3
are untouched — they are still why there is no measurement.

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
  cost mean $0.0801, p95 $0.1247, max $0.1867 — Sonnet 5 numbers (cause 4).
  Not adopted as I5 ceilings: they were measured against a charter step 2
  expects to rewrite, and I5 re-scores every run on disk.
- **`model_fallback_used` is a false positive on every seat turn, fund-wide.**
  Not this branch's bug and not this branch's fix — the seam is
  `agents/runtime.py:_unmatched_models`, it affects the droplet's daily
  scorecard at severity 3, and it has silently voided the guarantee that
  `model_id` is trustworthy when the event is absent. Reproduced on both the
  Critic and the PM seat. Needs an owner.
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
