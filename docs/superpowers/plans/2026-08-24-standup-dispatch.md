# Morning-standup dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `~/.claude/skills/morning-standup/SKILL.md` so the daily standup ends in lanes owned — polling whoever is live, computing lanes from a GitHub issue board, and dispatching the remainder — instead of stopping at a digest.

**Architecture:** One skill file, seven phases (0 Board → 1 Roster → 2 Poll → 3 Digest → 4 Reconcile → 5 Dispatch → 6 Record). The board is GitHub issues (map issue + sub-issues + `blocked_by`); claims live in `~/.claude/align/<repo>/map.md` keyed on `sessionId`. The skill must degrade to human-named lanes in any repo without a map issue.

**Tech Stack:** Markdown skill file with YAML frontmatter. Shell tools it invokes: `claude agents --json`, `gh` / `gh api`, `git worktree`. Claude Code tools it invokes: `SendMessage` (with `notify_when_idle`), `Bash`, `Read`, `Write`.

**Spec:** `docs/superpowers/specs/2026-08-24-standup-dispatch-design.md`. Where this plan and the spec disagree, the spec wins — fix the plan.

## Global Constraints

- **Degradation is mandatory.** This skill runs in every repo on this machine, most of which have no map issue. No map issue → say so in one line, fall back to human-named lanes, reconcile normally. Never invent a priority order from an unordered issue list.
- **Never read a plan-file checkbox as state.** Measured 2026-08-24 in `fund`: 359 unchecked, zero checked, across seven plan files including shipped work.
- **A peer cannot assign work to a peer.** Unowned work goes to the human as a flag and stays unowned until they say otherwise.
- **The absence rule.** A session can report an absence, never verify one. Every "nothing found" states the method used.
- **`map.md` is updated, never rewritten.** Rows keyed on `sessionId`. Stamp which skill touched it last. It is shared with `get-aligned` and `split-the-plan`.
- **One lane, one overseer.** A lane is owned, never worked. The seat that takes it runs it — fanning out to subagents, or splitting it — and does not implement it directly. New chats and already-running sessions take a lane on identical terms.
- **This skill provisions nothing.** It never creates a branch, a worktree, or a commit. It reads, it messages, and it writes two files under `~/.claude/align/`. Isolation is the owning overseer's decision, one level down.
- **Silence is not a release.** A rostered session that never answered has not given up its region.
- **An idle notice is not a reply.** `notify_when_idle` bounds the wait; the four fields are what count.
- **No arbitration.** Flags are surfaced, never resolved, exactly as today.
- Target file: `~/.claude/skills/morning-standup/SKILL.md`. It is outside this repo and outside git — the skills repo tracks skills but its `.gitignore` bans design docs, which is why this plan lives in `fund`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `~/.claude/skills/morning-standup/SKILL.md` | The whole skill. Frontmatter + 7 phases + sideways table | Rewritten (Tasks 2–6) |
| `~/.claude/skills/huddle/SKILL.md` | Sibling table naming what each multi-session skill ends in | One row (Task 6) |
| `docs/superpowers/specs/2026-08-24-standup-dispatch-design.md` | The spec | §9 answered (Task 1) |

No new files. The skill stays a single `SKILL.md` — every sibling in this family is one file, and splitting into `references/` would be the first exception without a reason.

---

### Task 1: Verify the two roster mechanics the design rests on

Phase 1 replaces a three-source join with one command. Two properties are assumed and untested. If either is false, Phase 1 keeps part of the old join — so this task runs **first** and its answer is written into the spec before any prose is rewritten.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-standup-dispatch-design.md` (§9, and §4 Phase 1 if a fallback is needed)

**Interfaces:**
- Produces: a settled answer to "does `claude agents --json --cwd <main checkout>` return sessions whose cwd is a worktree of that repo?" — Task 3 writes Phase 1's roster step from this answer.

- [ ] **Step 1: Create a worktree and start a session in it**

```bash
cd /Users/benjaminmatton/Developer/fund
git worktree add /Users/benjaminmatton/Developer/fund-worktrees/standup-probe -b probe/standup-roster
cd /Users/benjaminmatton/Developer/fund-worktrees/standup-probe
claude --bg "Print the current working directory, then stop. Do nothing else."
```

Expected: the command prints a session id and returns. If `claude --bg` errors, record the exact error — it also settles whether background dispatch is available, which the design deliberately does not use but the spec's §2 discussion references.

- [ ] **Step 2: Ask the roster whether it can see that session**

```bash
claude agents --json --cwd /Users/benjaminmatton/Developer/fund | jq -r '.[] | "\(.name)\t\(.kind)\t\(.cwd)"'
```

Expected: one of two outcomes, both informative —
- the probe session appears with a `cwd` under `fund-worktrees/standup-probe` → `--cwd` matching is by repo, and Phase 1 needs no worktree join
- the probe session is absent → `--cwd` matches by path prefix only, and Phase 1 must union `claude agents --json` (unscoped) filtered against `git worktree list --porcelain`

- [ ] **Step 3: Confirm which by testing the unscoped call**

```bash
claude agents --json | jq -r '.[] | select(.cwd | test("standup-probe")) | "\(.name)\t\(.cwd)"'
git worktree list --porcelain | awk '/^worktree /{print $2}'
```

Expected: the probe appears in the unscoped listing. This distinguishes "agent view cannot see it at all" from "`--cwd` scoping is prefix-based".

- [ ] **Step 4: Tear down**

```bash
claude agents --json | jq -r '.[] | select(.cwd | test("standup-probe")) | .sessionId' | xargs -I{} claude stop {} 2>/dev/null || true
cd /Users/benjaminmatton/Developer/fund
git worktree remove /Users/benjaminmatton/Developer/fund-worktrees/standup-probe --force
git branch -D probe/standup-roster
git worktree list
```

Expected: `git worktree list` shows only the main checkout and any pre-existing worktrees. Leaving a probe worktree behind is a plan failure — an unreaped worktree is a full codebase copy.

- [ ] **Step 5: Write the answer into the spec**

Replace §9 of `docs/superpowers/specs/2026-08-24-standup-dispatch-design.md` with the measured result, in this shape:

```markdown
## 9. Open question, settled 2026-08-24

`claude agents --json --cwd <main checkout>` DOES / DOES NOT return sessions whose cwd is a worktree of
that repo. Measured with a probe session in `fund-worktrees/standup-probe`: <exact observed output>.

Consequence for Phase 1: <either "no worktree join needed" or "Phase 1 unions the unscoped listing with
`git worktree list --porcelain` paths">.
```

If the answer is DOES NOT, also edit §4 Phase 1 to describe the union, so Task 3 implements the right thing.

- [ ] **Step 6: Commit**

```bash
cd /Users/benjaminmatton/Developer/fund
git add docs/superpowers/specs/2026-08-24-standup-dispatch-design.md
git commit -m "docs: the roster question the standup design rests on is measured, not assumed"
```

---

### Task 2: Phase 0 — the board becomes a query

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (replace the `## Phase 0 — read the plan` section)

**Interfaces:**
- Produces: the term **candidate lanes** — a list of `{issue number, title, region hint, plan path or none}` — consumed by Phase 2's poll and Phase 4's reconcile.

- [ ] **Step 1: Read the current file end to end**

```bash
cat ~/.claude/skills/morning-standup/SKILL.md
```

Expected: 165 lines, phases 0–3, a "When it goes sideways" table. Do not start editing before reading all of it — the phases share vocabulary and several rules are load-bearing prose that must survive the rewrite.

- [ ] **Step 2: Replace Phase 0 with the board query**

Replace the whole `## Phase 0 — read the plan` section with:

```markdown
## Phase 0 — the board

Lanes come from issues, not from plan files. Plan-file checkboxes are not progress: measured in one
repo, 359 unchecked and zero checked across seven plans, including work that had shipped. Ticking is
friction for the ticker and invisible to everyone else, so nobody ticks. Read a plan only when a lane
names one, and only as the *how*.

Find the phase **map issue** — an issue whose body holds the ordered child list for the current phase.
Then compute the **frontier**:

```bash
gh issue list --state open --json number,title,labels --limit 100
gh api repos/{owner}/{repo}/issues/<n> --jq '{sub_issues_summary, issue_dependencies_summary}'
```

A candidate lane is an open child of the map issue that is:

- **unblocked** — `issue_dependencies_summary.blocked_by == 0`
- **unclaimed** — no live row in `~/.claude/align/<repo-basename>/map.md`

in map order. That order is the human's priority decision, made once when the children were ordered.
Do not re-ask it, and do not re-derive it from titles, age, or labels.

**No map issue in this repo?** Say so in one line, list the open issues as context, and ask the human
to name today's lanes. Then continue to Phase 1 unchanged. Never invent a priority order from an
unordered issue list — a different order every morning is worse than no order.
```

- [ ] **Step 3: Verify the file still parses as a skill**

```bash
head -8 ~/.claude/skills/morning-standup/SKILL.md
grep -c "^## Phase" ~/.claude/skills/morning-standup/SKILL.md
```

Expected: frontmatter intact (`name: morning-standup`, `disable-model-invocation: true`), and the Phase count reflects the sections present so far. Frontmatter damage silently unregisters the skill.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: the standup reads a board it can query, not checkboxes nobody ticks"
```

---

### Task 3: Phase 1 — one command for the roster

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (replace the `## Phase 1 — roster` section)

**Interfaces:**
- Consumes: Task 1's settled answer about worktree visibility.
- Produces: the **roster** — rows of `{name, sessionId, cwd, startedAt}`, self excluded — consumed by Phases 2, 3, 4.

- [ ] **Step 1: Replace the three-source join**

Replace the roster-building portion of `## Phase 1 — roster` with:

```markdown
## Phase 1 — roster

```bash
claude agents --json --cwd <repo-root>
```

One row per session: `name`, `sessionId`, `cwd`, `kind`, `startedAt`. This listing **includes you**.
Find your own row by matching your session id and exclude it — you do not poll yourself. This is
positive self-identification; the old rule ("you are the one `ListAgents` omits") was inference by
elimination and broke whenever the listing was partial.

Window start is the newest file in `~/.claude/align/<repo-basename>/standups/`. No file yet — first
standup — use the last 24 hours. Keep only sessions active since then, by the mtime of
`~/.claude/projects/<escaped-cwd>/<sessionId>.jsonl`, where `<escaped-cwd>` is the cwd with every `/`
and `.` turned into `-` (`tr './' '-'`).

Still true, and still the things that catch people out:

- **`cwd` is where a session sits, not what it edits.** A session in the main checkout can commit into
  a worktree with `git -C`. The roster is a floor; reconcile it against what people report.
- **A session that entered a worktree writes to a worktree-scoped projects directory**, and its old
  path goes quiet. One round read that as a 3.5-hour stall on a session that was working. Check the
  worktree path before calling anyone idle.
- **Key records on `sessionId`.** Names churn — six renamed inside one day — and refs proved
  unreliable. Send to the name; record the id.
- **A peer's other repos are invisible to you.** "Nothing is mine" covers this repo only.
- **Re-check the roster immediately before you dispatch.** Fleets grow: one run built a roster of 3 and
  found 11 live sessions nineteen minutes later. State coverage — "polled 3 of 11".

If `claude agents --json` fails, stop with the error. Do not fall back to name matching.
```

If Task 1 found that `--cwd` scoping misses worktree sessions, add the union it recorded in the spec
instead of the bare `--cwd` call.

- [ ] **Step 2: Verify against the live machine**

```bash
claude agents --json --cwd /Users/benjaminmatton/Developer/fund | jq -r '.[] | "\(.name)\t\(.sessionId)"'
```

Expected: the fund sessions, with ids — the command in the skill text runs as written. A skill whose first command is wrong fails silently at 8am.

- [ ] **Step 3: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "fix: the roster is one supported command, and finding yourself stops being guesswork"
```

---

### Task 4: Phases 2 and 3 — poll for capacity, digest with evidence

Poll and digest change together: the digest prints the capacity answer beside the numbers that qualify it, so splitting them would leave one half referring to a field the other has not added.

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (replace `## Phase 2 — poll` and `## Phase 3 — digest, to everyone`)

**Interfaces:**
- Consumes: candidate lanes (Task 2), roster (Task 3).
- Produces: per session, `{did, doing, next, blocked, capacity}` — consumed by Phase 4.

- [ ] **Step 1: Replace Phase 2**

```markdown
## Phase 2 — poll

One `SendMessage` per rostered peer. Send this, with the user's agenda line appended if given:

> Morning standup. Answer at your next natural pause — do not abandon work in flight. Keep it to a
> few lines; this is a standup, not a report.
>
> Five fields:
> - **did** — what you worked on since <window start>, in your own words. Not a commit list. Point at
>   whatever artifact backs it *if one exists*. "No artifact yet, spent the morning reading `gate/`"
>   is a fine answer. Design work, debugging, and dead ends all count.
> - **doing** — one line. Name the lane or issue it serves if there is one; "not on the board" is a
>   fine and useful answer
> - **next** — one line
> - **blocked** — what unblocks you and who owns that. "nothing" is valid.
> - **capacity** — today's candidate lanes are listed below. Could you take **ownership** of one at
>   your next task boundary, or are you too deep in what you have? Owning a lane means running it —
>   fanning out to subagents, or splitting it — not typing it yourself. Name which lane if yes.
>   **This is not an assignment and nothing has been decided** — you are being asked, not told.
>
> <candidate lanes, one per line: #<issue> — <title>>
>
> Where you name a fact — a branch, a path, a state — check it rather than recalling it. A session
> that compacted an hour ago will confidently report a branch it left.

Then **end the turn**, saying who you are waiting on, and subscribe to each peer's next idle:

```
SendMessage(..., notify_when_idle: true)
```

**An idle notice is not a reply.** It fires when that session next finishes a turn — and fires
immediately if the session is already idle, possibly before it has read the poll at all. The notice
bounds the wait; the five fields are what count. Collection ends on a full house, or when every
subscribed session has both notified and taken a turn since the poll landed. A session that notifies
without answering is silent, and is treated as silent.
```

- [ ] **Step 2: Replace Phase 3's digest block, keeping its findings and flag rules verbatim**

Change only the per-session block; leave the "Against the plan", "Flags", and absence-rule prose as it
stands, except that "Against the plan" now reads against the board:

```markdown
## Phase 3 — digest, to everyone

1. **One block per session** — did / doing / next / blocked / capacity. Silent sessions named as
   silent.

   Print each capacity answer beside two numbers: the session's age (from `startedAt`) and its
   transcript size. The answer is what routes the lane; the numbers stop a "yes, I'm free" from a
   six-hour session being read blind. **Label them as proxies** — compaction count is not observable
   on this machine, so age and size hint at context pressure and do not measure it.

2. **Against the board** — three lines, no more:
   - lanes in flight, and who has each
   - **the next unclaimed lane nobody is on** — the single most useful line in the standup
   - **drift** — work reported that no open issue covers, and issues someone is treating as done that
     are still open

   Report drift as a fact, not a verdict. A session working off-board is often right and the board is
   often stale; the standup's job is to make the divergence visible, not to rule on it.
```

- [ ] **Step 3: Verify no orphaned references**

```bash
grep -n "sleep 300\|Four fields\|read the plan\|progress doc" ~/.claude/skills/morning-standup/SKILL.md
```

Expected: no matches. Each of those belongs to the replaced design; a leftover means two contradictory instructions in one file.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: the poll asks who can take a lane, and the digest shows what that answer is worth"
```

---

### Task 5: Phases 4 and 5 — reconcile, then dispatch

The new core. Reconcile and dispatch are one task because a lane's disposition (`covered` / `needs a chat`) is meaningless until something acts on it.

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (insert two new sections after Phase 3)

**Interfaces:**
- Consumes: candidate lanes, roster, poll replies.
- Produces: per lane, `{issue, disposition, owning sessionId or null}` — consumed by Phase 6's `map.md` write.

- [ ] **Step 1: Add Phase 4**

```markdown
## Phase 4 — reconcile

**One lane, one overseer.** A lane is *owned*, never *worked*. The seat that takes it runs it — fans
out to subagents, or fires `/split-the-plan` if it is big enough — and does not implement it directly.
This is uniform: a session that was already running and a chat opened this morning take a lane on
exactly the same terms, so "covered" means one thing in every row of the digest.

Each candidate lane resolves to exactly one of:

- **covered** — a live session answered capacity yes, and its stated region does not collide with the
  lane's
- **needs a chat** — nobody free, or the only candidate's region collides

**Region** means what `get-aligned` means: an area of code, not a filename (`parse_config` and its
callers; the retry block in `send()`). One file routinely has three owners, so filename matching
produces false conflicts and hides real ones.

A capacity yes whose region collides is a **flag, not an assignment** — say so and send it to
`/get-aligned`. Never bind two lanes to one session to make the numbers work.

**Silence is not a release.** A rostered session that never answered has not given up its region. Its
lanes are treated as needing a chat only where no live session claims that region; replacing a silent
session's head is the human's decision, never an inference from a missing reply.
```

- [ ] **Step 2: Add Phase 5**

```markdown
## Phase 5 — dispatch

Record `T = now` (epoch ms). Then tell the human exactly this much:

> Open **N** chats in this repo. Nothing else — I'll take it from there.

List what each will own so they can sanity-check the split. Do not give them prompts to paste. Do not
ask for session ids.

When they confirm, read the roster again and take every session with `startedAt > T` whose `cwd` is at
or under the repo. Those are the new chats. **If fewer appear than asked**, bind what exists, name the
unbound lanes, and wait.

**This skill provisions nothing.** A seat that owns a lane is an overseer, and an overseer does not
edit files — it reconciles, fans out, and reviews. It sits in the repo root. Isolation belongs one
level down, to whatever that overseer dispatches, and it is that overseer's call to make with the lane
in front of it. Creating worktrees for seats that may never write a line is cost paid on speculation,
and a worktree nobody wrote in still has to be reaped.

So: **this skill never creates a branch, a worktree, or a commit.** It reads, it messages, and it
writes two files under `~/.claude/align/`. Everything that touches the repo is done by a seat that owns
a lane.

Brief each bound session as an **overseer**, not an implementer:

- the lane — issue number, title, and the region it covers
- **that it owns the lane, and owns how the lane gets done**: `subagent-driven-development` for a lane
  that fits one seat's fan-out, `/split-the-plan` for one that needs several, `writing-plans` first
  where it needs a plan. The choice is the overseer's, not this skill's
- that isolation is its call — worktrees for what it dispatches, outside the repo, one at a time
- its neighbours: who owns the adjacent lanes, under what name, and that
  `coordinating-with-peer-sessions` governs how they talk
- that it reports at the next standup, not continuously

Then stop. Do not brief in waves and do not follow up to check receipt.
```

- [ ] **Step 3: Verify the two phases do not contradict the no-arbitration rule**

```bash
grep -n "flag\|assign\|decide" ~/.claude/skills/morning-standup/SKILL.md | head -20
```

Expected: every occurrence either surfaces a flag or explicitly routes a decision to the human or to
`/get-aligned`. If any line has the skill deciding a contested region itself, rewrite it — this family
of skills reports contested ownership and never settles it.

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "feat: the standup ends in lanes owned instead of a digest nobody acts on"
```

---

### Task 6: Phase 6, the sideways table, and the sibling rows

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (add Phase 6; rewrite the sideways table; update the frontmatter description)
- Modify: `~/.claude/skills/huddle/SKILL.md` (one row of its sibling table)

**Interfaces:**
- Consumes: lane dispositions from Task 5.

- [ ] **Step 1: Add Phase 6**

```markdown
## Phase 6 — record

Write the digest to `~/.claude/align/<repo-basename>/standups/YYYY-MM-DD.md` — outside every repo, so
no working tree is dirtied.

Write lane rows to `~/.claude/align/<repo-basename>/map.md`, the same file `get-aligned` and
`split-the-plan` use. One ownership map per repo, or those two will route around work this standup just
assigned. **Update rows keyed on `sessionId`; never rewrite the file wholesale**, and stamp which skill
touched it last.

**Release stale claims before writing new rows.** A row whose `sessionId` is no longer live, or whose
issue is closed, is released and struck. The measured failure in fleets like this is not two agents
racing for one task — it is an agent finishing and never letting go of the slot. Check release
observationally (is the session in the roster, is the issue closed); never ask.

Then **broadcast the digest to every rostered peer**, including the silent ones — that is the part that
makes it a standup. Give each copy a personalized tail naming **what concerns them**, never what they
should do next:

> **Concerns you:** <peer> is blocked on <thing> and named you as the owner. / You and <peer> are both
> in <region>. / You now own lane #<issue>. / Nothing concerns you today.

A tail is a relevance filter, not an instruction slot. Unowned work goes to the human as a flag and
stays unowned until they say otherwise.

Report to the human last, leading with the unclaimed lanes and the flags — those are the parts needing
a decision.
```

- [ ] **Step 2: Rewrite the sideways table**

```markdown
## When it goes sideways

| Situation | Do this |
|---|---|
| No sessions active, board has work | Skip the poll entirely. Report the lanes and say "open N". This is a normal run with an empty roster, not a failure |
| No sessions active, no board | Say so, write no file, stop. A quiet morning is not a failure |
| No map issue in this repo | Say so in one line, ask the human to name today's lanes, continue |
| First run, no `standups/` dir | Create it, window is the last 24 hours, say that in the digest |
| Peer answers with a wall of text | Summarize to the five fields in the digest. Do not re-ask; a standup does not block on one person |
| Peer never answers | Named silent; still receives the digest; its region is not reassigned |
| Human opens fewer chats than asked | Bind what exists, name the unbound lanes, wait. Never double-bind a session |
| Capacity yes but the region collides | A flag, not an assignment. Say `→ /get-aligned` |
| A flag looks urgent | Still just a flag. Let the human fire the next skill |
| A `map.md` row's session is gone | Released and struck before new rows are written |
| `claude agents --json` fails | Stop with the error. Do not fall back to name matching |
```

- [ ] **Step 3: Update the frontmatter description**

```yaml
description: Daily standup across every active session on this repo — everyone reports, everyone hears the digest, and the work nobody is on gets owned.
```

- [ ] **Step 4: Update huddle's sibling table**

In `~/.claude/skills/huddle/SKILL.md`, change the `/morning-standup` row from
`Daily, scheduled. Everyone hears everyone. Nothing happens afterwards` to:

```markdown
| `/morning-standup` | Daily, scheduled. Everyone hears everyone. Ends in lanes owned |
```

- [ ] **Step 5: Verify both files**

```bash
grep -n "Nothing happens afterwards" ~/.claude/skills/*/SKILL.md
head -6 ~/.claude/skills/morning-standup/SKILL.md
grep -c "^## Phase" ~/.claude/skills/morning-standup/SKILL.md
```

Expected: no matches for the old row; frontmatter intact with `disable-model-invocation: true`; seven Phase headings.

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md huddle/SKILL.md
git commit -m "feat: the standup records lanes and releases claims nobody is holding"
```

---

### Task 7: Run it cold, fix what breaks, commit

A skill is prose that another agent executes. The only test that means anything is a fresh context running it with no idea what it was supposed to say.

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md` (fixes found by the run)

- [ ] **Step 1: Dispatch a cold subagent**

Dispatch a subagent that has **not** read this plan, the spec, or this conversation. Give it exactly:

> Run the skill at `~/.claude/skills/morning-standup/SKILL.md` against the repo
> `/Users/benjaminmatton/Developer/fund`. Follow it literally. Stop before Phase 5 sends anything to a
> peer or asks the human to open chats — instead, report what you would have sent, to whom, and why.
> Then list every point where the skill was ambiguous, where a command failed, or where you had to
> guess.

Withhold the spec and the plan deliberately. If the skill only works for someone who has read the
design, it does not work.

- [ ] **Step 2: Confirm the two hard cases were exercised**

The `fund` repo has no map issue yet (artifact B is not built), so the run **must** hit the degradation
path. Check the subagent's report for:
- it said in one line that there is no map issue and asked for lanes, rather than inventing an order
- it did not read plan-file checkboxes as state
- it identified and excluded its own session from the roster

Any of those failing is a skill defect, not a subagent defect. Fix the prose.

- [ ] **Step 3: Fix every ambiguity the run surfaced**

Fix them in the skill file. An ambiguity a cold reader hit is a defect even when the intended reading is
obvious to you — you wrote it.

- [ ] **Step 4: Re-run cold with a second fresh subagent**

Same prompt, new subagent. Expected: no new ambiguities in the paths the first run exercised. If the
second run surfaces a fresh crop in the same phases, the phase is underspecified rather than unlucky —
rewrite it rather than patching a third time.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills
git add morning-standup/SKILL.md
git commit -m "fix: the standup survives a reader who has not seen its design"
```

- [ ] **Step 6: Record what is still untested**

Append to the spec, under a new `## 10. Not yet exercised` heading:

```markdown
## 10. Not yet exercised

Phase 5 has never dispatched: no run has asked the human to open chats, bound a session on
`startedAt > T`, provisioned a worktree, or written a release row. The cold runs in Task 7 stop short of
it deliberately. The first real dispatch is the first true test of Phases 5 and 6, and it should be run
with the human watching.

Artifact B (the `fund` board migration) is not built, so the frontier query in Phase 0 has only ever
taken its degradation path.
```

```bash
cd /Users/benjaminmatton/Developer/fund
git add docs/superpowers/specs/2026-08-24-standup-dispatch-design.md
git commit -m "docs: the standup spec says which of its phases have never run"
```

---

## What this plan does not build

**Artifact B — the `fund` board migration** (map issue, sub-issues, `blocked_by` edges, retiring dead
plans, the `issue-tracker.md` claim-key correction). It is a separate plan against a different repo.
Until it exists, `fund` exercises Phase 0's degradation path, which Task 7 verifies deliberately.

**Factoring out the duplicated roster logic.** `get-aligned` and `split-the-plan` keep their own copies
of the join Task 3 replaces. Collapsing all three onto `claude agents --json` is a real improvement and
a separate change; doing it here would put three skills in one diff.
