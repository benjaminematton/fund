# Four competing designs — `/morning-standup` lane mix

**Produced** 2026-08-25 by sessionId `27fcd833-4173-47d6-aedf-75bd73cd0014` (name at time of writing:
`fund-b4` — note `fund-4b` is a *different* live session; key on the id).
**Task** from `docs/superpowers/handoffs/2026-08-25-standup-lane-mix.md`.

Four designers ran in parallel, independently, none able to see the others' work. Each got the same
measured brief and constraints and a **different theory of what is broken**, with explicit permission
to reject its assigned theory. None did reject it outright; two relocated it.

- **A** — the human's attention is the scarce resource
- **B** — any mechanism that needs upkeep will decay
- **C** — the dispatch decision is the dangerous one
- **D** — a run is measured by what reaches done

**Standing caveat, and it binds the reading of this document:** four instances of one model.
Convergence below is evidence that an answer is *obvious*, not that it is *right*. From the
2026-08-25 digest: *independence of construction is not independence of model — two gates built
separately agreed on zero evasions because they shared a blind spot.*

---

## Independently verified before comparison

Re-measured by the comparing session rather than relayed. Commands given.

| Claim | Command | Result |
|---|---|---|
| The current skill dispatches **#3** tomorrow | `gh issue view 3 --json labels`; `gh api .../issues/3 --jq '.issue_dependencies_summary.blocked_by'` | `labels: []`, `blocked_by: 0`, region declared → passes every candidate filter |
| **25** open issues declare `Part of #49`; **16** are attached | `gh issue list --state open --search '"Part of #49" in:body'` vs `gh api .../issues/49/sub_issues` | 9 unattached: **#50 #53 #63 #64 #65 #66 #73 #75 #79** |
| A boarding write was made and **reverted in 68 seconds** | `gh api .../issues/49/timeline --paginate` | `sub_issue_added #58` `19:47:40Z` → `sub_issue_removed #58` `19:48:48Z` |
| **3** issues closed in the repo's entire history | `gh issue list --state closed --limit 200` | #5 (08-20), #26, #37 — the last at `18:04:54Z`, *before* the dispatch began |
| 4 open PRs, none merged since the dispatch | `gh pr list --state open` | #80, #34, #28, #24 — oldest open since 2026-08-21 |
| `/wayfinder` is not installed | `find ~/.claude ~/.claude-work -maxdepth 4 -iname '*wayfinder*'` | nothing |
| `wayfinder:task` label is worn by **zero** issues | `gh issue list --state all --label wayfinder:task` | 0 — the documented type taxonomy is already dead |

**#3 is the sharpest of these.** It is the issue two live lanes froze `run_stage` pending, because
they judged it Benjamin's call. It carries no label. So the current skill's single act tomorrow
morning is to dispatch a seat at a question it already knows is his.

---

## Where all four converged, independently

1. **`needs-decision` must route, not exclude.** Phase 0 currently excludes such a child from being a
   lane. All four make it a lane of a second type whose deliverable is a **decision brief / package**
   and whose explicit prohibition is *making the ruling*. Consequence: #38 (critical) and #42 (high) —
   the two most severe items on the board — become dispatchable instead of permanently un-startable.
2. **One batched decision list, as an obligation.** Not the current "the one flag worth leading with",
   which did not survive contact with nine lanes. Three of the four write the stronger form: *no ruling
   leaves the run except in that list.*
3. **Design lanes declare regions like any other lane.** No exemption from the constraint. Verified:
   all five `needs-decision` issues already declare one.
4. **All four rejected "derive design lanes from spec gaps"** (candidate direction 3) — the docs layer
   does not exist (`CONTEXT.md` absent, `VISION.md` unmerged as PR #24, 2 ADRs) *and* the design
   backlog is already written down by the lanes that hit it.
5. **All four rejected the second board** (candidate direction 2) — it breaks Phase 0's "exactly one
   `wayfinder:map` match is required" and adds a second thing with no writer.
6. **Three of four independently invented the same mechanism** for a design lane whose region a live
   lane already holds: route it to the incumbent rather than dispatch it. A calls it **frozen**, B
   **deferred to its owner**, C a **rider**. D inverts the same observation into a **pairing**.

## Where they genuinely disagree

| | **A** attention | **B** decay | **C** dispatch | **D** done |
|---|---|---|---|---|
| **What governs the split** | severity tier gates each track; design width `W = 1 + decisions cleared last run` | stream *order* only — a critical unclaimed defect puts defects first, else design first. No quota | precedence ladder + hard cap of 6 lanes, design ≤ half | budget: `retired + 1 + unblocks` new chats |
| **Where design lanes come from** | board children + carry-forward from previous digest; off-board work becomes a *boarding ask* to Benjamin | `needs-decision` label, **board-independent** | union read of `Part of #49` + a severity-ordered tail below map order | `needs-decision` label + a **package test** |
| **Throttles the run?** | no | no | yes, cap 6 | yes, aggressively |
| **Unmerged branches** | — | — | — | a **third lane type** (`land`) |
| **Lanes tomorrow** | #38, #42 (2 design, 0 remediation, N=0 chats) | #38, #42, #3 dispatched; #63, #73, #78 deferred to incumbents; N=3 | #38, #42 design + #64, #50, #53, #65 remediation; N≈4–6 | #63, #3, #38 decide + 1 orphan sweep; N=4 |
| **Self-named fatal flaw** | makes #63 — the repo's only off-board critical — unreachable, and calls it a virtue | reads a label to type work, on a signal with a demonstrated miss rate and **zero** retirement events ever | produces two lanes Benjamin could have named in ten seconds | would have permitted **one** lane on the most productive day this repo has had |

---

## The four designs, verbatim

What follows is each designer's return, unedited. Claim labels (`demonstrated` / `reasoned`) are
theirs. Where a design's measurement disagrees with the verified table above, the table wins.

---

# Design A — the human's attention is the scarce resource

## 1. Theory of the problem

`morning-standup` dispatches only defects because its only input is a 16-child board that nothing
refreshes and that was seeded from one audit — but the deeper fault is that its one rule for
human-blocked work is *exclude and flag*, which turns the highest-value items on the board into
permanent non-work. **Benjamin's attention is scarce, and the measured cost of its scarcity is not
stalling — it is an overseer manufacturing authority in his absence**: 40-odd rulings on 2 files read,
3 withdrawn, one of them over a question he had already settled and nobody told the overseer about. A
fixed morning dispatches urgent defects *and* a bounded number of **design lanes whose deliverable is
a decision brief, not a fix**, so the decisions he owes arrive prepared and batched. On a morning he
never appears, the design track still runs — briefs need no chat, no branch, no merge — so decision
debt falls even when nothing merges. I accept the assigned frame and relocate its object: the scarcity
binds at *ordering and authority*, not at technical rulings.

## 2. The design

### Phase 0 — three amendments

**0.a — `needs-decision` routes; it no longer excludes.** Replace the fifth candidate-lane bullet
(`SKILL.md:73-78`):

> - **decision-bearing** — a child carrying `needs-decision` (or this repo's equivalent) is **not
>   excluded; it is routed.** `blocked_by` models issue-to-issue edges only, so a child blocked on a
>   ruling reports `blocked_by == 0` and reads as ready. Dispatching it as remediation gets a seat to
>   guess at the ruling, which is the failure the board exists to prevent. But excluding it makes the
>   item nobody can start into the item nobody ever works. It is a **design lane**: its deliverable is
>   the brief that makes the ruling cheap, and its explicit prohibition is making the ruling itself.

**This does not violate constraint 1.** Phase 0 forbids re-deriving *lane priority* from labels.
Routing is not ordering: the children stay in map order, and a label decides only which of two
deliverables the lane owes. Phase 0 already reads labels this way — `refuted` suppresses,
`needs-decision` excludes — and its degradation path already severity-*ranks a report*
(`SKILL.md:108-114`).

**0.b — the carry-forward rule.**

> A child named in the **previous digest's `## Decision batch`** is on the design track this morning
> whether or not it carries the label. The digest is this skill's own durable record; a decision it
> reported yesterday does not become undecided because nobody labelled the issue. Where label and
> carry-forward disagree, take the union and report the disagreement as one line to the human.

`demonstrated` — the 2026-08-25 digest's flag 2 named #3 as `→ the human`, and
`gh issue view 3 --json labels` returns `[]` fourteen-plus hours later. Under label-only routing #3
would be dispatched tomorrow as a remediation lane, which is the exact bug the digest flagged.

**0.c — the off-board read and the boarding ask.**

> Read the open issues that are **not** children of the map. They are never lanes — boarding is
> ordering and ordering is the human's — but they are the board's only inflow, and a board with no
> inflow only shrinks. Report them as **the boarding ask**: the count, and the issues at the **top
> severity tier present off-board, in issue-number order** (never a pick — naming one issue per
> morning is deriving an order by the back door). One question, not a re-ordering: *board these, or
> say they wait.*

### New Phase 0.5 — tracks, and the split

> **Rule A — urgency gates the remediation track.** Let the **top severity tier present among
> remediation candidates this morning** be the urgent tier. Dispatch **every** urgent remediation
> candidate; urgency is never rationed. Dispatch the tail below that tier **only if the urgent tier is
> empty**. Measured 2026-08-25: nine simultaneous lanes produced 109 overseer messages, 337,683
> characters, **2 files read directly**, and three withdrawn rulings. An overseer that reads nothing
> adjudicates on testimony.
>
> **Rule B — attention gates the design track.** Dispatch design candidates in map order up to width
> **W**:
> - a design candidate at the **top severity tier present on the design track** is dispatched
>   regardless of W. Urgency is not rationed on either track.
> - beyond that, **W = 1 + C**, where **C** is the number of items in the previous digest's
>   `## Decision batch` now observably resolved (label removed, issue closed, PR merged). **Floor 1**,
>   so the track never dies; width is *earned*.
> - **Cold start.** Where the previous digest carries no `## Decision batch` section, C is not
>   measurable and **W = 1**, reported as *cold start* — not as "you cleared nothing." The two have
>   opposite remedies.
>
> A design lane's only product is a decision the human must read. Dispatching more than he clears
> manufactures an unread queue, and an unread brief is a lane's entire output wasted.

**What "the split" is:** not a quota and not a ratio. It is a **gate on each track, both keyed to the
same urgent-tier computation**, so the mix is a consequence of what is actually urgent that morning
rather than a number someone chose.

### Phase 2 — the poll

> Candidate lanes are listed **typed**: `#38 — <title> — **design lane (decision brief)**`.
>
> The **blocked** field gains one clause: *if what unblocks you is a decision only the human can make,
> say so and name the decision.* Those answers join the decision batch.

### Phase 3 — the decision batch replaces the `→ the human` flags

> Flags stay as they are, minus the human ones. Those become a single numbered section,
> `## Decision batch`, under a fixed heading so tomorrow's run can machine-read it. Each row: the
> decision, the issue or seat it belongs to, its severity, **how many days outstanding**, and whether
> a brief exists.
>
> **The batch is assembled once and reported once.** A decision discovered mid-run joins the batch; it
> does not get its own message.

### Phase 4 — regions for design lanes, and the frozen state

> A design lane's region is read from its board child exactly as a remediation lane's is. Phase 4's
> collision check runs unchanged.
>
> A design lane resolves to a **third state**: **frozen** — the lane is dispatched, and its region is
> closed to *writes* by anyone else until the ruling lands. A design lane collides in **authority**,
> not in the working tree. A remediation candidate whose region is frozen by a design lane is **not
> dispatched**; it is reported as blocked-on-a-decision, naming the design lane.
>
> Measured 2026-08-25: `fund-cd` and `fund-c6` reached this by hand over `orchestrator/daily.py:58-82`
> — *"leaving it owned by neither protects it from both of us."* The rule is that instance generalised.
>
> **Design lanes prefer `covered` over `needs a chat`.** A brief needs no worktree, no branch and no
> merge. **Remediation lanes are what new chats are for.** On a morning the human never appears, N is
> the remediation count, and the design track still runs at full width on seats that already exist.

### Phase 5 — the design-lane briefing

> - the deliverable is a **decision brief**, posted as a comment on the lane's own issue
> - **it does not make the ruling, and it writes nothing into the repo**
> - state the **shape of the answer** in the first message back, before the evidence

## 3. Worked run

**Phase 0.** #49 → 16 children, all open. `sub_issues` order stable across three calls and matching
the map body (`39 38 40 42 41 46 43 44 45 32 35 17 6 18 4 3`) — `demonstrated`; map order readable and
not re-derived. `blocked_by`: #44=1, #32=1, #18=2, all others 0, no nulls.

Nine children carry live `map.md` rows → not candidates. #44/#32/#18 blocked. #45 region "assorted" →
flag. Leaves **#38, #42, #3**.

**Routing.** #38 → design, critical. #42 → design, high. #3 → no labels, but named `→ the human` in the
2026-08-25 digest → **carry-forward** → design.

**Remediation candidates: zero.**

Rule A: urgent remediation tier empty, U = 0. Rule B: #38 dispatched unconditionally (critical); the
2026-08-25 digest has no `## Decision batch` heading → **cold start, W = 1** → #42. #3 falls outside W.

> **Lanes tomorrow: #38 and #42. Two design lanes, zero remediation lanes. N = 0 chats.** The morning
> runs to completion with Benjamin asleep.

**Counterfactual — the nine sessions dead by morning:** release strikes their rows; Rule A urgent tier
= critical → #39 only, the eight-item tail reported not dispatched; design → #38 + #42. **Lanes: #39,
#38, #42. N = 3 chats.** Versus yesterday's nine.

## 4. What it costs

One decision batch (tomorrow: 7 items, 2 with briefs already written) + one boarding ask + N chats
(tomorrow: 0). **10–20 minutes, once.** Against 109 messages over seven hours.

**What decays:** the board itself — refreshed only by Benjamin answering the boarding ask; in 30 days
with no answers it is a fossil. The `## Decision batch` heading refreshes itself every run — the only
self-maintaining piece, and what makes W a feedback loop. The `needs-decision` label is refreshed by
filers and is already demonstrated broken once (#3); the carry-forward rule covers exactly that gap.

## 5. Failure modes

**The one I would bet on: the boarding ask goes unanswered and the board starves.** It is non-blocking
by constraint, costs real thought, and competes with the decision batch for the same attention in the
same message. Evidence already in: the digest carried the #3 labelling problem as flag 2, and
`gh issue view 3 --json labels` still returns `[]`. Making a flag more visible is not making it binding.

**A design seat rules instead of briefing.** The digest is 916 lines of precisely this. My only
structural counter is that the deliverable is *named* as options-plus-recommendation, so a seat that
ruled produces an artifact visibly missing its options. `fund-7a`'s objection stands against me.

**`needs-decision` gets over-applied** once the label routes work to a track whose deliverable is a
document — filing it becomes the cheap way to avoid implementing. No measurement, no counter.

**W stays at 1 forever.** The floor prevents death, not stagnation.

**A design lane writes into the repo.** I claimed a design lane produces no diff. **Counter-attempt
run, and it fails:** `prototype` and `writing-plans` are both installed and a design seat could
reasonably reach for either; `writing-plans` lands files in `docs/superpowers/plans/`. So this is
**not a property, it is an imposed rule with no enforcement.**

## 6. What I could not verify

That Benjamin answers a boarding ask at all (zero data — the board has been boarded once, by hand, by
an agent). That C is measurable (the credit loop has never closed). That a brief shortens the ruling
(plausible, untested; the counter-case is that options-plus-recommendation invites disagreement with
the *framing*). Whether the nine sessions are live tomorrow. Whether one seat can hold both a lane and
a design lane.

## 7. Strongest argument against this design

**It makes the highest-severity open issue in the repo unreachable, and calls that a virtue.** #63 is
`severity:critical`, `needs-decision`, and my design will not dispatch it tomorrow — it goes in a
boarding ask that has never once been answered, because the mechanism that answers it does not exist
on this machine. Meanwhile I *do* dispatch #38, a peer critical, purely because someone happened to
make it a sub-issue on 2026-08-25. **The board is not a priority decision at this point; it is a
fossilised snapshot of one audit, and treating it as the human's ordering is a fiction that constraint
1 obliges me to maintain.**

I built it anyway because the alternative re-derives lane order every morning from labels, and the
labels are unstable in exactly the way that makes that dangerous — `needs-decision` was on 2 issues
yesterday and is on 5 today, and `severity` is absent from 13 of the 31 off-board issues. I would
rather ship a design whose single failure is *loud, named, and one question wide*.

---

# Design B — any mechanism that needs upkeep will decay

## 1. Theory of the problem

The board is not stale, it is **write-protected by the skill's own doctrine**: boarding is an ordering
decision, ordering is Benjamin's, so no lane may board — and the one time a lane tried, it was
reverted in 68 seconds (`sub_issue_added #58` at `19:47:40Z`, `sub_issue_removed #58` at `19:48:48Z` —
demonstrated). `/wayfinder` being absent is a second, independent reason for the same outcome, so
fixing the missing skill would not fix the feed. Meanwhile the corpus *is* being written: 31 issues
filed in one day by nine lanes, 24 of them carrying a machine-readable `**Region:**` line nobody asked
for, and 3 of the 5 `needs-decision` labels applied at creation by lanes as a byproduct of being
blocked. So the skill is not short of design work — **design work is the one class its Phase 0
explicitly deletes**, via the "not waiting on a human" exclusion.

## 2. The design

**Design lanes are the repo's decision debt, dispatched as lanes whose deliverable is a brief, not a
patch. Defect lanes stay exactly as they are. The split is not a quota — it is a stream order, and
urgency is the only thing that flips it.**

Critically: **design lanes do not read the board.** They need a label and a region, both of which lanes
write themselves. Defect lanes need the board because their priority is a human judgement that only the
board holds. That asymmetry means the boarding gap degrades the defect side and leaves the design side
intact — the inverse of today, where the gap kills everything.

### Phase 0 — two streams, one order rule

Replace the single bullet **"not waiting on a human"** with a **type test**. Everything else in Phase 0
is unchanged.

> **A candidate lane is of exactly one type, and the type is read, never inferred from the title.**
>
> A **design lane** is an open issue that carries a label marking it as awaiting a ruling
> (`needs-decision`, or this repo's equivalent), **or** is named in the previous standup's
> `→ the human` block and still open. It need not be a child of the map. Its deliverable is a decision
> brief — the options, what each costs, measured — and never a patch.
>
> A **defect lane** is an open child of the map that is unblocked, unclaimed, region-declared, and is
> not a design lane. Unchanged from before.
>
> **Both types require a declared region.** A design lane reads a region and writes a brief; it does
> not write code — but it can invalidate work in flight there, so the collision check runs on it
> unchanged. (Do not claim design lanes cannot collide. Counter-attempt: #63's brief on partial-payload
> detection would land while #39's branch is in review and could force it rewritten. The claim fails;
> the check stays.)
>
> **Order.** Boarded lanes go in map order — still the human's priority decision, still not re-derived.
> Unboarded design lanes follow, in ascending issue number, the same fixed tiebreak this skill already
> mandates for unlabelled issues. Ascending issue number is a fixed rule stated *in the skill*, not a
> ranking computed from the morning's data: it produces the same order every morning from the same
> inputs, which is the property the prohibition exists to protect.
>
> **Which stream goes first is the only thing urgency decides.** If any **unclaimed** defect lane
> carries `severity:critical`, defects go first and design lanes take what capacity remains. Otherwise
> design lanes go first. Measured 2026-08-25: nine defect lanes, one PR ready, **zero merged**, because
> every branch waited on a ruling — remediation that terminates in an unmade decision is not
> remediation, so below critical the decisions go first.
>
> **Boarding drift is reported, never corrected.** An open issue whose body says `Part of #<map>` that
> the sub-issues API does not list is drift: report the count and the numbers, and do not treat it as a
> child. This skill does not board issues.

### Phase 2 — one line changed

Type marker per candidate line: `#63 — <title> [design: decision brief]`. Capacity field gains:

> A **design lane** is owned the same way and is not lighter work: you run it, fan out to measure each
> option, and hand back a brief the human can rule on in one reading. You do not make the call.

### Phase 3 — the decision block leads the digest

> **The digest opens with the decision block, before the per-session blocks.** One numbered line per
> open decision — every design lane, every design lane deferred to an in-flight owner, and every
> decision carried from a previous digest that this run cannot show retired. Each line states the
> decision in one sentence, the options if the issue names them, who is waiting on it, and **how many
> days it has been open**. Nothing else goes in this block. It exists so every ruling the run needs is
> made in one sitting rather than discovered one lane at a time over seven hours.
>
> A decision retires when its issue closes, when its label is removed, or when the digest names it
> ruled. **A decision ruled in another window is invisible to this skill**: it carries with a growing
> day count and is marked `possibly ruled elsewhere — unverified; checked: issue open, label present`.

### Phase 4 — a third resolution

> A design lane whose region collides with a live claim resolves to **deferred to its owner** — not
> `needs a chat`. The decision is one that owner is already exposed to; opening a chat on it buys a
> seat re-deriving what the owner knows. It appears in the decision block naming that owner, gets a
> `Concerns you` tail, and gets **no `map.md` row**, because nothing was bound.

### Phase 5 — the design-lane briefing

> - the terminal state is a **brief comment on the issue it names**: the decision restated in one
>   sentence at the top, the options, the measured cost of each, a recommendation with its evidence,
>   and the smallest reversible thing that could be done under each. It opens no branch and lands no code.
> - **it removes the `needs-decision` label once the ruling is in and comments the ruling on the
>   issue.** This skill writes nothing to the tracker; the lane is the only thing that can retire the
>   decision, and a decision nobody retires is dispatched again tomorrow.

## 3. Worked run

**Roster** (demonstrated): 16 sessions; 11 with `cwd == /Users/benjaminmatton/Developer/fund`. Window
start = `mtime(standups/2026-08-25.md)` = today 20:05; **only 4 of the 11 have transcript activity
since** → Phase 1 polls 3 peers and must report "polled 3 of 10". A real coverage gap.

**Claims:** all nine `map.md` sessionIds live → #39 #40 #41 #46 #43 #35 #17 #6 #4 claimed, none a
candidate, nothing released.

**What today's skill dispatches tomorrow: exactly one lane, #3** — `blocked_by == 0`, region declared,
**zero labels**, so the `needs-decision` exclusion does not fire. It is the one issue on the board that
two live lanes have frozen `run_stage` pending, and it is the only thing the skill would hand out.
Zero design lanes, and the one defect lane is the wrong one.

**What this design dispatches:**

Design lanes — 3, all region-declared: **#38** (label, map p2), **#42** (label, map p4), **#3**
(*named in the digest's `→ the human` block*, carries no label, map p16). #3 is the case that shows the
digest-carry rule earning its place: the label mechanism misses it entirely and the previous digest
catches it, using a file this skill wrote itself.

Deferred to in-flight owners — 3, no chats: **#63** (critical) → `fund-5b`; **#73** (high) →
`fund-5b`; **#78** (high) → `fund-46`. All three were **filed by the very lanes they collide with**.

**Defect lanes: zero.** Urgency check: is any *unclaimed* defect lane critical? #39 is critical but
claimed; #63 is critical but typed design. No → design stream first, and there is no second stream.

**Phase 5 asks: open 3 chats** (2 if #42's `charters/` region collides with #6's live lane).

**The decision block, tomorrow — the actual product of the run:**

1. **Live production** — the news seat still writing false "no news" into `signals`. Does tomorrow's
   run go ahead on the current charter? *carried, day 1*
2. **#38** (critical) — how do caps compose across a batch? *day 2, dispatched*
3. **#63** (critical) — partial-payload detection. → `fund-5b`. *day 1*
4. **#42** (high) — how do the Rule-of-Two legs split? *day 2, dispatched*
5. **#73** (high) — should a missing optional broker field halt the fund? → `fund-5b`. *day 1*
6. **#78** (high) — baseline re-creation and `.baseline` staleness. → `fund-46`. *day 1*
7. **#3** — checkpoint granularity; `run_stage:58-82` frozen pending it. *carried, day 1, dispatched*
8. **#45** — splits into children or gets scoped. *carried, day 1*
9. **Structural** — the shared root checkout, 21 ahead / 169 behind, corrupted four of nine lanes.
   *carried, day 1*
10. **PR #80** carries three commits belonging to another party. *carried, day 1*

Nine defect lanes yesterday produced zero merges and this list, discovered one item at a time across
seven hours. Tomorrow it is the first thing on the page.

## 4. What it costs

**Before the run: nothing.** **After: one sitting** — ten numbered sentences, ~7 rulings, plus "open 3
chats". 10–15 minutes (reasoned).

**What must be maintained: nothing new — and that is the point.** The design side runs on two inputs
neither of which anyone curates: `needs-decision` on an open issue (written by lanes for their own
reasons — #63 `20:12:39Z`, #73 `21:13:22Z`, #78 `23:23:36Z`, each labelled within 2 seconds of
creation, all by dispatched lanes during the run, not one curation pass), and the previous digest's
`→ the human` block (written by this skill every morning, unconditionally).

**The thing that decays: retirement, not application.** `needs-decision` is monotonic today — across
all five issues, `gh api …/timeline` returns **zero `unlabeled` events**. The set only grows. Who
refreshes it: the design lane's owner, as its terminal step. **That has never happened and I am
asserting it, not demonstrating it.**

**What this does not fix: the board.** In 30 days #49 will still hold 16 children, still zero closed.
The defect stream will have shrunk to nothing as its children get claimed or blocked, and the run will
be design lanes only. That is a degradation, not a collapse — and it is the honest consequence of a
board no mechanism may write to.

## 5. Failure modes

| Situation | Behaviour |
|---|---|
| No `needs-decision`-equivalent label | Design stream empty; skill runs as today. Say so once, flatly. |
| No previous digest | Design stream is the label set only. |
| Both feeds empty, board dead | Zero lanes; existing degradation, untouched. |
| A decision was ruled elsewhere | **Re-dispatched.** Nothing observes a ruling made in Benjamin's own window. The lane discovers it in ten minutes and closes reporting so. Cost: one chat. |
| `needs-decision` false negative (#54 is one today — body says *"This is a decision issue"*, label absent) | Dispatched as a defect lane. Measured: all nine lanes escalated rather than guessed, and three labelled their blocker within the run. A mistype self-corrects on a one-day lag, and the correction *writes the label*. |
| Every design lane collides | Zero chats, full decision block. A legitimate outcome. |

**The failure I would bet on: the decision block grows monotonically and becomes furniture.** Benjamin
rules on #38 in his own window, the label stays, `decisions.md` (last written **2026-08-20**) stays
unwritten, and item 2 reappears at *day 3*, then *day 9*, then *day 30*. Within two weeks the block
leads with items nobody reads and the one live item is buried at position 11. The day-count is a
symptom display, not a countermeasure. The only real fix is a decision that removes its own label, and
that fix lives in a briefing — an instruction, not an artefact, which is precisely the shape the digest
identified as the one that gets forgotten.

## 6. What I could not verify

That a dispatched design lane retires its own label (zero precedent — no unlabel event exists in this
repo; the whole self-maintenance argument rests on it). That a lane briefed to write a brief writes a
brief. That Benjamin rules from a digest — he has never been given a batched decision list, and if ten
items in one sitting is worse for him than seven over seven hours, the batching premise inverts. That a
decision brief is worth a chat. The type-test error rate — one false negative found (#54), one caught
by the digest fallback (#3), in a corpus of 48 I did not fully read.

## 7. Strongest argument against this design

**It reads a label to decide what kind of work a lane is, and Phase 0's whole point is that labels are
not allowed to decide anything.** I am putting label-derived judgement at the centre — with a
demonstrated false-negative rate already, on a label applied five times in the repo's history and
removed zero times. And the sharper version: **I am reading the previous digest's `→ the human` block
as an input to the next digest's dispatch, which makes the skill's output its own input.** One wrong
flag becomes permanent, self-reinforcing, and increasingly credible for having appeared every morning —
which is exactly the day's own recurring defect, *a signal that keeps looking healthy after the thing it
reports on has moved*, built into the skill by design.

**Why I built it anyway.** The alternatives fail worse. Boarding requires Benjamin to board, and the one
boarding attempt in this repo's history was reverted in 68 seconds by policy. A second map is a second
thing with no writer. Spec gaps need a docs layer that does not exist. Naming lanes each morning makes
design work move only when he is awake first. And the prohibition I am bending is narrower than it
reads: its stated ground is *"a different order every morning is worse than no order,"* and a fixed rule
written into the skill produces the same order every morning from the same inputs. I am not ranking with
labels — map order still governs everything boarded. I am **typing** with one label and **cutting the
stream order** with one preemption clause, both stated in the skill's own text. Put the constraint in
the artefact, not in the instruction.

---

# Design C — the dispatch decision is the dangerous one

## 1. Theory of the problem

The skill dispatches only defects because its board is a **hand-attached list that only a human can
extend**, and yesterday nine lanes filed 41 issues onto nothing. Design work is not missing from the
tracker; it is *already there* (5 `needs-decision` issues, plus #51/#54/#79) and Phase 0 **excludes it
by rule** — so the two most severe items on the board have been undispatchable since the board was
created. I accept the assigned frame's mechanism and reject its terminal condition: all nine of
yesterday's lanes *finished*, and 0 merged — so the test is not "can this lane be finished" but **"can
its output be absorbed by the one human who has to rule on it."**

## 2. The design

### Phase 0 — four amendments

**(a) Board membership becomes a union, not a fallback.** Today `Part of #<map>` is only read when
sub-issues are disabled. Make it a union:

```bash
gh issue list --state open --limit 200 --search '"Part of #<map>" in:body' --json number,title,labels
```

*demonstrated* — returns the 16 boarded plus **9** that declare board membership in their own body and
were never attached. One command; no new convention; the filer's declaration is read rather than the
skill guessing.

**(b) Order gets two zones, never interleaved.** Boarded children keep map order — untouched. Appended
children sort **below every boarded child**, by the same fixed severity sequence the degradation path
already uses (critical, high, medium, low, unlabelled last, ties by issue number). *This is the one
place I retreat from constraint 1, and I retreat deliberately:* nobody has ordered the appended tail, so
there is no human ordering to override — only a vacuum to fill. The moment a child is attached it leaves
the tail and takes map order.

**(c) One candidate list becomes two.** A candidate carrying `needs-decision` is a **design candidate**;
one without is a **remediation candidate**. Both still require unblocked, unclaimed, region-declared.

> A `needs-decision` child is never dispatched as a remediation lane — dispatching it gets a seat to
> guess at a ruling nobody has made. It is dispatched as a **design lane**, whose contract forbids it
> from implementing and forbids it from choosing.

*demonstrated* — all five `needs-decision` issues declare a region. Constraint 2 needs no exception.

**(d) The rider rule** — the new filter that does most of the work. A candidate whose declared region
head (the text before the em dash) is **string-equal** to a live lane's region head is not a candidate.
It *rides*: routed to that lane's owner as a note, recorded as `held-in-region`, never dispatched.
Region-head equality only — never a judgement about adjacency.

*demonstrated:* of the 24 off-board issues that declare a region, **14 declare a region head identical
to a live lane's** — eval harness ×6 (#58 #59 #61 #62 #67 #72), repo guardrails ×5 (#51 #68 #76 #77
#78), market data ingestion ×2 (#63 #73), purity lint ×1 (#69). Yesterday's 41 issues did not broaden
the board; they **deepened the nine regions already claimed**. Without this rule, "add a design quota"
puts a second seat inside a live seat's region fourteen times.

**(e) Map body and issue must agree.** A child the map names as awaiting a ruling that carries no
`needs-decision` label is dispatched as **neither** type — excluded, flagged `→ the human`: label it or
correct the map. Disagreement between two sources of truth is a stop, not something to resolve.

### Phase 4 — the mix ("the ladder")

Rungs govern **which list a seat draws from**, never position within a list.

- **Rung 0 — live.** Any candidate the *previous digest* recorded as currently producing wrong output
  in production and still open. Pre-empts everything, uncapped.
- **Rung 1 — critical remediation.** Draw while the remediation head carries `severity:critical`.
- **Rung 2 — critical design.** Draw while the design head carries `severity:critical`.
- **Rung 3 — alternation.** Design, remediation, design, remediation, over each list's own order, until
  one empties; then the survivor fills the rest. A `severity:critical` list head pre-empts before every
  draw.
- **Caps.** Design lanes ≤ half the run's lanes. Total lanes ≤ **6**. *This 6 is reasoned, not
  measured* — yesterday 9 lanes produced 41 issues, 109 messages and **0** merges, and the overseer's
  own audit says it could not batch decisions across nine. Phase 6 must record lanes dispatched /
  rulings raised / rulings drained so the next revision is measured.
- **The floor is never filled by invention.** Shortfall is a flag: "the alternation wanted N design
  lanes; the board supplied M."

So *the split is a precedence rule with a cap*, not a quota or a percentage. Severity governs the
**mix**; map order governs the **sequence**. Constraint 1 survives intact.

### Phase 5 — two briefing shapes, one new pin

A design lane's brief adds: **the deliverable is a decision packet in the issue body** — the question in
one sentence, every option with its consequence, what was measured vs. reasoned, and a recommendation it
is prepared to defend. **It does not choose, does not implement, does not open a PR.** And: *the overseer
relays the packet without ruling on it.*

Both shapes gain the **source pin**: the ref the lane reads against, plus "report the ref you read in
your first answer." *demonstrated* — `git rev-list --count master..HEAD` → **21**, `HEAD..master` →
**169**, `merge-base --is-ancestor HEAD master` → false. Four of nine lanes were corrupted by this. Plus
the **staleness obligation**: verify the issue body against the pinned ref *before sizing* (three of nine
lanes did this unprompted and each one shrank its lane).

### Phase 6 — the decision block

One message, numbered, every open ruling with its age, sent once at the end of the run and **re-sent
unchanged each subsequent run until each item is answered**.

## 3. Worked run

**Candidate pool** = 16 boarded ∪ 9 `Part of #49`, minus claimed/blocked/regionless.

- **Riders:** **#63 → fund-5b** (#39), **#73 → fund-5b**. Both `needs-decision`, both critical/high.
- **Excluded + flagged:** #45 (region "assorted"), #3 (map says needs-ruling, issue carries no label).
- **Design list:** #38 (map p2, critical), #42 (map p4, high).
- **Remediation list** (all appended, severity-then-number): #64 (high), #50, #53, #65, #66, #75, #79.

Ladder, cap 6, design ≤ 3: rung 0 empty (#6 is live but claimed); rung 1 empty; rung 2 → **#38**;
alternation → **#42**, **#64**, design list exhausted → **#50**, **#53**, **#65**.

> **Tomorrow's lanes: #38, #42 (design) · #64, #50, #53, #65 (remediation).** 2/6 design. Shortfall
> flag: the alternation wanted 3 and got 2; #63 and #73 are design work riding with #39.

Expect Phase 4 flags on #53 vs #40/#41, #65 vs #6, #79 vs #35. Realistic N: 4–6 chats; capacity-yes from
the nine incumbents is likely 0–2.

Yesterday's rule would produce **zero** design lanes tomorrow. This produces two, and they are the #1
and #2 severity items on the board, frozen since 2026-08-25.

## 4. What it costs

Read the decision block (~10 items) — **15–25 minutes**, most of it one-word answers. Plus one optional
attach (`gh api ... /sub_issues`) to move something out of the appended tail into his own order — ~1
minute. Zero if he sleeps through it.

**What decays in 30 days: the `needs-decision` label.** Applied by lanes as an escape hatch, not by a
maintainer — 5 applications, all on one day — and **already under-applied**: #54's body says verbatim
*"This is a decision issue: it names a choice for Benjamin, not a task to pick up"* and carries only
`severity:high`. My countermeasure is a *report*, not a type: the digest lists issues whose body says
decide/undecided/ruling and carry no label, as a flag. Runner-up decay: the `Part of #49` line (9 of 31
— boilerplate living only in the last lane's head), and #49's Order table, which the body itself calls a
mirror and which goes stale on the first attach. Note also `wayfinder:task` exists and is on **zero**
issues — the documented type taxonomy is already dead, which is why I did not build on it.

## 5. Failure modes

1. **The one I'd bet on — the design list starves silently.** Dispatch more design lanes → fewer
   remediation lanes filing new decisions → the design list empties → next morning the alternation asks
   for design, gets none, and the run reverts to all-remediation *by a path nobody notices*. That is
   exactly today's state reached invisibly. The shortfall flag is the alarm and it must never be filled
   silently.
2. **The rider rule swallows the best work.** Today it swallows the #1 and #2 severity design items. A
   long-running lane makes its whole region invisible to dispatch. Mitigation: a rider older than two
   standups is a flag `→ the human`.
3. **Region-head equality has an unmeasured false-negative rate.** I can already see one: #79
   (`tests/test_schema_contract.py`) vs #35 (`contract tests`) is a direct collision the rule misses.
4. **Absent/stale dependency.** No map issue → the union has no anchor; degradation unchanged. No
   `blocked_by` data → reported unavailable, unchanged.
5. **Bad day.** Master moves hard and six lanes all report "my premise is gone" in hour one. That is the
   correct output — strictly better than yesterday, where three lanes discovered it after sizing and four
   never discovered it at all.

## 6. What I could not verify

**That a lane whose *only* deliverable is a decision packet terminates.** Five existence proofs (#51 #54
#63 #73 #78, all genuinely well-formed) but every one was a **by-product** of a lane with a code
deliverable and a "tests pass" stop condition. A packet-only lane has never run and has no terminal
condition. Load-bearing and unverified. Also: that `needs-decision` keeps being applied (n = one day);
that 6 is anywhere near the right cap; that Benjamin wants design *prepared* by seats rather than done
himself.

**Counter-attempt I ran that failed to refute me:** I tried to find a lane that *guessed* at a human
decision rather than filing it. Five for five declined — #51 says outright *"no agent seat should make
it"*. The guessing happened one layer up, at the overseer, which is why the design forbids the overseer
to rule on a packet.

## 7. Strongest argument against my own design

**Tomorrow it produces two design lanes that Benjamin could have named in ten seconds.** Candidate
direction 4 — the human names them each morning — reaches the same answer with zero mechanism, zero
decay surface, and no retreat from constraint 1. Everything I built buys almost nothing on this
particular board, and it introduces a severity-ranked tail that is a genuine, arguable violation of the
rule Phase 0 exists to enforce.

I built it anyway because the human-named design fails the constraint the skill is actually under: it
runs **daily and non-blocking**, and until yesterday every run landed in degradation with nobody awake.
And more important: **the split is the symptom, not the disease.** The disease is 31 issues filed onto
nothing in one day, 14 of them inside regions a live seat already owned. The rider rule and the boarding
union are what address that, and they are worth their cost even on a morning when the split does nothing.

---

# Design D — a run is measured by what reaches done

## 1. Theory of the problem

The skill dispatches only defects because the board only holds defects, but that is a symptom: the real
defect is that **no phase of the skill has any concept of retiring work**, so a run's net output is
monotonically increasing — 3 issues closed in this repo's entire history against 48 open, and 134
commits sitting on 17 branches ahead of `origin/master` with 4 PRs and zero merges since *before* the
one dispatch ran. Design work is not missing from `fund`; it is **misfiled and unboarded**. I reject the
framing that this is a *lane-mix* problem: run today's Phase 0 against today's board and the remediation
stream is **empty**, because all nine children with a clean frontier are claimed by still-live sessions
— a design quota would be a quota over an empty set. A fixed morning dispatches from **three** streams
(remediate / decide / land), sizes them from a measured **retirement ledger** rather than a quota, and
ends with Benjamin holding **one** numbered decision list. Urgency does not choose *which* lanes — the
map already did that — urgency chooses **how many** the fleet may start, given how much it is currently
failing to finish.

## 2. The design

### What is already true and load-bearing (all `demonstrated`)

| Claim | Result |
|---|---|
| 48 open issues | `48` |
| Board #49: 16 children, **all open, none ever closed** | `[{"n":16,"s":"open"}]` |
| **3 issues closed, ever** | `3` |
| Last close preceded the dispatch | `#37 2026-08-25T18:04:54Z` vs dispatch `02:10Z` → **retired since dispatch = 0** |
| 17 branches unmerged into `origin/master`, **134 commits** | `17` / `134` |
| Only 4 have PRs; **0 merged since the dispatch** | #80, #34, #28, #24 |
| **All nine lane owners still live**, 11 fund-rooted sessions | `fund-4b fund-5b fund-cd fund-bf fund-57 fund-b3 fund-ba fund-7a fund-46 fund-c6 fund-b4` |
| **40 of 48 open issues already declare a region** in board shape | `WITH board-region: 40  WITHOUT: 8` |
| `needs-decision` on 5, three of them **off-board** | `[78,73,63,42,38]` |
| `wayfinder:task` exists and is worn by **zero** issues | `0` |

The last two together are the whole diagnosis of the boarding gap: `docs/agents/issue-tracker.md`
already documents child types and already requires every child to declare a region — and the fleet is
*already complying*, on 40 issues, unprompted. **What is broken is one API call nobody makes:
`POST /issues/49/sub_issues`.** No taxonomy needs inventing.

`reasoned`: because 40 of 48 issues are board-shaped, "derive design lanes from spec gaps" is
unnecessary as well as illegal under constraint 1. I also do not adopt a second board: it breaks Phase
0's "exactly one match is required".

### Phase 0 — becomes the board **and the ledger**

Phase 0 keeps its map discovery, its `null`-`blocked_by` rule, its four filters, and both degradation
shapes **verbatim**. Three things are added.

**0a — remediation stream: unchanged.** This stream is not broken.

**0b — the decision stream.**

> **Design lanes come from the repo's own `needs-decision` issues, on the board or off it.**
>
> A `needs-decision` issue is excluded from the *remediation* stream — that rule stands. It is **not**
> excluded from being a lane. It is a lane of a different type, whose deliverable is a **decision
> package**, and which carries **no authority to answer it and no authority to implement any option**.
>
> **A `needs-decision` issue that already carries a decision package is not a lane.** Dispatching a seat
> to restate an analysis a lane already wrote is the amplification failure in a new colour. The test is
> structural, not editorial: does the body or any comment enumerate **two or more named options**?
>
> ```bash
> gh issue view <n> --json body,comments --jq '.body + "\n" + ([.comments[].body]|join("\n"))' \
>   | grep -icE 'option|candidate answer|three candidate|choice'
> ```
>
> Package present → an **item in Phase 3's decision list**, never a lane. Package absent → **decide
> lane**. This check is a search, not a verdict: say what you grepped. Being wrong costs one seat
> writing a package that existed; not running it costs what 2026-08-25 cost.
>
> **A decide lane declares a region on the same terms as any other lane.** Measured: 40 of 48 open
> issues already carry `<!-- board-region -->`, including all five `needs-decision` issues, so the
> constraint costs nothing today and remains a real gate for a repo where it would.

**Why `needs-decision` and not a new label.** It is already applied, already correctly, by the lanes
themselves — #73 and #78 were filed with it by `fund-5b` and `fund-46` from inside their own branch
reviews, *before* those branches merged. A label the fleet already emits does not decay; a label I mint
today decays the moment I stop reminding people.

**0c — the ledger, and the split.**

> **Before any lane is dispatched, measure what the fleet has failed to retire.** `open` (open issues),
> `retired` (closed since window start), `unlanded` (local branches ahead of `origin/master`, split into
> *stranded* = no PR and *stalled* = PR open with age).
>
> **The split is a budget, not a quota.** A run may open at most **`retired + 1 + unblocks`** new chats,
> where `unblocks` is the number of decide lanes a live session named, in its poll answer, as the ruling
> blocking its own unlanded branch. The `+1` is the floor: a fleet that retired nothing must still start
> exactly one thing, because zero makes the ledger a trap that can never be escaped. The `unblocks`
> slots go to those specific decide lanes; the `retired + 1` slot goes to the top of the map-ordered
> remainder, remediation first.
>
> **Everything above the budget is reported, not dispatched**, with the cap and the withheld lanes
> named. Withholding is visible, and one line from the human overrides it.

This is the answer to *what happens when the board produces more work than it retires*: **it narrows the
mouth of the funnel in exact proportion to the blockage, and says so out loud.** It does not cap issue
*filing* — capping what a lane may discover is how you get a fleet that stops reporting. It caps what a
morning may *start*. And it is self-clearing: land three branches today and tomorrow's budget is 4+.

**0d — the landing stream.**

> **Unlanded work is a lane type.** Each unlanded branch is a candidate **land lane**: *open a PR with a
> named reviewer, or kill it with a one-paragraph obituary in the issue it served, or report the exact
> ruling it waits on.* A land lane's region is the union of the directories its diff touches
> (`git diff --name-only origin/master...<branch>`) — computed from the branch's own content, not
> derived from a title, an age, or a label, so it does not touch Phase 0's prohibition.
>
> **Land lanes open no new chats.** They bind only to the session whose work the branch is. A branch
> whose author is dead is an **orphan**, and orphans are drained at a fixed rate: **one orphan-sweep
> lane per run**, taking every orphan whose diff does **not** intersect a live lane's region, and
> reporting the ones it dropped. One sweep lane, not one per branch — triage of five branches is not
> five lanes of work.

**The one place urgency ranks anything.**

> **A standing lane outranks the budget.** An item the *previous* digest marked `⚠️ LIVE` — harm that is
> occurring, not possible — is re-dispatched every morning until its issue closes, and is not counted
> against the cap. This is not a derivation: the previous run wrote it down. The 2026-08-25 digest named
> the distinction itself: *"Why this is different from everything else in the queue: the tense. The
> other items are rulings. This is current production output."*

### Phase 2 — poll

> **Candidate lanes are listed under three headings — `remediate` / `decide` / `land` — and the capacity
> field states what each type produces.** A session too deep for a remediation lane is very often not
> too deep for a land lane on a branch it wrote itself; that is the cheapest capacity in the fleet, and
> it is invisible to the current poll.
>
> **Seventh field — `stranded`:** *"Branches you or your subagents left ahead of `origin/master` and
> unmerged. For each: `ready` / `dead` / `blocked-on: #<issue>`. Check it — do not recall it."*

This is the field that converts "zero merges was determined by hour two" from a post-mortem into an input.

### Phase 3 — digest

> **1. The digest opens with the ledger, in one line.**
> `open 48 · retired 0 since 2026-08-25 · unlanded 17 branches / 134 commits (13 stranded, 4 stalled) ·
> budget 3`
>
> **2. `Decisions for Benjamin` is a section, and it is an obligation.** Every item requiring a ruling,
> one numbered list. Each entry: the issue, the question in one sentence, the options with what each
> costs, and **what stays stopped until it is answered**. **No ruling leaves this run except in that
> list.**
>
> **3. Drift becomes two-sided.** Add: **issues opened since the window start, counted, with the lane
> that filed each.** Yesterday that was 41 across 9 lanes and nothing in the skill could see it.

### Phase 4 — reconcile

> **A lane carries a type, and the type is part of the binding.** Never silently retype.
>
> `decide` ∩ `decide` on one region **is** a collision → `/get-aligned`. `decide` ∩ `remediate` is **not**
> a collision — a decide lane writes no code — it is a **pairing**, and the decide lane reads the
> remediation lane's thread first and addresses that owner as its first correspondent. `land` ∩ anything
> **is** a collision: a branch and a live lane in the same code are the merge conflict, not a risk of one.
>
> **Land lanes bind by authorship**, or to nobody. Where it must go to a non-author: *"You did not write
> this. Your first job is to establish whether it should live, not to finish it."*

### Phase 5 / Phase 6

N is the count of `needs a chat` lanes **after the budget**; state the budget and name what it withheld.
Type-specific briefing clauses inlined (decide: *"You own the question, not the answer… you may not
choose it, and you may not implement any option. If your reading makes the question moot, say so — that
is a valid package and the strongest one."* / land: *"Do not extend it. A branch that grows during a land
lane has failed the lane."*). `map.md` rows gain **type** and **branch** columns; a land-lane row whose
branch is merged is released and struck. The ledger line is written as the first line of the standup file.

## 3. Worked run

**Ledger:** `open 48 · retired 0 · unlanded 17 branches / 134 commits (13 stranded, 4 stalled) ·
unblocks 2 → budget 3 new chats`.

**0a — remediation stream: EMPTY.** Board children minus the nine claimed leaves #38, #42, #44, #45,
#32, #18, #3. #44/#32/#18 blocked; #45 region "assorted" → flagged; #38/#42 carry `needs-decision`. **#3
is the only survivor** and it routes to 0b. **Today's board dispatches zero remediation lanes.**

**0b — package test** run on all six:

| # | hits | Verdict |
|---|---|---|
| #73 | **5** — body §"The open question" states it verbatim | **package present → decision item, not a lane** |
| #78 | **10** — *"Three candidate answers…"* | **package present → decision item, not a lane** |
| #38 | **0**, 0 comments, 309 words | **decide lane** |
| #63 | **0** | **decide lane** |
| #42 | **1** | **decide lane** |
| #3 | **1** — names one fix, no alternatives | **decide lane** (carry-forward; carries no labels) |

This inverted my own expectation — I assumed #73/#78 would be the design lanes because they are the most
active. The test says the opposite: the lanes that filed them *already did the design work*, and
dispatching a seat at them would be the fleet auditing its own audit. That is the check earning its place.

**Budget allocation.** `unblocks = 2`: #63 and #3 take the earned slots. The `retired + 1` slot goes to
**#38** (map position 2). **#42 is withheld and reported.**

**0d — land lanes**, bound to authors, opening no chats: PR #80 (`fund-46`, blocked-on #78);
`fix/positions-payload-validation` (11 commits, `fund-5b`, blocked-on #73); `fix/order-recording-reconcile`
(13, `fund-cd`); `fix/purity-lint-evasions` (25, `fund-b3`, self-blocked); `test/schema-spec-contract`
(9, `fund-ba`); `fix/preflight-live-schema` (6, `fund-7a`); `fix/news-get-news-limit` (4, `fund-57`,
**⚠️ LIVE standing lane**); `fix/reflection-writer-idempotent` (4, `fund-c6`); `fund-bf`'s three branches
as one lane.

**Orphan sweep — one chat.** Five orphans; region-intersection drops `docs/adr-stop-amend` (→ #39, #35),
`worktree-model-usage-volumes` (→ #40), `news-seat-eval` (→ #41). Survivors: **PR #24 `docs/vision`** and
**`docs/overseer-bookends`**, docs-only, no live lane.

> **Open 4 chats.** decide **#63**, decide **#3**, decide **#38**, and the **orphan sweep**.
> **Withheld by budget:** decide #42. **Remediation stream: empty.**
>
> **Decisions for Benjamin — 4 items, and every one stops a branch:** #78 (stops PR #80), #73 (stops 11
> commits), #42 (nothing in flight waits), #3 (label it or correct #49's Fog section — recorded as
> human-blocked in the map body for two runs while carrying zero labels).

Nine seats spend the day finishing. Four chats open. **Zero new remediation lanes** — which is correct,
and is a thing the current skill has no way to say.

## 4. What it costs

**One read and up to four rulings — target 10 minutes.** Every entry states what stays stopped, so
triage is by consequence rather than by reading four issue threads. He answers nothing to make the run
proceed.

**The thing that decays is the fleet's `needs-decision` habit**, and the refresher is not a person: it is
the line in `owning-a-lane` and in Phase 5's decide briefing. A rule in the artefact, per the day's own
finding, rather than a rule the overseer repeats. **Visible in the ledger the day it happens**, because
the `decide` count goes to 0 while `open` keeps rising.

**#49's child order cannot go stale by neglect** — the 16 children are static and the design adds nothing
to it. **Total new persistent surface: two columns in `map.md` and one line at the top of the standup
file.** No second board, no new labels, no spec-gap diff, no `wayfinder:*` types created.

## 5. Failure modes

| Situation | What it does |
|---|---|
| Budget starves a real emergency | The `⚠️ LIVE` standing lane bypasses it entirely; withheld lanes are named and one line overrides. |
| `git fetch` fails / no remote | `unlanded` unavailable → report it as unavailable, set `unblocks = 0`, fall back to `retired + 1`. Do **not** treat "cannot measure" as "zero stranded" — that is the `null == 0` mistake one level down. |
| No branches ahead of master | The cap does not apply. The design is invisible in a healthy repo, which is the point. |
| Repo never adopted the board | Unchanged. 0b still runs — a label query, not a board query. 0c still runs — git and PRs exist everywhere. **The degradation path gets strictly more useful, not less.** |
| A decide lane rules anyway | Detectable, not preventable — the package goes into the issue where Benjamin sees the answer sitting under a recommendation. |
| A land lane extends its branch | The next run's ledger shows `ahead` going up on a branch that had a land lane. **Measured, not asserted.** |

**The failure I would bet on: `unblocks` gets gamed by optimism.** It is computed from a self-report. A
session that wants its question answered will report `blocked-on:` where the honest answer is `dead` or
`needs one more day`, and every such report buys a new chat. Yesterday's digest documents three sessions
confidently mis-identifying *themselves*; a session mis-classifying its own branch's blocker is a far
easier error. The budget then inflates in exactly the direction that defeats it, and it inflates while
looking healthy — the day's own recurring shape. **The countermeasure is one line and it is the weakest
part of the design:** a `blocked-on: #N` claim only earns a slot if `#N` is open and carries
`needs-decision`. That catches the fabricated blocker; it does not catch the sincere one.

## 6. What I could not verify

That the `stranded` field gets honest answers at all — **Phase 2 has never run**, and the seventh field is
untested along with every field beside it. That a decide lane produces a package a human can rule from in
a minute — no decide lane has ever run anywhere, and the whole `+unblocks` term assumes it. That
`owning-a-lane` does not fight the land-lane brief — its description says *"yours through to done"* and it
contains no merge or PR guidance, so "kill it with an obituary" may read as abandoning a lane. That
authorship of a branch is recoverable — `git log --format=%an` says the same GitHub user for all of them.
That 11 live sessions is the steady state and not an artefact of one day.

## 7. Strongest argument against this design

**It answers a question nobody asked, and the thing it adds — the budget — is a governor on a fleet whose
one measured day produced 41 issues that are, by the overseer's own accounting, mostly good.** The task
was *dispatch a mix of defect and design lanes, split by urgency*. This design's split is not by urgency
at all. A reasonable reading is that I substituted my frame's question for Benjamin's and built the
machinery for mine. Worse: `retired + 1` would have permitted **one** lane on the morning of 2026-08-25 —
the day that produced #58, #61, #67, #72 and the six-instance fails-open pattern, none of which was
visible that morning and all of which the overseer explicitly classified as *not waste*. **A design that
would have prevented that day from happening is a design with a very heavy burden of proof, and I cannot
discharge it: I have one day of data and the throttle would have vetoed it.**

**Why I built it anyway.** Because the throttle would *not* have vetoed that day — it would have deferred
eight of its nine lanes by roughly one day each, and every one of those eight is still open and still live
twenty hours later, so nothing would have been lost that has since been gained. And because the
alternative is worse in a way that is measured rather than hypothesised: run today's Phase 0 against
today's board and the remediation stream is **empty**, in a repo with 41 issues filed in a day, 134
unmerged commits, and 3 closes in its entire history. **The category finding — "instrument work
self-replicates" — is not a reason to cap discovery; it is a reason to make the fleet's inability to
retire visible on the line above the lanes, every single morning, in one number Benjamin cannot skip.**
If the budget bites too hard, it is one arithmetic expression in one phase, and the ledger it is computed
from is the part I would fight for.
