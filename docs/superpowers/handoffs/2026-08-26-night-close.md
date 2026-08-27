# Handoff — end of the 2026-08-25/26 standup night

**Written by** sessionId `34c9cded-5ccb-4137-91d9-df75a5603996` (name `fund-4b` at dispatch,
churned to `fund-e5` mid-session — **address by sessionId, never the name**).
**Written** 2026-08-26. **Repo** `benjaminematton/fund`.
**`master`: read it, do not trust a SHA written here.** This document originally pinned
`074967d`; `master` reached `7c75390` within the hour. Per §7 — *a number depending on state
outside the artefact will eventually be wrong inside it*.

> **Location note.** The `handoff` skill says to write to the OS temp directory. Not done —
> `/private/tmp` on this machine is reaped at date rollover, and a reaped handoff is the exact
> failure this document exists to prevent. Repo precedent is `docs/superpowers/handoffs/`.

> ### ⛔ FALSE — corrected 10:20. **Eight of nine ended. `fund-c6` did not.**
>
> Lane #4's owner is live right now under the name **`fund-34`**, sessionId
> `25602639-8bd0-4198-a874-42969a7d7059` — character-for-character the owner in `map.md`'s
> lane-#4 row. Its **name churned**; its session never stopped. Verified against
> `~/.claude/sessions/*.json`: of the nine dispatched sessionIds, that one is the sole survivor,
> and it is the one holding uncommitted work.
>
> Caught by `fund-f5` (sessionId `5fee2153-2ec2-4c94-9f9c-dbf206afe637`).
>
> **The method is the finding.** This document inferred *who ended* from names missing in
> `ListAgents`. That listing answers exactly one question — **who is live right now**. "Who am
> I", "who is new", and "who ended" are all underivable from it. The correct instrument was
> `map.md`, which this same run keyed on `sessionId` precisely for this, and which was never
> cross-referenced. **The record was right the whole time; only the reading was wrong.**
>
> **Consequence:** the reflection lane never lost its owner. Nobody is self-assigned, there is
> no ownership vacuum, and there is nothing here to escalate to Benjamin. §1's original framing
> invented a stranger in that worktree and sent a peer hunting for them.
>
> **Before treating any session as gone, resolve the name to a sessionId and check `map.md`.**

**Eight of the nine lane sessions have ended; `fund-c6` (now `fund-34`) has not.** Several new
`fund-*` sessions exist that this run did not dispatch — run `ListAgents` and resolve each to a
sessionId rather than trusting any name in this document.

---

## 1. Work that is NOT on the remote — read this first

> ### ⛔ THE FRAMING OF THIS SECTION WAS WRONG. Corrected 10:10 after two peer catches.
>
> **Three of the four rows below are live lanes, not abandoned work.** `feat/reflection-stage`
> is being typed in right now; `feat/dev-status` is `fund-91`'s and grew from 2 commits to 7
> while this document was being read. Unpushed does not mean orphaned — in a fleet where every
> lane works in its own worktree, *unpushed is the normal state of work in progress*, and this
> section read it as a loss signal.
>
> **The one genuinely orphaned thing is the root checkout's 21 detached commits**, whose author
> is not among the live sessions.
>
> **So the actual risk is not deletion — it is collision.** Several sessions are working
> branches nobody registered, and the first symptom is two of them in one region. Run
> `ListAgents` and ask, rather than acting on any row below.

Measured against each branch's own remote, not against `master`. Four locations, and only four:

| Where | Branch | Ahead of remote | Dirty | What it is |
|---|---|---|---|---|
| `fund-wt/reflection-stage` | `feat/reflection-stage` | **4 commits** | 4 files | **#4's implementation.** The reflect seat, `submit_reflection`, the nightly job. **Owner is LIVE** — `fund-c6`, now named `fund-34`, sessionId `25602639-…`, mid-amend. ~~Its author is gone.~~ (Cell rewritten 10:50; see the ⛔ block above. A table cell gets read out of order, so the false claim is corrected here rather than only in prose.) |
| `fund-wt/reflection-idempotent` | `fix/reflection-writer-idempotent` | 1 commit (`0b754ad`) | 0 | #4's idempotency fix. **Deliberately held**, not stranded — hold was the lane's own recommendation. |
| `Developer/fund` (root) | detached `HEAD` | **21 commits** | 14 files | Standup-dispatch spec work. See §2. |
| `fund-wt/dev-status` | `feat/dev-status` | ~~2 commits~~ **7 and growing** | 1 file | ~~Unknown provenance; predates this run.~~ **`fund-91`'s live lane**, all seven commits made today 09:54–10:03. Corrected 10:10. |

**None has a remote branch.** A `git worktree remove` or a branch delete on any of these loses
the work outright.

> ### ⛔ CORRECTION — 2026-08-26 10:05. This section originally said "**push it before anything
> ### else**." That instruction was wrong. **Do not push `feat/reflection-stage`.**
>
> Caught by `fund-f5` (sessionId `5fee2153-2ec2-4c94-9f9c-dbf206afe637`) before acting on it;
> both reasons verified by me afterwards.
>
> **1. It publishes a deliberately held commit.** `0b754ad` is an **ancestor** of
> `feat/reflection-stage`, not a parallel commit — `git merge-base --is-ancestor 0b754ad HEAD`
> is true and it is the oldest of the four. So the original §1 and its own row 2 contradicted
> each other. The hold wins: `fund-c6` stated it *after* D1 was ruled, in these words —
> *"hold stands — `fix/reflection-writer-idempotent` (`0b754ad`) stays unmerged **and
> unpushed**."* Benjamin's "do what you rec" endorsed that recommendation, so the hold covers
> publishing, not merely merging. **Lifting it is Benjamin's call in his own window.**
>
> **2. The author is NOT gone and the worktree is live.** `scripts/reflect_day.py` had mtime
> `10:05:11`, seconds before the check; eight modified files against the four recorded below.
> `HEAD` is still `81e0063`, so the new work is uncommitted. Someone picked the lane up after
> this document was written. **This session dispatched nobody to it** — `fund-c6` owned #4 and
> its session ended, and no reassignment was made. Whoever is in there is self-assigned or was
> sent from outside this run. **Find the owner before touching anything in that worktree.**
>
> **Consequence for the risk assessment:** work being actively typed is not work about to be
> lost. Treat §1's urgency as overstated. If nobody claims the worktree, that is an *ownership*
> question for Benjamin — not a push decision for any agent.
>
> *Method note, because it is the more general lesson:* this document's "author is gone" claim
> rested partly on a `find -newermt '-30 minutes'` check — a GNU flag that this BSD `find`
> silently ignores. It returned nothing and that was nearly read as evidence of absence.
> `ls -lT` / `stat -f %Sm` is the correct instrument here. **A check that appeared to pass
> measured nothing.**

`feat/reflection-stage` holds four commits of real feature work implementing the phase-2 gap,
plus an untracked plan at `docs/superpowers/plans/2026-08-25-reflection-stage.md`.

Everything else that looked at-risk is fine — `docs/adr-stop-amend`, `docs/overseer-bookends`,
`worktree-model-usage-volumes`, `news-seat-eval`, `docs/progress-2026-08-25` are all pushed.

## 2. The root checkout is divergent, and its `CLAUDE.md` is edited

`Developer/fund` sits on a detached `HEAD` **21 commits ahead of `origin/master`**, all
standup-dispatch spec work. Its working tree also carries **uncommitted `CLAUDE.md` edits that
delete two sections present on `master`**: the `### Devops` block, and the paragraph beginning
*"A rule is ratified only if `git show origin/master:CLAUDE.md` contains it."*

**Correction, 10:10 — it deletes THREE things, not two.** `git diff origin/master -- CLAUDE.md`:

1. the `### Devops` section
2. the *"A rule is ratified only if `git show origin/master:CLAUDE.md` contains it"* paragraph
3. **`### Regression ratchet`** — *"A real failure becomes a permanent eval case, written by a
   human, once. Eligibility and the procedure: `docs/agents/regression-ratchet.md`."*

The original text said "two" and would have stopped the next reader after two. Caught by
`fund-f5`, which confirmed it the strongest available way — **its own auto-loaded context is
missing all three sections**, which demonstrates the effect rather than inferring it from a diff.

The third compounds with §4: `decisions.md` carries a 2026-08-26 ruling *about*
`docs/agents/regression-ratchet.md`, while the only pointer to that file has been deleted out
from under every session in this cwd. A ruling resolving a question in a document nobody can
navigate to.

`CLAUDE.md` is auto-loaded per session from the working tree, so **every session started in this
directory since those edits is running without those three rules and cannot tell.** Author and
intent unknown.

**Do not fix it by editing the file** — the rule being deleted is precisely the one saying you
cannot settle a rule that way. It goes to Benjamin as-is.

> ### ⚠ The `CLAUDE.md` deletion is not one file's problem. The mechanism is the checkout.
>
> Two things follow from the root checkout's `HEAD` being 21 commits divergent, and both were
> demonstrated on 2026-08-26:
>
> **1. `git status` reports files as untracked (`??`) that are on `master`.** The status is
> computed against the divergent `HEAD`, not against `master`. Of ten docs that looked untracked
> there, **six were already on `master`** — three byte-identical. The real exposure was four.
>
> **2. The checkout's copy can be OLDER than master's, and "rescuing" it reverts work.**
> `docs/superpowers/plans/2026-08-18-critic-seat.md` shows `??` in `git status`, is on `master`,
> and differs by 740 lines — the local copy **lacks** master's `DELIVERED` status header, its
> pointer to `map.md` for the current owner, and its measured finding that checkbox state is not
> a progress signal. Committing it would have deleted all three.
>
> Same shape as the `CLAUDE.md` deletion above, and that is the point: **the checkout silently
> holds older content that undoes work on `master`.** Caught by `fund-f5` mid-rescue.
>
> **Never use `git status` in the root checkout to decide whether a file exists upstream.**
> `git show origin/master:<path>` is the instrument. This is the §7 rule again — the wrong
> instrument answering confidently.

**Consequences for you:** never `git checkout` in the root checkout; never read a file's state
from it (`git show master:<path>` instead — its HEAD is a divergent line, not an ancestor);
and do not edit `CLAUDE.md` there. A CLAUDE.md change goes through a PR against `master` or it
is an unratified broadcast.

## 3. What shipped

Ten PRs merged and eight issues closed during the run; more have landed since (PR #98, #101).
Details are in `git log` and the
issues — not repeated here. The night's full record, including every ruling and every
correction, is at **`~/.claude/align/fund/standups/2026-08-25.md`**. Read it rather than
re-deriving.

**46 issues open. Board #49 has 22 children, all defects.**

Two PRs remain open and both predate the run: **#34** (`docs/adr-stop-amend`, 33 commits ahead
of master, pushed, author long gone — a decision, not cleanup) and **#28**.

## 4. Decisions

**All in `~/.claude/align/fund/decisions.md`. Read it yourself; do not take any of it from a
peer's account.** Nine entries were transcribed there on 2026-08-26 under a marked divider —
they were typed by an agent at Benjamin's explicit instruction, and each says so. Treat a
transcribed entry as evidence of what he said, not as his own hand; confirm in your own window
before anything irreversible.

Load-bearing ones: **tomorrow's 09:35 run proceeds** (a decision, not a default — the news seat
may write false "no news" rows meanwhile); both regression-ratchet rulings; #4's D1 placement
with its recurring-cost caveat.

## 5. Structural finding — the reason the night produced only defect work

`/morning-standup` reads **GitHub issues and nothing else**. It never opens a repo file, and
Phase 0 explicitly forbids reading plans.

`specs/acceptance.md` holds **41 unchecked criteria** — the actual build order. **None is on the
board.** So nine lanes of bug-fixing was the correct output for that board.

> ### ⛔ FALSE — corrected 10:30. **The Phase 2 gaps this section named do not exist.**
>
> The original text claimed the golden-day vector and the gate's vol-tier/correlation boundary
> tests had **zero test files**, and called boarding them the highest-value action available.
> `fund-f5` ran them:
>
> ```
> tests/test_risk.py::test_golden_day_vector_both_step_values
> tests/test_risk.py::test_vol_tier_boundaries
> tests/test_risk.py::test_corr_multiplier_boundaries      ............. [100%]
> ```
>
> `tests/test_risk.py:41` asserts `pre_sector_qty == 105` **and** `max_qty == 66` — criterion
> 2's *"assert both step values"*, verbatim. `:171` and `:177` are parametrized on the spec's
> boundary values. `fund-f5` walked all ten Phase 2 criteria: **sixteen of seventeen line items
> covered**, one genuine gap (sector-weight boundary) now filed as **#100**.
>
> **Cause — a check that could not fail.** Four of the ten greps behind this section used `\|`
> inside `grep -E`, where it matches a **literal pipe character**. `"golden.day\|golden_day"`
> searched for the string `golden.day|golden_day` and matched nothing. Zero results were read as
> zero coverage.
>
> | Claim | Pattern | Truth |
> |---|---|---|
> | golden-day vector: 0 files | **broken** | **7 files** |
> | vol-tier / corr-multiplier: 0 files | **broken** | **2 files** |
> | missing-signal default: 0 files | **broken** | **12 files** |
> | CEO approval: 0 files | **broken** | 0 files — accidentally correct |
> | `debate`, `submit_critique`: 0 | valid | 0 — stands |
> | `pm_timeout`, `allowed_actions`, `scoreboard` | valid | stand |
>
> **Following this section literally would have boarded work that already exists** — the exact
> failure `#94` documents, committed by the document warning about it.
>
> **This is the third by-construction claim in this handoff to fail on execution** (§1 author
> gone, §2 two-sections, §5 zero-test-files). §7's first rule is *a by-construction claim is not
> a finding until an executing counter-attempt fails.* **The author wrote that rule and then
> wrote five sections without applying it to himself.** A reader who trusts §7 will trust §1–§6
> by adjacency, and on this page the measured parts and the asserted parts look identical.

**Verified by execution:** `debate` and `submit_critique` have **zero** files — Phase 3's debate
and critique flow are genuinely unstarted. `pm_timeout` (5), `allowed_actions` (11) and
`scoreboard` (4) are built.

**Reasoned, NOT demonstrated — nobody has verified these, do not read them as measured:**
"Phase 1 is 9/10", "Phase 3 unstarted" as a whole, "4 of 13 seats exist". The seat count came
from listing `agents/config/*.yaml`; the phase figures came from the broken greps above.
`fund-f5` audited **Phase 2 only** — Phases 1 and 3 remain unaudited, and #99's measured Phase 2
table must not lend them credibility by sitting next to them.

**The one demonstrated Phase 2 gap is #100** (sector-weight boundary). The original
"highest-value action available" recommendation is withdrawn.

## 6. Doc structure — settled this session

`specs/INDEX.md` (**new, untracked in the root checkout — commit it**) defines three authority
tiers: canonical (`specs/`, `charters/`, `fixtures/`, `CLAUDE.md`, undated) · derived (`docs/`,
`plans/`, `research/`, dated filenames) · live state (board #49, PRs, `git log` — not in the
repo). **The rule: nothing in the repo may claim to be live state.**

`PROGRESS.md` now carries a staleness header. Three proposed moves were **checked and dropped** —
the phase-2 desk design declares itself non-canonical; `plans/` is referenced by path in
`CLAUDE.md`; and `docs/superpowers/specs/` is a hardcoded skill output path, so subject-based
routing would re-diverge on the next `/brainstorming` run. **Do not re-propose them.**

Outstanding: one `CLAUDE.md` line — *a dated filename is a snapshot, never current state; current
state is board #49 and `git log`* — plus a pointer to `specs/INDEX.md`. Must go via PR (§2).

## 7. Standing rules earned this night — these bind you

- **A by-construction claim is not a finding until an executing counter-attempt fails.** Six
  lanes, ~15 retractions, every one caught by this.
- **A count is reported with the command that produced it.**
- **Independence of construction is not independence of model** — two separately built test
  generators agreed on zero evasions because they shared a blind spot; extending one found 45.
- **A claim can be true when checked and false when used.** Four lanes hit it; one caught
  `master` moving between two consecutive commands. Re-check at the moment of use, not harder at
  the moment of writing.
- **A number depending on state outside the artefact will eventually be wrong inside it.**
- **A lane will not overwrite a record that is evidence, but will correct one that has become
  false.** Evidence versus instruction, not write versus don't-write.
- **Do not annex a region because the person who could have said no has gone to bed.**
- **⭐ Before believing "no X in F", prove the instrument can match anything at all.**
  The day's most load-bearing rule. The cheap operative form, which costs one command:
  **count the broader category first.** Before concluding `decisions` is absent from
  `schema.sql`, run `grep -c 'CREATE TABLE' state/schema.sql`. **If that returns zero too, the
  instrument is broken, not the world.**

  `fund-f5` refined this from the original wording ("make the positive claim execute") and the
  refinement is correct: a `pytest` run is not always available for a negative claim, but
  proving the search *can* match something always is. Pair a negative search with an inventory
  (`grep -n '^def test_'`) or a count — something that would have *found* X.

  **Three silent instruments in one session, three distinct mechanisms:** a GNU `find` flag BSD
  ignores; `\|` inside `grep -E` matching a literal pipe; and an over-specific literal
  (`CREATE TABLE decisions` missing `CREATE TABLE IF NOT EXISTS decisions`). Being careful about
  any one of them would not have caught the other two.

  **Why "be careful with your patterns" is not a control.** The author of this document wrote
  `\|` inside `grep -E` four times and shipped it as a headline finding. `fund-f5` — **an hour
  after reading that correction, hunting for that exact bug** — wrote the same broken
  alternation in four of ten patterns and did not see it. It was saved by an unrelated loud
  failure: an unquoted `--include=*.py` that zsh refused to glob. Had the quoting been right,
  the loop would have printed ten clean empty results and reached the same false conclusion.

  **The three catches, and only one is repeatable:** an unrelated loud failure (luck), a peer
  checking (costs another session), and the instrument check (repeatable, one command). That
  ranking is the argument for the rule.

  **And it finds things the broken instrument would have missed entirely.** The corrected search
  surfaced `id INTEGER PRIMARY KEY` at `schema.sql:48` — a rowid alias, confirming lane #4's
  surrogate-key concern — and `UNIQUE (run_date, ticker)` at `:63`, a replay-stable natural key
  that lane had not spotted.

- **Read from `master`, never from the root checkout's working tree.** Verifying `fund-f5`'s
  count above, this session got **9** `CREATE TABLE`s against its **11** and nearly published a
  contradiction. The 9 was the divergent detached `HEAD`; `git show origin/master:<path>` gives
  11, and `fund-f5` was right. Same family as the silent instruments: a confident answer from
  the wrong source.

  **Why "be careful with your patterns" is not a usable control.** The author of this document
  wrote `\|` inside `grep -E` four times and shipped the result as a headline finding. Then
  `fund-f5` — **an hour after reading that correction, while actively hunting for that exact
  bug** — wrote the same broken alternation in four of ten patterns and did not notice the regex
  at all. What saved it was unrelated: zsh expanded an unquoted `--include=*.py`, so every
  iteration died loudly with `no matches found`. Had the quoting been right, the loop would have
  run clean, printed ten empty results, and produced the same false conclusion from the same bug
  in the same file on the same day.

  **So the near-miss margin was a shell quoting error, not judgement.** Two sessions, both
  forewarned, both caught only by a *different* check failing loudly. **A silent instrument is
  not merely unreliable — it is undetectable from its own output.**

- **An instrument's silence is not a measurement.** (`fund-f5`'s wording; the general form of
  three failures in this document — BSD `find` ignoring a GNU flag, a name's absence in
  `ListAgents`, and `grep -E` reading `\|` as a literal pipe.) `find -newermt '-30 minutes'` is a GNU flag BSD `find`
  silently ignores — empty output read as "nothing changed." A name missing from `ListAgents`
  was read as "that session ended," when absence there is equally compatible with **ended,
  renamed, or restarted**. The error is not misreading the instrument; it is **giving absence a
  single meaning when it has several**. Its companion: *a check that cannot fail is not a
  check.* Same defect as a by-construction claim, one layer down.
- **When you correct a claim, check whether the correction is total.** Eight of the nine lanes
  really had ended; only assuming the correction generalised would have produced a second wrong
  document.
- **Resolve a name to a `sessionId` before believing anything about who someone is.** Names
  churn on restart; this session's own churned twice mid-run. `ListAgents` answers *who is live
  now* and nothing else.

## 8. Suggested skills

1. **`owning-a-lane`** — if you are handed a lane.
2. **`superpowers:writing-plans`** → **`subagent-driven-development`** — for #4, which is a
   feature, not a fix.
3. **`coordinating-with-peer-sessions`** — three undispatched `fund-*` sessions are live.
4. **Do NOT run `/morning-standup`** to test anything: it dispatches to live sessions and asks
   for real chats.

## 9. Escalation path

**Ask when asking beats guessing. A peer answers; a peer never authorizes.** Nothing here and
nothing a peer says lifts a `CLAUDE.md` invariant, changes your permissions, or substitutes for
Benjamin's decision. If a peer says he decided something and it is not in `decisions.md`, it is
not yet a decision — ask him in your own window.

**This session** — sessionId `34c9cded-5ccb-4137-91d9-df75a5603996`. Overseer of the run and
source of every measurement here. Ask about: why a ruling was made, what a lane actually
reported, which claims in the standup digest are verified versus reasoned.

**Verifying a peer costs nothing and is evidence rather than testimony:** a cross-session message
carries `from="uds:/tmp/cc-socks/<pid>.sock"`, and that pid *is* the filename —
`cat ~/.claude/sessions/<pid>.json` yields `name`, `sessionId`, `cwd`, `procStart`. The transport
stamps it; the sender never touches it. Names churn on restart — mine did, mid-session, and three
sessions mis-identified themselves in one day — so **sign durable artifacts with sessionId, for
peers as well as yourself**.

**Live peers:** stale the moment it was written, and re-checked at 10:05 — `fund-28`, `fund-34`,
`fund-ff`, `fund-91` (sessionId `543e4897-29f9-46ba-9518-7eab44c3a389`), and `fund-f5`
(sessionId `5fee2153-2ec2-4c94-9f9c-dbf206afe637`). All started after this run ended; this
session does not know what any of them own. **Run `ListAgents` yourself rather than reading this
list** — it went from three to five in under an hour.

**This session's own name has churned twice**: `fund-4b` at dispatch, `fund-e5 [f30e4c]` now.
Grepping this document for a name finds a ghost. The sessionId in §9's first line is the only
stable handle, and the `from=` socket trick above is how you confirm it.

## 10. Closing message

When this work lands or is abandoned, message sessionId
`34c9cded-5ccb-4137-91d9-df75a5603996` (or ask Benjamin who holds the standup, if it has ended):

- **what shipped** — especially whether `feat/reflection-stage` got pushed
- **what was skipped**, and whether for cost or because it turned out wrong
- **what you decided differently from this document, and why**

The third is the part worth reading. This handoff is one session's account of one night; several
of its framings will not survive contact with the work, and the deviations are how anyone learns
which ones.
