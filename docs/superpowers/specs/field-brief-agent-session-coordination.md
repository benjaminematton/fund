# Field brief: coordinating parallel AI coding agent sessions on one repo

**Date:** 2026-08-24 · **Prepared for:** grounding a redesign of a `morning-standup` skill that polls live sessions, reads a board, computes lanes, and dispatches work · **Depth:** normal-deep, 4 searches / 8 fetches / 4 local verification runs

## State of the field *(prose)*

Through 2026 the parallel-coding-agent problem has settled on one answer for isolation and no answer
for coordination. Isolation is solved and uncontroversial: one git worktree per agent, so agents never
need to coordinate file access at all. Anthropic ships this first-party (`claude --worktree`, plus
enforced blocking of any edit or command that reaches back into the main checkout), and every
third-party orchestrator surveyed — Baton, Code Conductor, Gas Town — independently converged on
worktree-per-agent rather than on lock protocols. Coordination is where the field is still moving.
Anthropic now offers three distinct surfaces that overlap but do not compose: **agent teams**
(experimental, off by default — a lead spawns teammates who share a task list with file-locked
claiming), **agent view** (`claude agents` — a dashboard built for dispatching and monitoring
background-dispatched sessions, each auto-isolated into its own worktree; its `--json` listing, though,
lists every local session including human-opened interactive ones, distinguished by a `kind` field),
and **cross-session messaging** (`ListAgents`/`SendMessage` — plain-text transport between
independently-opened sessions, explicitly not a coordination feature). The gap none of them fills is a
fleet of long-lived sessions a human opened by hand, spread across worktrees, working a durable board
that outlives any one session — agent view's JSON listing can see such a fleet, but none of the three
surfaces polls it, reads a board, or assigns lanes. Outside Anthropic the dominant
pattern for that board is GitHub Issues: label the issue, an agent claims it, a worktree per issue, a
PR closes it.

## Core concepts and vocabulary

- **Worktree isolation** — one `git worktree` per agent so parallel edits cannot collide; the field's
  default answer to duplicate work, in preference to locks
- **Agent team** — Anthropic's lead-plus-teammates construct; one team per session, lead fixed, teammates
  spawned by the lead rather than joined by existing chats
- **Shared task list** — the team's work queue at `~/.claude/tasks/{team}`, with pending/in-progress/
  completed states, dependencies, and automatic unblocking when a blocker completes
- **Self-claim** — a teammate picking up the next unassigned, unblocked task on its own, guarded by
  file locking against races
- **Agent view** — `claude agents`, an interactive dashboard built for dispatching and monitoring
  background sessions; a human-opened session appears in that dashboard only after `/bg` backgrounds
  it, but the underlying `claude agents --json` listing includes human-opened interactive sessions too,
  distinguished by a `kind` field
- **Cross-session messaging** — `ListAgents` + `SendMessage` between independent sessions; plain text
  only, no history, no files
- **`notify_when_idle`** — a one-shot subscription asking a watched session to report once when it next
  goes idle; no polling, expires after 12 hours
- **Claim release** — freeing a work item when its agent finishes; Baton detects this by checking whether
  a PR exists for the issue's branch, having found that agents otherwise hold slots after finishing
- **One file, one owner** — the stated core rule of multi-agent file partitioning
- **Verification bottleneck** — the observation that generation is no longer the constraint; checking the
  output is

## Live debates and open questions *(prose)*

**Where the task queue lives.** Anthropic's answer is session-local: agent teams keep the list under
`~/.claude/tasks/{team}`, and the docs are explicit that a team is scoped to one session and cannot be
shared across sessions. The third-party camp — Ryan Mac's Code Conductor, Muhammad Raza's Baton —
puts the queue in GitHub Issues, so it survives every process and is visible to humans in the same
place. The tradeoff is latency and structure: the local list gets file-locked claims and automatic
dependency unblocking that GitHub does not give you for free, while the issue tracker gets durability
and human visibility that a session-scoped directory cannot. Nobody has published a convincing hybrid.

**Whether claim protocols are needed at all.** Addy Osmani's framing is that isolation should be so
hard that agents "don't need to coordinate at all," and Baton's author reports encountering no serious
race conditions, attributing that to worktrees. Anthropic disagrees in practice: agent teams implements
explicit file locking on task claiming, which only matters if two agents can reach for one task. Both
can be true — worktrees prevent *edit* collisions while saying nothing about two agents choosing the
same work — and the field has not cleanly separated the two failure modes.

**Self-report versus observation for agent status.** Anthropic's agent teams leans on push signals: idle
notifications fire automatically, and `notify_when_idle` exists precisely so nobody polls. Yet the same
docs list "task status can lag — teammates sometimes fail to mark tasks as completed, which blocks
dependent tasks" as a standing limitation, which is exactly the failure mode a self-report system
produces. The observational alternative (read the commits, read the tree) is cheaper and cannot be
optimistic, but cannot distinguish an agent building the right thing from one confidently building the
wrong thing.

## Key claims log

The full log with per-claim statuses lives alongside this brief in
`claims-log-agent-session-coordination.md`. The load-bearing ones:

| Claim | Status | Source(s) |
|---|---|---|
| `claude agents --json [--cwd <path>]` returns a repo-scoped roster with `name`, `sessionId`, `cwd`, `kind`, `startedAt`, **including the calling session** | single-source (direct local run) | Direct local run; [agent view docs](https://code.claude.com/docs/en/agent-view) |
| `ListAgents` excludes the calling session | single-source | [cross-session messaging docs](https://code.claude.com/docs/en/cross-session-messaging); consistent with measured notes in existing skill files |
| Agent teams is experimental, off by default, one team per session, lead fixed, teammates spawned by the lead | single-source (doc + local test) | [agent teams docs](https://code.claude.com/docs/en/agent-teams); local test |
| Agent teams does not worktree-isolate teammates | single-source | [run agents in parallel](https://code.claude.com/docs/en/agents) |
| Worktree-per-agent, not locking, is the ecosystem's duplicate-work answer | **verified** | [Baton](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/); [code-conductor](https://github.com/ryanmac/code-conductor); [Addy Osmani](https://addyosmani.com/blog/code-agent-orchestra/) |
| GitHub Issues as an agent work queue is an established pattern | **verified** | [code-conductor](https://github.com/ryanmac/code-conductor); [Baton](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) |
| `acceptEdits` deadlocks an unattended agent; autonomous runs need `bypassPermissions` plus disposable worktrees | single-source | [Baton](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) |
| No first-party surface coordinates human-opened, long-lived, worktree-spread sessions against a durable external board | **inference** | Derived from the three Anthropic docs' stated scopes |

## Practitioner heuristics *(prose)*

People who run these fleets well isolate first and coordinate second: the worktree is the safety
mechanism, and every protocol on top of it is a convenience. They partition by file ownership before
dispatching anything, because "one file, one owner" is cheaper to enforce up front than to repair in a
merge. They keep teams small — Anthropic's own guidance is 3–5 agents, and that three focused beat five
scattered — and they size a task as a self-contained deliverable, since too-small tasks lose to
coordination overhead and too-large ones run for hours before anyone notices they went wrong. They
prefer observed progress (commits, trees, PRs) to self-reported status, and they build an explicit
release step, because the measured failure is not agents fighting over work but agents finishing and
never letting go of the slot. For unattended runs they use `bypassPermissions` inside disposable
worktrees rather than `acceptEdits`, which looks safer and in fact hangs forever on the first prompt
nobody is there to answer. And they treat verification, not generation, as the real constraint: the
quality gate after the fan-in is where the time goes.

## Source shelf

- [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams) — canonical; the shared task list, claiming, mailboxes, and an unusually frank limitations section **(read)**
- [Message your other Claude Code sessions](https://code.claude.com/docs/en/cross-session-messaging) — canonical for the transport this class of skill runs on, including trust rules and loop throttling **(read)**
- [Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees) — canonical for isolation and its enforcement **(read)**
- [Run agents in parallel](https://code.claude.com/docs/en/agents) — the comparison table that separates the four surfaces **(read)**
- [Manage agents with agent view](https://code.claude.com/docs/en/agent-view) — the dashboard, its worktree auto-isolation, and how human-opened sessions join **(read)**
- [Addy Osmani — The Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/) — best independent synthesis; source of "one file, one owner" and the verification-bottleneck framing **(read)**
- [Muhammad Raza — Building Baton](https://muhammadraza.me/2026/building-baton-autonomous-agent-orchestrator/) — the most useful practitioner postmortem: claim release, the `acceptEdits` deadlock **(read)**
- [ryanmac/code-conductor](https://github.com/ryanmac/code-conductor) — second independent instance of issues-as-queue; claim mechanism undocumented in the README **(read)**
- [Augment Code — 9 open-source agent orchestrators](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — landscape survey naming GNAP, Gas Town, CLAWE **(search-level)**

## Coverage edges *(prose)*

This brief covers coordination and isolation, not the quality gate that follows fan-in — review,
merge-queue design, and how to verify N parallel branches are individually correct are named as the
bottleneck by one source and otherwise unexplored here. The third-party orchestrators are covered
thinly: Baton and Code Conductor were read, but GNAP, Gas Town, and CLAWE are search-level only and
their claim protocols are unknown. Everything about agent teams' internals is single-source from
Anthropic's own docs, since the feature was not enabled locally and was therefore not tested. One
load-bearing question remains open and is flagged in the claims log: whether `claude agents --cwd
<main checkout>` also returns sessions whose working directory is a *worktree* of that repo — no live
worktree session existed during the run to test it.
