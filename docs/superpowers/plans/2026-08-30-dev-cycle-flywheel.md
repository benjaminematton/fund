# Dev-Cycle Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give any repo a per-region journal that the evening writes and the morning reads, plus a human-only skill that seeds the three descriptor files the loop consumes.

**Architecture:** One new prompt-driven skill (`/setup-dev-cycle`) with three seed templates, and three additive edits to `morning-standup`, `owning-a-lane`, `eod-digest` that make region files flow through brief → lane → close-out. The skills live in the `~/.claude/skills` git repo; the per-repo files live under `.claude/` in each target repo. No code — every deliverable is prose, so each task's test is a fresh-subagent scenario run **before** and **after** the change (`superpowers:writing-skills`, "The Iron Law").

**Tech Stack:** Markdown skills with YAML front matter; `git`; the `Agent` tool for baseline/verification scenarios. Spec: `docs/superpowers/specs/2026-08-30-dev-cycle-flywheel-design.md`.

## Global Constraints

- The skill is human-only: `disable-model-invocation: true`. No session may run it on a repo; only Benjamin invokes it.
- **Never write a descriptor without verifying it first**: run the health command (exit 0 required); open every intent file; resolve every `paths:` entry to ≥1 file. Fail → write nothing for that output, say why.
- `intent_sources` are **named files only, never a glob**.
- A region file's `# … — standing` section changes only by human commit. The `# Journal` section is **append-only**.
- The skill never touches `~/.claude/align/<repo>/` or `docs/agents/*`.
- The three consumer edits are additive: with `.claude/regions/` absent, every existing phase behaves exactly as today.
- Skill edits are tested per `writing-skills`: baseline scenario **without** the change (RED, record the rationalization verbatim), then **with** it (GREEN). An edit with no failing baseline is deleted, not kept.
- Commits: conventional prefixes (`feat:`, `docs:`, `chore:`). **No `Co-Authored-By` trailer** — Benjamin rejects commits that carry one.
- `~/.claude/skills` is a git repo on `main` with private origin `benjaminematton/claude-skills`; commit skill edits there. Its `.gitignore` excludes `docs/` — plans and specs never go in that repo.
- Fund-side changes go on the `dev-cycle-flywheel` branch in worktree `~/Developer/fund-wt/dev-cycle-flywheel`, never in the main `~/Developer/fund` checkout (peers occupy it; no `git checkout` there).
- Fixture repos for scenarios live in the session scratchpad, which is reaped at date rollover — recreate, never assume.

---

## File Structure

| Path | Responsibility |
|---|---|
| `~/.claude/skills/setup-dev-cycle/SKILL.md` | The process: detect → verify → confirm → write, and what it refuses to do |
| `~/.claude/skills/setup-dev-cycle/health.md` | Seed template for `.claude/health.md` |
| `~/.claude/skills/setup-dev-cycle/standup.md` | Seed template for `.claude/standup.md` |
| `~/.claude/skills/setup-dev-cycle/region.md` | Seed template for `.claude/regions/<name>.md` |
| `~/.claude/skills/morning-standup/SKILL.md` | Phase 5: fifth inlined brief item; two "sideways" rows |
| `~/.claude/skills/owning-a-lane/SKILL.md` | "Where" table: journal entry is part of Done |
| `~/.claude/skills/eod-digest/SKILL.md` | Phase 6: journal-entry check → *Concerns you* tail; two "sideways" rows |
| `~/.claude/skills/README.md` | One row under "The daily dev cycle" |
| `~/.claude/CLAUDE.md` | One row in the cross-session routing table |
| `<fund>/.claude/regions/*.md`, `<fund>/CLAUDE.md` | Produced by Benjamin running the skill on fund (Task 7) |

---

### Task 1: Scenario fixture — a repo with one broken and one working health candidate

A throwaway repo every later baseline runs against. Not committed anywhere.

**Files:**
- Create: `$SCRATCH/fixture-repo/` (where `$SCRATCH` is the session scratchpad directory)

**Interfaces:**
- Produces: a git repo at `$SCRATCH/fixture-repo` with `make check` exiting 1, `make test` exiting 0, one intent file that marks next-steps, one that only describes, one package with tests.

- [ ] **Step 1: Create the fixture**

```bash
SCRATCH=/private/tmp/claude-501/-Users-benjaminmatton-Developer-fund/a75ce612-3377-4ec8-b3eb-212a8ec81443/scratchpad
mkdir -p $SCRATCH/fixture-repo && cd $SCRATCH/fixture-repo && git init -q
cat > Makefile <<'EOF'
check:
	@echo "checker not wired" >&2; exit 1
test:
	@echo "3 passed"; exit 0
EOF
mkdir -p core/tests docs/superpowers/specs
printf 'def add(a, b):\n    return a + b\n' > core/__init__.py
printf 'from core import add\ndef test_add():\n    assert add(1, 2) == 3\n' > core/tests/test_add.py
cat > PROGRESS.md <<'EOF'
# Progress
## Done
- core package scaffolded
## Open items
- wire the checker so `make check` means something
EOF
cat > docs/superpowers/specs/2026-08-01-arch.md <<'EOF'
# Architecture
The core package holds arithmetic. There is one test file.
EOF
cat > CLAUDE.md <<'EOF'
# fixture
## Architecture map
`core/` (arithmetic) — tests in `core/tests/`.
## Agent skills
### Issue tracker
Local markdown. See `docs/agents/issue-tracker.md`.
EOF
git add -A && git commit -qm "chore: fixture" && git log --oneline -1
```

Expected: one commit printed, e.g. `abc1234 chore: fixture`.

- [ ] **Step 2: Verify the two health candidates behave as designed**

Run: `cd $SCRATCH/fixture-repo && make check; echo "exit=$?"; make test; echo "exit=$?"`
Expected: `checker not wired` then `exit=2` (make wraps the 1), then `3 passed` then `exit=0`.

No commit — fixture only.

---

### Task 2: `/setup-dev-cycle` skill + seed templates

**Files:**
- Create: `~/.claude/skills/setup-dev-cycle/SKILL.md`
- Create: `~/.claude/skills/setup-dev-cycle/health.md`
- Create: `~/.claude/skills/setup-dev-cycle/standup.md`
- Create: `~/.claude/skills/setup-dev-cycle/region.md`

**Interfaces:**
- Consumes: fixture from Task 1.
- Produces: the region-file shape every later task relies on — front matter `paths:` (list), `# <name> — standing`, `# Journal` with `## YYYY-MM-DD · #<issue> · <session name>` headings.

- [ ] **Step 1: Baseline (RED) — run the scenario without the skill**

Dispatch one subagent (general-purpose) with exactly this prompt, substituting `$SCRATCH`:

```
You are setting up the repo at $SCRATCH/fixture-repo for a daily standup skill.
Write `.claude/health.md` with YAML front matter `health_command: <command>` naming the
repo's health check, and `.claude/standup.md` with front matter `intent_sources:` listing
the files that say what is next. Look at the Makefile and the docs to decide. Do it now
and report what you wrote. Do not ask questions.
```

Expected failure (record verbatim in the task notes): the agent names `make check` as `health_command` — the target's *name* says health — without running it, and/or lists `docs/superpowers/specs/2026-08-01-arch.md` as an intent source although it only describes the world. Note the exact sentence it uses to justify each. If the baseline agent happens to run the command and refuse, tighten the prompt with "Be quick; the Makefile target names are self-explanatory" and re-run — the rationalization must be observed, not assumed.

- [ ] **Step 2: Delete the baseline's output**

Run: `rm -rf $SCRATCH/fixture-repo/.claude && cd $SCRATCH/fixture-repo && git status --short`
Expected: empty.

- [ ] **Step 3: Write the seed templates**

`~/.claude/skills/setup-dev-cycle/health.md`:

```markdown
---
health_command: <the one command that was run and exited 0>
suppress: []
---

# What healthy means in this repo

`<health_command>` runs <what it reads>. It is read-only: it never writes,
deploys, or mutates anything.

## Interpreting the output

- <one bullet per section of the output: what a green row means, what a
  red one means, and which rows are known noise. A check named in
  `suppress` still prints, marked `[suppressed]`.>

## Filing what this finds

<How a finding becomes tracked work in this repo — the issue-tracker
convention, and the label or title shape that stops a finding being
re-derived by the next session.>
```

`~/.claude/skills/setup-dev-cycle/standup.md`:

```markdown
---
intent_sources:
  - <named file>
---

# Intent sources for morning-standup Phase 0c

Read only what each source marks as next or open:

- <file>: <which marker — a Landing order section, unticked `- [ ]` items in
  the current phase (<phase>), entries under an Open items heading>

These feed the standup's Today proposal as intent items. They are not lanes
and are not dispatched; an off-board item enters work only when the human
confirms it in the Today reply or files it as an issue.

## Keeping this list current

A dated spec is a snapshot and goes stale by design: when a newer design
becomes the one being landed, replace that line, never accumulate. Standing
sources (an acceptance file, a progress file) stay. When the current phase
advances, update the phase named above — the standup reads this file, not
the board, to know which phase's boxes matter.
```

`~/.claude/skills/setup-dev-cycle/region.md`:

```markdown
---
paths:
  - <path prefix or glob>
---
# <name> — standing

<What the region is. The invariants a lane here must not break. Where its
tests are and how to run only them. What "done" means here. Seeded from what
the repo already says about itself; owned by a human from then on. Changes
only by human commit — including promoting a journal line up into this
section, or into `health.md`'s `suppress` list. The loop may propose that
as a lane; it may not make the edit.>

# Journal

<Append-only. One heading per lane: `## YYYY-MM-DD · #<issue> · <session name>`.
Under it, what the next owner would otherwise re-derive — never status (the
issue holds that), never a restatement of the standing section. A lane that
learned nothing writes one line saying so.>
```

- [ ] **Step 4: Write `SKILL.md`**

```markdown
---
name: setup-dev-cycle
description: Configure a repo for the daily dev cycle — verify, then write .claude/health.md, .claude/standup.md and .claude/regions/*.md so morning-standup and eod-digest can read it. Human-only; run once per repo, re-run to add regions.
disable-model-invocation: true
---

# Setup the dev cycle

Three files under `.claude/` make a repo legible to `morning-standup` and `eod-digest`:

| File | Answers | Written by | Read by |
|---|---|---|---|
| `health.md` | what healthy means | human, here | `morning-standup` 0b · `eod-digest` 1 |
| `standup.md` | what next is | human, here | `morning-standup` 0c |
| `regions/<name>.md` | what owning `<name>` requires, and what past owners learned | standing part: human, here · journal: every lane | `morning-standup` 5 · `eod-digest` 6 |

This skill writes them. It is prompt-driven, not a script: detect, verify, confirm, write. It
is the sibling of `setup-matt-pocock-skills`, which owns `docs/agents/*`; `health.md` only points
at that.

## The constraint that shapes everything

A file that is **absent** is skipped silently by both consumers — "its absence is not a finding."
A file that is **present but broken** has its error rendered as a finding, morning and evening,
every day, until someone fixes it. **A broken descriptor is strictly worse than none.**

So: never write a descriptor whose command you have not run, whose intent file you have not
opened, or whose paths you have not resolved. **A candidate is a claim, not a fact.** A Makefile
target named `check` is a claim that a check exists.

## 1. Detect — never guess

Read what is there. A repo with no candidates in a column gets nothing written in that column,
and that is a correct outcome, not a failure.

| Output | Candidates |
|---|---|
| health | `Makefile` targets; `package.json` scripts; files in `scripts/` whose name says check, status, health, or test |
| intent | `docs/superpowers/specs/*`, `docs/superpowers/plans/*`, `PROGRESS.md`, `ROADMAP.md`, `TODO.md`, `specs/acceptance.md` |
| regions | the architecture map in `CLAUDE.md` or `AGENTS.md`; top-level packages that carry their own tests; `CODEOWNERS`; region strings in `~/.claude/align/<repo-basename>/map.md` if it exists |

Also read: whether `.claude/health.md`, `.claude/standup.md`, `.claude/regions/` already exist
(§5), and whether `CLAUDE.md`/`AGENTS.md` has an `## Agent skills` block (§4).

## 2. Verify — a candidate is a claim

**Health.** Run each candidate command. Keep only one, and only if it exited 0. Read its output
so the prose in §4 can say what each section means. A command that exits non-zero is reported
with its stderr and **not written** — not even with a note saying "fix later". The consumers
would render that note as a finding every day.

**Intent.** Open each candidate. Keep it only if it marks something as *next* or *open*: a
Landing order whose steps can be told apart as landed/unlanded, unticked `- [ ]` items under a
phase, an Open items heading. A file that only describes the world is dropped — the consumer
will not mine prose for implied work, so naming it produces nothing and reads as a source that
never yields. Record which marker each kept file uses; that goes in the prose.

**Regions.** For each candidate name, propose `paths:` — prefixes or globs — and resolve them.
Every entry must match at least one existing file. Two candidates whose paths overlap is a
**refusal for both**, reported with the overlap; the human splits or merges them in §3. A
region's standing section is seeded from what the repo already says about it (its line in the
architecture map, a `docs/agents/*` file that covers it) and nothing else — do not write what
you have not read.

## 3. Confirm — one section at a time

Take the outputs in order — health, intent, regions — one section, one answer. Lead with the
recommended answer so the human can accept it in a word. Skip a section entirely when §2 kept
nothing for it, and say so in one line.

For regions the human names, renames, and strikes. Do not argue for a region they strike.

## 4. Write and commit

Write only what verified in §2 and was confirmed in §3, from the seed templates beside this
file: [health.md](./health.md), [standup.md](./standup.md), [region.md](./region.md).

Then one pointer in the repo's instructions file — `CLAUDE.md` if it exists, else `AGENTS.md`,
never both, and never create the second when the first exists. If an `## Agent skills` block
exists, add a sub-block inside it; otherwise append the block:

    ### Dev cycle

    `.claude/health.md`, `.claude/standup.md` and `.claude/regions/` feed `morning-standup`
    and `eod-digest`; `/setup-dev-cycle` writes them. A lane's region-journal entry rides in
    its PR.

Commit on the repo's current branch, `chore: set up the dev cycle`. **In a checkout other
sessions share, ask before committing** — an edit to `CLAUDE.md` is live instruction for every
session started after it.

Once, ever: one row in `~/.claude/CLAUDE.md`'s cross-session routing table pointing at this
skill. Check for it before adding it.

## 5. Re-running on a repo that already has files

- An existing `health.md` or `standup.md` is **re-verified** (§2) and **left byte-identical**
  unless verification fails, in which case report it and do not touch it — deleting a
  descriptor is the human's call.
- Only **missing** region files are seeded. An existing region file is never rewritten, and a
  `# Journal` section is never touched by this skill under any circumstances.
- A re-run that changes nothing says so and commits nothing.

## 6. Does not touch

`~/.claude/align/<repo-basename>/` — every file there is written on demand by the skill that
owns it. `docs/agents/*` — `setup-matt-pocock-skills` owns it.

## Rationalizations — stop

| Thought | Reality |
|---|---|
| "The target is called `check`; that is what it does" | A name is a claim. Run it. |
| "I'll write it with a note to fix the command later" | The consumers render that note as a finding every day. Write nothing. |
| "This spec is obviously the current one" | Open it. If it does not mark next-or-open, it yields nothing and is dropped. |
| "A glob for `intent_sources` saves updating the file" | Globs pull in every dated snapshot forever. Named files only. |
| "I'll seed the standing section from what I understand of the code" | Seed from what the repo *says*; understanding goes in a journal entry by a lane that earned it. |
| "The journal is empty; I'll add a first entry" | The skill never writes to a journal. |
```

- [ ] **Step 5: Verify (GREEN) — same scenario, skill present**

Dispatch a fresh subagent with the Task 2 Step 1 prompt, prefixed by:

```
First read ~/.claude/skills/setup-dev-cycle/SKILL.md and follow it exactly. Where it says to
confirm with the human, assume the human accepts every recommended answer.
```

Expected: it runs `make check`, reports the non-zero exit, writes `health_command: make test`
(the only candidate that exited 0); lists `PROGRESS.md` in `intent_sources` and drops the arch
spec with a reason; seeds `.claude/regions/core.md` with `paths: [core/]` and a standing section
derived from the CLAUDE.md line; touches no journal.

Run: `cd $SCRATCH/fixture-repo && cat .claude/health.md .claude/standup.md .claude/regions/core.md`
Expected: front matter as above; `standup.md` has no glob; `core.md` ends with an empty `# Journal` section.

- [ ] **Step 6: Refactor — plug what the GREEN run rationalized**

If the GREEN subagent violated any rule, add its exact sentence to the Rationalizations table and re-run Step 5. Repeat until clean. Then reset the fixture: `cd $SCRATCH/fixture-repo && git checkout -q -- . && rm -rf .claude`.

- [ ] **Step 7: Commit**

```bash
cd ~/.claude/skills && git add setup-dev-cycle && git commit -m "feat: setup-dev-cycle — verify-then-write the three dev-cycle descriptors"
```

---

### Task 3: `morning-standup` Phase 5 — inline the region file into the brief

**Files:**
- Modify: `~/.claude/skills/morning-standup/SKILL.md:1341-1352` (the four inlined brief items) and `:1529-1530` (sideways table)

**Interfaces:**
- Consumes: region-file shape from Task 2.
- Produces: the brief item wording that Task 4's lane expects ("region file(s) inlined below").

- [ ] **Step 1: Baseline (RED)**

Prepare the fixture: `mkdir -p $SCRATCH/fixture-repo/.claude/regions` and write `$SCRATCH/fixture-repo/.claude/regions/core.md`:

```markdown
---
paths:
  - core/
---
# core — standing
Arithmetic. Tests: `pytest core/tests`.

# Journal
## 2026-08-29 · #7 · fx-01
- Issue #7's body says `add` overflows; it does not — Python ints are unbounded. The real report was about the JS port that no longer exists.
```

Dispatch a subagent with:

```
Read ~/.claude/skills/morning-standup/SKILL.md, Phase 5 only (from "## Phase 5" to "## Phase 6").
Repo: $SCRATCH/fixture-repo. Write the brief you would send to a session bound to this lane:
issue #7 "add() overflows on large ints", region `core/`, remediate lane, escalation address
"overseer", no neighbours. Output the brief verbatim and nothing else.
```

Expected failure (record verbatim): the brief carries issue, region, ownership sentence, escalation address, neighbours — and **no content from `core.md`**. The lane would start by re-deriving that #7 is moot.

- [ ] **Step 2: Add the fifth inlined item**

In `~/.claude/skills/morning-standup/SKILL.md`, after the bullet ending `\`coordinating-with-peer-sessions\` governs how they talk` and before `Those four are inlined rather than left to the pointer`, insert:

```markdown
- **the region file(s), inlined** — every `.claude/regions/<name>.md` whose `paths:` cover the
  lane's region, content in full: the standing section and every journal entry. Match the lane's
  region text against each file's `paths:`; when it is ambiguous, inline every plausible file —
  over-inclusion costs tokens, under-inclusion costs a re-derivation. **No file matches → say
  so in the brief** and tell the lane to report the gap in its standup reply. Seeding a region
  is `/setup-dev-cycle`'s job; a lane never creates one. `.claude/regions/` absent → this item
  is omitted and nothing else changes.
```

Change the sentence `Those four are inlined rather than left to the pointer` to `Those five are inlined rather than left to the pointer`, and `a session that misses the first two starts typing` stays as is.

- [ ] **Step 3: Add the sideways rows**

After the row beginning `| An \`intent_sources\` entry does not exist or does not parse |`, insert:

```markdown
| `.claude/regions/` absent | Brief without the region item. Not a finding — same contract as a missing `health.md` |
| A region file has unparsable front matter, or its `paths:` match nothing in the tree | One line as a finding; brief without it. Two files claiming one path → inline both and say so |
```

- [ ] **Step 4: Verify (GREEN)**

Re-dispatch the Step 1 prompt to a fresh subagent.
Expected: the brief contains the `core.md` standing section and the 2026-08-29 journal entry verbatim, under a line identifying it as the region file.

- [ ] **Step 5: Verify the absent case**

Run: `mv $SCRATCH/fixture-repo/.claude/regions $SCRATCH/regions.bak`; re-dispatch Step 1's prompt.
Expected: a brief identical in shape to the baseline — no region item, no mention of a missing directory as a problem. Then `mv $SCRATCH/regions.bak $SCRATCH/fixture-repo/.claude/regions`.

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills && git add morning-standup/SKILL.md && git commit -m "morning-standup: inline the lane's region file(s) into the Phase 5 brief"
```

---

### Task 4: `owning-a-lane` — the journal entry is part of Done

**Files:**
- Modify: `~/.claude/skills/owning-a-lane/SKILL.md:81-87` (the "Where" table)

**Interfaces:**
- Consumes: brief wording from Task 3; region-file heading shape from Task 2.
- Produces: the heading form `## YYYY-MM-DD · #<issue> · <session name>` that Task 5's check greps for.

- [ ] **Step 1: Baseline (RED)**

Dispatch a subagent with:

```
Read ~/.claude/skills/owning-a-lane/SKILL.md. You have been briefed as owner of a remediate lane,
issue #7, region `core/`, in the repo at $SCRATCH/fixture-repo. The brief inlined
.claude/regions/core.md — read that file too. Do not do the work. List, exactly, every artifact
your lane must produce before it is done, and where each one goes.
```

Expected failure (record verbatim): a PR carrying `closes #7`, progress comments on the issue — and no mention of a journal entry in `core.md`.

- [ ] **Step 2: Add the row**

In the "Where" table, after the `| Done |` row and before `| Stuck, unsure, scope changed |`, insert:

```markdown
| What you learned about the region | **An entry appended to `.claude/regions/<name>.md`**, under `# Journal`, headed `## YYYY-MM-DD · #<issue> · <your session name>` — for every region file your brief inlined. It rides in your PR; a lane with no PR ships it as a one-file PR. Content is what the next owner would otherwise re-derive: which claims in the issue were wrong, what turned out to be noise, where the real seam was. Not status — the issue holds that. Learned nothing → one line saying so; silence and omission must not look alike. **Append only.** Never edit or remove a prior entry: the file is evidence. This is a convention exactly like `closes #N` — nothing warns when it is omitted, and the next owner pays for it |
```

- [ ] **Step 3: Verify (GREEN)**

Re-dispatch Step 1's prompt.
Expected: the list includes a journal entry in `.claude/regions/core.md` with the heading form and "rides in the PR".

- [ ] **Step 4: Verify the pressure case**

Re-dispatch with this appended to Step 1's prompt: `You are out of budget and the PR is already approved; the only question is whether to merge now or add anything.` 
Expected: it still names the journal entry as owed before merge. If it rationalizes skipping it, add the exact sentence to the skill's `## Rationalizations` table and re-run.

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills && git add owning-a-lane/SKILL.md && git commit -m "owning-a-lane: the region-journal entry is part of done"
```

---

### Task 5: `eod-digest` Phase 6 — check the journal, tell the session

**Files:**
- Modify: `~/.claude/skills/eod-digest/SKILL.md` — Phase 6, before `Then **broadcast the digest to every rostered peer**`; sideways table after `| \`health_command\` missing or non-zero |`

**Interfaces:**
- Consumes: heading form from Task 4.

- [ ] **Step 1: Baseline (RED)**

Fixture: ensure `$SCRATCH/fixture-repo/.claude/regions/core.md` exists as in Task 3 (journal dated 2026-08-29 only). Dispatch:

```
Read ~/.claude/skills/eod-digest/SKILL.md, Phase 6 only. Today is 2026-08-30. Repo:
$SCRATCH/fixture-repo. One lane closed today: session fx-02, issue #7, region `core/`,
PR merged at 15:10. Write the "Concerns you" tail you would send to fx-02, and nothing else.
```

Expected failure (record verbatim): `Nothing concerns you tonight.` — the missing journal entry is invisible.

- [ ] **Step 2: Add the check**

Insert immediately before the paragraph beginning `Then **broadcast the digest to every rostered peer**`:

```markdown
**Region journals.** For each lane that closed today, open every `.claude/regions/<name>.md`
whose `paths:` cover its region and look for a heading `## <today> · #<issue> · ` under
`# Journal`. Missing → one line in that session's *Concerns you* tail: *"Your lane #<issue>
closed with no entry in `.claude/regions/<name>.md` — the next owner of `<name>` re-derives
what you learned."* **A tail line, never a finding in the digest body**: a daily finding trains
the reader to skip it, and the next real one goes past. `.claude/regions/` absent → skip this
check; not a finding. Never write the entry yourself — it is the lane's, and this skill holds
no lane.
```

- [ ] **Step 3: Add the sideways rows**

After `| \`health_command\` missing or non-zero | Render stderr as a finding; the poll still runs |`, insert:

```markdown
| No `.claude/regions/` | Skip the journal check. Not a finding |
| A closed lane's region matches no region file | Say so in that session's tail; nothing to check against. Seeding is `/setup-dev-cycle`'s job |
```

- [ ] **Step 4: Verify (GREEN)**

Re-dispatch Step 1's prompt.
Expected: a tail naming `#7`, `core.md`, and the missing entry — and not offering to write it.

- [ ] **Step 5: Verify the satisfied case**

Append to `core.md`: `\n## 2026-08-30 · #7 · fx-02\n- Nothing new; #7 was already moot per the prior entry.\n`. Re-dispatch.
Expected: `Nothing concerns you tonight.` Then restore the file to its Task 3 content.

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills && git add eod-digest/SKILL.md && git commit -m "eod-digest: check each closed lane's region journal, report in the tail"
```

---

### Task 6: Routing — README row and global CLAUDE.md row

**Files:**
- Modify: `~/.claude/skills/README.md` — "The daily dev cycle" table
- Modify: `~/.claude/CLAUDE.md` — the "Cross-session" routing table

- [ ] **Step 1: README row**

After the `| [\`eod-digest\`](eod-digest/) |` row, add:

```markdown
| [`setup-dev-cycle`](setup-dev-cycle/) | `/name` | Makes a repo legible to the two above — verifies, then writes `.claude/health.md`, `.claude/standup.md`, and one `.claude/regions/<name>.md` per region, whose journal is what the evening writes and the morning reads |
```

Update the count in the README's first line (`41 of them` → `42 of them`) only if that number is still 41; otherwise leave it.

- [ ] **Step 2: Global CLAUDE.md row**

In `~/.claude/CLAUDE.md`, in the "Cross-session — several chats on one repo at once" table, after the `/eod-digest` row, add:

```markdown
| Make a repo legible to the daily cycle — health, intent, regions | `/setup-dev-cycle` |
```

- [ ] **Step 3: Verify**

Run: `grep -c "setup-dev-cycle" ~/.claude/skills/README.md ~/.claude/CLAUDE.md`
Expected: `1` for each.

- [ ] **Step 4: Commit and push the skills repo**

```bash
cd ~/.claude/skills && git add README.md && git commit -m "docs: list setup-dev-cycle" && git push origin main
```

`~/.claude/CLAUDE.md` is not in a repo; the edit is live once saved.

---

### Task 7: Run on fund — human step, verified by the executor

**Files:**
- Produced by Benjamin: `~/Developer/fund-wt/dev-cycle-flywheel/.claude/regions/*.md`, one `### Dev cycle` sub-block in `CLAUDE.md`

The skill is `disable-model-invocation: true`; no agent can run it. The executor prepares, Benjamin runs, the executor verifies.

- [ ] **Step 1: Record the pre-state**

```bash
cd ~/Developer/fund-wt/dev-cycle-flywheel && git fetch -q origin && git rebase -q origin/master && \
  shasum -a 256 .claude/health.md .claude/standup.md > $SCRATCH/pre.sha && cat $SCRATCH/pre.sha
```

(`$SCRATCH` is the session scratchpad; it is reaped at date rollover, so Steps 1–3 run on the same day.)

- [ ] **Step 2: Ask Benjamin**

Send, in your own window: *"Task 7 is yours: in `~/Developer/fund-wt/dev-cycle-flywheel`, run `/setup-dev-cycle`. Expected regions to confirm or strike: `gate`, `orchestrator`, `agents`, `stratgate`, `calibration`, `fundbt`, `state`, `ops`, `skills` (the `~/.claude/skills` region `map.md` tracks). Tell me when it has committed."* Stop until answered.

- [ ] **Step 3: Verify after the run**

```bash
cd ~/Developer/fund-wt/dev-cycle-flywheel && shasum -a 256 -c $SCRATCH/pre.sha && \
  ls .claude/regions/ && for f in .claude/regions/*.md; do \
    python3 -c "import sys,re;t=open('$f').read();assert t.startswith('---\npaths:'),'$f: no paths';assert '\n# Journal\n' in t,'$f: no journal';assert t.rstrip().endswith('# Journal'),'$f: journal not empty'"; done && \
  grep -n "### Dev cycle" CLAUDE.md && git status --short | wc -l
```

Expected: both `OK`; region files listed; no assertion output; the grep hits one line; `0` (everything committed).

- [ ] **Step 4: Open the PR**

```bash
cd ~/Developer/fund-wt/dev-cycle-flywheel && git push -u origin dev-cycle-flywheel && \
  gh pr create --title "chore: dev-cycle descriptors + region files" \
    --body "Spec: docs/superpowers/specs/2026-08-30-dev-cycle-flywheel-design.md. Plan: docs/superpowers/plans/2026-08-30-dev-cycle-flywheel.md. Seeds .claude/regions/ via /setup-dev-cycle; health.md and standup.md re-verified and unchanged."
```

Expected: a PR URL. Do not merge — that is Benjamin's.

---

### Task 8: Run on a bare repo — human step, verified by the executor

**Files:**
- Produced by Benjamin in `~/Developer/pricing-intel` (or `~/Developer/vcguru` if he prefers): `.claude/health.md`, `.claude/standup.md`, `.claude/regions/*.md`, or a stated reason per missing output

- [ ] **Step 1: Confirm the target has none of the three**

Run: `ls ~/Developer/pricing-intel/.claude/ 2>&1`
Expected: no `health.md`, `standup.md`, or `regions/`.

- [ ] **Step 2: Ask Benjamin**

*"Task 8: run `/setup-dev-cycle` in `~/Developer/pricing-intel`. Pass is all three outputs written, or a stated reason for each one not written. Tell me the outcome."* Stop until answered.

- [ ] **Step 3: Verify**

```bash
cd ~/Developer/pricing-intel && for f in .claude/health.md .claude/standup.md; do [ -f $f ] && echo "$f present" || echo "$f ABSENT — reason must be in Benjamin's reply"; done; \
  [ -f .claude/health.md ] && eval "$(sed -n 's/^health_command: //p' .claude/health.md)"; echo "health exit=$?"; \
  grep -n '\*' .claude/standup.md && echo "GLOB IN intent_sources — FAIL" || echo "no glob"
```

Expected: each present file listed; `health exit=0`; `no glob`.

---

### Task 9: The flywheel test — two days on fund

Observational; nothing to write. Runs after Task 7's PR is merged and a morning standup has read the region files.

- [ ] **Day 1 morning:** in the standup's Phase 5 output, find one brief. Pass: it contains the text of the matching `.claude/regions/<name>.md`. Record the lane's issue number.
- [ ] **Day 1 close:** `git log origin/master --oneline -- .claude/regions/` shows a commit from that lane's PR; the file has a heading `## <today> · #<issue> · <session>`. If the lane closed without one, the eod tail for that session names it — check `~/.claude/align/fund/standups/<today>.md`.
- [ ] **Day 2 morning:** a fresh chat bound to a lane in the same region receives, in its brief, the entry written on day 1. **This is the flywheel test.** Pass = something a day-1 lane wrote reached a day-2 chat with no human carrying it.
- [ ] **Record the result** as a comment on the Task 7 PR, or on the issue Benjamin files for it — with the lane numbers and the standup file paths, so the claim is checkable.
