# Species Two — reframing `specs/design.md` around the improvement loop

**Date:** 2026-08-30 · **Status:** derived audit, not canonical. Defers to `specs/design.md`,
`specs/contracts.md`, `specs/calibration.md`, `specs/strategy.md`, and `CLAUDE.md`. Companion to
the `VISION.md` change on the same branch, which it takes as the target. Every edit proposed here
touches a canonical file and so lands only by human commit, in its own PR, after the decisions in
§3 are ruled.

Source of the framing: HappyRobot's manifesto (happyrobot.ai/blog/manifesto) — "Species One"
coordinates through people and improves at the speed of their attention; "Species Two" runs on a
live model of itself, applies what it learns without being asked, and keeps humans for the
exceptions. The fund's version, as `VISION.md` now states it: **the record compounds and flows back
as data; anything that would change a rule or a prompt is proposed by the firm and committed by the
human.** Nothing here relaxes an invariant. Charters and gate thresholds still change only by human
commit — the change is who writes the diff.

## 0. The two tiers (the frame the audit uses)

| Tier | What changes | Who acts | Approval |
|---|---|---|---|
| **1 — rule-driven** | analyst weights, sleeve allocation, kill/probation, desk narrowing | code, nightly, from the record | the rule was the approval (human-committed once) |
| **2 — judgment-driven** | a charter, a threshold, the watchlist, which desks exist, a strategy family | a seat writes a proposal with evidence; code projects it as a PR or `#ceo-office` post | human merges or refuses |

Tier 1 is already the design's stated intent in two places (`calibration.md` §2, `strategy.md` §6)
and built in neither pipeline. Tier 2 exists in canon once — `strategy.md` §7, "Risk Officer …
owns `stratgate/` parameter change proposals (human-committed)" — and nowhere in code.

## 1. What is actually wired today (demonstrated, 2026-08-30, `origin/master` e97b16a)

- `resolve_day.py` writes `resolutions`; `reflect_day.py` writes one `resolutions.reflection` per
  resolved decision. **Nothing reads the reflection back** — journal/Slack projection is deferred
  (#57; `agents/tools/fund_server.py:309`).
- `calibration/` is built and tested. **No job runs it and no brief consumes a weight.** The only
  reference in `orchestrator/daily.py` is a comment (`:162`). The PM's `get_stage_brief` carries
  signals, allowed actions, and its own journal — no weights.
- What `run_day.py:post_scorecard` posts is `scripts/score_day.py`: a fixed-severity ranking of
  *which turns a human should read first*. That is a Species-One artifact by construction — its
  docstring says so — and it is not the "weekly per-agent stats" scoreboard `design.md` §4
  describes. The two share a name and nothing else.
- Journals: `orchestrator/daily.py` appends decisions; `fund_server._journal` feeds a seat its own
  recent entries. So "recent record" reaches prompts; "lessons" (reflections) do not.
- The lab (`fundbt/`, `stratgate/`) has no caller outside lint scripts. G4's automatic kill rules
  run nowhere because nothing is incubating.
- Every charter, config, threshold, and watchlist change to date is a human commit authored by a
  human (or a dev session the human directed). Zero proposals have originated from the running
  firm.

## 2. Section-by-section audit of `design.md`

### §0 Invariants — unchanged; none blocks the loop

Invariant 3 (gate/stratgate/calibration import no LLM) is what makes tier 1 safe: the code that
moves weights and kills sleeves cannot be talked to. Invariant 7 (structured data only via MCP
tools) is what makes tier 2 safe: a proposal is a strict tool call, never prose code parses.
**Proposed:** no edit. The loop rides on the invariants as written.

### Non-goals — keep the fence, name the door

"No autonomous charter self-modification — charters change only by human commit (same rule as gate
thresholds)" stays verbatim. **Proposed addition (one sentence):** *The firm may propose a charter,
threshold, watchlist, or desk change — as a structured proposal projected to a PR — and never apply
one; the human commit is the only path from proposal to instruction.*

### §1 System overview — a fourth moving part

"Three moving parts" (agent processes, orchestrator, gate) are the *trading day*. The improvement
loop is the night, and it is missing from the diagram entirely. **Proposed:** add
**4. The improvement loop** — nightly, orchestrator-driven: `resolve → reflect → score → apply
(tier 1, no LLM) → propose (tier 2, one seat)`. Diagram gains a `State → calibration/ → weights`
edge and a `proposals → PR` edge that leaves the box toward the CEO.

### §2 Seats — one seat added, two rows corrected

- **Ops** row says "scoreboard". Split the word: the *calibration scoreboard* is a tier-1 code job
  (Ops does not compute it — `calibration.md` §0.1 forbids any seat from computing a score); the
  *attention scorecard* (`score_day.py`) is what Ops posts today. Row text should say which.
- **Reflect** row is correct and stays single-decision. It is the wrong seat to write proposals:
  a proposal needs the cross-decision view (a seat's whole record, a threshold's whole history)
  and a different tool surface.
- **Proposed new row — Proposer** (working name; see D1): fast tier, `tools` = `mcp__fund__*`
  only (no Alpaca toolset, same lock as Reflect), one fund tool `submit_proposal`. Inputs: the
  calibration scoreboard, the resolved decisions + reflections since the last proposal, the
  current charter/config text of its target, and its own proposal record (see §2 §8). Runs
  nightly after `apply`, at most N proposals per night (N=1 to start — the vision's "one thing,
  with the evidence"). Never in the daily cycle; never mentioned from Slack.

**Output contract addition:** `submit_proposal` (strict; schema to `contracts.md` §4):
`target` (enum: `charter`, `seat_config`, `gate_threshold`, `stratgate_threshold`, `watchlist`,
`desk`), `subject` (path or key), `change` (typed by target — `section` + `new_text` for a
charter; `from` + `to` for a number; `add`/`remove` for watchlist/desk), `evidence` (≥1
structured reference: scorecard row, resolution ids, reflection ids — no free-text-only
evidence), `expected_effect` (which scorecard metric, which direction), `horizon_days`. Handler
validates, writes a `proposals` row, and projects — code, not the seat, runs `gh` — as a PR for
file targets or a `#ceo-office` post for `desk`/capital. Default on any failure: no proposal, one
alert; the night still completes.

### §3 Daily cycle — the Nightly row becomes three

Today's table has one Nightly row (Reflection). **Proposed:**

| Time | Stage | What happens |
|---|---|---|
| Nightly 1 | Resolve + Reflect | unchanged |
| Nightly 2 | **Score + Apply** (no LLM) | `calibration/` recomputes per-seat skill and PM weights → `weights` table; `strategy.md` §6 kill/probation/ramp rules run on incubating sleeves; a desk whose total skill is below floor for the configured window is **narrowed** (its allowed tickers reduced per a human-committed rule) — narrowed, never retired, because retiring is a desk-existence decision (tier 2). Every change is a row with the inputs that produced it. |
| Nightly 3 | **Propose** (one seat, one turn) | Proposer reads the above and either submits ≤N proposals or none. A proposal becomes a PR / `#ceo-office` post by morning. |

The morning changes correspondingly: the PM's 11:00 brief carries the current `weights` row (tier
1 reaching the decision); every seat's research brief carries its reflections on similar past
calls (the "lessons" half of §4 that #57 defers) — same-ticker + recency retrieval, as §4 already
specifies, nothing fancier.

### §4 Infrastructure — State & memory is where the loop closes

- **Journals** bullet: "Injected into prompts as 'recent record + lessons'" is half-true (see
  §1). **Proposed:** make it true — reflections project into journals (this *is* #57), and the
  brief reads them. No new mechanism.
- **Scoreboard** bullet: "weekly per-agent stats … posted by Ops. The feedback loop for tuning
  charters (the PM's charter weights analyst signals by calibration)." This sentence describes a
  loop that terminates in a human reading Slack. **Proposed rewrite:** the scoreboard is a nightly
  table, not a weekly post; the PM's brief reads weights from it (tier 1); the Proposer reads it
  (tier 2); Ops posts a projection of it. The feedback loop for tuning charters is the Proposer's
  PR, not a human's reading.
- **New:** a `proposals` table (DDL to `contracts.md` §1) with a state machine
  `proposed → {merged, refused, expired}` driven only by code observing the PR/thread — the
  Proposer never transitions its own row — and a `weights` table keyed by (as_of_date, seat).
  Transitions through `state/transition()` like every other workflow table.

### §5 Deterministic risk gate — unchanged; thresholds gain a proposal path

The gate reads its thresholds from human-committed config and always will. **Proposed addition
(one line):** *A threshold change may originate as a `gate_threshold` proposal from the Proposer,
carrying the record that argues for it; it takes effect only when the human merges it — identical
to the `stratgate/` rule `strategy.md` §7 already states for the Risk Officer.*

### §6 Repo layout — one module

`orchestrator/improve.py` (or `nightly.py`): the score/apply/propose stages. `calibration/` stays
pure. The Proposer's tool handler lives in `agents/tools/fund_server.py` beside `submit_reflection`.
The PR projection is a script under `scripts/` that only code invokes.

### §7 Build order — the loop moves ahead of the remaining seats

Today: phase 3 (bull/bear, risk persona, macro, ops, CEO approval, interrupts) → phase 4 ("tune
charters from scoreboard data" — i.e. the human does the loop by hand) → phase 5 (lab).

**Proposed:** insert **Phase 2b — Close the loop** before phase 3, containing: (a) calibration
job + `weights` table + weights in the PM brief; (b) reflections → journals → briefs (#57); (c)
desk-narrowing rule; (d) `proposals` table + Proposer seat + PR projection; (e) the meta-loop
(§8). Rationale: every seat added before the loop closes is another ungraded opinion the human
has to tune by hand; every seat added after it is graded from its first call and can be narrowed
or proposed against. Phase 4's "tune charters from scoreboard data" becomes "merge or refuse the
firm's proposals; count how many you had to write yourself."

Honest caveat on timing: `calibration.md` §2 gives a seat mean weight until it has 50 graded
calls. At two desks, a small active set, and a 5-day horizon, weights will not move for weeks
after (a) ships. That is a reason to build the plumbing *now*, not later — the clock on the moat
starts when the first graded call lands in a table something reads.

### §8 Risks — one new risk, and the containment is the same loop

**Proposal laundering.** A proposal is prose with a diff attached; fluent nonsense can write one
as easily as a thesis. Three containments: (1) `evidence` must reference rows, never only text —
enforced by schema; (2) `expected_effect` names a scorecard metric and direction, so every merged
proposal is itself a call that resolves at `horizon_days`; (3) **proposals are graded.** The
Proposer's own record — did the metric move as predicted after merge — is a scoreboard row like
any analyst's, and a Proposer with no skill is narrowed like any desk (fewer targets it may
propose against). The firm's improvement is measured the same way the firm's opinions are. This
is the vision's success number made mechanical.

### Out of scope for this audit

The manifesto's *Twin* (a live model, event-driven action) maps to the Phase 6 resident-seats
sketch (`2026-08-28-resident-seats.md`, especially R5) and is not reframed here. *Frontal* is the
PM + gate and is already the design. Neither needs the loop to be built first; the loop needs
neither.

## 3. Decisions for Benjamin

- **D1 — who writes proposals.** New seat (Proposer, `submit_proposal` only) vs. extend Reflect.
  **Rec: new seat.** Reflect's contract is one decision, bound server-side, prose only; a
  proposal is cross-decision, structured, and produces an artifact outside the DB. Different
  inputs, different tool surface, different blast radius — same pattern that keeps Exec and PM
  apart.
- **D2 — how a proposal reaches you.** PR (code runs `gh`) vs. `#ceo-office` post.
  **Rec: PR for anything that is a file** (charter, config, threshold, watchlist) — review is the
  merge, and the merge is already the only ratification the repo recognises; **`#ceo-office`
  for desk existence and capital**, which are not diffs.
- **D3 — build order.** Phase 2b before phase 3. **Rec: yes**, per §2 §7 above.
- **D4 — proposals are graded from day one.** **Rec: yes**, as columns on `proposals`
  (`expected_effect`, `horizon_days`, `resolved_delta`) — the scorer comes later; the data must
  not.
- **D5 — N per night.** **Rec: 1.** One thing, with the evidence, waiting in the morning.

## 4. What this branch does and does not do

Does: rewrites `VISION.md` to name the two-tier loop as the firm's kind, and records this audit.
Does not: edit `specs/design.md`, `contracts.md`, or `CLAUDE.md` — each proposed edit above is a
canonical change and waits on §3. No code. No seat.
