# Design: `owning-a-lane` — the shared overseer role

**Date:** 2026-08-25 · **Status:** approved design · **Artifact:** one new skill + one edit to `morning-standup`

Follows `2026-08-24-standup-dispatch-design.md`, which introduced the role but described it inline in a
briefing message.

## 1. Problem

`morning-standup` Phase 5 briefs each seat that takes a lane by *describing* what an overseer is —
inside a `SendMessage`. Three things are wrong with that:

- **A message cannot carry doctrine.** The briefing has to stay short enough to read at a task
  boundary, so it can only gesture at how to run a lane.
- **The definition has nowhere to live.** `split-the-plan` states the role for itself ("You are the
  **overseer**. You do not implement."); `morning-standup` restates it for others; `huddle` and
  `get-aligned` use "the overseer seat" as a greeting label with no definition behind it.
- **It cannot improve.** Every lesson learned about running a lane has to be edited into whichever
  briefing text happens to mention it.

The fix is the pattern this family already uses for peer etiquette: doctrine lives in a skill, and
messages point at it. `coordinating-with-peer-sessions` is that precedent, and `morning-standup`
already names it in the same briefing.

## 2. Name

**`owning-a-lane`**, not `overseer`.

`become-expert` already uses "overseer mode" to mean a grounded reviewer of the user's own work — a
different job. A seat that receives "you own lane #39" should not have to disambiguate two senses of
the same word before it can act. The gerund also matches the family's naming (`coordinating-with-peer-sessions`,
`using-git-worktrees`, `finishing-a-development-branch`).

"Overseer" stays as the word for the *seat*. The skill is what that seat invokes.

## 3. What an overseer is

A seat that owns one lane end to end and is accountable for it reaching done — **without writing any
of it**.

**It never edits.** Not a one-liner, not a typo. The fan-out is not ceremony; it is where the review
gate lives. An overseer that edits becomes the author of the thing it is supposed to review, and the
structural guarantee that a writer and a reviewer are different contexts is gone. This is the rule the
skill exists to carry, and it is the one to state first and hardest.

**The fan-out scales to the lane.** One subagent for a one-line fix; `writing-plans` then
`subagent-driven-development` for a real feature; `/split-the-plan` when the lane needs several seats.
Choosing among those is the overseer's judgment and nobody else's — the briefing hands over a lane, not
a method.

**Isolation is its call.** Worktrees for what it dispatches, outside the repo, provisioned one at a
time. The seat that briefed it provisions nothing.

## 4. The chain

An overseer has *its* overseer: the seat that briefed it. This makes escalation directional and gives
"I am not sure" a destination.

**Escalate rather than guess** when the lane's scope is ambiguous, when done-ness is unclear, when the
work turns out to need a region the lane does not own, or when confidence in a result is low. An
overseer that guesses at scope produces a confidently wrong lane, and the fan-out multiplies it.

Two rules bound this so the chain does not become a debating society:

- **Escalate up, never sideways.** A question for another lane's owner is a peer message governed by
  `coordinating-with-peer-sessions`, not an escalation.
- **An escalation names the decision, not the discomfort.** State the fork and the option you would
  take absent an answer, so the answer can be one line.

## 5. What a lane is, and where it comes from

A lane is **what one overseer can own end to end**. Granularity follows from that:

| Source | Becomes | Why |
|---|---|---|
| An issue | one lane | Already the right size — one chat's work |
| A plan being executed | **one** lane; its tasks are that overseer's internal fan-out | Tasks share files and sequence; splitting them across seats creates exactly the conflicts worktrees exist to prevent |
| A plan too large for one seat | one lane per package, via `/split-the-plan` | That skill partitions by disjoint file regions and writes the rows already |

A plan's tasks are **not** fleet-level lanes. The 7-task `standup-dispatch` plan was executed as one
lane with an internal fan-out; had its tasks been lanes, seven chats would have contended over one
file.

This also keeps the plan-checkbox problem dead: a lane's state is a `map.md` row plus commits, never a
tick someone has to remember.

**Lane id.** `map.md` rows key on it and it must work for both sources: `#<issue>` for an issue-derived
lane, and the plan path (or the package slug `/split-the-plan` assigns) for a plan-derived one.

## 6. Where the work is described

**The issue — body and thread.** The body says what the lane is and what done means; the thread is
where that gets corrected. It is durable, readable without asking anyone, survives the seat dying, and
`morning-standup` already reads issues.

**Reading the thread is a rule, not a courtesy.** Measured during testing: on a lane whose body pointed
at tightening an assertion, a peer had already established in the comments that the assertion was fine
and the real fix was a different test. Every seat that worked from the body alone — two without the
skill, one with it — implemented the wrong change. It was the only variable that separated a correct
outcome from a confident wrong one, so the skill states it outright and lists it as a red flag.

A plan file is written only when the lane is big enough to need one, and the issue links to it. The
plan is the *how*; the issue stays the *what*.

For a plan-derived lane with no issue, the plan file is the description and the `map.md` row is the
claim.

## 7. Where updates go

Two channels, because progress and trouble have different urgency:

| | Channel | Why |
|---|---|---|
| **Progress** | a comment on the lane's issue | Durable, pullable, survives the seat, and costs no other seat a turn. `morning-standup` reads it without polling |
| **Stuck, unsure, or scope changed** | `SendMessage` to the seat that briefed it | Needs an answer now. A comment nobody is watching is not an escalation |

**A plan-derived lane has no issue to comment on.** Its progress lives in its `map.md` row and in what
it reports when the standup polls it. Do not file an issue solely to have somewhere to post progress —
that puts a second, competing record of the same work on the board.

**When the briefing seat is gone**, escalate to the human instead. A dead escalation address is not a
reason to guess, and it is not a reason to stall: the lane is still owned, and the question still needs
an answer from someone who can give one.

An overseer answers the standup poll when polled; it does not push routine status upward between
standups. The daily pulse is a pull, not a stream.

## 8. The inline-vs-pointer boundary

This family's own rule, stated in three of its skills: a message inlines the rules it depends on,
*because a receiving session may never invoke the skill*. A bare pointer is therefore not enough.

**The briefing carries** — the lane and its id, the region, the escalation address, and the one
non-negotiable line: *you own this lane, you do not implement it yourself*.

**The skill carries** — how to size the fan-out, how to choose among `writing-plans` /
`subagent-driven-development` / `/split-the-plan`, the isolation rules, what to escalate and how to
phrase it, where progress goes, and what done means.

Test for which side a rule belongs on: if a seat that never loads the skill would do something
irreversible without the rule, it goes in the message.

## 9. Invocability

`owning-a-lane` is **model-invocable** — no `disable-model-invocation`. This is the opposite of its four
siblings (`morning-standup`, `get-aligned`, `huddle`, `split-the-plan`), all of which are human-only,
and it is deliberate: a seat must load this on being handed a lane, with the human typing nothing.

Its `description` is what makes that fire, so it must match the situation a briefed seat is in — being
handed ownership of a piece of work — not the abstract topic of delegation.

## 10. What changes in `morning-standup`

Phase 5's briefing stops describing the role and instead carries the four inline items from §8, plus a
pointer to `owning-a-lane`. Nothing else in the skill changes.

## 11. Out of scope

- **Rewiring `split-the-plan`, `huddle`, and `get-aligned`.** `split-the-plan` describes the role for
  *itself*, which is a different use than briefing someone else into it; the other two only use the
  word as a greeting label. Nothing is duplicated badly enough today to justify touching three more
  skills in this change.
- **A tool-level edit restriction.** "Never edits" is doctrine, not enforcement; a skill cannot remove
  `Edit` from a seat that already has it.
- **Changing the board.** Lane sources and ids are described here as they already work; no new
  mechanism is introduced.

## 12. Not yet exercised

Nothing in the lane pipeline has run for real. `morning-standup`'s Phases 2, 4, 5 and its broadcast
have never executed (see `2026-08-24-standup-dispatch-design.md` §10), so no seat has ever been briefed
into this role, no escalation has ever been sent, and the two-channel update split has never been used.
The first real dispatch tests this skill and that one at the same time, and should be watched.
