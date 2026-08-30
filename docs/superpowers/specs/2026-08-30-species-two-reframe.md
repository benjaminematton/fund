# Species Two — reframing `specs/design.md` around the improvement loop (v2, reconciled)

**Date:** 2026-08-30 · **Status:** derived audit, not canonical. Defers to `specs/design.md`,
`specs/contracts.md`, `specs/calibration.md`, `specs/strategy.md`, and `CLAUDE.md`. Companion to
the `VISION.md` change on the same branch, which it takes as the target.

**v2.** v1 (same day, earlier) re-derived — and in three places contradicted — a design that already
existed: `research/improvement-loops.md` (2026-08-18, amended after a grilling pass against its own
field brief). v2 sits on top of that design and on `research/field-brief-self-improving-agents.md`
(2026-08-30, 21 sources), and §3 says exactly what v1 got wrong. Every edit proposed here touches a
canonical file and lands only by human commit, in its own PR, after the decisions in §4 are ruled.

Source of the framing: HappyRobot's manifesto — "Species One" coordinates through people and
improves at the speed of their attention; "Species Two" applies what it learns without being asked
and keeps humans for the exceptions. The fund's version, as `VISION.md` now states it: **the record
compounds and flows back as data; anything that would change a rule or a prompt is proposed by the
firm and committed by the human.** Nothing here relaxes an invariant.

## 0. The frame, mapped onto the design that already exists

`VISION.md`'s two tiers are `improvement-loops.md`'s safety classes in different words:

| VISION tier | improvement-loops.md class | What moves | Approval |
|---|---|---|---|
| **1 — rule-driven** | **A** — autonomous in code (+ **B** — a seat's own append-only journal) | analyst weights (S1), sleeve allocation and kill rules (S4), desk narrowing (new), lessons distillation (S2) | the rule was the approval |
| **2 — judgment-driven** | **C** — human-gated | charters (S3), harness/stage config (S5), model/budget (S6), thresholds, watchlist, desks | a proposal with evidence → human merges or refuses |

The prior design's nine invariants — measured by code never self-reported; every experiment
pre-registered and counted; charters/configs/thresholds only by human commit; journals append-only;
attribution before iteration; **one change per seat per incubation window**; default no-change; a
human reads traces before an agent categorises them; the factual half of a reflection is computed —
are adopted here without amendment. The brief's findings sharpen four of them and add two risks
(§2 §8); they overturn none.

## 1. What is actually wired (demonstrated 2026-08-30, `origin/master` e97b16a)

Shipped from the 2026-08-18 plan: **P1** traces (`FUND_TRACES`, `scripts/run_day.py:700`;
`evals/traces/`), **P2** attribution (`charter_version`/`model_id` in `state/schema.sql`), **S0**
the daily scorecard (`scripts/score_day.py`), the **S2 computed frame** (`orchestrator/reflect.py`),
**S7** the regression ratchet (`docs/agents/regression-ratchet.md`).

Not shipped: **S1's job** — `calibration/` is built and tested but no job runs it and no brief
consumes a weight (the only reference in `orchestrator/daily.py` is a comment at `:162`; the PM's
brief carries `journal`, `positions`, `signals`, `allowed_actions`, `agents/tools/fund_server.py:528-539`);
**S2's weekly distillation** (no distiller, no `lessons.md`, nothing reads `resolutions.reflection`
back — #57); **S3/S5/S6** (Class C, blocked by design on the taxonomy trigger); **S4's** discovery
cadence (the lab has no caller outside lint scripts).

**Correction to v1.** v1 called `score_day.py` "a Species-One artifact" because it ranks turns for a
human to read. It is S0, and it ranks turns for a human to read *by design*: the sourced practitioner
line (Husain & Shankar, in the 2026-08-18 brief) is that the operator reads raw traces before any
agent categorises them, and at 10–25 traces a day the fund can read all of them. S0 is a
prerequisite of the loop, not a competitor to it. The name collision with `design.md` §4's
"scoreboard" (per-agent calibration stats) is real and is fixed in text below — the two share a
word and nothing else.

## 2. Section-by-section audit of `design.md`

### §0 Invariants — unchanged; none blocks the loop

Invariant 3 (no LLM in `gate/`, `stratgate/`, `calibration/`) is what makes tier 1 safe: the code
that moves weights and kills sleeves cannot be talked to. Invariant 7 (structured data only via MCP
tools) is what makes tier 2 safe: a proposal is a strict tool call. **Proposed:** no edit.

### Non-goals — keep the fence, name the door

"No autonomous charter self-modification — charters change only by human commit" stays verbatim.
**Proposed addition (one sentence):** *The firm may propose a charter, threshold, watchlist, or
desk change — as a structured, pre-registered proposal projected to a PR — and never apply one; the
human commit is the only path from proposal to instruction.*

### §1 System overview — a fourth moving part, pointing at its own spec

"Three moving parts" are the trading day. **Proposed:** add **4. The improvement loop** — nightly
`resolve → reflect → score → apply` (no LLM), weekly `distill`, monthly `propose` (one seat) —
with a pointer to `specs/improvement.md` (see D8: the prior design's buildable half, promoted to
canon as that document itself proposed) rather than expanding §3/§4 of this file.

### §2 Seats — two rows corrected, one added, one fenced

- **Ops** row: split the word. The *calibration scoreboard* is a tier-1 code job (no seat computes
  a score — `calibration.md` §0.1); the *daily scorecard* (`score_day.py`) is what Ops posts. Say which.
- **Reflect** row stays single-decision; it is the wrong seat to propose (cross-decision view,
  different surface).
- **Proposed new row — Proposer** (D1): fast tier, `tools = mcp__fund__*` only, one fund tool
  `submit_proposal`. **Cadence: monthly, ≤1 proposal per seat per incubation window** (prior
  invariant 6; see D5 — v1's "nightly, N=1" is withdrawn). Inputs: the calibration scoreboard,
  resolutions + reflections since its last proposal, the current text/value of its target, and its
  own proposal record. **Fenced like the exec seat, not like a dev seat** (`setting_sources=[]`,
  explicit allow-array): its write surface is `submit_proposal` and nothing else; `calibration/`,
  `gate/`, `stratgate/`, its own charter, and every seat's output contract and confidence-step
  instruction are outside its target enum. This is AHE's controllability rule and invariant 2's
  pattern — the optimiser cannot reach the verifier, the budget, or the seat's scoring interface.

**Output contract — `submit_proposal`** (strict; schema to `contracts.md` §4; borrows AHE's
change manifest): `target` (enum, **initially** `gate_threshold`, `stratgate_threshold`,
`seat_config`, `watchlist`, `tool_contract`; `charter` and `desk` admitted later — D6),
`subject`, `change` (typed by target), `evidence` (≥1 row reference — resolution, scorecard,
reflection ids; free text alone is rejected by schema), `inferred_cause` (prose), `expected_effect`
(scorecard metric + direction), `horizon_calls` (graded calls on the touched seat before
resolution — not days), `at_risk` (≥1 metric that must **not** move: abstention rate, confidence
granularity, coverage, cost), `trial_id` (pre-registration; the family count feeds the same
deflation logic `fundbt/`'s registry feeds). Handler validates, writes a `proposals` row, and
projects — code, not the seat, runs `gh` — as a PR for file targets or a `#ceo-office` post for
desk/capital. Default on any failure: no proposal, one alert.

### §3 Daily cycle — the Nightly row becomes three, and two of them are not nightly

| When | Stage | What happens |
|---|---|---|
| Nightly 1 | Resolve + Reflect | unchanged |
| Nightly 2 | **Score + Apply** (no LLM) | `calibration/` recomputes per-seat skill and PM weights → `weights` table (S1); `strategy.md` §6 ramp/probation/kill rules run on incubating sleeves (S4); a desk whose total skill sits below floor for the configured window is **narrowed** (allowed tickers reduced by a human-committed rule) — narrowed, never retired, because retiring is tier 2. Every change is a row carrying the inputs that produced it. |
| Weekly | **Distill** (S2, Class B) | a fresh distiller seat regenerates `journals/<seat>.lessons.md` (≤40 lines, each citing resolution ids); the lessons file, not the raw journal, is what the morning brief injects. |
| Monthly | **Propose** (Class C, one seat) | the Proposer reads the above and submits ≤1 proposal per seat, or none. A proposal becomes a PR / `#ceo-office` post. |

Morning consequence: the PM's 11:00 brief carries the current `weights` row (tier 1 reaching the
decision); every research brief carries its seat's lessons (the "recent record **+ lessons**" §4
promises and #57 defers).

### §4 Infrastructure — State & memory is where the loop closes

- **Journals** bullet — "injected as 'recent record + lessons'" is half-true today. **Proposed:**
  make it true via S2 (frame → reflection → lessons → brief). No new mechanism.
- **Scoreboard** bullet — describes a loop that terminates in a human reading Slack. **Proposed
  rewrite:** nightly table, not weekly post; the PM brief reads weights from it; the Proposer reads
  it; Ops posts a projection.
- **New tables** (DDL to `contracts.md` §1): `weights` (as_of_date, seat, weight, inputs hash);
  `proposals` — **one table for every tier-2 target**, absorbing the prior design's `charter_trials`
  rather than adding a sibling: pre-registration fields, predicted effect, at-risk set, resolution
  fields (`resolved_delta`, `at_risk_moved`), state machine `proposed → {merged, refused, expired}
  → {kept, reverted}` driven only by code observing the PR and the incubation window. Transitions
  through `state/transition()`.

### §5 Deterministic risk gate — unchanged; thresholds gain a proposal path

**Proposed addition (one line):** *A threshold change may originate as a `gate_threshold` proposal
carrying the record that argues for it; it takes effect only when the human merges it — the rule
`strategy.md` §7 already states for `stratgate/` and the Risk Officer.*

### §6 Repo layout — one module, one canonical file

`orchestrator/improve.py` for score/apply; the distiller and Proposer handlers beside
`submit_reflection` in `agents/tools/fund_server.py`; PR projection under `scripts/`, code-invoked
only. `specs/improvement.md` becomes the canonical home (D8).

### §7 Build order — Phase 2b is the 2026-08-18 design's remainder, sequenced by attribution cost

**Proposed:** insert **Phase 2b — Close the loop** before phase 3, containing, in order:
(a) S1 job + `weights` + weights in the PM brief; (b) #57 reflections → journals → briefs;
(c) S2 weekly distillation; (d) the desk-narrowing rule; (e) `proposals` + Proposer with
**numeric/config/watchlist/tool-contract targets only** + PR projection; (f) proposer grading
(resolution columns + the DiD evaluator); (g) **charter targets — gated on the taxonomy trigger
(≥100 traces read by hand, per `improvement-loops.md` §1), not scheduled.**

Why this order (sourced in the brief): attribution cost rises left to right and so does transfer
risk — a threshold is one number; tool/contract wording was the most reliable surface in Zup's
production experience; system-prompt-only evolution *regressed* below seed in AHE's ablation while
structural components transferred. Why before phase 3: every seat added before the loop closes is
another ungraded opinion the human tunes by hand; every seat added after it is graded from its
first call. Honest caveat on timing: `calibration.md` §2 gives a seat mean weight until 50 graded
calls, and the luck-vs-skill arithmetic says ~100 questions before a large skill gap beats luck at
1σ — weights will not move for weeks after (a) ships. That is the reason to build (a) now: the
clock on the moat starts the night a weight row is written, not when it first differs from mean.

### §8 Risks — three, and the containment is the same loop

**Proposal laundering.** Fluent nonsense can write a proposal as easily as a thesis. Contained by
schema (`evidence` references rows), by pre-registration (`trial_id`; the family is counted), and
by grading the proposer (below).

**Regression blindness** (new; AHE §4.4.2). A self-evolving loop predicted its fixes at ~5× chance
and its breakages at ~2× chance. The Proposer will not name what it might break; the `at_risk`
field forces the question, and the resolution evaluator checks those metrics whether or not the
proposer named them well. The three cheapest levers a charter edit can pull to flatter Brier —
abstention rate, confidence granularity, coverage — are always in the at-risk set by default.

**Gate habituation** (new; Habituation at the Gate, 400 reviewers / 11,429 reviews). Approval of
agent PRs rose 14.5 pp with exposure while scrutiny fell, in repos where human-PR approval was
falling. VISION's success number — "the human's part is mostly saying yes" — is that signature
read as a goal. Contained by: one proposal per PR, refused by code if it touches two targets; the
morning post shows the approver's own approval/refusal trajectory beside what merged proposals
later did; three consecutive merges flag the fourth for a second read; approval and
change-request rates on proposals are *monitored*, never targeted.

### Out of scope

The manifesto's *Twin* maps to the Phase 6 resident-seats sketch (R5 especially) and is not
reframed here. *Frontal* is the PM + gate and already exists. Neither needs the loop first; the
loop needs neither.

## 3. What v1 got wrong

1. **Nightly, one proposal per night.** Right for attribution, wrong for power. At 10–25 graded
   calls a day across two desks and a five-day horizon, no per-seat change resolves inside a
   month; a proposal cadence faster than its resolution horizon just stacks unattributable changes
   — exactly what prior invariant 6 forbids. Withdrawn in favour of monthly, ≤1 per seat per window.
2. **Charters in tier 2 from day one.** The strongest single finding in the brief says the
   opposite; and the 2026-08-18 design had already gated charters on a hand-derived taxonomy for a
   sourced reason. Charters move to last, gated, not scheduled.
3. **`score_day.py` as a Species-One artifact.** It is S0, and the human-reads-first line it
   serves is the one part of the review loop both camps in the prior brief agree on. Retracted.
4. **A new phase with a new name.** Phase 2b is the buildable remainder of an existing design plus
   the tier-1 wiring; naming it as such keeps one lineage.

Survives from v1: the VISION frame, D1 (Proposer as a new seat), D2 (PR for files, `#ceo-office`
for desks/capital), D3 (before phase 3), D4 (proposals graded from day one — sharpened).

## 4. Decisions for Benjamin (reconciled)

- **D1 — who proposes.** New seat, `submit_proposal` only, fenced like exec. **Rec: yes.**
- **D2 — how a proposal reaches you.** PR for file targets; `#ceo-office` for desk existence and
  capital. **Rec: yes.**
- **D3 — build order.** Phase 2b (the 2026-08-18 remainder + tier-1 wiring) before phase 3.
  **Rec: yes.**
- **D4 — grading.** Every proposal is a pre-registered forecast with predicted effect, at-risk set,
  and a horizon in graded calls; resolved by DiD against untouched seats; the *proposer's* record
  (fix precision, at-risk hit rate) is the scoreboard row, accumulated across proposals — no
  per-proposal significance bar. **Rec: yes.**
- **D5 — cadence.** Monthly, ≤1 per seat per incubation window, adopted from
  `improvement-loops.md` §10; revisit when graded-call volume grows ~10×. **Rec: adopt; v1's
  nightly withdrawn.**
- **D6 — charter targets.** Admitted to the Proposer's enum only after the taxonomy trigger
  (≥100 traces read by hand); until then tier 2 is thresholds, seat config, watchlist, tool/output
  contracts. **Rec: keep the prior design's gate.**
- **D7 — gate-decay monitoring.** One target per PR enforced by code; approver's own trajectory
  shown with outcomes; streak flag at three; approval/change-request rate monitored. **Rec: yes —
  cheap, and it is what makes VISION's number honest.**
- **D8 — canonical home.** Promote `improvement-loops.md`'s buildable half to `specs/improvement.md`
  (as that document proposed), amended by §2 above; `design.md` gets the seat row, the §3 rows, and
  a pointer. **Rec: yes** — one canonical file for the loop beats spreading it across §3/§4.

## 5. What this branch does and does not do

Does: rewrites `VISION.md` to name the two-tier loop as the firm's kind; records this audit (v2)
and the research brief behind it. Does not: edit `specs/`, `contracts.md`, or `CLAUDE.md` — each
proposed edit above is a canonical change and waits on §4. No code. No seat.
