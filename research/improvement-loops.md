# Improvement Loops — continuous agent improvement (PROPOSAL)

Status: draft for human review. Lives in `research/` until reviewed; promote to
`specs/improvement.md` when accepted. Extends `specs/calibration.md` (which
built the first improvement loop) to every improvable surface in the system.

The design question: the fund's agents run daily forever — what makes them
*better* next month than this month, and what stops "improvement" from becoming
self-modification or prompt p-hacking? Answer: every improvement flows through
the same shape as `specs/strategy.md` — hypothesize (LLM), validate (code),
deploy (human or deterministic rule), monitor, kill. LLMs propose; code and
humans decide. Nothing here weakens that.

---

## 0. Invariants — bind every loop below

1. **Improvement claims are measured by `calibration/` code, never
   self-reported.** An agent saying it improved is a claim; a shrunk-BSS delta
   over ≥50 graded calls is evidence (calibration §4 minimums apply).
2. **Every experiment is pre-registered and logged.** Charter trials get a
   registry like `fundbt/`'s trial registry. Iterating prompts until the
   scoreboard looks good is the same disease as re-running backtests until
   Sharpe looks good; the correction is the same: count the trials.
3. **Charters, configs, and thresholds change only by human commit** (extends
   CLAUDE.md invariant 3). No agent ever edits its own charter, any charter,
   `agents/config/*.yaml`, or anything under `gate/`/`stratgate/`. Improvement
   loops emit *proposals with evidence*, as PR-shaped artifacts.
4. **Journals are append-only** (via `state/journal.py` only). Distillation
   writes new artifacts; it never rewrites history.
5. **Attribution before iteration.** Every `signals` and `decisions` row
   records `charter_version` and `model_id` (contracts change, §5 below).
   Score deltas that can't be attributed to a version are noise.
6. **One change per seat per incubation window.** Two simultaneous charter
   changes on one seat are unattributable. Same rule as one-item-per-loop.
7. **Default is no-change.** An eval that errors, times out, or is ambiguous
   resolves to keeping the incumbent charter/config — never to shipping the
   candidate.

## 1. The improvement surfaces

| # | Surface | Loop | Safety class | Status |
|---|---|---|---|---|
| S1 | Analyst → PM weights | scoreboard → deterministic weights | A (autonomous, pure code) | built (`calibration/`) |
| S2 | Agent memory | nightly reflection + weekly lesson distillation | B (autonomous, append-only, audited) | reflection spec'd; distillation new |
| S3 | Charters (system prompts) | evidence → pre-registered change → offline eval → human commit → incubation → keep/revert | C (human-gated) | new |
| S4 | Strategy portfolio | G1–G4 + kill rules + discovery cadence | A/C mixed | spec'd (`specs/strategy.md`); heartbeat new |
| S5 | Harness & orchestration | weekly trace analysis → config/stage proposals | C (human-gated) | new |
| S6 | Model/budget allocation | skill-per-dollar review → config proposal | C (human-gated) | new |

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

## 2. S2 — memory: reflection + distillation

Already spec'd (design.md §3 Nightly): resolutions at horizon → deciding agents
write reflections → journals, injected next morning as "recent record +
lessons." Two additions:

**Weekly distillation.** Raw journals grow monotonically; injected context must
not (the context-rot failure mode). Weekly, per seat, a fresh distiller agent
(not the seat itself — it will flatter its own record) reads the seat's journal
+ scoreboard slice and regenerates `journals/<seat>.lessons.md`: ≤40 lines,
each lesson tagged with the resolutions that support it. The lessons file — not
the raw journal — is what the morning session injects. Raw journal remains the
append-only audit log.

**Lesson grading (later, Phase 4+).** A lesson is testable: calls made after
its first appearance, on situations it covers, versus before. Lessons whose
adoption coincides with a worse Brier get dropped at the next distillation with
a note. Do not build before the simpler loop has data.

Failure semantics: distiller crash or malformed output → last good lessons file
stands (never inject a partial); lessons file exceeding the line cap → truncated
oldest-first + `#risk` line.

## 3. S3 — charters: the strategy lifecycle, applied to prompts

The scoreboard was designed as "the feedback loop for tuning charters"
(design.md §4). This section makes that loop explicit and safe. Lifecycle
mirrors strategy.md: IDEA → SPEC → EVAL → DEPLOY → INCUBATE → KEEP/REVERT.

**CH-1 Evidence (IDEA).** Triggers come from the Murphy decomposition, which
already maps failure signatures to charter fixes (calibration §3): reliability
term high → add confidence-language anchors + show the agent its reliability
curve; resolution low → narrow coverage or propose seat retirement; Critic
objection hit-rate low → tighten the objection rules. Plus qualitative
triggers: repeated debate patterns in transcripts, recurring journal lessons,
malformed-output trends. A monthly analysis agent (Ops-adjacent, read-only over
SQLite + journals) emits at most ONE proposal per seat.

**CH-2 Pre-registration (SPEC).** A charter trial row, written before any
eval: seat, charter diff (section-scoped, bump version per `_template.md`),
the single metric it should move (shrunk BSS, reliability term, objection
hit-rate, malformed-rate), expected direction, incubation horizon (calls, not
days), and an invalidation ("if reliability worsens ≥X, revert"). Un-pre-
registered charter changes don't get scored — they get reverted.

**CH-3 Offline eval (the hard part).** A subtlety that shapes everything:
**`make replay` cannot evaluate a charter.** Replay feeds *recorded* decisions
to the runtime — the candidate prompt is never exercised. Charter evals need
real LLM calls. So: `make eval-charter SEAT=<s> CANDIDATE=<file>` runs N
sim-days over frozen historical fixture windows with *known resolutions*,
incumbent vs candidate on identical data (paired), scored by `calibration/`
functions. Output: metric delta + N + cost, appended to the charter-trial
registry. Honesty rules: N_eff for overlapping horizons (calibration §4); the
registry's family-N counts every candidate tried against the same metric, so
"we tried 12 prompts and one looked good" is visible for exactly what it is.
Eval is a screen, not proof — fixture windows are in-sample by construction;
the real test is incubation. Cost note: analyst seats are fast-tier; budget
~$1–3 per eval batch, capped in config.
**Manipulation note:** the seat under eval must not know it is under eval —
prompts are identical to production by construction (no per-run values in
prompts, CLAUDE.md), which is what makes this cheap to guarantee.

**CH-4 Deploy (human commit).** Winning candidate → PR: charter diff +
registry row + eval evidence. Human merges. Version bump lands in the charter
header and changelog per `_template.md`.

**CH-5 Incubation (live-paper).** The seat runs the new charter until ≥50
graded calls (calibration §4: below that, deltas are provisional). `signals`
rows carry `charter_version`, so incumbent-vs-candidate comparison is a
GROUP BY. **Auto-revert is Class A:** if the pre-registered invalidation fires,
or shrunk BSS (new version) < shrunk BSS (old version) − 0.05 at ≥50 calls,
Ops reverts to the prior charter version (a git revert PR, flagged in `#risk`)
— reverting to a known-good state is the one charter change that needs no eval.

**Rate limiter, stated honestly:** at 3–5 tickers/day a seat accrues ~50 graded
calls in 2–4 weeks, so charter evolution runs at *roughly one validated change
per seat per month*. That is the physics of statistical honesty, not a process
failure. Raising call volume (more tickers) speeds the loop; shortcutting the
N does not.

## 4. S4/S5/S6 — portfolio, harness, allocation

**S4 — strategy discovery heartbeat.** Gates and kill rules are built/spec'd;
what's missing is the automation that keeps hypotheses flowing. Weekly (not
nightly — trial budgets are the scarce resource, strategy.md §8: most
proposals *should* die at G2), the Quant seat gets a scheduled discovery turn:
review family menu + decay scoreboard + post-mortems, spend at most the
configured trial budget on the single most promising SPEC. Post-mortems from
kills (written by a non-proposing seat) land in journals, closing the loop
into S2. All existing gates unchanged — this adds cadence, not authority.

**S5 — harness hill-climb.** Weekly analysis agent over SQLite only (`events`,
`costs`, stage timings, timeout/malformed counters): e.g. rising
`pm_timeout` → stage-length or budget proposal; `malformed_signals` trend on
one seat → output-contract wording fix (feeds CH-1); debate threads hitting
reply caps without convergence → debate-round config proposal. Emits ≤3
proposals/week to `#ops`, PR-shaped, human-committed. Never touches gate math.

**S6 — model/budget allocation.** Monthly: join `costs` × scoreboard → skill
per dollar per seat. Proposals only (config YAML is human-committed): upgrade
a fast-tier seat whose resolution term is strong but reliability is noisy;
*don't* upgrade a seat with resolution ≈ 0 — calibration §3 says that seat
adds nothing at any model tier; narrow or retire it instead.

## 5. Contract changes required (human edits, do before Phase 2 lands)

1. `signals` + `decisions`: add `charter_version TEXT NOT NULL`,
   `model_id TEXT NOT NULL` (attribution — invariant 5).
2. New table `charter_trials` (mirrors `fundbt` registry): seat, versions,
   diff hash, pre-registered metric + direction + horizon + invalidation,
   eval delta, N, status (proposed/eval/deployed/kept/reverted), timestamps
   from injected Clock.
3. New table (or events reuse) for distillation runs: seat, input row count,
   lessons hash, timestamp.
4. Makefile: `eval-charter` target (Phase 4+; requires sim-day, Phase 1–2).

## 6. Cadences

| When | Loop | Class |
|---|---|---|
| Nightly | resolutions → reflections (built into daily cycle) | B |
| Weekly | scoreboard → PM weights (S1) · lesson distillation (S2) · decay scoreboard (S4) · harness analysis (S5) · quant discovery turn (S4) | A/B/C |
| Monthly | charter evidence scan + ≤1 proposal/seat (S3) · sleeve rebalance (S4, spec'd) · skill-per-dollar (S6) · reliability curves to Slack (spec'd) |A/C |
| Quarterly | seat retirement review (resolution ≈ 0 seats) · family menu review vs decay priors | C (human) |

## 7. Acceptance criteria (feed to devloop when phased in)

- [ ] `signals`/`decisions` rows reject inserts without `charter_version`/`model_id`; sim-day records them end-to-end.
- [ ] Distiller: given a fixture journal + scoreboard slice, produces ≤40-line lessons file, each line citing ≥1 resolution id; malformed distiller output → previous file stands (assert byte-identical), `#risk` event row exists.
- [ ] Distiller never writes through any path except `state/journal.py` API (purity-style AST check extended to the distiller job).
- [ ] `charter_trials`: inserting an eval result without a matching pre-registration row → rejected. Two active trials for one seat → rejected (invariant 6).
- [ ] `eval-charter`: paired incumbent/candidate run over one frozen fixture window produces deterministic scoring given recorded LLM outputs (record/replay applies to the *eval harness* itself); cost cap enforced (budget exhaustion → eval `aborted`, incumbent stands).
- [ ] Auto-revert: fixture where candidate underperforms by > 0.05 shrunk BSS at 50 calls → revert PR artifact generated, `#risk` event, seat config points at prior version.
- [ ] Un-pre-registered charter version detected in `signals` (version not in `charter_trials` as deployed) → `#risk` alarm within one scoreboard run.
- [ ] Analysis agents (S3 evidence scan, S5, S6) run with read-only DB access and no write tools; attempting a write → tool denied (hook test, like the trader's).

## 8. What is deliberately NOT in this design

- Agents editing any prompt, config, or threshold autonomously — Class C
  exists because a self-modifying agent can remove its own rules.
- RL / fine-tuning from outcomes: wrong regime at ~250 resolutions/year/seat;
  revisit only if call volume grows 100×.
- Embedding-based situation-similarity journal retrieval: already deferred to
  Phase 4+ by design.md §4; distillation comes first because it reduces
  injected tokens instead of adding machinery.
- Autonomous seat creation/retirement: quarterly human review gets the
  evidence pack; the org chart is capital allocation, and that's the CEO's.

## Appendix — sources

Loop-engineering framing: LangChain "The Art of Loop Engineering" (the
hill-climbing loop over traces = S5); Osmani "Loop Engineering" (maker/checker
split, memory outside context = S2 discipline); Huntley "Ralph Wiggum"
(anti-verification-theater; the trial-registry analogy for prompt iteration).
Statistical machinery: this repo's own `specs/calibration.md` §4 (minimums,
N_eff) and `specs/strategy.md` §5 (deflated-Sharpe logic that motivates the
charter-trial registry). The eval-can't-be-replay observation follows from
design.md §4 Testability (recordings capture decisions, not prompts).
