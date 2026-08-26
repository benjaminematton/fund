# `/morning-standup` lane mix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a standup run dispatch three lane types — `remediate`, `decide`, `land` — instead of defect remediation only, with a precedence order rather than a quota, and with every lane ending in a durable artifact.

**Architecture:** The deliverable is **prose in two skill files**, not code. `morning-standup/SKILL.md` gains rules in Phases 0, 2, 3, 4, 5 and 6; `owning-a-lane/SKILL.md` gains the terminal-artifact contract for the two new types. Nothing executes these files but an agent reading them, so each task's test is a **read-only command that proves the rule the prose states is actually computable against the live repo**, with its output compared to what the prose claims. That is the whole point: this design exists because the fleet kept finding rules that looked right and measured nothing.

**Tech Stack:** Markdown. `gh` CLI (GitHub REST + issue/PR subcommands), `git`, `jq`, POSIX shell. No language runtime, no test framework, no build.

**Source of truth:** `docs/superpowers/specs/2026-08-26-standup-lane-mix-design.md` (in `fund`). Where this plan and the spec disagree, the spec wins — stop and report the divergence rather than reconciling it yourself.

## Global Constraints

- **Two repos, and the skills one is edited through a worktree.** All SKILL.md edits and their commits land in **`/Users/benjaminmatton/Developer/skills-wt/lane-mix`**, on branch `feat/lane-mix-clean` (repo `git@github.com:benjaminematton/claude-skills.git`). The spec and `docs/agents/issue-tracker.md` live in `fund`. Never commit a skill edit into `fund` or vice versa.
- **Never edit or commit in `~/.claude/skills` itself.** That is a *single shared checkout used by every Claude session on this machine* — it has no per-session isolation, so a branch switched there is switched for everyone and another session's commit lands on whatever branch happens to be checked out. Measured during this plan's own execution: an unrelated commit to `whats-the-play/SCENARIOS.md` landed on this work's feature branch mid-task. Read from `~/.claude/skills` if you must; write only in the worktree.
- **Never invoke `/morning-standup` to test anything.** It sends real messages to live sessions and asks a human to open real chats. There is no dry-run flag. Every verification in this plan is a read-only query you run yourself.
- **Never read a tracked `fund` file from the working tree at `/Users/benjaminmatton/Developer/fund`.** That checkout is detached on a divergent line — commits ahead that are not ancestors of `master`, and far behind. Read with `git show master:<path>`. This corrupted four of nine lanes on 2026-08-25 and made two sessions "independently verifying" agree because they shared a bad source.
- **Every count in the spec is a snapshot.** The board moved 16 → 22, open PRs 1 → 10, closed issues 3 → 5, and three branches committed between two consecutive commands, all while the spec was being written. **Verification steps below check shape, not exact numbers.** A step that says "expect 22" means "expect the number the command prints to be self-consistent with the rule under test" — if a literal count differs from this plan, that is expected and is not a failure.
- **Edits are anchored on quoted text, not line numbers.** Line numbers shift as earlier tasks land. Every task gives the exact string to find.
- **Do not weaken or delete an existing rule to make a new one fit.** If a new rule contradicts existing prose, stop and report it.
- Conventional commits: `feat:`, `fix:`, `docs:`. **No AI attribution or `Co-Authored-By` trailer in any commit message.**

---

## File Structure

| File | Repo | Responsibility | Tasks |
|---|---|---|---|
| `morning-standup/SKILL.md` | claude-skills | The run itself: where lanes come from, how they are typed, ordered, briefed, recorded | 1–10 |
| `owning-a-lane/SKILL.md` | claude-skills | What a seat owes once it holds a lane, per type | 11 |
| `docs/agents/issue-tracker.md` | fund | The repo-side convention the skill reads: how a decision issue is filed | 12 |
| `docs/superpowers/specs/2026-08-26-*.md` | fund | The spec and the four candidate designs — currently untracked, need landing on `master` | 13 |

Tasks 1–10 are ordered so each rule's verification can run before the next is added. Tasks 11–13 are independent of each other and of 1–10. Task 14 is the acceptance gate and must run last.

---

### Task 1: Phase 0a — board membership is a union, and the unattached are report-only

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the sub-issues paragraph in Phase 0

**Interfaces:**
- Produces: the terms **board** (attached children, in map order) and **the unattached** (declares `Part of #<map>`, never attached — never called "the tail", which this file already uses for a peer's broadcast suffix). Tasks 2, 4 and 14 use both.

- [ ] **Step 1: Verify the rule is computable, and record the numbers before you change anything**

Run:
```bash
cd /Users/benjaminmatton/Developer/fund
gh api repos/benjaminematton/fund/issues/49/sub_issues --jq '[.[].number] | length'
gh issue list --state open --limit 200 --search '"Part of #49" in:body' --json number --jq '[.[].number] | length'
comm -13 <(gh api repos/benjaminematton/fund/issues/49/sub_issues --jq '.[].number' | sort) \
         <(gh issue list --state open --limit 200 --search '"Part of #49" in:body' --json number --jq '.[].number' | sort) | tr '\n' ' '
# NOTE: plain `sort`, never `sort -n`. `comm` requires lexicographic order; feeding it a numeric sort
# silently reports far more "unattached" issues than exist. Measured: 20 reported against 11 true.
```

Expected: the first number is the board size, the second is the number of open issues declaring membership, and the third line prints **the unattached** — issue numbers that declare membership and are not attached. That set must be non-empty; if it is empty, the union read buys nothing and you should stop and report that.

- [ ] **Step 2: Find the anchor**

In `~/.claude/skills/morning-standup/SKILL.md`, find:

> Where sub-issues are not enabled, that call returns nothing usable: fall back to the task list in the
> map issue's body, whose children each carry a `Part of #<n>` line at the top of their own body — the
> convention documented in `docs/agents/issue-tracker.md`.

- [ ] **Step 3: Replace it with the union rule**

```markdown
Where sub-issues are not enabled, that call returns nothing usable: fall back to the task list in the
map issue's body, whose children each carry a `Part of #<n>` line at the top of their own body — the
convention documented in `docs/agents/issue-tracker.md`.

**Read that convention as a union, not only as a fallback.** An issue can declare membership in its
own body and never have been attached, and on a busy repo most of them have not been:

```bash
gh issue list --state open --limit 200 --search '"Part of #<n>" in:body' --json number,title,labels
```

Attached children are **the board**, in map order. Issues that declare membership and were never
attached are **the unattached**.

**The unattached are reported, and never dispatched from the board's order.** No dispatch order is
invented for them — not by severity, not by issue number, not by age. Attaching is the only way into
the board's order, and attaching is the human's act.

**Reporting order and dispatch order are different things, and only the second is forbidden.** This
skill already ranks a *report* by severity where no map order exists. So the digest names the
unattached: the count, and the issues at the top severity tier present, so the human can see what is
sitting outside the board. Print the attach command beside them:

```bash
gh api -X POST repos/{owner}/{repo}/issues/<n>/sub_issues -f sub_issue_id=<id>
```

**Attaching is how the human re-orders, and it is the only way into dispatch.** One call, ten
seconds. This skill never attaches anything: boarding is an ordering decision, and the one time a
lane made that decision for itself the attach was reverted 68 seconds later.

**Why they are not simply dispatched in severity order.** That would be a *dispatch* order nobody
chose, and one label edit would reorder it overnight — the instability the map-order rule exists to
prevent — while severity is absent from a third of these issues anyway. Map order is the human's
priority decision. A map with many children in an invented order is worth less than one with few in a
real one, and that holds for a dispatch queue exactly as it holds for a map.
```

**Naming note, and it is binding on later tasks:** call this set **the unattached**, never "the tail".
This file already uses "tail" for the personalized suffix appended to each peer's broadcast copy in
Phase 6. Two meanings of one word in a document an agent reads literally is a defect.

- [ ] **Step 4: Verify the prose matches what the commands produce**

Re-read the edited section and confirm: (a) the union command in the prose is the one you ran in Step 1, (b) nothing in it dispatches from the unattached, (c) the attach command names `sub_issue_id`, which is the **numeric issue id**, not the issue number — confirm with:

```bash
gh api repos/benjaminematton/fund/issues/50 --jq '{number, id}'
```
Expected: two different numbers. If the prose implies they are the same, fix it.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: the board is a union of attached children and issues declaring membership"
```

---

### Task 2: Phase 0b/0c — `needs-decision` routes instead of excluding, with carry-forward

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the fifth candidate-lane bullet in Phase 0

**Interfaces:**
- Consumes: **board** and **tail** from Task 1.
- Produces: the lane types **decide** and **remediate**. Tasks 3, 4, 6, 7, 8, 9 and 13 use both.

- [ ] **Step 1: Verify both routing sources exist and disagree**

Run:
```bash
gh issue list --state open --label needs-decision --json number --jq '[.[].number]|sort|join(" ")'
gh issue view 3 --json labels --jq '[.labels[].name]'
grep -c 'the human' ~/.claude/align/fund/standups/2026-08-25.md
```
Expected: a non-empty label set; `[]` for #3; a non-zero count of `→ the human` flags in the previous digest. **#3 carrying no labels while the previous digest names it is the case that proves the carry-forward rule is load-bearing** — the label route misses it entirely.

- [ ] **Step 2: Find the anchor**

Find the bullet beginning:

> - **not waiting on a human** — it carries no label marking it as awaiting a ruling (`needs-decision`,

Read to the end of that bullet (it ends with "which is the failure the whole board exists to prevent").

- [ ] **Step 3: Replace the whole bullet**

```markdown
- **typed** — a candidate carrying a label marking it as awaiting a ruling (`needs-decision`, or this
  repo's equivalent) is **not excluded; it is routed.** `blocked_by` models **issue-to-issue edges
  only**, so an issue blocked on a decision nobody has made reports `blocked_by == 0` and reads as
  ready. Dispatching it **as remediation** gets a seat to guess at the ruling, and that rule stands —
  measured on this machine, two such issues sat at the top of a freshly built board and one was the
  second lane the first dispatch would have handed out. But excluding it altogether turns the item
  nobody can *start* into the item nobody ever *works*, and on that board those were the two most
  severe items on it. It is a **decide lane**: its deliverable is the package that makes the ruling
  cheap, and its explicit prohibition is making the ruling. Everything else is a **remediate lane**.

  **Decide lanes are board-independent, and this is the one carve-out from the unattached rule.** A
  decision-bearing issue is a decide lane whether or not it is an attached child, so **this list's
  opening clause — "an open child of the map issue" — does not bind a decide lane.** Every other
  property in the list still does: unblocked, unclaimed, region-declared.

  **The carve-out is narrow and its edge is exact.** It reaches an unattached issue *only* where that
  issue carries the ruling label or is carried forward from the previous digest. An unattached issue
  with no such label stays reported-only. **Why it is safe:** a decide lane's deliverable is a package
  for the human, not a change to the code the board orders, so it neither consumes the board's priority
  ordering nor needs a place in it. Remediate lanes do need the board, because their priority is a
  human judgement only the board holds. That asymmetry is the point — a boarding gap starves
  remediation and leaves decisions reachable, rather than killing both.

  **This is not re-deriving priority from labels.** Routing is not ordering. Boarded children keep map
  order; the label decides only which deliverable a lane owes. Phase 0 already reads labels this way.

  **Carry-forward.** A lane named under the previous digest's **`## Decisions for the human`** heading
  and still open is a decide lane whether or not it carries the label. **Read that heading, not the
  prose around it** — Phase 3 writes it under exactly that string every morning so this rule has
  something to find rather than a section to infer. The digest is this skill's own durable record; a decision
  it reported yesterday does not become undecided because nobody labelled the issue. Where the label
  and the carry-forward disagree, take the **union** and report the disagreement in one line — label
  it, or correct the map. Do not resolve it silently in either direction.
```

- [ ] **Step 4: Verify no contradiction was introduced**

Run:
```bash
grep -n 'not waiting on a human' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: **no output.** The old exclusion must not survive anywhere, including in the sideways table. If it appears, the rule now says two opposite things — remove the stale copy.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: an issue awaiting a ruling becomes a decide lane instead of being dropped"
```

---

### Task 3: Phase 0d — the package test

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — insert after the typed bullet from Task 2

**Interfaces:**
- Consumes: **decide** from Task 2.
- Produces: the distinction **decide lane** vs **decision-list item**. Tasks 7 and 13 use it.

- [ ] **Step 1: Verify the test discriminates**

Run, for each open `needs-decision` issue:
```bash
for n in $(gh issue list --state open --label needs-decision --json number --jq '.[].number'); do
  L=$(gh issue view $n --json body,comments --jq '.body + "\n" + ([.comments[].body]|join("\n"))' | wc -l)
  echo "#$n  $L lines"
done
```
Expected: the counts differ substantially between issues. A test that cannot separate them is useless; if every issue returns a similar length, report that and stop.

- [ ] **Step 2: Read two of them and confirm by hand**

Run:
```bash
gh issue view 73 --json body --jq '.body' | head -40
gh issue view 38 --json body --jq '.body' | head -40
```
Expected: one enumerates two or more named options with their consequences; the other states a problem without naming alternatives. **This is the judgement the rule asks for, and you have just made it once by hand — that is the point of the step.**

- [ ] **Step 3: Insert the rule**

Immediately after the typed bullet from Task 2:

```markdown
  **A decision-bearing issue that already carries a decision package is not a lane.** A package is two
  or more **named options with their consequences**. Dispatching a seat to restate an analysis another
  lane already wrote is how a fleet audits its own audit.

  Pre-filter by size, then read:

```bash
gh issue view <n> --json body,comments --jq '.body + "\n" + ([.comments[].body]|join("\n"))' | wc -l
```

  **The size is a pre-filter, not the verdict.** Read the body of each candidate — there are rarely
  more than a handful — and report what you found, quoting the option list where one exists. A grep
  count is a verdict dressed as a measurement. Under the absence rule, say what you read.

  **A consequence must be stated, not implied.** Naming two branches — *do X, or do Y* — is not a
  package. What makes it one is that each option carries what it costs or what it forecloses, written
  down, so a human can rule without reconstructing the argument.

  Package present → an item on Phase 3's decision list, never a lane. Package absent → a decide lane.
  **Uncertain → a decide lane, and say so.** An issue whose package is arguable is one whose options
  are not clearly stated, which is precisely what a decide lane produces — and the decide-lane briefing
  in Phase 5 makes *"the package already existed and the question is moot"* a valid answer and the
  strongest one, so nothing is lost by choosing that direction under doubt.

  **This resolves and then reports; it does not decline to resolve.** That is the carry-forward
  pattern — take the reading, state the disagreement — and deliberately *not* the pattern used for
  absent dependency data, which is left unrouted precisely because no reading of it is available. Here
  a reading is available; it is only uncertain. Say what you read.

  Being wrong toward a lane costs one seat restating a package that existed. Being wrong toward an item
  puts a half-argued decision in front of the one person whose attention the whole list exists to
  protect. Not running the test at all costs a fleet re-deriving its own findings.
```

- [ ] **Step 4: Verify**

```bash
grep -n 'named options with their consequences' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: exactly one match.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: an issue that already carries a decision package is an item, not a lane"
```

---

### Task 4: Phase 0e/0f — the rider rule and rider grouping

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — insert after the region paragraph in Phase 0

**Interfaces:**
- Consumes: **board**, **tail**, **decide**, **remediate**.
- Produces: **rider**, **held-in-region**, and **region head**. Tasks 7, 8 and 13 use them.

- [ ] **Step 1: Verify the rule fires, and measure how hard**

Run:
```bash
cd /Users/benjaminmatton/Developer/fund
# region heads currently held by a live lane
grep -oE '^\| #[0-9]+ \| [^—|]+' ~/.claude/align/fund/map.md | sed 's/.*| //' | sed 's/ *$//' | sort -u
echo "--- candidates and their region heads ---"
for n in $(gh issue list --state open --limit 200 --json number --jq '.[].number'); do
  h=$(gh issue view $n --json body --jq '.body' | sed -n 's/^\*\*Region:\*\* \([^—]*\)—.*/\1/p' | sed 's/ *$//')
  [ -n "$h" ] && echo "#$n	$h"
done | sort -t$'\t' -k2
```
Expected: a substantial number of candidates share a region head with a live lane. **If no candidate collides, the rider rule is unnecessary on this board and you should report that rather than adding it.**

- [ ] **Step 2: Find the anchor**

Find the paragraph beginning:

> **Each candidate lane carries a region**, read from that sub-issue's body

Insert the new text immediately **after** that paragraph ends (it ends with "exactly like any other region-undeclared lane.").

- [ ] **Step 3: Insert the rider rule**

```markdown
**A candidate whose region is already claimed rides; it is not dispatched.** A region is claimed when
**either** test holds, and both are run every time:

1. a **live lane** holds it — a `map.md` row for that region whose `sessionId` is still in the live
   session listing; **or**
2. **a lane this same run has already dispatched** holds it.

Compare **region heads** — the text before the em dash — by string equality, never by a judgement
about adjacency. Anything subtler goes to the poll, and Phase 4 resolves it against what sessions say
they own.

A rider is routed to that incumbent as a **decision request**, recorded `held-in-region`, and named
on Phase 3's decision list. It gets no `map.md` row: nothing was bound.

**A rider carries the ruling, never the work.** Measured: two lanes handed a question sideways
mid-flight both answered fast and well — one with three reasons to refuse, including one the overseer
had missed — and **neither did the work**. Filing is where a lane offloads, not where it queues: one
lane filed two issues from inside its own review and worked neither. So a rider whose item needs
implementing returns to the dispatch pool — it stays an ordinary candidate and is dispatched on its
own terms — rather than sitting with the incumbent.

**Riders are reported grouped by incumbent, and a group larger than two is a flag `→ the human`, not
a queue.** Six decisions dropped on one seat is not routing, it is a split signal — `/split-the-plan`
on that lane, which is the human's call and not this skill's. Without this the rule quietly converts
one overloaded region into one overloaded seat. Measured on one morning: the board gained six
children and every one of them shared a region head with a single live lane, so the board grew by six
and the dispatchable set grew by nothing.

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

The rest of that rule:
region invisible to dispatch.

**Why the second test exists**, since it is the one an implementer will be tempted to drop: without
it, a run that releases an incumbent dispatches every candidate in that region at once.
Measured: when one lane's owner exited, its issue was released and the five children sharing its
region head had no incumbent left to ride to — six candidates, one region, and a rule checking only
*live* lanes sees no collision. Where several candidates share a region head and nothing claims it,
**dispatch the first in map order and hold the rest `held-in-region` behind it**, named on the
decision list as a split question exactly like riders behind a live incumbent.

**Region-head equality has false negatives and this skill states so rather than claiming coverage.**
Two issues can name the same code under different heads. Report the rule's shape, not a claim that it
is complete.

**Where the map body records a child as awaiting a ruling and the issue carries no such label**, the
carry-forward rule makes it a decide lane **and** the digest asks for the label in one line. Two
sources of truth disagreeing is a thing to report, never a thing to resolve silently.
```

- [ ] **Step 4: Verify**

```bash
grep -c 'region head' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: at least 2. Then confirm by reading that the skill never says a rider is dispatched.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: a candidate inside a live lane's region rides to its owner instead of dispatching"
```

---

### Task 5: Phase 0g — the ledger

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — insert at the end of Phase 0, before `## Phase 1`

**Interfaces:**
- Produces: the **ledger line**. Tasks 8 and 10 write it; Task 14 checks it.

- [ ] **Step 1: Verify every ledger term is computable**

Run:
```bash
cd /Users/benjaminmatton/Developer/fund
gh issue list --state open --limit 300 --json number --jq 'length'
gh issue list --state closed --limit 200 --json number,closedAt --jq '[.[] | select(.closedAt > "2026-08-25T00:00:00Z")] | length'
git fetch -q origin 2>/dev/null || echo "FETCH FAILED"
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/ | grep -v '^master$'); do
  set -- $(git rev-list --left-right --count master...$b 2>/dev/null)
  [ "${2:-0}" -gt 0 ] && echo "$b +$2 -$1"
done | wc -l
```
Expected: four numbers, or the literal string `FETCH FAILED`. **If the fetch fails, `unlanded` is *unavailable*, not zero** — this is the same `null ≠ 0` discipline Phase 0 already applies to `blocked_by`.

- [ ] **Step 2: Insert the ledger**

Before `## Phase 1 — roster`:

```markdown
**Before any lane is dispatched, measure what the fleet failed to retire.** One line, and it opens the
digest:

```
open <N> · retired <M> since <window start> · filed since <window start>: <F> · unlanded <B> branches /
<C> commits (<blocked>, <stale>) · <R> refs at +0 with uncommitted worktrees · <U> unreferenced merges
· measured <instant>
```

- **open** — every open issue in the repo, not only the board's children. The gap between this and the
  board is the point: a board that cannot grow while `open` climbs is the thing the line exists to show.
- **window start** — the same instant Phase 1 computes for the roster, the mtime of the newest file in
  the standups directory. Computed once, used by both.
- **retired** — issues closed since the window start.
- **filed** — issues opened since the window start, and the lane that filed each. On one measured day
  that was 41 across 9 lanes and no phase of this skill could see it.
- **unlanded** — local branches **ahead of `origin/master`**. Count by `ahead > 0`, never by
  `--no-merged`, for two separate reasons. `git branch --no-merged | wc -l` counts **lines, not
  branches** — a detached HEAD prints an extra pseudo-branch line that is not one. And past that,
  `--no-merged` answers *ancestry*, where this line asks *has unique work*. **Never count with `-a`
  either**: remote-tracking refs duplicate the local branches.
- **`<blocked>` and `<stale>`** split `unlanded` by whether the branch has fallen behind. A branch
  `0` behind is **blocked** — a clean fast-forward waiting on a ruling, not rotting. A branch behind
  is **stale**. A land lane that assumes staleness is wrong about the branches that matter most.
- **`<R> refs at +0`** — refs carrying no commits at all whose worktree holds uncommitted work. These
  are **not** unlanded branches and are never counted inside that number; they are reported beside it.
  A `+0` ref reads as *no progress* and can be a written-but-uncommitted changeset — measured, two such
  refs held reviewed work, and all three committed within two minutes of being looked at. **Report
  them; never reap one.**
- If `git fetch` fails, **unlanded is *unavailable*, not zero.** Report it as unavailable.

- **`<U>` unreferenced merges** — merged PRs whose head branch maps to a lane and whose body carries no
  closing reference. **This is a detectable defect, not a judgement call:**

```bash
for p in $(gh pr list --state merged --limit 30 --json number --jq '.[].number'); do
  gh pr view $p --json body,headRefName \
    --jq 'select((.body | test("(?i)(close[sd]?|fixe?[sd]?|resolve[sd]?) +#[0-9]+")) | not) | .headRefName'
done
```

  It measures whether **the record is consistent**, not whether a lane was productive — so it does not
  punish the lane whose best output was a finding rather than a diff. **The count goes in the line; the
  branch names go beneath it whenever `<U>` is non-zero**, the same shape the unattached are reported
  in. A bare count of a defect nobody can locate is not actionable.

**The ledger governs nothing.** It is measured and reported so the fleet's inability to retire is
visible on the line above the lanes, every morning, in a number nobody can skip. It is not a cap and
must never be used as one. **And it never reads PR count as a lane's worth** — the most valuable
output of one measured lane was a single sentence that no diff contains.

**`filed` is two mechanisms and the digest separates them.** Measured on one day: 22 of 34 new issues
landed in the first 72 minutes, before any lane wrote code — nine agents inventorying nine regions for
the first time, which is one-time and does not recur once those regions are known. The remaining 12
trickled over seven and a half hours and were review-generated. **A first-contact number and a
review-generated number mean opposite things**, and reporting them as one total invites the reader to
mistake a new region's inventory for a fleet that cannot stop filing.

**The line carries the instant it was measured, because on a live fleet it is perishable.** Measured
in one sitting: three branches committed between two consecutive commands, the board grew by six,
open PRs went from one to ten, and closed issues went from three to five. **A branch or board state is
a measurement with a lifetime of minutes, not a standing property.**
```

- [ ] **Step 3: Measure both instruments against the SAME baseline before shipping prose comparing them**

```bash
cd /Users/benjaminmatton/Developer/fund
git branch --no-merged origin/master | wc -l
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/ | grep -v '^master$'); do
  set -- $(git rev-list --left-right --count master...$b 2>/dev/null)
  [ "${2:-0}" -gt 0 ] && echo "$b"
done | wc -l
```
Expected: the first number is materially larger than the second. If they are equal, the prose's claim is false on this repo — report it rather than shipping it.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: a ledger line reports what the fleet failed to retire, and governs nothing"
```

---

### Task 6: The precedence order — which stream draws first

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — insert at the end of Phase 0, after the ledger

**Interfaces:**
- Consumes: **remediate** / **decide** / **land** (Task 2), the previous digest (Task 2's carry-forward reads the same file).
- Produces: the ordering the poll lists candidates in (Task 7) and the order seats are allocated in (Task 9).

- [ ] **Step 1: Verify the top severity tier is computable from unclaimed remediate candidates alone**

Run:
```bash
cd /Users/benjaminmatton/Developer/fund
for n in $(gh api repos/benjaminematton/fund/issues/49/sub_issues --jq '.[] | select(.state=="open") | .number'); do
  claimed=$(grep -cE "^\\| *#$n +\\|" ~/.claude/align/fund/map.md)  # tolerate column padding; a fixed-width
  # pattern silently drops single-digit issue rows and reads a claimed lane as unclaimed
  [ "$claimed" -gt 0 ] && continue
  gh issue view $n --json number,labels \
    --jq '"\(.number) \([.labels[].name] | map(select(startswith("severity:"))) | join(","))"'
done
```
Expected: a list of unclaimed children with their severity labels, several of them blank. **A blank severity is not a tier** — if every unclaimed remediate candidate is unlabelled, there is no top tier present and the rule below falls through to decisions-first. Confirm you can tell those two cases apart before writing prose that depends on it.

- [ ] **Step 2: Check whether the previous digest carries a standing item**

```bash
grep -n '⚠️ LIVE' ~/.claude/align/fund/standups/*.md | tail -3
```
Expected: either a match — the previous run recorded harm that was *occurring* — or no output. Both are valid inputs; the rule must handle each.

- [ ] **Step 3: Insert the precedence order at the end of Phase 0, after the ledger**

```markdown
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

**Land candidates are computed before the poll and bound after it.** The `stranded` answers are what
establish authorship, so Phase 2 lists them **by branch, not by owner** — *these branches are
unlanded; if one is yours, say so in `stranded`* — and Phase 4 binds each to whoever claimed it.
Listing them by owner before the poll would require knowing the answer the poll exists to ask.

**The split is a precedence order, not a quota, a ratio, or a cap.** Every candidate that survives the
filters is dispatched. Urgency does exactly one thing: it decides which stream draws first.

1. **A standing lane pre-empts everything.** An item the *previous* digest marked `⚠️ LIVE` — harm that
   is **occurring**, not harm that is possible — is re-dispatched every morning until its issue closes.
   This is not a derived priority: the previous run wrote it down, and this skill already carries facts
   forward. The distinction that matters is tense. The rest of the queue is rulings; a standing item is
   current output, and it recurs on the next run unless something changes.
2. **Then, if any unclaimed *remediate* candidate carries the top severity tier present among
   remediate candidates, remediation draws first. Otherwise decisions draw first.** Measured: nine
   defect lanes produced zero merges because every branch waited on a ruling. **Remediation that
   terminates in an unmade decision is not remediation.**

   **If no unclaimed remediate candidate carries any severity label, there is no top tier present.**
   Say so in one line and draw decisions first. That is a fact about the labels, not a judgement about
   the work — and it is the same fallback this skill already applies when it has to rank an unordered
   open-issues report and finds no matching label in the repo.

3. **Land lanes bind by authorship and consume no chats**, so they sit outside the order entirely.

**What the order actually rations.** It does not limit how many lanes exist — it decides which lanes
get the seats, and the seats are the real constraint: live sessions plus the chats the human opens.
**The fleet's size is the cap. The precedence order decides who gets the seats.** Do not read this as a
throttle and do not add one: capping what a morning may start was considered and rejected, on the
measurement that the arithmetic would have permitted one lane on the most productive day on record.

**The unattached are not in this order at all** — they are reported, and attaching is the only way into dispatch.
```

- [ ] **Step 4: Verify no cap crept in**

```bash
grep -inE 'at most [0-9]|no more than [0-9]|cap of|maximum of [0-9]|budget' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: matches only inside the sentence that *forbids* a cap. Any rule that limits the number of lanes dispatched is a divergence from the spec — stop and report it.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: urgency decides which stream draws first, and nothing caps the run"
```

---

### Task 7: Phase 2 — typed candidates, the blocked clause, and the `stranded` field

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the poll message block in Phase 2

**Interfaces:**
- Consumes: **remediate** / **decide** / **land**.
- Produces: the poll's seventh field `stranded`, whose answers Task 7 puts on the decision list.

- [ ] **Step 1: Find the anchor**

Find, inside the block-quoted poll message:

> > - **blocked** — what unblocks you and who owns that. "nothing" is valid.

- [ ] **Step 2: Replace that line and add the seventh field**

```markdown
> - **blocked** — what unblocks you and who owns that. "nothing" is valid. **If what unblocks you is a
>   decision only the human can make, say so and name the decision** — those answers go on the
>   decision list, and they need nobody to have labelled anything.
```

Then, immediately after the `owns` bullet, add:

```markdown
> - **stranded** — branches you or your subagents left ahead of `origin/master` and unmerged. For each,
>   **two things**: its disposition — `ready` / `dead` / `blocked-on: #<issue>` — **and the issue it
>   served**, as `serves #<issue>`, or `serves nothing`. Name the issue in every case, not only when you
>   are blocked: that issue's region becomes the lane's region, and a branch with no issue named cannot
>   be dispatched. **Check it, do not recall it:**
>   `git rev-list --left-right --count master...<branch>`
```

- [ ] **Step 3: Type the candidate-lane list**

Find:

> > <candidate lanes, one per line: `#<issue> — <title>` for an issue-derived lane, the name itself for
> > a human-named one>

Replace with:

```markdown
> <candidate lanes under three headings — `remediate` / `decide` / `land` — one per line:
> `#<issue> — <title>` for an issue-derived lane, the name itself for a human-named one. State what
> each type produces: a remediate lane produces one or more PRs; a decide lane produces a decision
> package and no code; a land lane produces a PR, an obituary, or a named blocker, and may not extend
> its branch.>
>
> **`land` lanes are listed by branch, not by owner, because who owns them is what this field asks.**
> Each line is a branch ahead of `origin/master`: *if one of these is yours, say so in `stranded` and
> it becomes yours.* Do not claim a branch you did not write — reconstructing another session's intent
> from a diff is the most expensive guess available, and Phase 4 refuses a non-author's claim anyway.
```

- [ ] **Step 4: Update the capacity field's parenthetical**

Find `Owning a lane means running it —` and confirm the sentence still reads correctly now that three types exist. Add after "Name which lane if yes.":

```markdown
>   A session too deep for a remediate lane is often not too deep for a **land** lane on a branch it
>   wrote itself — that is the cheapest capacity in the fleet.
```

- [ ] **Step 5: Verify the poll still has a coherent field count**

```bash
grep -n '^> Six fields:' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: one match, and it now **understates** the count. Change `Six fields:` to `Seven fields:` and re-run — expected: no match for "Six fields".

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: the poll types its candidate lanes and asks what each seat left unmerged"
```

---

### Task 8: Phase 3 — the ledger line and the decision list as an obligation

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the digest construction in Phase 3

**Interfaces:**
- Consumes: **ledger line** (Task 5), **decide** (Task 2), package-test items (Task 3), **riders** (Task 4), `stranded` answers (Task 6).
- Produces: the digest's `## Decision list` section, which Task 2's carry-forward reads next morning.

- [ ] **Step 1: Find the anchor**

Find:

> Build the digest:
>
> 1. **One block per session**

- [ ] **Step 2: Insert two items ahead of the per-session blocks**

```markdown
Build the digest:

1. **The ledger line, first.** Phase 0's one line, verbatim, with its measurement instant. It is the
   only line about the fleet rather than about a lane, which is why it goes above everything.

2. **The decision list — and it carries human-bound items only.** One numbered entry each for: decide
   lanes' packages, packages found by the package test, riders routed to incumbents and anything else resolved `held-in-region`, **any `blocked`
   answer naming a decision only the human can make**, `blocked-on:` answers from the `stranded` field,
   and anything carried from a previous digest that this run cannot show retired. **Both blocked-shaped
   sources count** — the prose one in `blocked` and the structured one in `stranded` — because Phase 2
   promises the decision list to each, and a field promised a home and not given one is worse than a
   field never asked for. Each entry states the question in one sentence, the options with what each costs, **what
   stays stopped until it is answered**, and how many days it has been open.

   **The list is written under the fixed heading `## Decisions for the human`.** Phase 0's
   carry-forward reads that heading in the previous digest, so it must be spelled the same every
   morning. A rule that has to find a section by prose inspection is a consumer with an unreliable
   writer — the same defect as a marker nothing produces.

   **No human-bound decision leaves the run except in this list.** It is assembled once and reported once. **It is assembled here and completed after Phase 5**, because two of its inputs do not exist yet: `held-in-region` resolutions come from Phase 4 and unbound lanes from Phase 5. Phase 3 holds a draft; Phase 6 writes the finished list. It is still reported exactly once — an item discovered mid-run joins the list rather than getting its own message.

   **Mark a standing item `⚠️ LIVE`, and this is the only thing that produces one.** Where the run
   records harm that is *occurring* — wrong output reaching production now, not a defect that could
   produce some — mark that item `⚠️ LIVE`. Phase 0's precedence order reads this marker from the
   *previous* digest and pre-empts everything with it, so **a run that sees live harm and does not
   mark it leaves the next run blind to it.** The distinction is tense, not severity: the rest of the
   queue is rulings, a standing item is current output, and it recurs on the next run unless something
   changes.

   **Technical rulings are not batched and stay serial.** This distinction is measured, and getting it
   backwards makes things worse: on the day this rule comes from, the technical rulings genuinely
   depended on what each lane went and read — one ruling was made early, made wrong, and reversed by a
   file the lane opened afterwards — while the human-bound decisions were independent of each other
   and sittable in one sitting at hour two, and were instead trickled out across seven hours. Batch
   the second class. Never the first.

   **A decision ruled in the human's own window is invisible to this skill.** It carries with a growing
   day count, marked `possibly ruled elsewhere — unverified; checked: issue open, label present`. Say
   what was checked.

   **Riders are grouped by incumbent**, and a group larger than two is flagged as a split question
   rather than listed as several routings.

3. **One block per session** — did / doing / next / blocked / capacity / owns / stranded. Silent
   sessions named as silent.
```

4. **Drift becomes two-sided.** The existing drift lines report work no issue covers and issues
   someone treats as done that are still open. Add the other direction: **issues opened since the
   window start, counted, with the lane that filed each.** On one measured day that was 41 across nine
   lanes and no phase of this skill could see it. Report it as a fact, not a verdict — a fleet filing
   issues is a fleet reading its regions for the first time as often as it is a fleet failing to
   finish.

Renumber the remaining digest items (`Against the board`, `Flags`) accordingly, and **delete the
`→ the human` bullet for "a child waiting on a human ruling"** from the Flags list — it is now the
decision list's job. Leave every other flag exactly as it is.

- [ ] **Step 3: Verify the flag was moved, not duplicated**

```bash
grep -n 'a child waiting on a human ruling' ~/.claude/skills/morning-standup/SKILL.md
grep -c 'Decision list' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: no output from the first; at least one match from the second.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: human-bound decisions go up as one list, technical rulings stay serial"
```

---

### Task 9: Phase 4 — type in the binding, and the collision matrix

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the resolution list in Phase 4

**Interfaces:**
- Consumes: **remediate** / **decide** / **land**, **region**.
- Produces: the resolutions **covered**, **needs a chat**, **held-in-region**.

- [ ] **Step 1: Find the anchor**

Find:

> A capacity yes whose region collides is a **flag, not an assignment**

- [ ] **Step 2: Insert the matrix before that paragraph**

```markdown
**A lane's type is part of the binding.** A session that answers yes to `#38` takes it as the type the
board typed it. Never silently retype a lane to fit an answer.

**Collision depends on the pair of types, because a decide lane writes no code:**

| Pair | Resolution |
|---|---|
| `decide` ∩ `decide` on one region | **Collision** → `/get-aligned`. Two packages over one region produce two contradictory recommendations |
| `decide` ∩ `remediate` on one region | **Not a collision — a pairing.** The decide lane reads the remediate lane's issue thread first and addresses that owner as its first correspondent |
| `land` ∩ anything on one region | **Collision.** A branch and a live lane in the same code are the merge conflict, not the risk of one |

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

**Land lanes bind by authorship, or to nobody, and they open no chats.** Where one must go to a
non-author, say so in the briefing: *"You did not write this. Your first job is to establish whether
it should live, not to finish it."* Reconstructing another session's intent from a diff is the most
expensive guess available.
```

- [ ] **Step 3: Verify Phase 4's resolution list is still exhaustive**

Read the phase and confirm every candidate resolves to exactly one of: covered, needs a chat, held-in-region. If a candidate can fall through all three, the phase is incomplete — fix it before committing.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: collision depends on lane type, because a decide lane writes no code"
```

---

### Task 10: Phase 5 and Phase 6 — briefings, durable artifacts, and the record

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` — the briefing list in Phase 5, the record in Phase 6, and the sideways table

**Interfaces:**
- Consumes: everything above.
- Produces: `map.md` rows carrying **type** and **branch**.

- [ ] **Step 1: Verify the `closes #N` mechanism before writing prose that depends on it**

Run:
```bash
gh issue view 39 --json state,stateReason,closedAt --jq '"\(.state) \(.stateReason) \(.closedAt)"'
gh api repos/benjaminematton/fund/issues/39/timeline --paginate \
  --jq '.[] | select(.event=="cross-referenced" or .event=="closed") | "\(.created_at) \(.event)"' | tail -3
```
Expected: `CLOSED COMPLETED`, with a `cross-referenced` event shortly before the `closed` event. **This is the mechanism the briefing relies on: the merge performs the registry write, not the lane.** If the issue is not closed, or closed without a preceding cross-reference, stop — the rule's evidence does not hold and the prose must not claim it.

- [ ] **Step 2: Find the Phase 5 briefing anchor**

Find:

> - **that it owns the lane and does not implement it itself**, and that `owning-a-lane` is how to run
>   one.

- [ ] **Step 3: Add the type-specific contracts after the four existing briefing bullets**

```markdown
**Every lane ends in a durable artifact that is not a message to you.** A report to the overseer does
not survive the session, is not observable to the human, and closes nothing. Measured: nine lanes ran
seven hours, all nine reported completion, and the open-issue count went **up 39**.

- **remediate** — *"You end in one or more PRs. Each PR body carries `closes #<issue>` for the issue
  it closes."* **Not one PR per lane** — one lane produced three unrelated changesets, and bundling
  them to satisfy a rule is worse than three PRs. A lane whose work is a feature may produce none for
  days, and its terminal artifact is then a decision package.
- **decide** — *"You own the question, not the answer. Produce a decision package on the issue: the
  named options, what each costs, the evidence for each, and the one thing the human must choose. You
  may not choose it and you may not implement any option. If your reading makes the question moot, say
  so — that is a valid package and the strongest one."*
- **land** — *"This branch is ahead of `origin/master` and unmerged. Your deliverable is exactly one
  of: a PR with a named reviewer; a closed branch with a one-paragraph obituary in the issue it
  served; or a `blocked-on: #<issue>` naming the exact question. **Do not extend it.** A branch that
  grows during a land lane has failed the lane."*

**`closes #N` is why this works — but it is a convention, not a mechanism, and the brief must treat it
as required.** Lanes decline registry writes on principle: measured, one fleet performed zero issue
closures across seven hours and explicitly refused to correct records serving as evidence. Where a
merged PR carried the reference, **GitHub performed the write** and the issue closed, every time.

**The omission is silent and common.** Measured on one repo: **a minority of merged PRs had ever
carried a closing reference**, and several of the most recent batch omitted it while their fixes
shipped. Nothing warns, nothing fails, and the issue simply stays on the board reading as outstanding
work while its fix sits on `master`. (Deliberately stated as a shape rather than a count — this skill
ships to every repo, and a number measured in one of them expires in days. Re-measure yours.) So the briefing does not say *the merge will close your issue* — it
says **the reference is part of the deliverable, and a remediate lane's terminal artifact is
incomplete without it.**

**The one case plumbing cannot reach is a decision, which has no merge event.** There, the decide lane
retires the marker itself — and frame it as a **correction, not an exception**: once the ruling is
recorded, the label is a *false instruction* telling every future run that a decision is outstanding
when it is not. Correcting false instructions is already what these seats do unprompted; overwriting
records that are *evidence* is what they refuse, and rightly. **The lane retires the marker on its own
decision, records the ruling that made it false, and touches nothing that is evidence.**

**Every brief carries the source pin:** the ref to read against, plus *"report the ref you read in your
first answer."* And the **staleness obligation**: verify the issue body against that ref **before
sizing**. Measured: four of nine lanes were corrupted by an unpinned source, two of them reading their
agreement as corroboration; three lanes checked unprompted and every one of them shrank its lane.

**A branch's state is an input to the brief, never a verdict.** State the classification, the command,
and the instant it was measured, and require the lane to re-measure first. **Never reap a ref on a
classification** — three refs that read as empty held reviewed uncommitted work, and all three
committed within two minutes of being measured.
```

- [ ] **Step 4: Extend Phase 6's record**

Find:

> **A lane row states, at minimum: the lane (issue number or human-named lane), its region, the
> owning `sessionId`, its status, and which skill wrote it.**

Add after it:

```markdown
A lane row also states its **type**, and for a land lane its **branch**. Release gains one clause: a
land-lane row whose branch is merged into `origin/master` is released and struck, checked
observationally with `git branch --merged origin/master`, never by asking.

The ledger line is written as the **first line** of the standup file, so the next run reads the trend
rather than re-deriving it.

**Report that the board changed, never who changed it.** Every session authenticates as the same
GitHub user, so a `sub_issue_added` event names the human whether the human attached the child or an
agent did. Map order is the human's priority decision and this skill **cannot verify that it is**.
State what changed and by how much, under the same rule that lets a session report an absence but
never verify one.
```

- [ ] **Step 5: Add the sideways-table rows, and fix what the earlier tasks made stale in it**

First repair, then add. The table predates the lane types and the seventh poll field, so it carries
counts and phrasings that are now wrong. Find and fix every one:

```bash
grep -inE 'six fields|five fields|candidate lane|waiting on a human' morning-standup/SKILL.md
```

Expected: any hit inside the `## When it goes sideways` table is stale — the poll now has **seven**
fields, and a child awaiting a ruling is routed as a decide lane rather than excluded. Correct them in
place. **Do not delete a row to avoid fixing it**, and if a row's whole premise is now void, say so in
the report rather than removing it silently.

Then add:

At the end of the `## When it goes sideways` table:

```markdown
| A candidate's region is held by a live lane | It rides — routed to that incumbent as a decision request, `held-in-region`, no `map.md` row |
| More than two riders on one incumbent | A flag `→ the human` as a split question, never a queue on one seat |
| `git fetch` fails | `unlanded` is **unavailable**, not zero. Report it as unavailable |
| A branch reads `+0` ahead | Report it. **Never reap a ref** — it may hold uncommitted work, and the measurement is minutes old |
| A decision-bearing issue already carries a package | An item on the decision list, never a lane |
```

- [ ] **Step 6: Verify the whole file is internally consistent**

```bash
grep -c 'decide lane' ~/.claude/skills/morning-standup/SKILL.md
grep -n 'Nothing happens afterwards' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: several matches for the first; **no output** for the second.

- [ ] **Step 7: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: every lane ends in a durable artifact, and a merged PR closes its issue"
```

---

### Task 11: `owning-a-lane` — the terminal artifact per type

**Files:**
- Modify: `~/.claude/skills/owning-a-lane/SKILL.md` — the "Where your work is described" table and the red flags

**Interfaces:**
- Consumes: the three type contracts from Task 10. **Names must match Task 10 exactly:** `remediate`, `decide`, `land`.

- [ ] **Step 1: Find the anchor**

The table row:

> | Progress | A comment on the issue. Durable, costs no one a turn, and the standup reads it without polling you |

- [ ] **Step 2: Add a row after it**

```markdown
| Done | **A durable artifact, never a message to your overseer.** A **remediate** lane: one or more PRs, each body carrying `closes #<issue>` — **the reference is part of the deliverable, not something the merge guarantees.** It is a convention: nothing warns when it is omitted, and the issue sits open reading as outstanding work while your fix ships. A **decide** lane: a decision package on the issue — the named options, what each costs, a recommendation. A **land** lane: a merged PR or a written obituary. Telling your overseer you are done is not done |
```

- [ ] **Step 3: Add the decide-lane prohibition to the escalation section**

Find `**Name the decision, not the discomfort.**` and add after that paragraph:

```markdown
**If your lane is a decide lane, the escalation *is* the deliverable — and this is the one stated
exception to "you do not write it" at the top of this skill.** You own the question, not the
answer. Produce the package — named options, the cost of each, what is demonstrated and what is
reasoned, and the recommendation you would defend — and do not choose, and do not implement any
option. **If your reading makes the question moot, say so: that is a valid package and the strongest
one.**
```

- [ ] **Step 4: Add two red flags**

At the end of the `## Red flags — stop` list, before the closing line:

```markdown
- You are about to extend a branch during a **land** lane
- You are about to overwrite a record whose staleness is itself the evidence
```

- [ ] **Step 5: Verify the type names match Task 10**

```bash
grep -oE '\b(remediate|decide|land)\b' ~/.claude/skills/owning-a-lane/SKILL.md | sort -u
grep -oE '\b(remediate|decide|land)\b' ~/.claude/skills/morning-standup/SKILL.md | sort -u
```
Expected: both print the same three words. A mismatch means a seat briefed by one skill will not find its contract in the other.

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills
git add owning-a-lane/SKILL.md
git commit -m "feat: a lane is done when it has produced a durable artifact, not when it reports"
```

---

### Task 12: `fund` — the issue-tracker convention

**Files:**
- Modify: `docs/agents/issue-tracker.md` (in `fund`, on a worktree off `master` — see Task 13 Step 1)

- [ ] **Step 1: Read the current file from `master`, never from the working tree**

```bash
cd /Users/benjaminmatton/Developer/fund
git show master:docs/agents/issue-tracker.md | grep -n 'wayfinder\|Region\|assignee' | head -20
```

- [ ] **Step 2: Add one paragraph to the wayfinding section**

```markdown
**A decision issue is filed with `needs-decision` and a region, the same as any other child.** It
names a choice for a human, not a task to pick up, and the standup dispatches it as a *decide* lane
whose deliverable is a decision package rather than a diff. **A decision issue that already enumerates
two or more named options is not dispatched at all** — it goes straight onto the standup's decision
list, because a seat sent to restate it would only re-derive what the filer already wrote.

**Claims key on `sessionId` in `map.md`, never on the GitHub assignee.** Every session authenticates
as the same GitHub user, so the assignee cannot distinguish one session from another — and neither can
the sub-issue timeline, which names the same user whether a human attached a child or an agent did.
```

- [ ] **Step 3: Verify the claim about assignees before asserting it**

```bash
gh api repos/benjaminematton/fund/issues/49/timeline --paginate \
  --jq '[.[] | select(.event=="sub_issue_added") | .actor.login] | unique'
```
Expected: a single-element list. That single login covering both hand-boarding and agent-boarding is the evidence for the paragraph.

- [ ] **Step 4: Commit** (in the Task 13 worktree)

```bash
git add docs/agents/issue-tracker.md
git commit -m "docs: a decision issue is filed with needs-decision and a region"
```

---

### Task 13: Land the spec and the candidate designs on `master`

**Files:**
- Move onto `master`: `docs/superpowers/specs/2026-08-26-standup-lane-mix-design.md`, `docs/superpowers/specs/2026-08-26-standup-lane-mix-candidates.md`, `docs/superpowers/plans/2026-08-26-standup-lane-mix.md`

Both spec files and this plan currently exist **untracked in a detached, divergent checkout**. They must be committed from a worktree based on `master`, never from the root checkout.

- [ ] **Step 1: Create a worktree off `master`**

```bash
cd /Users/benjaminmatton/Developer/fund
git worktree add /Users/benjaminmatton/Developer/fund-wt/standup-lane-mix master
```
Expected: `Preparing worktree ... HEAD is now at <sha>`.

- [ ] **Step 2: Branch, and copy the three documents in**

```bash
cd /Users/benjaminmatton/Developer/fund-wt/standup-lane-mix
git switch -c docs/standup-lane-mix
mkdir -p docs/superpowers/specs docs/superpowers/plans
cp /Users/benjaminmatton/Developer/fund/docs/superpowers/specs/2026-08-26-standup-lane-mix-design.md docs/superpowers/specs/
cp /Users/benjaminmatton/Developer/fund/docs/superpowers/specs/2026-08-26-standup-lane-mix-candidates.md docs/superpowers/specs/
cp /Users/benjaminmatton/Developer/fund/docs/superpowers/plans/2026-08-26-standup-lane-mix.md docs/superpowers/plans/
```

- [ ] **Step 3: Verify you copied from the untracked originals and not from a stale tracked copy**

```bash
head -5 docs/superpowers/specs/2026-08-26-standup-lane-mix-design.md
wc -l docs/superpowers/specs/*.md docs/superpowers/plans/2026-08-26-standup-lane-mix.md
```
Expected: the design doc's header names sessionId `27fcd833-4173-47d6-aedf-75bd73cd0014`, and all three files are non-trivial in length.

- [ ] **Step 4: Commit and open a PR**

```bash
git add docs/superpowers/
git commit -m "docs: four competing designs and the spec for a standup that dispatches a lane mix"
git push -u origin docs/standup-lane-mix
gh pr create --title "The standup dispatches remediate, decide, and land lanes" \
  --body "Spec, the four independent candidate designs it was chosen from, and the implementation plan.

Closes nothing on its own — the skill edits land in \`claude-skills\`."
```

- [ ] **Step 5: Confirm the tree is clean and nothing was committed to the detached checkout**

```bash
cd /Users/benjaminmatton/Developer/fund
git status --porcelain | grep '^[MARD]' && echo "UNEXPECTED STAGED CHANGES" || echo "root checkout untouched"
```
Expected: `root checkout untouched`.

---

### Task 14: Acceptance — a cold dry-run of Phases 0 through 4

This is the gate. It executes the skill as written, by hand, and compares the result to the spec's worked run. **It dispatches nothing and messages no one.**

- [ ] **Step 1: Re-read the edited Phase 0 and compute the board and tail**

Run the Task 1 Step 1 commands. Write down the board, the unattached, and the count of each.

- [ ] **Step 2: Apply the four candidate filters, one at a time, recording the exclusion reason for each child**

```bash
cd /Users/benjaminmatton/Developer/fund
for n in $(gh api repos/benjaminematton/fund/issues/49/sub_issues --jq '.[] | select(.state=="open") | .number'); do
  b=$(gh api repos/benjaminematton/fund/issues/$n --jq '.issue_dependencies_summary.blocked_by')
  l=$(gh issue view $n --json labels --jq '[.labels[].name]|join(",")')
  r=$(gh issue view $n --json body --jq '.body' | sed -n 's/^\*\*Region:\*\* \([^—]*\)—.*/\1/p' | sed 's/ *$//')
  claimed=$(grep -cE "^\\| *#$n +\\|" ~/.claude/align/fund/map.md)  # tolerate column padding; a fixed-width
  # pattern silently drops single-digit issue rows and reads a claimed lane as unclaimed
  printf '#%-4s blocked=%-5s claimed=%s region=%-28s labels=%s\n' "$n" "$b" "$claimed" "${r:-NONE}" "$l"
done
```

- [ ] **Step 3: Type each survivor, run the package test on the decision-bearing ones, and apply the rider rule**

Produce three lists by hand: **decide lanes dispatched**, **riders (grouped by incumbent)**, **remediate lanes dispatched**.

- [ ] **Step 4: Run the release check FIRST — a stale row makes an issue unclaimed**

Release is Phase 6 in the skill's order, but a dry run must apply it before computing candidates, or
every dead owner's issue reads as claimed. For each `map.md` lane row, check both release triggers:

```bash
cd /Users/benjaminmatton/Developer/fund
claude agents --json > /tmp/live-sessions.json
grep -oE '\| #[0-9]+ \| [^|]+ \| `[0-9a-f-]{36}`' ~/.claude/align/fund/map.md | while read -r line; do
  n=$(echo "$line" | grep -oE '#[0-9]+' | head -1 | tr -d '#')
  sid=$(echo "$line" | grep -oE '[0-9a-f-]{36}')
  live=$(grep -c "$sid" /tmp/live-sessions.json)
  state=$(gh issue view "$n" --json state --jq '.state')
  printf '#%-4s session_live=%s issue=%s  -> %s\n' "$n" "$live" "$state" \
    "$([ "$live" = "0" ] || [ "$state" = "CLOSED" ] && echo RELEASE || echo keep)"
done
```

Expected: **both release triggers fire on real rows.** Rows whose session is gone release; a row whose
session is *live* but whose issue has *closed* releases too, for the different reason. **Yesterday's run
exercised neither — all nine owners were live and no issue closed — so this is the first coverage the
release path has ever had.** If every row reads `keep`, the check is broken.

- [ ] **Step 5: Judge the run on reproducing known-wrong states, not on completing**

A cold run that produces a clean, plausible answer has told you nothing. Judge it against states whose
true value is known. **Measured on this board at plan time — re-measure, and expect movement, but the
*shapes* below should still hold:**

| # | Assertion | Why it is the test |
|---|---|---|
| 1 | **A naive count of dispatchable lanes is roughly the count of open children minus `needs-decision` minus blocked.** If the dry run reports anything near that, it is wrong. | Every knock-down below is a rule that must fire. A healthy-looking board is the failure symptom. |
| 2 | **The `eval harness` children collapse to one region.** Six issues share that region head. | Rider grouping must fire and flag a split question. The board grew by six and the dispatchable set must not grow by six. |
| 3 | **An issue owned by a live session mid-work must not surface as unclaimed** — even when it is open, unblocked, and carries no labels. | The easy one to get wrong: nothing about the issue itself says it is taken. Only the `map.md` row does. |
| 4 | **A released incumbent must not free its whole region at once.** When the owner of the region-holding lane has exited, its issue releases — and the candidates sharing its region head then have no incumbent to ride to. | This is the intra-run claim rule from Task 4. Without it the run dispatches six lanes into one region. **Highest-value assertion in this plan** — it is the path that had zero coverage. |
| 5 | **Children with no labels at all sort last, in issue-number order.** | Correct behaviour that reads as a bug: a deliberately-frozen issue lands at the bottom of the board. Record it as expected so nobody "fixes" it. |

**A shape mismatch is a failure. Stop and report which side is wrong, rather than editing either to
agree.** Exact issue numbers will differ — the board grew by six, eight children closed, and the live
session count fell from ten to five while this plan was being written.

- [ ] **Step 5: Confirm the skill never instructs a destructive act on a stale measurement**

```bash
grep -inE 'delete the ref|reap|git branch -D|prune' ~/.claude/skills/morning-standup/SKILL.md
```
Expected: **no output**, or matches only in the sentence forbidding it. A skill that tells a seat to delete a `+0` branch would have destroyed reviewed work on the day this design was written.

- [ ] **Step 6: Push the skill edits**

```bash
cd ~/.claude/skills
git log --oneline main...origin/main
git push origin main
```
Expected: eleven task commits (Tasks 1–11), pushed to `benjaminematton/claude-skills`.

- [ ] **Step 7: Confirm the closing-reference defect query returns something actionable**

```bash
cd /Users/benjaminmatton/Developer/fund
for p in $(gh pr list --state merged --limit 15 --json number --jq '.[].number'); do
  gh pr view $p --json body,headRefName \
    --jq 'select((.body | test("(?i)(close[sd]?|fixe?[sd]?|resolve[sd]?) +#[0-9]+")) | not) | "#\(.headRefName)"'
done
```
Expected: a short list of recently merged branches whose PR carried no closing reference. **That list is
what the ledger reports each morning.** If it is empty, say so — an empty result here is a fact about
this window, not proof the convention is being followed.

---

## Out of scope

- **Any change to `get-aligned`, `split-the-plan`, or `huddle`.** The rider rule points at `/get-aligned` and `/split-the-plan` but adds no requirement to either.
- **Factoring out the roster logic** the three skills duplicate. Real, and a separate change.
- **Boarding anything.** No task in this plan attaches a sub-issue, closes an issue, or edits a label. Boarding is the human's ordering decision.
- **A first live run.** Phases 2, 4, 5 and 6 have never executed against a real poll; this plan does not make them execute. The first run under the new prose should be watched by a human, and is the first test of the phases underneath it as much as of these changes.
