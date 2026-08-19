# Improvement Loops — continuous agent improvement (AMENDED)

Status: amended 2026-08-18 after a grilling pass against
`research/field-brief-agent-improvement-loops.md`. Sections marked **buildable**
are ready to spec; sections marked **blocked** name the trigger that unblocks
them. Extends `specs/calibration.md` (which built the first improvement loop) to
every improvable surface in the system. Promote the buildable half to
`specs/improvement.md` when accepted; leave the blocked half here.

The design question: the fund's agents run daily forever — what makes them
*better* next month than this month, and what stops "improvement" from becoming
self-modification or prompt p-hacking? Answer: every improvement flows through
the same shape as `specs/strategy.md` — hypothesize (LLM), validate (code),
deploy (human or deterministic rule), monitor, kill. LLMs propose; code and
humans decide. Nothing here weakens that.

What the amendment changed: the loop now starts from **recorded traces and a
deterministic daily scorecard** rather than from charter theory, three surfaces
were demoted to blocked-until-evidence, and the reflection mechanism was
inverted — facts are computed, the seat only interprets.

**Provenance markers.** `[S]` = backed by an outside source (see the field
brief's claims log). `[B]` = Benjamin's decision, made against a stated
alternative. `[C]` = Claude's inference, unsourced — challenge freely.

---

## 0. Invariants — bind every loop below

1. **Improvement claims are measured by `calibration/` code, never
   self-reported.** An agent saying it improved is a claim; a shrunk-BSS delta
   over ≥50 graded calls is evidence (calibration §4 minimums apply).
2. **Every experiment is pre-registered and logged.** Charter trials get a
   registry like `fundbt/`'s trial registry. Iterating prompts until the
   scoreboard looks good is the same disease as re-running backtests until
   Sharpe looks good; the correction is the same: count the trials.
   `[S]` This is now measured, not feared: across 18 models, 37 annotation
   tasks and 2,361 hypotheses, prompt-variant selection produced incorrect
   conclusions in ~31% of hypotheses for frontier models and ~50% for smaller
   ones, and paraphrasing alone can make virtually anything look significant.
3. **Charters, configs, and thresholds change only by human commit** (extends
   CLAUDE.md invariant 3). No agent ever edits its own charter, any charter,
   `agents/config/*.yaml`, or anything under `gate/`/`stratgate/`. Improvement
   loops emit *proposals with evidence*, as PR-shaped artifacts.
4. **Journals are append-only** (via `state/journal.py` only). Distillation
   writes new artifacts; it never rewrites history.
5. **Attribution before iteration.** Every `signals` and `decisions` row
   records `charter_version` and `model_id` (contracts change, §8 below).
   Score deltas that can't be attributed to a version are noise.
6. **One change per seat per incubation window.** Two simultaneous charter
   changes on one seat are unattributable. Same rule as one-item-per-loop.
7. **Default is no-change.** An eval that errors, times out, or is ambiguous
   resolves to keeping the incumbent charter/config — never to shipping the
   candidate.
8. **NEW — a human reads the traces before an agent categorizes them.**
   `[S]` The practitioner consensus draws the automation line in a specific
   place: never automate open coding of raw traces, taxonomy validation,
   ground-truth labeling, or root-cause analysis; automate first-pass
   categorization *after* a human has coded a batch, mapping annotations onto
   known failure modes, and pattern analysis over already-labeled data. The
   stated reason is that reading traces is how the operator acquires the
   intuition the automation exists to scale. One named owner — a domain expert,
   not a committee — owns the failure taxonomy.
9. **NEW — the factual half of any reflection is computed, never narrated.**
   `[S]` A re-analysis of Reflexion's own logs (134 ALFWorld environments, 15
   trials) found 32% of environments developed frozen reflective memory in
   which *zero of 121* stored reflections named the correct target; frozen
   environments took 7.6 trials to solve versus 1.5. The mechanism is an
   information vacuum: given a coarse outcome signal and no step-level detail,
   the model emits a plausible, causally wrong diagnosis that then persists
   *because* it reads as credible. Programmatic extraction of failure signals
   beat prompting for evidence-grounded reflection, 86% correct versus ~0%.

## 1. The improvement surfaces

| # | Surface | Loop | Safety class | Status |
|---|---|---|---|---|
| S0 | Daily process quality | deterministic scorecard over the day's rows → ranked list of turns worth reading | A | **buildable** — new |
| S1 | Analyst → PM weights | scoreboard → deterministic weights | A | built (`calibration/`) |
| S2 | Agent memory | computed factual frame + seat interpretation; weekly distillation | B | **buildable** — revised |
| S7 | Regression ratchet | flagged live failure → hand-written `evals/` case → runs in `make test` forever | A (promotion is human) | **buildable** — new |
| S3 | Charters (system prompts) | evidence → pre-registration → paired offline eval → human commit → incubation → keep/revert | C | **blocked** |
| S4 | Strategy portfolio | G1–G4 + kill rules + discovery cadence | A/C mixed | spec'd (`specs/strategy.md`); heartbeat blocked |
| S5 | Harness & orchestration | weekly trace analysis → config/stage proposals | C | **blocked** |
| S6 | Model/budget allocation | skill-per-dollar review → config proposal | C | **blocked** |

Safety classes:
- **A — autonomous in code.** Outcome data → pure Python → behavior change. No
  LLM anywhere in the decision. This is the calibration pattern; it is the only
  class allowed to act without a human.
- **B — autonomous with audit.** An agent changes only *its own future
  context*, append-only, through `state/journal.py`, with the diff visible in
  Slack. Cannot touch rules, tools, or other seats.
- **C — human-gated.** LLM analysis produces a proposal + evidence pack; an
  offline eval scores it; a human merges it. The loop automates everything
  except the authority.

Everything marked **blocked** carries one trigger: *a failure taxonomy exists,
derived by hand from ≥100 live traces.* `[S]` The number and the stopping rule
(keep reading until ~20 consecutive traces reveal nothing new) are the sourced
criterion; at 3–5 tickers/day across the analyst, PM, critic and exec seats the
fund generates roughly 10–25 traces a trading day, so 100 traces is one to two
weeks of live running. `[B]` Counting to 100 wins over stopping at saturation
alone, which is how you talk yourself into stopping at 30.

## 2. Prerequisites — these land before any loop

**P1 — live runs persist traces.** `[S]` Every improvement loop found in the
wild runs on traces: production traces → classified or scored → flagged to a
human review queue → promoted into a regression dataset → run in CI on future
changes. The fund currently records none. `evals/trace.py` already defines the
format and `evals/grade.py` is a pure function of `(Trace, Case)` — its design
note that "a NEW invariant retroactively covers every trace ever recorded" is
worth nothing without a corpus. `scripts/run_day.py` writes one `Trace` per
seat turn, every seat including the Execution Trader: that seat's turn is where
a charter regression becomes an order, so it is the last one to leave blind.

Storage posture: traces live under `FUND_TRACES` beside the DB and journals,
inside `/var/lib/fund` (`750 fund:fund`), the same trust zone as `fund.sqlite`
— which already stores orders, tickets, and fills. Secrets stay in `/etc/fund`
(`700`, env file `0600`), a zone traces never enter.

`ops/backup.sh` enumerates its targets explicitly, so traces must be added to
it or they are born unbacked and never reach the Mac. **No retention policy.**
Measured, not estimated: real traces are ~6.5 KB each and the existing 131 total
1.0 MB, so 10–25 turns/day is 65–160 KB/day — **16–40 MB a year**, the same
order as the DB snapshots the no-prune argument was written for. `backup.sh`'s
reasoning holds unchanged, and the deployment still contains no destructive
operation.

**P2 — attribution columns.** `signals` and `decisions` gain
`charter_version TEXT NOT NULL` and `model_id TEXT NOT NULL`. Charters are
already versioned (`# Portfolio Manager — v6`, changelog per
`charters/_template.md`), so the value is real going forward; historical rows
span versions with no record of which, so they backfill to `unknown` rather
than to a constant.

Three values, never NULL: a real version (a seat wrote it under that charter),
`none` (the orchestrator wrote it because a seat was silent — no charter
produced it), `unknown` (predates attribution). `[B]` **Rows with `none` or
`unknown` are excluded from every charter comparison.** A defaulted row
measures the seat's *reliability*, not the charter's *judgment*; folding it in
would penalize a good charter for an SDK timeout. Seat reliability is S0's
severity-0 line, which is where it belongs. NOT NULL is deliberate — a NULL
drops silently out of a GROUP BY, making the exclusion an accident instead of a
decision. The `none` population grows with seat count as the defaulted-row
guarantee moves from per-ticker to per-seat. Lands as its own change with its own tests —
`specs/contracts.md` first, since it is canonical for the DDL.

`[B]` **P1 goes first.** Both lose history every day they are missing, but a
trace is unreconstructable while attribution is at least correct from the
migration onward.

## 3. S0 — the daily scorecard (Class A, buildable)

The daily loop the fund actually needs, and the one the original draft did not
have: its cadence table gave daily nothing but reflections, while everything
with teeth was weekly or monthly.

**Its job is to rank which turns you read.** `[S]` This is the correction that
matters. Generic metrics "waste time and create false confidence"; their one
legitimate use is finding the traces worth reviewing. So the scorecard succeeds
when it surfaces the turn that was actually bad, and fails when the number is
green and the day was not — not the other way round.

Every input is already in SQLite and needs no LLM: `critiques.note`
(`critic_timeout`), `decisions.status` (rejected / held / failed / expired),
`tickets.reason`, `events(kind='gate_rejected')` payloads, `pm_timeout` alerts,
`costs` per seat per day, `checkpoints.updated_at` for stage latency, and
coverage (tickers researched versus decisions produced).

`scripts/score_day.py` — a new sibling to `audit_day.py`, same zero-dependency
argv-driven rule. `[B]` Not an extension of `audit_day.py`: that script's
docstring is a tight argument about one thing — what a clean day means — and
its exit-1 contract is wired into `run_day.py` and the systemd failure path. A
scorecard that never fails the day does not belong behind that contract. Not in
`calibration/` either: that package is analyst scoring feeding PM weights, and
widening it to process metrics blurs a boundary CI enforces.

**Ranking is a fixed severity order, not a weighted score.** `[B]` Seats that
defaulted and malformed submissions first, then gate rejects, then statistical
outliers (confidence far from the seat's own recent mean, cost outliers, stage
latency outliers). Not resolutions invalidated early — `invalidated` is a
constant 0 today, so ranking on it would silently rank on nothing. A weighted score is a number
you would start tuning — a scoreboard you can p-hack without a single LLM call.
Fixed order is auditable and introduces no threshold the gate does not already
own.

**Projection.** Its own outbox event, appended at the end of the run and
drained on the normal path. `[C]` Not attached to the 16:35 P&L post:
`scripts/close_pnl.py` has three paths that log and exit 0 posting nothing
(SPY's last bar is not today's, equity unusable, already posted), so
piggybacking would make the scorecard vanish silently on exactly the abnormal
days worth reading — and its absence would look like a quiet day rather than a
skipped job.

No LLM narrative on top, yet. It is added only once there is a taxonomy for it
to speak in.

## 4. S2 — memory: computed frames, then interpretation (Class B, buildable)

Half-built as of 2026-08-18. `orchestrator/resolve.py` + `scripts/resolve_day.py`
now write `resolutions` at horizon, riding `fund-pnl.service`'s 16:35 fire as a
second ExecStart; `reflection` is left NULL on purpose — "it is an agent write,
and this job holds no LLM." This section is that follow-on. The rest of
design.md §3 Nightly stands: reflections → journals, injected next morning as
"recent record + lessons." Two changes and one addition.

**The factual frame is computed.** `[B]` Invariant 9 applied literally: remove
the vacuum rather than ask the model to fill it honestly. A join over
`resolutions` + `signals` renders what the seat predicted, at what confidence,
and what actually happened (`realized_return`, `alpha_vs_spy`). It does **not**
report invalidation: `orchestrator/resolve.py` writes `invalidated` as a
constant 0 because neither invalidation signal the fund has — the broker's stop
leg, Ops' watch on the free-text condition — is readable from that job. A frame
that rendered it would state "not invalidated" as a fact on every row, which is
the exact failure invariant 9 exists to prevent. The field re-enters the frame
only when something actually writes it. The seat receives that frame and writes only interpretation
on top of it. `resolutions.reflection` stores **frame + prose concatenated**, so
the numbers survive next to the claim even when the claim is wrong — the
auditable-trail property achieved by storage rather than by asking the model to
cite its evidence.

`[C]` Transfer caveat, stated honestly: the 0%-versus-86% result came from
ALFWorld with binary pass/fail feedback, and the whole mechanism is the vacuum
that binary feedback creates. The fund's resolutions carry continuous returns,
alpha, and an invalidation flag — a richer signal, so the effect should be
weaker here. The change still costs nothing, which is why it stands.

**Weekly distillation.** Raw journals grow monotonically; injected context must
not (the context-rot failure mode). Weekly, per seat, a fresh distiller agent
(not the seat itself — it will flatter its own record) reads the seat's journal
+ scoreboard slice and regenerates `journals/<seat>.lessons.md`: ≤40 lines,
each lesson tagged with the resolutions that support it. The lessons file — not
the raw journal — is what the morning session injects. Raw journal remains the
append-only audit log.

**Lesson grading (later).** A lesson is testable: calls made after its first
appearance, on situations it covers, versus before. Lessons whose adoption
coincides with a worse Brier get dropped at the next distillation with a note.
Do not build before the simpler loop has data.

Failure semantics: distiller crash or malformed output → last good lessons file
stands (never inject a partial); lessons file exceeding the line cap → truncated
oldest-first + `#risk` line.

## 5. S7 — the regression ratchet (Class A, buildable)

`[S]` The one loop both the practitioner and vendor camps agree on, and the
original draft omitted entirely: a flagged production failure becomes a
permanent test case, so every regression caught once is caught forever after.

The fund needs no new machinery for it. `evals/cases`, `evals/invariants`, and
a grader that runs offline in `make test` already exist; promotion is a human
writing a case file against a recorded trace.

**Promotion is manual.** `[B]` Automating it is where the agent starts
inventing the failure taxonomy — invariant 8. **Eligibility: only failures
reproducible from the recorded inputs**, where the trace plus fixtures fully
determine the wrong output. `[B]` This matches what `evals/grade.py` already
assumes (it reads traces and runs nothing), so a case whose failure depends on
unrecorded state cannot be graded honestly anyway. Deliberately *not* gated on
a failure recurring twice: that would mean letting the first instance of every
failure class through by design.

## 6. S3 — charters (Class C, blocked)

**Idea.** The scoreboard was designed as "the feedback loop for tuning
charters" (design.md §4). The lifecycle mirrors strategy.md: IDEA → SPEC →
EVAL → DEPLOY → INCUBATE → KEEP/REVERT. Evidence comes from the Murphy
decomposition (calibration §3) plus qualitative signals; a charter trial is
pre-registered before any eval with its metric, direction, horizon and
invalidation; the candidate is scored offline; a human merges; the new version
incubates live; a pre-registered invalidation auto-reverts.

**Blocked until:** a failure taxonomy exists from ≥100 live traces. `[B]` The
original CH-1…CH-5 detail was written before the fund had a single live trace,
and its triggers ("reliability term high → add confidence-language anchors")
are hypotheses about failure modes rather than observations of them — precisely
what the error-analysis pass exists to replace. The detail is not preserved
here; it is rewritten when the trigger fires.

Two things settled during review that survive the rewrite:

- **`make replay` cannot evaluate a charter.** Recordings capture decisions,
  not prompts, so the candidate prompt is never exercised. Charter evals need
  real LLM calls against frozen historical fixture windows with known
  resolutions, incumbent and candidate on identical data. `[S]` Paired
  evaluation on identical inputs is the design both the academic and vendor
  sources endorse; the eval remains a screen, not proof, since fixture windows
  are in-sample by construction.
- **Live incubation stays as promotion evidence.** `[B]` Decided against the
  alternative of demoting it to a revert-only guardrail. `[C]` The objection
  was that one seat runs one charter at a time, so incumbent and candidate
  samples come from different calendar periods and market regime sits in the
  measured delta — an unsourced argument, nothing trading-specific was
  researched. **Mitigation adopted:** seats run concurrently on the same
  tickers, so the seats whose charters did *not* change are a control living
  through the identical regime. Compare the changed seat's BSS delta against
  the unchanged seats' delta over the same window — difference-in-differences —
  rather than against its own past. Costs nothing beyond P2's
  `charter_version`.

## 7. S4 — strategy portfolio (spec'd; heartbeat blocked)

Gates and kill rules are built/spec'd in `specs/strategy.md`; what is missing is
the automation that keeps hypotheses flowing. Weekly (not nightly — trial
budgets are the scarce resource, strategy.md §8: most proposals *should* die at
G2), the Quant seat gets a scheduled discovery turn: review family menu + decay
scoreboard + post-mortems, spend at most the configured trial budget on the
single most promising SPEC. Post-mortems from kills (written by a non-proposing
seat) land in journals, closing the loop into S2. All existing gates unchanged
— this adds cadence, not authority. Blocked on the same trigger as S3.

## 8. S5 and S6 (Class C, blocked)

**S5 — harness hill-climb.** A weekly analysis agent over SQLite only
(`events`, `costs`, stage timings, timeout and malformed counters) emitting at
most three PR-shaped proposals a week to `#ops`: stage-length or budget changes
on a rising `pm_timeout`, output-contract wording fixes on a malformed-signal
trend, debate-round config changes when threads hit reply caps without
converging. Never touches gate math. **Blocked until** the taxonomy exists —
otherwise the agent is inventing the failure categories and the fixes in one
step, unsupervised. S0's scorecard is the deterministic subset of this that
ships now.

**S6 — model/budget allocation.** Monthly, join `costs` × scoreboard → skill
per dollar per seat. Proposals only: upgrade a fast-tier seat whose resolution
term is strong but reliability noisy; *don't* upgrade a seat with resolution
≈ 0 — calibration §3 says that seat adds nothing at any model tier, so narrow
or retire it instead. **Blocked until** ≥1 full scoreboard cycle of live data
exists to join against.

`[C]` Noting the one place the field is split rather than settled: vendor
tooling ships daily automated trace classification over every trace with 1–10%
scorer sampling, and would consider invariant 8 over-cautious. That posture is
aimed at teams whose trace volume a human could not read. At 3–5 tickers/day,
this fund can read everything, so the practitioner line wins here — and stops
winning if call volume grows an order of magnitude.

## 9. Contract changes required (human edits)

1. **P2, lands alone and first among schema work:** `signals` + `decisions` add
   `charter_version TEXT NOT NULL`, `model_id TEXT NOT NULL`. Historical rows
   backfill to `unknown`.
2. **P1:** `FUND_TRACES` env var, trace write in `scripts/run_day.py`, traces
   added to `ops/backup.sh`'s tarball. No retention policy.
3. New table `charter_trials` (mirrors `fundbt` registry): seat, versions,
   diff hash, pre-registered metric + direction + horizon + invalidation, eval
   delta, N, status (proposed/eval/deployed/kept/reverted), timestamps from
   injected Clock. **Deferred with S3.**
4. New table (or events reuse) for distillation runs: seat, input row count,
   lessons hash, timestamp.
5. Makefile: `score-day` target. `eval-charter` deferred with S3.

## 10. Cadences

| When | Loop | Class |
|---|---|---|
| Daily | S0 scorecard → ranked turns worth reading, posted to Slack · resolutions → computed frame + reflection (S2) | A/B |
| Weekly | scoreboard → PM weights (S1) · lesson distillation (S2) · decay scoreboard (S4) | A/B |
| Every 2–4 weeks | `[S]` comprehensive error-analysis pass over 100+ fresh traces; 10–20 traces weekly on outliers between passes; always after an incident, a usage spike, or a metric change | human |
| Monthly | charter evidence scan + ≤1 proposal/seat (S3) · sleeve rebalance (S4) · skill-per-dollar (S6) · reliability curves to Slack | A/C |
| Quarterly | seat retirement review (resolution ≈ 0 seats) · family menu review vs decay priors | C (human) |

`[S]` The error-analysis cadence numbers replace the original draft's guesses.
`[B]` The daily row is what this amendment exists to add.

## 11. Acceptance criteria — buildable half only

- [ ] `run_day.py` writes one `Trace` per seat turn, every seat including exec,
      under `FUND_TRACES`; a sim-day produces a readable corpus and
      `evals/grade.py` scores it unchanged.
- [ ] `ops/backup.sh` includes traces; a restore test recovers them.
- [ ] `signals`/`decisions` reject inserts without `charter_version`/`model_id`;
      sim-day records them end-to-end; historical rows read `unknown`.
- [ ] `scripts/score_day.py` runs zero-dependency against a live DB, emits a
      severity-ordered list, and **never** exits non-zero on a low score
      (that contract belongs to `audit_day.py` alone).
- [ ] Scorecard ranking is asserted on a doctored day: a `critic_timeout`, a
      gate reject, and a cost outlier appear in that order.
- [ ] Scorecard appends its own outbox event; a day where `close_pnl.py` posts
      nothing still produces a scorecard post.
- [ ] Distiller: given a fixture journal + scoreboard slice, produces a ≤40-line
      lessons file, each line citing ≥1 resolution id; malformed distiller
      output → previous file stands (assert byte-identical), `#risk` event row
      exists.
- [ ] Distiller never writes through any path except `state/journal.py`
      (purity-style AST check extended to the distiller job).
- [ ] `resolutions.reflection` contains the computed frame verbatim plus the
      seat's prose; a seat that writes nothing still leaves the frame.
- [ ] A promoted regression case fails against the trace that motivated it and
      passes after the fix, offline, in `make test`.

## 12. Sequencing

P1 traces → P2 attribution → S0 scorecard → S7 ratchet → S2 frame → *(≥100
traces read by hand, taxonomy written)* → unblock S3/S5/S6.

## 13. What is deliberately NOT in this design

- Agents editing any prompt, config, or threshold autonomously — Class C
  exists because a self-modifying agent can remove its own rules.
- Automated promotion of failures into the eval suite (invariant 8).
- An LLM narrative layer over the daily scorecard, until a taxonomy exists for
  it to speak in.
- RL / fine-tuning from outcomes: wrong regime at ~250 resolutions/year/seat;
  revisit only if call volume grows 100×.
- Embedding-based situation-similarity journal retrieval: already deferred to
  Phase 4+ by design.md §4; distillation comes first because it reduces
  injected tokens instead of adding machinery.
- Autonomous seat creation/retirement: quarterly human review gets the
  evidence pack; the org chart is capital allocation, and that's the CEO's.

## Appendix — sources

Outside evidence, with per-claim status and coverage edges:
`research/field-brief-agent-improvement-loops.md`. The load-bearing sources are
Husain & Shankar's evals FAQ (the automation line, error-analysis cadence, the
warning about generic metrics), the LLM-hacking study (the measured base rate
behind invariant 2), the *Honest Lying* confabulation re-analysis (invariant 9),
*When Generic Prompt Improvements Hurt* (eval overfitting), and Braintrust's
continuous-evaluation write-up (the traces → review → regression loop).

Internal machinery unchanged: `specs/calibration.md` §4 (minimums, N_eff) and
`specs/strategy.md` §5 (the deflated-Sharpe logic that motivates counting
prompt trials).

Original framing sources retained from the first draft: LangChain "The Art of
Loop Engineering" (hill-climbing over traces = S5); Osmani "Loop Engineering"
(maker/checker split, memory outside context = S2); Huntley "Ralph Wiggum"
(anti-verification-theater). `[C]` These are blog posts and were the whole of
the first draft's evidence base; the field brief supersedes them where they
conflict.

**Not researched:** anything trading-specific. Outcome-labelled decisions that
resolve on a horizon differ from annotation and rubric tasks, and no source
here speaks to that. The difference-in-differences mitigation in §6 and the
regime objection it answers are both `[C]`.
