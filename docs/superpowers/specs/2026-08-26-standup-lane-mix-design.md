# Design: `/morning-standup` dispatches a mix — remediate, decide, land

**Date:** 2026-08-26 · **Status:** design, not yet planned
**Written by** sessionId `27fcd833-4173-47d6-aedf-75bd73cd0014` (name at time of writing `fund-b4`;
`fund-4b` is a *different* live session — key on the id).
**Supersedes nothing.** Amends `2026-08-24-standup-dispatch-design.md`, which stays current for every
phase this document does not touch.

**Provenance:** produced by four independent designers run in parallel, each given a different theory
of the problem and no visibility into the others; their verbatim returns and the comparison are in
`2026-08-26-standup-lane-mix-candidates.md` alongside this file. Reviewed by sessionId
`34c9cded-5ccb-4137-91d9-df75a5603996`, the overseer of the 2026-08-25 dispatch, which falsified one
load-bearing assumption and corrected two rules. §10 records what each contributed and what was
rejected.

---

## 1. Problem

`morning-standup` dispatches only defect remediation. That is not a policy choice — it is a
consequence of two independent facts:

1. **The board only holds defects, and it cannot grow.** `docs/agents/issue-tracker.md` names
   `/wayfinder` as the board's maintainer; `/wayfinder` is not installed on this machine and never
   has been. Board #49 was hand-created `2026-08-25T18:06:02Z`, holds 16 children, and **not one has
   ever closed.**
2. **Phase 0 deletes the design work that does exist.** Its `not waiting on a human` filter excludes
   any child carrying `needs-decision`. That filter was written for a good reason — dispatching such
   a child "gets a seat to guess at" a ruling nobody has made — but its effect is that the two most
   severe items on the board have been undispatchable since the board was created.

So the request "dispatch a deliberate mix of defect and design lanes" cannot be answered by adding a
quota. A quota over an empty set produces nothing.

**And the run has no concept of retiring work.** On 2026-08-25 nine lanes ran for seven hours and
produced 2 issues closed against 41 opened, one PR ready and zero merged. Nothing in any phase can
see that, name it, or report it.

---

## 2. What was measured

Every count carries the command that produced it. Measured 2026-08-25/26 against the live API and
`master` — never against the root checkout, which is detached 21 ahead of a divergent line and 169
behind (`git rev-list --left-right --count master...HEAD` → `169 21`).

> **Every number below is a snapshot, and this fleet moves them in hours.** While this document was
> being written: three branches committed between two consecutive commands; the board went 16 → 22;
> open PRs went 1 → 10 with 2 merged; closed issues went 3 → 5; `wayfinder:task` went from worn by
> zero issues to worn by six. **Nothing here should be read as a standing property**, and §6's rule —
> a classification is an input to a brief, never a verdict — is the general form of that.

| Claim | Command | Result |
|---|---|---|
| 48 open issues | `gh issue list --state open --limit 200` | 48 |
| Board #49: 16 children, all open, none ever closed | `gh api .../issues/49/sub_issues` | 16, all `open` |
| **25** open issues declare `Part of #49`; **16** attached | `gh issue list --state open --search '"Part of #49" in:body'` | 9 unattached: #50 #53 #63 #64 #65 #66 #73 #75 #79 |
| A boarding write was made and **reverted in 68 seconds** | `gh api .../issues/49/timeline --paginate` | `sub_issue_added #58` `19:47:40Z` → `sub_issue_removed #58` `19:48:48Z` |
| **3** issues closed in the repo's entire history | `gh issue list --state closed --limit 200` | #5, #26, #37 — last at `18:04:54Z`, *before* the dispatch began |
| **Zero lane-initiated label retirements, ever** | `gh api .../issues/N/timeline` over #3 #38 #42 #45 #51 #54 #63 #73 #78 #80 | one `unlabeled` event total: `#78 02:14:06Z benjaminematton` — the overseer's own severity bump |
| `needs-decision` on 5 open issues, 3 of them off-board | `gh issue list --label needs-decision` | #38 #42 #63 #73 #78 |
| `wayfinder:task` was worn by **zero** issues, then six | `gh issue list --state all --label wayfinder:task` | 0 at 2026-08-25; **6** after the 04:16Z boarding — the taxonomy was dead and is now in use |
| **The board grew for the first time: 16 → 22** | `gh api .../issues/49/timeline --paginate` | #61 #72 #58 #67 #59 #62 added `2026-08-26T04:16:24Z`–`04:16:40Z`; #58's 68-second removal is thereby superseded |
| `/wayfinder` not installed | `find ~/.claude ~/.claude-work -maxdepth 4 -iname '*wayfinder*'` | nothing |
| Local branches carrying unique commits | `git rev-list --left-right --count origin/master...<ref>` per local ref, `ahead > 0` | 12 at re-measurement — **and this row moved while being measured**; three refs changed state between two consecutive commands. An earlier claim here that `--no-merged` overcounts by ~25 is **retracted**, see §6 |
| All nine 2026-08-25 lane owners still live | `claude agents --json` filtered by exact-path-or-containment | 11 fund-rooted sessions |

**Three of these change the shape of the problem.**

**The current skill dispatches exactly one lane tomorrow, and it is the wrong one.** #3 has
`labels: []`, `blocked_by: 0`, and a declared region, so it passes every candidate filter. It is also
the issue `fund-c6` and `fund-cd` deliberately froze `orchestrator/daily.py:58-82` pending, because
they judged it a human's call. The label that would stop it was never applied.

**The board is write-protected by doctrine, not merely unmaintained.** Installing `/wayfinder` would
not have changed the #58 revert. Boarding is an ordering decision and ordering is the human's; a lane
that boards an issue is making that decision. Both causes produce the same starvation and each is
sufficient alone.

**No lane has ever performed a registry action.** All nine reported back unprompted and in detail.
None ever closed an issue, removed a label, or attached a child — and the one lane that tried to
attach one had it reverted. This falsifies the assumption that a design lane can be relied on to
retire its own marker as a side effect, and §4 is written accordingly.

---

## 3. Three lane types

| Type | Deliverable | Never |
|---|---|---|
| **remediate** | a fix, on a branch, as today | — |
| **decide** | a decision package on the issue: the question in one sentence, the named options, the cost of each, what is demonstrated vs reasoned, and a recommendation it will defend | makes the ruling; implements any option; opens a branch |
| **land** | exactly one of: a PR with a named reviewer · a closed branch with a one-paragraph obituary in the issue it served · a `blocked-on: #N` naming the exact question | extends the branch |

A lane's type is **read, never inferred from a title**, and is part of the binding: a session that
takes #38 takes it as a decide lane, and Phase 4 never silently retypes.

### Every lane ends in a durable artifact that is not a message to the overseer

> A remediate lane ends in **one or more PRs, each carrying `closes #N`**. A decide lane ends in a
> decision package on the issue. A land lane ends in a merged PR or a written obituary. **A report to
> the overseer is not a terminal state** — it does not survive the session, is not observable to the
> human, and closes nothing.

**Not one PR per lane.** Measured: #41 produced three unrelated changesets (PRs #89, #90, #91), and
forcing them into one bundles unrelated work to satisfy a rule; #4 became a feature and will produce
no PR for days, its terminal artifact being a ten-item decision package; #35's deliverable was partly
a *verification* — three measured runs proving a failure mode closed — which no diff carries.

**`closes #N` is the answer to the closure gap — and it is a convention, not a mechanism.** Nine lanes
ran seven hours and closed zero issues, because they refuse registry writes on principle. Then #39
closed `COMPLETED` 88 seconds after PR #87 was cross-referenced, #46 closed via PR #80, and #35, #43,
#17, #67 and #6 followed the same way. **Every write was performed by GitHub on merge; none was
performed by a lane.** Open issues went 48 → 43 in one night, after three closures in the repo's
entire prior history.

**But the reference is optional and its omission is silent.** Measured on this repo at one instant:
of **31 merged PRs, 8 carried a closing reference**, and three of the most recent eight omitted it
(#86, #89, #91). Nothing warns and nothing fails — the fix ships and the issue stays on the board
reading as outstanding work. **The counts are already stale** (the merged total passed 40 within
hours); what does not expire is the shape, and the skill's own prose states the shape rather than the
number, because it ships to repos these figures say nothing about. So the design cannot say *the merge does the registry write*.
It says **the brief requires the reference, and a remediate lane's terminal artifact is incomplete
without it** — otherwise the plumbing is built and the tap left optional.

**That gives the ledger a checkable defect it did not have:** a merged PR whose head branch maps to a
lane and whose body carries no closing reference. A query, not a judgement — and it measures whether
the *record* is consistent rather than whether the lane was productive, so it avoids the over-fitting
that counting PRs would introduce.

The exception in Phase 5 therefore narrows to exactly one case: **a decision has no merge event**, so
`needs-decision` cannot be retired by plumbing and a decide lane must still retire it as a correction.

**Second-order, and it was chosen deliberately by two lanes:** every lane that merged locally created
the `master` divergence; every lane that opened a PR did not. `fund-7a` and `fund-cd` both chose
PR-over-local-merge on the reasoning that the repo's convention is CI-green-before-merge.

**Caution, and it constrains the ledger:** PR-per-lane makes a lane's output *look* complete when the
valuable part was a finding. #43's best output was *"a generated gate measures its generator"*, which
no PR contains. **The ledger counts PRs; it must never read PR count as a lane's worth**, or it
undercounts the finding lane and overcounts the mechanical one.

**Land lanes are formed in Phase 4, after the poll — not here.** Phase 0's ledger *counts* unlanded
branches, and that count is a measurement, not a candidate list. A land lane needs two facts only the
poll can supply: **who owns the branch**, and **which issue it served**, whose region becomes the
lane's region. Forming one here would mean knowing the answers the poll exists to ask.

**So the rider rule never pre-empts on a land region.** No land lane exists before the poll, so
`anything ∩ land` is not computable in Phase 0 and is not attempted. It resolves in Phase 4, where both
sides are known.

**`backup/*` and other rebase-safety refs never become land lanes** — snapshots, not work. **And if
`git fetch` fails, the unlanded count is *unavailable*, not zero**, so the land stream is unavailable
too: report it that way rather than running a morning with no land lanes and no reason given.

### The split

**There is no quota, no ratio, and no cap.** Every candidate that survives the filters is dispatched.
The split is a **precedence order over the three streams**, and urgency does exactly one thing: it
decides which stream draws first.

1. **A standing lane pre-empts everything.** An item the *previous* digest marked `⚠️ LIVE` — harm
   that is occurring, not harm that is possible — is re-dispatched every morning until its issue
   closes. This is not a derived priority: the previous run wrote it down, and Phase 3 already carries
   facts forward. The 2026-08-25 digest named the distinction itself — *"the other items are rulings.
   This is current production output."*
2. **Then, if any unclaimed remediate candidate carries the top severity tier present among
   remediate candidates, remediation draws first. Otherwise decisions draw first.** The reason is
   measured: nine defect lanes produced zero merges because every branch waited on a ruling.
   *Remediation that terminates in an unmade decision is not remediation.*

   **If no unclaimed remediate candidate carries any severity label, there is no top tier present** —
   say so in one line and draw decisions first. A fact about the labels, not a judgement about the
   work, and the same fallback the skill already applies to an unordered open-issues report.

3. **Land lanes bind by authorship and consume no chats**, so they sit outside the order entirely.

**What the precedence order actually rations.** With no cap, it does not limit how many lanes exist —
it decides which lanes get the seats, and the seats are the real constraint: live sessions plus the
chats the human opens. State this plainly in the skill so a later reader does not mistake the order
for a throttle. **The fleet's size is the cap. The precedence order decides who gets the seats.**

---

## 4. Phases

Every rule in `2026-08-24-standup-dispatch-design.md` not named here is unchanged.

### Phase 0 — board, streams, ledger

**0a — board membership is a union, not a fallback.** Today `Part of #<map>` is read only where
sub-issues are disabled. Read both, always:

```bash
gh api repos/{owner}/{repo}/issues/<map>/sub_issues --jq '.[] | {number, title, state}'
gh issue list --state open --limit 200 --search '"Part of #<map>" in:body' --json number,title,labels
```

Attached children are the board, in map order — unchanged, still the human's priority decision, still
never re-derived.

**Issues that declare membership but were never attached are the unattached. They are reported, and
never dispatched from the board's order.** No *dispatch* order is invented for them. Attaching is the
only way into the board's order, and attaching is the human's act. (**One carve-out**, defined under
Phase 0b: a *decision-bearing* unattached issue is still a decide lane, because a decide lane does not
draw on the board's ordering at all. An unattached issue with no ruling label is reported-only.)

**Reporting order and dispatch order are different things and only the second is forbidden** — the
skill already ranks a report by severity where no map order exists. So the digest names them: the
count, plus the issues at the top severity tier present, and the exact attach command beside them:

```bash
gh api -X POST repos/{owner}/{repo}/issues/<map>/sub_issues -f sub_issue_id=<id>
```

**Attaching is how the human re-orders, and it is the only way into dispatch.** One call, ten
seconds. The skill never attaches anything: boarding is an ordering decision, and the one time a lane
made that decision it was reverted in 68 seconds.

> **Why they are not simply dispatched in severity order.** That is a *dispatch* order nobody chose.
> The reasoning behind the boarding decision recorded in the previous spec's §7 — *"a map with
> seventeen children in an invented order is worth less than one with five in a real one"* — applies to
> a dispatch queue exactly as it applies to a map. Severity-ranking additionally lets one label edit
> reorder it overnight, which is the instability the prohibition exists to prevent, and severity is
> absent from 13 of the 31 off-board issues.

**Naming:** this set is **the unattached**, never "the tail" — `SKILL.md` already uses "tail" for the
personalized suffix on each peer's broadcast copy, and two meanings of one word in a document an agent
reads literally is a defect.

**0b — `needs-decision` routes; it no longer excludes.** Replace the `not waiting on a human` bullet:

> - **decision-bearing** — a child carrying a label marking it as awaiting a ruling (`needs-decision`,
>   or this repo's equivalent) is **not excluded; it is routed.** `blocked_by` models issue-to-issue
>   edges only, so such a child reports `blocked_by == 0` and reads as ready. Dispatching it as
>   *remediation* gets a seat to guess at the ruling — that rule stands and its reason stands. But
>   excluding it altogether turns the item nobody can start into the item nobody ever works. It is a
>   **decide lane**, whose deliverable is the package that makes the ruling cheap and whose explicit
>   prohibition is making the ruling.

**Decide lanes are board-independent, and this is the one carve-out from the unattached rule.** A
decision-bearing issue is a decide lane whether or not it is an attached child, so the candidate
list's opening clause — *an open child of the map issue* — does not bind a decide lane. Every other
property still does: unblocked, unclaimed, region-declared.

**The carve-out is narrow and its edge is exact.** It reaches an unattached issue *only* where that
issue carries the ruling label or is carried forward from the previous digest. An unattached issue
with no such label stays reported-only, exactly as Phase 0a says.

**Why it is safe.** A decide lane's deliverable is a package for the human, not a change to the code
the board orders — so it neither consumes the board's priority ordering nor needs a place in it.
Remediate lanes do need the board, because their priority is a human judgement only the board holds.
That asymmetry is the point: the boarding gap degrades the remediate stream and leaves the decide
stream intact, which is the inverse of today, where the gap kills everything.

**This does not re-derive priority from labels.** Routing is not ordering. Boarded children keep map
order; a label decides only which of three deliverables a lane owes. Phase 0 already reads labels this
way — `refuted` suppresses, `needs-decision` excluded.

**0c — carry-forward.** A lane named under the previous digest's **`## Decisions for the human`**
heading — read the heading, not the prose around it, since Phase 3 writes exactly that string every
morning so this rule has something to find rather than a section to infer — and still open is a decide
lane whether or not it carries the label. The digest is this skill's own durable record; a decision it
reported yesterday does not become undecided because nobody labelled the issue. Where the label and
the carry-forward disagree, take the union and report the disagreement in one line.

*This is what catches #3, which the label mechanism misses entirely and which the current skill would
otherwise hand to a seat tomorrow morning.*

**0d — the package test.** A `needs-decision` issue that **already carries a decision package** — two
or more named options with their consequences — is an item on Phase 3's decision list, **never a
lane**. Dispatching a seat to restate an analysis a lane already wrote is the amplification failure in
a new colour.

Pre-filter with a search, then read:

```bash
gh issue view <n> --json body,comments --jq '.body + "\n" + ([.comments[].body]|join("\n"))' | wc -l
```

**The search is a pre-filter, not the verdict.** The overseer reads the body of each candidate — there
are five today — and reports what it found, quoting the option list where one exists. A grep hit count
is a verdict dressed as a measurement; under Phase 3's absence rule, say what you read. Reading five
issue bodies is not the expensive part of a day in which the overseer read two files.

**A consequence must be stated, not implied.** Naming two branches — *do X, or do Y* — is not a
package; what makes it one is that each option carries what it costs or forecloses, written down, so
a human can rule without reconstructing the argument. Measured by hand against this board: #73 (four
named directions, each with its tradeoff) and #78 (both answers argued symmetrically) are packages
and are rulable cold from the artifact rather than from the thread; #38 names two branches and states
the consequence of neither, so it is not.

**Uncertain → a decide lane, and say so.** An issue whose package is arguable is one whose options are
not clearly stated, which is exactly what a decide lane produces — and the decide-lane briefing in
Phase 5 makes *"the package already existed and the question is moot"* a valid answer and the
strongest one, so nothing is lost by choosing that direction under doubt.

**This resolves and then reports; it does not decline to resolve.** That is the carry-forward pattern
— take the reading, state the disagreement — and deliberately *not* the `blocked_by == null` pattern,
where the child is left unrouted because no reading is available at all. Here a reading is available
and merely uncertain.

Being wrong toward a lane costs one seat restating a package that existed. Being wrong toward an item
puts a half-argued decision in front of the one person whose attention the list exists to protect.
Not running the test at all costs a fleet re-deriving its own findings.

**0e — the rider rule.** A candidate whose declared **region head** — the text before the em dash — is
string-equal to a live lane's region head is not dispatched. It **rides**: routed to that incumbent as
a decision request, recorded `held-in-region`, and named on the decision list. Region-head equality
only, never a judgement about adjacency; anything subtler goes to the poll and Phase 4 resolves it
against what sessions say they own.

**A rider carries the ruling, never the work.** Measured: two sideways handoffs on 2026-08-25 both
produced good answers fast — one of them three reasons to refuse, including one the overseer had
missed — and **neither incumbent did the work**. `fund-5b` filed #63 and #73 from inside its own #39
review and worked neither. *Filing is where a lane offloads, not where it queues.* So a rider whose
item needs implementation returns to the dispatch pool rather than sitting with the incumbent.

**The rider rule is not type-blind — it applies the same pairwise test Phase 4 uses.** Riding runs
*before* the poll, so a pair resolved silently here never reaches Phase 4's matrix at all. Resolve the
pair before riding:

- **`decide` ∩ `remediate`** — not a collision. The decide candidate is **dispatched and paired** with
  the remediate lane; it does not ride. A decide lane writes no code.
- **`decide` ∩ `decide`** — a collision. The later in order rides, **and the pair is flagged
  `→ /get-aligned` on the decision list** rather than silently resolved. Phase 4's handling exists for
  exactly this pair, and a silent ride is what would stop it ever firing.
- **anything ∩ `land`** — the candidate rides, `held-in-region`.
- **`remediate` ∩ `remediate`** — the later in order rides.

**Ordering between an attached candidate and a board-independent decide lane on one region:** the
attached one goes first. Map order is a decision a human made; board-independence is an exemption from
needing one, not a claim to precedence. State it rather than leaving it to whichever the query
returned first.

**A rider older than two standups is a flag `→ the human`**, or a long-running lane makes its whole
region invisible to dispatch. **Its age comes from the decision list**, where every rider is named with
how many days it has been open and that count carries forward the same way a decision's does — not from
a stamp nothing writes.

**A region is claimed when either test holds, and both run every time: a live lane holds it, or a lane
this same run has already dispatched holds it.** This belongs in the rule's opening definition, not as
a later refinement — a reader who anchors on a one-test topic sentence implements one test. Compare against
both, or a run that releases an incumbent dispatches every candidate in that region at once. Measured:
when `fund-bf` exited, #41 was released and the five `wayfinder:task` children sharing its region head
had no incumbent left to ride to — six candidates, one region, and a rule that only checks *live*
lanes sees no collision at all. Where several candidates share a region head and nothing claims it,
**the first in map order is dispatched and the rest are `held-in-region` behind it**, named on the
decision list as a split question exactly like riders behind a live incumbent.

*Measured against the lane regions recorded in `map.md`: 14 of the 24 region-declaring off-board
issues name a region head identical to a live lane's — eval harness ×6, repo guardrails ×5, market
data ingestion ×2, purity lint ×1. Yesterday's 41 issues did not broaden the board; they deepened the
nine regions already claimed. Without this rule, adding a design quota puts a second seat inside a
live seat's region fourteen times.*

**Known false negative:** region-head equality misses #79 (`tests/test_schema_contract.py`) against
#35 (`contract tests`). Phase 4 catches it only if the incumbent answers the poll. Report the rule's
shape, not a claim that it is complete.

**0f — map and issue must agree.** Where the map body records a child as awaiting a ruling and the
issue carries no such label, 0c makes it a decide lane **and** the digest asks for the label in one
line: label it, or correct the map. Do not resolve the disagreement silently in either direction.

**0g — the ledger. Measured, reported, governs nothing.** One line, first line of the digest:

```
open 48 · retired 0 since 2026-08-25 · filed since 2026-08-25: 41 · unlanded 14 branches / 101 commits
(9 blocked, 1 stale-substantive, 4 stale-thin) · 3 refs at +0 with uncommitted worktrees ·
instruments 28 / fund 17 · measured 2026-08-26T04:12Z
```

**The ledger line carries the instant it was measured**, because on a live fleet it is perishable:
three of these refs changed state between two commands issued one after the other (§6).

- **`retired`** — issues closed since the window start.
- **`filed`** — issues opened since the window start, with the lane that filed each. Yesterday that
  was 41 across 9 lanes and nothing in the skill could see it.
- **`unlanded`** — see §6. **Counted by `ahead > 0`** — the instrument that says what it means — and
  **never with `-a`**, which adds remote-tracking refs that duplicate the local branches.
- **`instruments / fund`** — a hand count with its method stated, under the absence rule. It is the
  variable that actually predicted the 41-opened ratio, and it is a free-text classification, so it is
  reported and never dispatched on.

**If `git fetch` fails, `unlanded` is *unavailable*, not zero** — the same `null ≠ 0` discipline
Phase 0 already applies to `blocked_by` one level down.

### Phase 2 — poll

Candidate lanes are listed under three headings, `remediate` / `decide` / `land`, and the capacity
field states what each type produces. A session too deep for a remediation lane is very often not too
deep for a land lane on a branch it wrote itself — the cheapest capacity in the fleet, and invisible
to the current poll.

The **blocked** field gains one clause: *if what unblocks you is a decision only the human can make,
say so and name the decision.* Those answers join the decision list, from a source that needs nobody
to have labelled anything.

**Seventh field — `stranded`:**

> Branches you or your subagents left ahead of `origin/master` and unmerged. For each, **two things**:
> its disposition — `ready` / `dead` / `blocked-on: #<issue>` — and **the issue it served**, as
> `serves #<issue>` or `serves nothing`. Name the issue in every case, not only when blocked: that
> issue's region becomes the land lane's region, and a branch with no issue named cannot be dispatched.
> Check it — `git rev-list --left-right --count master...<branch>` — do not recall it.

### Phase 3 — the decision list is an obligation

> **The digest opens with the ledger, then the decision list, then the per-session blocks.**
>
> **The decision list carries human-bound items only.** One numbered entry each for: decide-lane
> packages, packages found by 0d, riders routed to incumbents and anything else resolved `held-in-region`, `blocked-on:` answers from the poll,
> and anything carried from a previous digest that this run cannot show retired. Each entry states the
> question in one sentence, the options with what each costs, **what stays stopped until it is
> answered**, and how many days it has been open.
>
> **The list is written under the fixed heading `## Decisions for the human`.** Phase 0's
> carry-forward reads that heading in the previous digest, so it must be spelled the same every
> morning — a rule that finds a section by prose inspection is a consumer with an unreliable writer.

> **No human-bound decision leaves the run except in that list.** It is assembled once and reported once. **It is assembled here and completed after Phase 5**, because two of its inputs do not exist yet: `held-in-region` resolutions come from Phase 4 and unbound lanes from Phase 5. Phase 3 holds a draft; Phase 6 writes the finished list. It is still reported exactly once — an item discovered mid-run joins the list rather than getting its own message.
>
> **Technical rulings are not batched and stay serial with the overseer.**

> **Drift becomes two-sided.** The existing drift lines report work no issue covers and issues
> someone treats as done that are still open. Add the other direction: **issues opened since the
> window start, counted, with the lane that filed each.** On one measured day that was 41 across
> nine lanes and no phase of this skill could see it. Report it as a fact, not a verdict.

> **Mark a standing item `⚠️ LIVE`, and this is the only thing in the skill that produces one.** Where
> the run records harm that is *occurring* — wrong output reaching production now, not a defect that
> could produce some — mark that item `⚠️ LIVE`. §3's precedence order reads the marker from the
> *previous* digest and pre-empts everything with it, so **a run that sees live harm and does not mark
> it leaves the next run blind to it.** Without this producer the pre-emption rule is a consumer with
> no writer — which is the same defect as a board whose only maintainer does not exist (§1).

> **A decision ruled in another window is invisible to this skill.** It carries with a growing day
> count, marked `possibly ruled elsewhere — unverified; checked: issue open, label present`. Say what
> was checked.

**The human-bound-only restriction is load-bearing and was measured.** On 2026-08-25 the technical
rulings were genuinely serial: the #39 ruling turned on `orchestrator/protection.py:352-357`, which
surfaced only because the lane went and read it — the overseer ruled early, ruled wrong, and was
reversed by that file. The human-bound decisions were independent by construction — the v2→v3 fixture
bump, #4's D1 placement, the committed-baseline ruling — and were sittable at hour two but trickled
across seven. **Batching the first class would have made it worse. Not batching the second is what
cost the day.**

### Phase 4 — reconcile

Type is part of the binding; never silently retype.

- **`decide` ∩ `decide`** on one region **is** a collision → `/get-aligned`. Two packages over one
  region produce two contradictory recommendations.
- **`decide` ∩ `remediate`** on one region is **not** a collision — a decide lane writes no code. It
  is a **pairing**: the decide lane reads the remediation lane's thread first and addresses that owner
  as its first correspondent.
- **`land` ∩ anything** on one region **is** a collision. A branch and a live lane in the same code
  are the merge conflict, not a risk of one.

**`held-in-region` is the general state, not a land-lane special case.** Any candidate whose region is
already claimed resolves to it — claimed by a live lane, by a lane this run has already dispatched
(a `land` lane included), or by a `land` lane no author is available for. This is the same claim test
Phase 0's rider rule runs; Phase 4 only names the resolution.

**So the other party to a `land` collision has a state.** A `decide` or `remediate` candidate sharing a
region with a `land` lane is **`held-in-region` behind it** — not covered, and not needing a chat. A
land lane is short and is about work that already exists, so letting it reach its terminal state clears
the region rather than racing it. Without this rule that candidate matches none of the three states and
falls through, which is the defect this phase exists to prevent.

**Land lanes are formed here, from the poll's answers — this is the only thing that creates one.**
Every branch a session claimed in `stranded` becomes a land lane **owned by that session**, carrying
**the region of the issue that session named**. Both facts come from the same answer, which is why
formation waits for the poll.

- A branch whose owner named **`serves nothing`** is region-undeclared: flagged `→ the human`, never
  dispatched — the same treatment any region-undeclared candidate gets.
- A branch **no session claimed** is an **orphan**. **An orphan has no region**, because the only thing
  that names one is an owner it does not have. So orphans are never region-matched — inventing a
  comparison for them is the failure this skill spends its Phase 0 forbidding.
- **All orphans gather into one sweep lane per run**, which resolves to **`needs a chat`** — the one
  land-shaped lane that does, precisely because it has no author. Its first job is to establish, for
  each branch, whether it should live; that determination is what produces a region, if any. **It is
  the only lane in this skill briefed to a non-author**, and its briefing says so.

Once formed, a land lane resolves through the matrix above like any other candidate.

**Land lanes bind by authorship, or to nobody.** Where one must go to a non-author, the briefing says
so plainly: *"You did not write this. Your first job is to establish whether it should live, not to
finish it."* Reconstructing another session's intent from a diff is the most expensive guess
available.

### Phase 5 — dispatch

Type-specific clauses, inlined because a briefed session may never invoke the pointer:

- **remediate** — unchanged.
- **decide** — *"You own the question, not the answer. Produce a decision package on the issue: the
  named options, what each costs, the evidence for each, and the one thing the human must choose. You
  may not choose it and you may not implement any option. If your reading makes the question moot, say
  so — that is a valid package and the strongest one."*
- **land** — *"This branch is ahead of `origin/master` and unmerged. Your deliverable is exactly one
  of: a PR with a named reviewer; a closed branch with a one-paragraph obituary in the issue it
  served; or a `blocked-on: #<issue>` naming the exact question. **Do not extend it.** A branch that
  grows during a land lane has failed the lane."*

**Every lane's brief carries the source pin**: the ref to read against, plus *"report the ref you read
in your first answer."* Four of nine lanes were corrupted on 2026-08-25 by an unpinned source, and two
sessions read their agreement as corroboration. And the **staleness obligation**: verify the issue
body against the pinned ref *before sizing*. Three of nine lanes did this unprompted and each one
shrank its lane.

**Registry actions are reported deliverables, never side effects.** Where a lane is asked to retire a
label, close an issue, or comment a ruling, that action is named in its deliverable list and reported
back, and the next run verifies it observationally. **Nothing in this design may rest on an unreported
registry action.** Measured: across the ten decision-bearing issues there is exactly one `unlabeled`
event in the repo's history and it was performed by the human, not a lane. Nine lanes performed zero.

**And the norm the lanes enforce is not "don't write to the registry" — it is sharper than that, and
the sharp version is what makes this instruction acceptable to a well-behaved seat.** `fund-bf`
declined to correct #41's stale body because *"its staleness is itself evidence"*, and on the same
night **rewrote a paragraph out of six issue bodies** — the "deliberately NOT a child of #49" note,
true when filed and false once those issues were boarded. Same lane, same night, opposite actions. The
rule that predicts both:

> **A lane will not overwrite a record that is evidence. It will correct a record that has become
> false and would mislead the next reader.**

#41's staleness *is* the evidence — it records what was believed on 2026-08-25. A "do not board this"
paragraph on a boarded issue is a stale **instruction**, and the next reader acts on it. Evidence
versus instruction, not write versus don't-write.

**So Phase 5 states the retirement as a correction, not as an exception.** Once the ruling is
recorded, `needs-decision` is a false instruction: it tells every future run that a decision is
outstanding when it is not. Correcting false instructions is already what these lanes do unprompted
and uninstructed. Framed as a carve-out from a norm, a good seat may refuse it and be right to;
framed as the case its own judgement already covers, no exception is needed. **The lane retires the
marker on its own decision, records the ruling that made it false, and touches nothing that is
evidence.**

*Caveat, and it is the reviewer's own: this is one lane on one night. The blunt rule came from three
observations and survived four hours. Treat the sharp rule as better-fitting, not as established.*

### Phase 6 — record

`map.md` lane rows gain a **type** column and, for land lanes, a **branch** column. Release gains one
clause: a land-lane row whose branch is merged into `origin/master` is released and struck, checked
observationally with `git branch --merged origin/master`, never by asking.

The ledger line is written as the first line of the standup file, so the next run reads the trend
rather than re-deriving it.

---

## 5. What does not change

Daily, cheap, one round, no arbitration. Flags surfaced, never resolved. The digest goes to every
rostered peer including the silent ones. A peer cannot assign work to a peer. **No phase blocks on the
human being awake**, and a run always produces its digest. **The skill still creates no branch, no
worktree, and no commit** — it reads, it messages, and it writes two files under `~/.claude/align/`.
Both degradation shapes stand verbatim, and both get strictly more useful: 0b is a label query and 0g
is a git query, and neither needs a map issue. **Disputed — see §9.13**, where the skill's own text
reads a no-board repo as having no declared regions, which would leave 0b with no decide lane to form.

---

## 6. The branch pile, measured

**RETRACTED, and the retraction is the more useful finding.** This section previously claimed that
`git branch --no-merged origin/master` overcounts by ~25 refs carrying no unique commits. **That claim
was false, and it failed the first executing counter-attempt run against it** — during Task 5 of the
implementation plan, an implementer refused to ship the assertion, and re-measurement confirmed it:

```
git branch --no-merged origin/master | wc -l                    -> 13   (12 branches + the detached-HEAD line)
count of local branches with ahead > 0 vs origin/master         -> 12
```

The two instruments **agree**. The original ~25 came from comparing `git branch **-a** --no-merged`
— which includes remote-tracking refs that duplicate local branches — against a *local* `master` that
is not `origin/master`. **Two different baselines, and the gap between them was reported as a property
of `--no-merged`.** That is precisely the defect this document is about, committed in this document,
by its own author.

**What survives.** `ahead > 0` is still the right instrument, for a smaller and more honest reason: it
says exactly what it means, where `--no-merged` conflates *behind* with *has unique work*. And the
practical rule is `-a` — **never count branches with `-a`**, because remote-tracking refs duplicate
the local ones.

**The candidate test is `ahead > 0`.** That gives **14 branches carrying 101 commits**, in three
states:

| State | Branches | What a land lane does |
|---|---|---|
| **blocked, not stale** — all `0` behind. 9 branches, 56 commits | `fix/purity-lint-evasions` +25, `fix/order-recording-reconcile` +10, `fix/positions-payload-validation` +8, `test/schema-spec-contract` +6, `fix/preflight-live-schema` +3, `fix/tamper-guard-evals` +1 (PR #80), `fix/news-get-news-limit` +1, `fix/reflection-writer-idempotent` +1, `docs/progress-2026-08-25` +1 | clean fast-forwards waiting on rulings — `blocked-on: #N`, and the ruling joins the decision list |
| **stale, substantive** — 1 branch, 32 commits | `docs/adr-stop-amend` +32/−39, pushed | a human decision, not an orphan-sweep item: 32 commits of abandoned substantive work |
| **stale, thin** — 4 branches, 13 commits | `docs/overseer-bookends` +5/−39, `worktree-model-usage-volumes` +4/−67, `docs/vision` +2/−73 (PR #24), `news-seat-eval` +2/−109 | orphan sweep, one lane per run, skipping any whose diff intersects a live lane's region |

**Nothing in today's set is rotting.** A land-lane design that assumes staleness is wrong for the
nine branches that matter.

**A fourth state exists, and measuring it taught the rule that governs all four.**
`docs/evals-saturation-open-coding`, `fix/duplicate-case-ids` and `fix/evals-tag-counts` read `+0/−0`
— ref created, nothing committed. That reads as *no work*, and it was wrong: two of the three held
uncommitted changesets in their worktrees (a 731-line re-measurement; a 17-line fix plus an untracked
test file), and the third was clean only because it had committed seconds earlier. **The state is
`written, never committed`, and nothing in a ref-based view can see it.**

**Then all three committed while this section was being written** — 86 seconds, 88 seconds and 2
minutes between two measurements taken one command apart — and went to `+1/−8`, `master` having moved
under them in the same window.

> **The rule this produces, and it governs every land lane: a branch's state is a measurement with a
> lifetime of minutes, and a land lane may never act destructively on one.**
>
> - **Never reap a ref on a classification.** A `+0/−0` ref is *reported*, never swept. Deleting it is
>   a destructive act justified by a measurement that may already be false, and on this fleet it would
>   have destroyed reviewed work twice in one afternoon.
> - **The classification is an input to the brief, not a verdict.** A land lane's brief states the
>   ref state it was classified on, with the command and the time, and the lane's **first job is to
>   re-measure** — the same staleness obligation the other two lane types already carry. Uniform
>   across all three types, so no seat has to remember which kind it is.
> - **`0 behind` is perishable too.** The nine blocked branches are clean fast-forwards *as measured*;
>   `master` moved 8 commits during this measurement alone.
> - **Adding a worktree check does not fix this**, it only moves the race. The fix is that nothing
>   destructive keys on the classification at all.

**Two `backup/pre-rebase-*` refs** (+9/−169, +14/−144) are excluded by name: they are rebase safety
nets, not work, and a land lane pointed at one would be reconstructing intent from a snapshot.

---

## 7. Worked run — tomorrow, against the real board

**The board is now 22 children**, six having been added at `2026-08-26T04:16Z` — the first growth
since it was created.

Nine children carry live `map.md` rows → claimed. #44/#32/#18 blocked. #45's region is *"assorted"* →
flagged. #38 and #42 carry `needs-decision`; #3 carries no labels but is named in the previous digest.

**All six new children have region head `eval harness`** — which is #41's region, held live by
`fund-bf`. Every one is `blocked_by == 0`, region-declared, and unclaimed, so every one is a clean
remediate candidate, and every one **rides**.

- **remediate dispatched: zero.** Six new candidates, six riders. *The board grew by six and the
  dispatchable set grew by nothing* — because instrument work deepens the regions already held rather
  than broadening the board. That is the category effect stated as a measurement instead of a
  prediction, and it is the strongest evidence for the rider rule in this document.
- **decide: #38, #42, #3.** #73 and #78 carry packages → decision-list items, not lanes. #63 has no
  package but its region head equals #39's → **rider to `fund-5b`**.
- **riders: seven** — #61 #72 #58 #67 #59 #62 → `fund-bf`; #63 → `fund-5b`.
- **land:** nine author-bound (no chats), one stale-substantive to the human, four to the orphan
  sweep, three `+0` refs reported and not touched.
- **N = 3 chats**, minus whatever the incumbents take.

> **Riders are reported grouped by incumbent, and a group larger than two is a flag `→ the human`, not
> a queue.** Six decisions dropped on the seat holding #41 is not routing, it is a split signal —
> `/split-the-plan` on that lane, which is the human's call and not the standup's. Without this the
> rider rule quietly converts one overloaded region into one overloaded seat. *The threshold of two is
> reasoned, not measured.*

**Decision list:** #78 (stops PR #80) · #73 (stops 11 commits) · #63 (rider, `fund-5b`) · #42 · #3
(label it or correct #49's Fog section) · #45 (split or scope it) · the six riders on `fund-bf` as one
split question · `docs/adr-stop-amend` (32 pushed commits, author dead) · the shared root checkout ·
the ⚠️ LIVE news-seat charter question.

---

## 8. Failure modes

| Situation | Behaviour |
|---|---|
| No `needs-decision`-equivalent label in the repo | Decide stream empty. Say so once, flatly, as a fact — never a daily question. |
| No previous digest | No carry-forward and no standing lane; the decide stream is the label set only. |
| `git fetch` fails / no remote | `unlanded` **unavailable**, not zero. Report it as unavailable. |
| Repo never adopted the board | Unchanged, and strictly more useful: 0b and 0g both run without a map issue. **Disputed — see §9.13**, where the skill's own text reads a no-board repo as having no declared regions and so no decide lane to form. |
| Every decide candidate is a rider | Zero decide lanes, full decision list. A legitimate outcome. |
| A decide lane rules anyway | Detectable, not preventable — the package arrives with the answer sitting under a recommendation. |
| A land lane extends its branch | The next run's ledger shows `ahead` rising on a branch that had a land lane. Measured, not asserted. |
| A decision was ruled in the human's own window | Re-dispatched; the lane discovers it in minutes and closes reporting so. Cost: one chat. |

**The failure to bet on: the decision list grows monotonically and becomes furniture.** `needs-decision`
has five applications and zero lane-initiated retirements in the repo's history, and
`~/.claude/align/fund/decisions.md` was last written 2026-08-20. If rulings land in other windows and
no registry action records them, item 2 reappears at day 3, then day 9, then day 30, and the live item
is buried at position 11. **The day count is a symptom display, not a countermeasure.** The
registry-action-as-deliverable rule in Phase 5 is the only structural answer, and it is asserted
rather than demonstrated.

---

## 9. Load-bearing and unverified

> **What the review of this branch covers, and what it does not.**
>
> This branch has been reviewed at the level of text, across ten passes, and never executed. What that
> review covers: cross-phase contradiction; terms used before definition or used in two senses;
> consumers with no producer and producers with no consumer; candidate states that no rule resolves or
> that two rules resolve differently; operators applied across kinds; and justifications wider than the
> rules they carry. Eleven defects were found this way, none of which required execution to see, and the
> state grid has been derived independently twice to the same result.
>
> What it does not cover: whether the commands in the text return the fields the rules read; whether
> live sessions answer in the shapes the poll asks for; whether the timing rules behave as described
> against the real messaging primitives; and whether any lane type produces useful output. Phases 2, 4,
> 5 and 6 have never executed at all — the one real dispatch had a roster of zero (§9.6) — so the first
> run remains the first test of the phases this branch is built on, independent of anything in it.

**Every finding of that review is text-level reasoning, never a demonstration.** It found real defects
in the text and proves nothing about runtime behaviour. Items 7–13 below are what it could not reach.
The measured claims in items 1–5 carry their own provenance and are unaffected by it.

1. **That a decide lane retires its own label when told to report it as a deliverable.** Zero
   precedent — nine lanes performed zero registry actions. This is the single largest assumption.
2. **That a lane whose only deliverable is a decision package terminates.** Five well-formed packages
   exist (#51 #54 #63 #73 #78) but every one was a *by-product* of a lane with a code deliverable and
   a "tests pass" stop condition. A package-only lane has never run and has no terminal condition.
3. **That the human rules from a batched list.** He has never been given one. If ten items in one
   sitting is worse than seven over seven hours, the batching premise inverts.
4. **That `needs-decision` keeps being applied.** n = one day. It is already under-applied: #54's body
   says verbatim *"This is a decision issue: it names a choice for Benjamin, not a task to pick up"*
   and carries no such label.
5. **That map order is the human's decision — which the API cannot confirm.** Every session
   authenticates as the same GitHub user, so `sub_issue_added` carries actor `benjaminematton` whether
   the human attached the child or an agent did. The original 16, the #58 revert, and the six added at
   `04:16Z` are indistinguishable in the timeline. The previous spec established this for assignees
   (*"claims are not GitHub assignees"*); it applies to boarding, and boarding is where the entire
   priority order comes from. **The skill's foundational input is unauthenticatable.** The only
   available countermeasure is to report the change, never the author: the digest states that the
   board changed since the last standup, by how much, and which children are new — under the same rule
   that lets a session report an absence but never verify one.

   **Where metadata cannot carry provenance, content can.** The skill cannot authenticate a writer, but
   a writer can describe itself: **any registry write states in its own body what performed it and
   why.** Measured — one issue closed with no linked PR event, and the closer was recoverable only
   because the session that did it left a comment naming the merge commit and explaining the gap. That
   is not authentication, and a lying writer still lies; it defeats the failure this design actually
   cares about, which is a *later reader* unable to reconstruct what happened. **Self-describing
   writes, not authenticated ones.** The same argument is why `decisions.md` sits unwritten: a line an
   agent wrote would be indistinguishable from one the human wrote, which breaks the file's evidence
   property for every future reader, not merely for that entry.
6. **Phases 2, 4, 5 and 6 have still never executed.** The one real dispatch had a roster of zero — no
   poll, no reconcile, no binding, no broadcast. This design adds two lane types and a seventh poll
   field to phases that are entirely untested. **The first run under it is still the first test of the
   phases underneath it.**

### First-run unknowns, ranked

**Item 7 is the only one on this list that is falsifiable before any dispatch.** 8–12 are discoverable
only by running. All are reasoned; none has been measured.

7. **Do region heads declared in issue bodies and region heads given in `owns` poll answers ever match
   by string equality?** **The one item here that can be falsified before a single lane is dispatched.**
   Six review rounds rest on the assumption that they do, and **nobody has seen a single matching
   pair.** If they do not match in practice, every candidate reads as free: the rider rule (0e) never
   fires, Phase 4's collision matrix is inert, and a run dispatches lanes into each other's regions —
   **an active harm, not a missing feature.** The check is cheap: dump the region heads declared in
   issue bodies alongside the `owns` answers from a single poll and look for any pair matching by
   string equality. **Ten minutes, and it gates the value of the whole design** — §7's worked run, the
   rider rule, and every `held-in-region` resolution all stand on this one comparison succeeding.
8. **That `gh api` returns `sub_issues` and `issue_dependencies_summary.blocked_by` in the shapes
   Phase 0 reads.** §2 exercised the `sub_issues` call by hand and it returned `{number, title, state}`
   — but by hand, never down Phase 0's own path, and **`blocked_by` carries no row in §2 at all.**
   Phase 0's `null ≠ 0` discipline is written against a field whose shape has not been measured.
9. **That a real peer answers the `stranded` poll in the two-part shape asked, rather than in prose.**
   Phase 4 forms every land lane from both halves of that answer — disposition *and* `serves #<issue>`,
   whose issue supplies the lane's region. A prose answer leaves the region underivable and the land
   stream empty, and no peer has ever been asked (§9.6).
10. **That `notify_when_idle`, the 2-hour collection cap, and the 20-minute empty-roster window behave
    as described against real `SendMessage`.** Three timing rules read out of tool documentation and
    never run. The poll's whole shape — one round, no arbitration — assumes all three.
11. **That the orphan sweep, briefed to a non-author, produces anything but noise.** Phase 4 makes it
    the only lane in the skill briefed to a session that did not write the work, and it is **the one
    lane type with no precedent at all**: remediate ran nine times on 2026-08-25, decide and land each
    have partial analogues in that run, the sweep has none.
12. **That a lane ever retires a marker.** Not a new item — **§9.1**, named there as the single largest
    assumption with zero precedent. Listed here only so the first-run set is complete; it is the one
    assumption on this list that an active norm cuts against (Phase 5).

### A text gap, settleable by reading

13. **Whether a board-independent decide lane can exist in a repo with no map issue is unsettled, and
    the skill and this spec disagree.** The skill states that with no map issue there are no
    sub-issues and therefore **no declared regions at all** — a claim about sub-issues generalised into
    a claim about the repo. But a decision-bearing issue carries its region in its own body, so in a
    no-board repo holding `needs-decision` issues, declared regions *do* exist and Phase 4 has
    something to run against. **§8's own row says the no-board case is *"unchanged, and strictly more
    useful: 0b and 0g both run without a map issue"* — and 0b is the decide routing.** So the spec's
    stated benefit for no-board repos is the thing the skill reads as impossible. The failure mode is
    not a wrong dispatch: it is **the decide stream silently never forming, in the repo shape this
    design was most meant to help.** This is a contradiction between two texts, not an execution
    unknown — it is settled by reading, not by running. **To settle before this ships to a second
    repo.**

---

## 10. How this design was produced, and what was rejected

Four designers, run in parallel, independently, each with a different theory of the problem and
explicit permission to reject it. Returns in `2026-08-26-standup-lane-mix-candidates.md`.

**Converged independently, all four:** `needs-decision` must route rather than exclude; one batched
decision list as an obligation; design lanes declare regions with no exemption; spec-gap derivation
rejected; a second board rejected. Three of four independently invented the rider/defer mechanism.

**Adopted:** the board-independent decide stream and the stream-order split (B); the `Part of #49`
union read and the rider rule (C); the package test, the ledger, and land lanes (D); the
carry-forward rule (A and B, independently).

**Rejected, with the reason:**

- **A retirement budget capping dispatch** (D). Its own strongest argument defeats it: `retired + 1`
  would have permitted one lane on 2026-08-25, the day that produced #58, #61, #67 and #72. The ledger
  is kept; the governor is not. Ruled by the human: measure it, do not govern on it.
- **Earned design width `W = 1 + decisions cleared`** (A). The credit loop has never closed — no
  digest carries a machine-readable decision section — so it cold-starts at 1 and cannot be measured.
- **A severity-ranked or issue-number-ranked tail** (C, B). Both are invented orders. The tail is
  reported and attaching is the only way into dispatch. Ruled by the human.
- **A hard cap of six lanes** (C). Reasoned from a single run, not measured.
- **Splitting `remediate` on instrument-vs-fund** (proposed in review). It is the variable that
  predicted the ratio, but deriving it means classifying a free-text region string, and capping on it
  is the throttle already declined. It goes in the ledger as a counted number with its method stated.

**Falsified in review by sessionId `34c9cded`, and re-verified here:**

- **The label-retirement assumption** (§2, §9.1). The design was rewritten around it.
- **The rider carrying work.** Two measured sideways handoffs produced good answers and no work.
  Riders now carry the ruling only.
- **The batching obligation's scope.** Technical rulings were genuinely serial and batching them would
  have made them worse. The obligation now covers human-bound items only.
- **The land-lane branch model.** Today's branches are `0` behind, not stale; and a fourth state
  exists that the three lane types missed. The reviewer then **corrected its own correction**: it first
  called that state *started-never-written*, and on being challenged found the work existed
  uncommitted in the worktrees — *"my error was inferring the working-tree state from the ref count, a
  by-construction claim I did not run a counter-attempt against."* Had the first version shipped, the
  orphan sweep's disposition would have deleted reviewed work.

**Confirmed directly by Benjamin in the authoring session, not relayed.** The durable-artifact rule
(§3) reached this session as a peer's account of his words — *"each overseer's lane should encapsulate
a PR"* — and `~/.claude/align/fund/decisions.md` was unchanged (mtime 2026-08-20), so it was treated
as a claim and put to him directly. He confirmed the **durable-artifact form** and declined strict
1:1 lane-to-PR. The `closes #N` plumbing stands on its own evidence independently of either.

**Found here, in neither the designs nor the review:**

- ~~`--no-merged` overcounts the branch pile by ~25 refs carrying no unique commits~~ — **retracted,
  see §6.** The claim compared two different baselines; measured like-for-like the two instruments
  agree. It was broken by an implementer who declined to ship it unverified, which is the plan's
  verify-before-asserting step doing exactly its job on its own author.
- **The branch classification is racy on a timescale of minutes.** All three `+0/−0` refs committed
  between two consecutive measurements, and `master` moved 8 commits in the same window. This is the
  day's own recurring defect — *a signal that keeps looking healthy after the thing it reports on has
  moved* — reaching the one lane type whose disposition is destructive. It is why no land lane may reap
  a ref, and why the classification is an input to a brief rather than a verdict (§6).
- **Lanes refuse registry writes on principle, not from neglect**, which turns §9.1 from an untested
  assumption into an instruction that cuts against an active norm (Phase 5).
