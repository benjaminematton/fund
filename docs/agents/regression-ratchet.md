# Promoting a live failure to a permanent eval case

A failure the fund has already had, and can have again, is worth more than a
failure someone imagined. The ratchet is the rule that a real one becomes a
permanent case: it happens once, and after that it is graded on every run
forever.

Promotion is a **human writing a case**. It is not automated and it is not an
agent's job, because deciding what class a failure belongs to *is* building
the failure taxonomy — and a taxonomy an agent invents from its own traces is
one nobody has checked against reality. A human reads the trace first.

## Which failures are eligible

**Only failures fully determined by their recorded inputs.**

`evals/grade.py` reads a `Trace` and a `Case` and runs nothing — no SDK, no
network, no DB. That is what makes a new invariant retroactively cover every
trace ever recorded, and it is also the constraint: a case whose failure
depends on state nobody wrote down cannot be graded honestly. If reproducing
the failure would need a broker reply, a clock position, or a Slack thread
that is not in the trace, it is not eligible. Write an issue instead.

**One instance is enough.** There is deliberately no "must recur twice" rule.
A recurrence bar sounds prudent and is not: it lets the first instance of
every failure class through by design, which is precisely the instance you
had the evidence for.

**Do not promote permitted behaviour.** Check the charter before deciding a
turn was wrong. `evals/metrics.py` exists for exactly this: a single vague
invalidation breaks no rule, because `charters/pm.md` §"Output contract"
permits leaving invalidation unset for non-price conditions. What is worth
watching there is the *rate*, and a rate is a metric, never an invariant.
Promoting a permitted behaviour to a hard case does not tighten the fund, it
writes a rule the charter does not contain.

## Where the failures come from

Two corpora, and they are not equally strong evidence.

- **Live traces** — `$FUND_TRACES` on the droplet, one JSON per seat turn,
  written by `evals/live.py` from a real trading day. These are the real
  thing: a live failure has already cost something.
- **Eval-suite traces** — `evals/traces/<run>/<sha>/<case>/<trial>.json`,
  from `make eval`. Real model turns against real charters, so a failure here
  is real too — but the run may be a deliberate charter ablation, in which
  case the "failure" is the experiment working. Check what the run was before
  promoting from it.

`evals/traces/recorded/` is neither. It is a hand-built fixture for testing
the grader (`report_cli.NOT_A_RUN` excludes it), and its I1 `oversize` verdict
is a planted one. Never promote from it.

## Doing it

1. **Read the trace.** Find one turn whose wrong output is fully determined by
   its recorded snapshot and prompt. Write down in one sentence what the seat
   should have done instead. If that sentence needs a fact the trace does not
   contain, stop — it is not eligible.

2. **Write the case.** `evals/cases/pm/r<nn>-<slug>.yaml`, in the shape
   `evals/cases.py:load_case` expects: `id`, `seat`, `clock` (timezone-aware),
   `tickers`, `snapshot`, `signals`, `journal`, `expect`, `notes`. Put the
   trace's snapshot in **verbatim** — paraphrasing it is how a case stops
   testing the failure it came from. `notes` must name the date and run of the
   live failure, so a future reader can find the original.

3. **Verify it fails against the trace that motivated it.** Grade the
   originating trace against the new case and confirm the invariant that
   describes the failure reports FAIL. A case that does not fail on the
   evidence it was written from is testing something else.

4. **Then either it is a guard or it is a task.** If the fix is already in,
   the case passes and is now a permanent guard. If it is not, the case stays
   red and the fix is its own piece of work. **Do not weaken the case to make
   it green** — `CLAUDE.md`'s red-test rule applies here exactly as it does in
   `make test`.

5. **Commit the case and the reasoning together.** The `notes` field and the
   commit message are the only record of why this case exists; a case whose
   motivation is lost is one a future reader will eventually delete as
   redundant.

## Grading the corpus

Free and offline — it re-scores recorded traces and never runs a turn:

```bash
make eval-report                       # every run
make eval-report RUN=<run>             # one run
make eval-report RUN=<run> BASELINE=<sha>   # diffed against a baseline
```

There is deliberately no "latest": runs are labelled by hand as well as by git
sha, and a merge rewrites every directory's mtime, so neither name order nor
mtime identifies the run you meant.
