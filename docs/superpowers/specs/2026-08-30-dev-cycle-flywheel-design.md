# The dev-cycle flywheel: `/setup-dev-cycle` and region journals

Design spec. The deliverable is one new human-only skill, three edits to existing skills, and a
per-repo file layout under `.claude/`. First repo: `fund`. Second: any repo with none of the files.

Claims are marked **[M]** (observed — a file was opened or a command run and its output read) or
**[R]** (reasoned; has not been exercised).

## 1. The problem

The goal is a flywheel: a development loop where agents own parts of a repo, become the domain
experts of those parts, and each turn of the loop starts smarter than the last without a human
carrying the knowledge across.

The loop exists — `morning-standup` → `owning-a-lane` → `eod-digest`. What it accumulates today
[M]:

| Artifact | Accumulates | Read on the next turn by |
|---|---|---|
| `~/.claude/align/<repo>/standups/YYYY-MM-DD.md` | day outcomes, ledger trend | the next morning — ledger line only |
| `~/.claude/align/<repo>/map.md` | region → **sessionId** | nobody once the session closes |
| issue threads | per-issue corrections | whoever reopens that issue |
| the skills' "Measured:" lines | global lessons | every repo — edited by hand |
| `.claude/health.md`, `.claude/standup.md` | nothing — static descriptors | every morning and evening |

Nothing accumulates **per region**. `morning-standup` Phase 5 briefs a lane with the issue, the
region name, the escalation address and the neighbours (`morning-standup/SKILL.md:1341-1350` [M]).
What the previous owner of that region learned — where the traps are, which issue bodies lied,
which findings are noise — is in a day file nobody reopens and a transcript that is gone. The
next owner re-derives it.

Evidence, `standups/2026-08-28.md` [M]: the phase pointer in `.claude/standup.md` went stale and
the loop could only "report and ask"; the overseer built a land objective from health output
without reading the issue thread, and that lesson is prose in the day file.

The fund's own trading agents already run the missing mechanism [M]: a seat is a human-edited
**charter** plus an agent-written **journal** (`journals/pm.md` via `state/journal.py`), inside a
deterministic loop. Charters change only by human commit; journals accumulate daily. This design
applies that split to the dev cycle.

## 2. Outputs — three files under `.claude/`, one skill that seeds them

| File | Answers | Written by | Read by |
|---|---|---|---|
| `health.md` | what healthy means | human, via the skill | `morning-standup` 0b, `eod-digest` 1 |
| `standup.md` | what next is | human, via the skill | `morning-standup` 0c |
| `regions/<name>.md` | what owning `<name>` requires; what past owners learned | standing part: human · journal: every lane | `morning-standup` 5, `eod-digest` 6 |

### 2.1 `health.md` and `standup.md`

Unchanged from the existing contract, which both consuming skills already implement [M]:

- `health.md` front matter: `health_command` (one command, exit 0 = healthy), `suppress`
  (check ids that are known noise). Prose: how to read the output, including the known false
  positives.
- `standup.md` front matter: `intent_sources` — **named files only, never a glob**. Prose: which
  entries are standing sources and which are dated snapshots to replace, and which phase is current.

The fund's hand-written versions (`.claude/health.md`, `.claude/standup.md` [M]) are the reference
shape.

### 2.2 `regions/<name>.md`

```markdown
---
paths:
  - gate/
  - tests/test_gate*.py
---
# gate — standing
What the region is. The invariants a lane here must not break. Where its tests
are. What "done" means here. Changes only by human commit.

# Journal
## 2026-08-28 · #141 · fund-46
- Issue body said tighten the assertion; the thread had already established
  the assertion is fine and the real gap is dedup keyed on (recorded, held).
- `db_broker_agreement` warns "unwired" — accurate, not broken; don't file it.
```

**Front matter.** `paths:` is a list of path prefixes or globs. It is the join: a lane maps onto
region files by the paths its region covers. Regions in `map.md` are free text at lane
granularity ("the `_promised_stop` neighbourhood") [M]; region files are the stable, coarser
vocabulary, and `paths:` is what makes the match deterministic.

**Standing section.** Human-edited. Seeded by the skill from what the repo already says about the
region (its `CLAUDE.md` architecture map, `docs/agents/*`), then owned by the human. Nothing in
this design lets the loop edit it.

**Journal.** Append-only; one heading per lane, keyed `date · issue · session name`. The content
rule: **what the next owner would otherwise re-derive.** Not status — the issue holds that. Not a
restatement of the standing section. A lane that learned nothing writes one line saying so, because
silence is indistinguishable from omission.

**Region names** come from the repo's own architecture. For `fund`: `gate`, `orchestrator`,
`agents`, `stratgate`, `calibration`, `fundbt`, `state`, `ops`, `skills` (the `~/.claude/skills`
region that `map.md` already tracks as "outside this repo" [M]) — confirmed, not assumed, at setup.

`map.md` is untouched. It still answers *who holds it today*; the region file answers *what
holding it requires*.

## 3. The skill — `/setup-dev-cycle`

Human-only (`disable-model-invocation: true`), per repo, re-runnable. Lives at
`~/.claude/skills/setup-dev-cycle/SKILL.md`. Prompt-driven, in the shape of
`setup-matt-pocock-skills` [M]: explore, present, confirm one section at a time with the
recommended answer first, write.

### 3.1 Detect — a candidate is a claim, not a fact

| Output | Candidates come from |
|---|---|
| health | `Makefile` targets, `package.json` scripts, `scripts/*` whose name says check/status/health/test |
| intent | `docs/superpowers/{specs,plans}/`, `PROGRESS.md`, `ROADMAP.md`, `TODO.md`, `specs/acceptance.md` |
| regions | the architecture map in `CLAUDE.md`/`AGENTS.md`; top-level packages with their own tests; `CODEOWNERS`; region strings in `~/.claude/align/<repo>/map.md` |

Never guess. A repo with no candidates in a column gets nothing written in that column.

### 3.2 Verify

- **Health:** run the candidate command. Exit 0 required. A target's *name* is not evidence.
- **Intent:** open each candidate. Keep it only if it marks something as next or open (a landing
  order, unticked acceptance items, an open-items heading). A file that only describes the world
  is dropped — `morning-standup` 0c will not mine it [M], so naming it produces nothing.
- **Regions:** every entry in a candidate's `paths:` must resolve to at least one existing file.
  Two candidates claiming the same path is a refusal, not a merge.

### 3.3 Confirm

One section per output, recommended answer first. The human names, renames and strikes regions
here. Skip a section entirely when detection found nothing for it.

### 3.4 Write and commit

Only what verified and was confirmed. Then:

- **Re-run on a repo that already has files** (fund): existing `health.md` and `standup.md` are
  re-verified and left alone unless broken; only missing region files are seeded; existing region
  files are never rewritten. Journals are never touched.
- One pointer line in the repo's `## Agent skills` block (`CLAUDE.md`, else `AGENTS.md`, never
  both — the `setup-matt-pocock-skills` rule [M]).
- One row in the global `~/.claude/CLAUDE.md` skill-routing table, once.
- Commit on the repo's current branch with a `chore:` message. **In a shared checkout, ask before
  committing** — the fund rule that an uncommitted `CLAUDE.md` edit is a fleet broadcast [M]
  applies to the pointer line.

### 3.5 Fail → write nothing

A health command that exits non-zero, an intent file that does not mark next-or-open, a region
whose paths resolve to nothing: say why and write nothing for that output. This is the governing
constraint, carried from the sketch:

- file **absent** → the consuming phase is skipped silently; "its absence is not a finding"
  (`morning-standup/SKILL.md:472` [M])
- file **present but broken** → its stderr is rendered as a finding, morning and evening, every day
  (`morning-standup/SKILL.md:489`, `eod-digest/SKILL.md:102` [M])

A broken descriptor is strictly worse than none. That is why verification runs the command
instead of trusting the target's name.

### 3.6 Does not touch

`~/.claude/align/<repo>/` (each file there is written on demand by the skill that owns it);
`docs/agents/*` (`setup-matt-pocock-skills` owns it — `health.md` only points at it).

## 4. Three skill edits that turn files into a flywheel

All three are additive. No existing phase changes behaviour when `regions/` is absent.

### 4.1 `morning-standup` Phase 5 — inline the region file into the brief

A fifth inlined item after the existing four (`SKILL.md:1341-1350` [M]): the full content of every
region file whose `paths:` cover the lane's region. **Inlined, not pointed at**, for the reason the
existing four are: a briefed session may never follow a pointer.

- Match is by the lane's region text against each file's `paths:`. Ambiguous → inline every
  plausible file. Over-inclusion costs tokens; under-inclusion costs a re-derivation.
- No match → the brief says so, and the lane reports the gap in its standup reply. Seeding a
  region is the setup skill's job; a lane never creates one.

### 4.2 `owning-a-lane` — the journal entry is part of done

One row added to the "Where" table (`SKILL.md:81-87` [M]): the lane's journal entry rides in its
PR. A lane with no PR (a decide lane) ships it as a one-file PR. Same standing as `closes #N` [M]:
a convention the brief treats as required, because nothing warns when it is omitted.

### 4.3 `eod-digest` Phase 6 — check, and report to the session

For each lane that closed today, look for today's heading in its region file(s). Missing → one
line in that session's *Concerns you* tail. **A report to the session, never a finding in the
digest body**: a daily finding trains the reader to skip it, and the next real one goes past
(`morning-standup/SKILL.md:485-487` [M]).

## 5. The outer ratchet stays human

A journal line that says "check X is always noise" is promoted into `health.md`'s `suppress` list,
a standing section, or a charter — **by the human, by commit**. This is `docs/agents/regression-
ratchet.md`'s rule [M] and the split the trading seats already run. The loop may *propose* such an
edit as a lane (the 2026-08-28 stale-phase case was exactly that [M]); it may not make it.

Inner loop (automated): lane → journal → next brief.
Outer loop (human): journal → standing section / `health.md` / charter.

## 6. Failure modes

| Case | Behaviour |
|---|---|
| `regions/` absent | Phase 5 briefs as today; eod check skipped. Not a finding |
| Region file with unparsable front matter, or `paths:` matching nothing | One line as a finding; brief without it |
| Two region files claim one path | Setup refuses to write. If it happens anyway, Phase 5 inlines both and says so |
| A journal entry edits or deletes a prior entry | Caught in PR review — the file is evidence. Stated in the standing rule, not enforced by code |
| Lane learned nothing | One-line entry. Omission and silence must not look alike |
| Health command passes at setup, breaks later | Already handled — rendered as a finding by both consumers [M]; the fix is a lane |

## 7. Testing

Skills are prose; the test is a cycle. [R] throughout — none of this has run.

1. `writing-skills` pass on the new skill and on each of the three edits.
2. Run `/setup-dev-cycle` on **fund**. Pass: existing `health.md` and `standup.md` re-verified and
   byte-identical; region files seeded for the confirmed names; nothing else in the tree changed.
3. Run it on a repo with none of the three files (`pricing-intel` or `vcguru`). Pass: all three
   written, or a stated reason per output and nothing written for it.
4. One full day on fund. Pass: the morning brief for a lane carries its region file's content; the
   lane's PR carries a journal heading dated today; the eod tail is silent for that session.
5. Day two. Pass: a fresh chat is briefed with something a day-one lane wrote. **This is the
   flywheel test; the other four are setup.**

## 8. Non-goals

No dev-seat runtime (long-lived per-region processes), no scoring of dev lanes, no automated edit
to any standing section, `health.md`, or charter, no change to `map.md`'s shape. Those are the
next design, and they consume what this one produces.
