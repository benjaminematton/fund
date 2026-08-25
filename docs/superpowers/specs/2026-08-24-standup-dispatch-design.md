# Design: morning-standup ends in lanes owned

**Date:** 2026-08-24 · **Status:** approved design, not yet planned · **Artifacts:** two (see §7)

Grounding for the "why not use a first-party feature" reasoning is
`field-brief-agent-session-coordination.md`; claim statuses are in
`claims-log-agent-session-coordination.md`, both alongside this file.

**Note on location:** the skill this designs lives in `~/.claude/skills/`, but that repo's
`.gitignore` states `# No spec/design docs in this repo — skills only`. This spec therefore lives in
`fund`, which also carries artifact B (§7) and already holds `docs/superpowers/specs/`.

## 1. Problem

`morning-standup` today polls the live sessions on a repo, digests what they say, and stops. Its
sibling table states the property outright: *"Daily, scheduled. Everyone hears everyone. Nothing
happens afterwards."*

Two failures follow from that stopping point:

1. **With no live sessions it produces nothing.** The skill's own sideways table says: no sessions
   active since the window → say so, write no file, stop. On a morning when the fleet is empty but the
   work is not, the ritual is a no-op.
2. **It reports motion, never dispatch.** It can name "the next plan task nobody is on" — it calls that
   the single most useful line in the digest — and then has no way to put anyone on it.

The redesign closes both: the same skill polls whoever exists, reads the board, computes the lanes the
work needs, matches lanes to sessions, and tells the human how many chats to open for the remainder.

## 2. Why this is not already solved by a first-party feature

Three Claude Code surfaces overlap this and none covers it:

- **Agent teams** — a lead spawns teammates who share a file-locked task list. Cannot adopt chats the
  human already opened; one team per session; the lead is fixed for its lifetime; teammates are not
  worktree-isolated. Experimental and off by default.
- **Agent view** (`claude agents`) — built for dispatching and monitoring *background-dispatched*
  sessions; its interactive dashboard surfaces a human-opened session only after `/bg` backgrounds it.
  Its `--json` listing is not so limited: it lists every local session, human-opened interactive ones
  included, distinguished by a `kind` field (`"interactive"` vs `"background"`). It has no poll, no
  digest, no board, and no lane assignment.
- **Cross-session messaging** — the `ListAgents`/`SendMessage` transport this skill already runs on.
  Transport, explicitly not coordination.

The gap is a fleet of long-lived, human-opened, worktree-spread sessions working a board that outlives
any one session. Agent view's JSON listing can see such a fleet; nothing in it polls, reads a board, or
assigns lanes. That is what this skill owns.

Two mechanisms are worth taking from those surfaces, and §4 does: `notify_when_idle`, and an explicit
claim-release step.

## 3. The board

**Issues are the board. Plan files are the inside of a lane. Todos stay private.**

| Question | Home |
|---|---|
| What work exists, in what order, is it ready? | GitHub issues: one map issue per phase, work issues as its sub-issues, native `blocked_by` dependency edges |
| Which session holds which lane right now? | `~/.claude/align/<repo-basename>/map.md`, keyed on `sessionId` |

An issue never records which chat holds it. `map.md` never records what work exists.

**Why not plan-file checkboxes.** Measured in `fund` on 2026-08-24: 359 unchecked boxes and zero
checked, across all seven plan files, including plans whose work demonstrably shipped. Checkbox state
is not a progress signal — ticking is friction for the ticker and invisible to everyone else. Plans are
referenced *from* an issue as the how; they are never read as state.

**Ordering and dependencies still come from a plan-shaped spine** — they have moved from plan-file
sequence into the map issue's child order and its `blocked_by` edges, which are queryable and current.

**Claims are not GitHub assignees.** Every session authenticates as the same GitHub user, so
`--add-assignee @me` cannot distinguish session A from session B. The assignee stays a coarse
"someone is on it" flag; the binding claim is the `map.md` row.

## 4. Phases

### Phase 0 — Board

Frontier query: open sub-issues of the phase map, minus any with `issue_dependencies_summary.blocked_by
> 0`, minus any already claimed in `map.md`, in map order. These are the **candidate lanes**.

Read a plan file only when a candidate lane names one, and only as the how.

**Degradation is mandatory.** This skill runs in repos with no map issue. When no map issue exists:
say so in one line, skip the frontier query, and ask the human to name the lanes. Never invent a
priority order from an unordered issue list; never read a plan checkbox as state.

**No confirmation gate.** The candidate lanes go straight into the poll. Priority was decided when the
map issue's children were ordered; re-asking every morning re-asks a settled question. The human's
involvement in a normal run is: order the board (once, not daily), open the chats Phase 5 asks for, and
read the digest.

### Phase 1 — Roster

```bash
claude agents --json
git worktree list --porcelain
```

`claude agents --json --cwd <repo-root>` matches by exact path, not by repo identity (§9): it drops
every session sitting in a worktree, which is most of this repo's live sessions. So Phase 1 calls
`claude agents --json` unscoped and keeps only rows whose `cwd` equals `<repo-root>` or is under one
of the paths `git worktree list --porcelain` reports for this repo (which includes `<repo-root>`
itself plus every registered worktree, in or out of tree). This is exact-path equality or containment
under `path + "/"`, never a bare string-prefix test: siblings like `fund-probes/`, `fund-worktrees/`,
and `fundablePlayground/` share a string prefix with the repo root without being under it, and
`cwd.startswith(repo_root)` would wrongly sweep their sessions into the roster.

Each surviving row carries `name`, `sessionId`, `cwd`, `kind`, `startedAt` — **including the calling
session**, which is identified by matching its own `sessionId` and then **excluded from the poll**
(this seat does not poll itself). This replaces the previous three-source join
(`git worktree list` ∪ `~/.claude/sessions/*.json` ∪ `ListAgents`) with a two-source union
(`claude agents --json` ∪ `git worktree list --porcelain`) and retires the "you are the one
`ListAgents` omits" heuristic — self-identification is now positive rather than by elimination.

Filter to sessions active since the window start (newest file in
`~/.claude/align/<repo-basename>/standups/`, else the last 24 hours), by transcript mtime at
`~/.claude/projects/<escaped-cwd>/<sessionId>.jsonl`, where `<escaped-cwd>` is the cwd with every `/`
and `.` turned into `-`.

Retained from the current skill because they are still true: a session's `cwd` is where it sits, not
what it edits, so the roster is a floor; a session that entered a worktree writes to a worktree-scoped
projects directory and its old path goes quiet, so check there before calling anyone idle; re-check the
roster immediately before dispatch, and state coverage ("polled 3 of 11").

### Phase 2 — Poll

One `SendMessage` per rostered peer: the existing four fields (**did / doing / next / blocked**), plus

- **capacity** — could you take **ownership** of one of these lanes at your next boundary, or are you
  too deep in what you have? Owning a lane means running it — fanning out to subagents, or splitting
  it — not typing it yourself. The candidate lane list is inlined. The message states plainly that
  this is not an assignment and nothing has been decided.

Deadline: subscribe with `SendMessage`'s `notify_when_idle` rather than `Bash(run_in_background: true):
sleep 300`. It is one-shot, costs no tokens in the watched session, and expires after 12 hours.

**An idle notice is not a reply.** It fires when the watched session next finishes a turn — and fires
immediately if that session is already idle, which can happen before it has read the poll at all. The
notice bounds the wait; the four fields are what count. Collection ends on a full house, or when every
subscribed session has both notified and had a turn since the poll landed. A session that notifies
without answering is silent, and is treated as silent.

### Phase 3 — Digest, to everyone

Unchanged in shape: one block per session, silent sessions named as silent, findings surfaced not
resolved, each flag marked reported or verified with the method named, and the absence rule — a session
can report an absence, never verify one.

One addition: each **capacity** answer prints beside that session's age (`startedAt`) and transcript
size. A self-report is the routing input; the numbers sit next to it so a "yes, I'm free" from a
six-hour session is not read blind. Compaction count is not observable, so age and size are proxies and
must be labelled as proxies.

### Phase 4 — Reconcile

**One lane, one overseer.** A lane is *owned*, never *worked*. The seat that takes it runs it — fans
out to subagents, or fires `/split-the-plan` if it is big enough — and does not implement it directly.
This is uniform: a seat that was already running and a chat opened this morning take a lane on exactly
the same terms, so "covered" means the same thing in every row of the digest.

Each candidate lane resolves to exactly one of:

- **covered** — a live session answered capacity yes and its stated region does not collide
- **needs a chat** — nobody free, or the only candidate's region collides

**Region**, throughout, means what `get-aligned` means by it: an area of code, not a filename
(`parse_config` and its callers; the retry block in `send()`). One file routinely has three owners, so
filename matching produces false conflicts and hides real ones.

A capacity yes whose region collides is a flag, not an assignment: → `/get-aligned`.

Never bind two lanes to one session to make the numbers work.

### Phase 5 — Dispatch

Record `T = now` (epoch ms). Tell the human:

> Open **N** chats in this repo. Nothing else — I'll take it from there.

List what each will own. No prompts to paste, no session ids to copy. When they confirm, take every
session with `startedAt > T` whose cwd is at or under the repo. If fewer appear than asked, bind what
exists, name the unbound lanes, and wait.

**Standup provisions nothing.** A new chat is an overseer seat, and an overseer does not edit files —
it reconciles, fans out, and reviews. It sits in the repo root. Isolation belongs one level down, to
whatever that overseer dispatches, and it is that overseer's decision to make with the lane in front of
it. Standup creating worktrees for seats that may never write a line is provisioning cost paid on
speculation, and a worktree nobody wrote in still has to be reaped.

The consequence worth stating: **this skill never creates a branch, a worktree, or a commit.** It reads,
it messages, and it writes two files under `~/.claude/align/`. Everything that touches the repo is done
by a seat that owns a lane.

**Silence is not a release.** A rostered session that never answered has not given up its region: its
lanes are treated as needing a chat only where no live session claims that region. Replacing a silent
session's head is a decision for the human, never an inference from a missing reply.

Then brief each bound session as an **overseer**, not an implementer:

- the lane: issue number, title, and the region it covers
- **that it owns the lane, and owns how the lane gets done** — `subagent-driven-development` for a lane
  that fits one seat's fan-out, `/split-the-plan` for one that needs several. Where it needs a plan
  first, `writing-plans`. The choice is the overseer's, not standup's
- that isolation is its call: worktrees for what it dispatches, provisioned outside the repo, one at a
  time
- its neighbours — who owns the adjacent lanes, under what name — and that
  `coordinating-with-peer-sessions` governs how they talk
- that it reports back at the next standup, not continuously

### Phase 6 — Record

- Digest → `~/.claude/align/<repo-basename>/standups/YYYY-MM-DD.md`
- Lane rows → `~/.claude/align/<repo-basename>/map.md`, the same file `get-aligned` and
  `split-the-plan` use. One ownership map per repo, or those two route around work this skill just
  assigned. Update rows keyed on `sessionId`; never rewrite the file wholesale; stamp which skill
  touched it last.

**Claim release.** Before writing new rows, check existing ones: a row whose `sessionId` is no longer
live, or whose issue is closed, is released and struck. The measured failure in this class of system is
not races over work — it is agents finishing and never letting go of the slot. Release is checked
observationally (is the session live, is the issue closed), never by asking.

## 5. What does not change

The skill stays daily, cheap, one round, no arbitration. Flags are surfaced, never resolved. The digest
goes to every rostered peer including the silent ones, with a personalized tail naming what concerns
them and never what they should do next. A peer cannot assign work to a peer; unowned work goes to the
human. Its sibling table row changes from *"Nothing happens afterwards"* to *"Ends in lanes owned."*

## 6. Failure modes

| Situation | Behavior |
|---|---|
| No sessions, board has work | Skip the poll entirely. Report the lanes, say "open N". This is now the normal empty-roster path, not a degenerate one |
| No sessions, no board | Say so, write nothing, stop |
| No map issue in this repo | Say so in one line, fall back to human-named lanes, reconcile normally |
| Fewer chats opened than asked | Bind what appeared, name the unbound lanes, wait |
| Capacity yes but region collides | Flag → `/get-aligned`. No assignment |
| Fleet grew mid-run | Re-check the roster immediately before dispatch; state coverage |
| Peer never answers | Named silent; still receives the digest; its lanes are treated as needing a chat |
| A stale `map.md` row's session is gone | Released and struck before new rows are written |
| `claude agents --json` fails | Stop with the error. Do not fall back to name matching |

## 7. Artifacts

**A. The skill** — `~/.claude/skills/morning-standup/SKILL.md`, rewritten to §4. Ships and works in
every repo; in one with no map issue it degrades per §4 Phase 0.

**B. The `fund` board migration** — one-time, in the `fund` repo:

- create the phase map issue
- make the 8 open issues its sub-issues, in the order they should be worked
- add `blocked_by` edges where a real dependency exists
- mark shipped plans dead with a status header rather than ticking 359 boxes
- one line in `docs/agents/issue-tracker.md`: claims key on `sessionId` in `map.md`, not on the GitHub
  assignee

B is a prerequisite for A being *useful in fund*, not for A shipping. They can land separately.

## 8. Out of scope

- **Factoring out the duplicated roster logic.** `get-aligned` and `split-the-plan` carry their own
  copies of the join that Phase 1 replaces. Collapsing all three onto `claude agents --json` is a real
  improvement and a separate change; doing it here would put three skills in one diff.
- **Label taxonomy.** Eight issues do not need one. Map order plus dependency edges carry the
  information the frontier query needs.
- **Enabling agent teams.** Evaluated and rejected in §2 for this use case; nothing here depends on it.
- **Any change to `huddle`, `get-aligned`, or `split-the-plan`** beyond the one sibling-table row.

## 9. Open question, settled 2026-08-24

`claude agents --json --cwd <main checkout>` DOES NOT return sessions whose cwd is a worktree of
that repo. Measured with a probe session in `fund-worktrees/standup-probe`: the scoped call
(`claude agents --json --cwd /Users/benjaminmatton/Developer/fund`) returned exactly 4 rows, every
one with `cwd` equal to `/Users/benjaminmatton/Developer/fund` itself — no row for the probe. The
unscoped call (`claude agents --json`, no `--cwd`) did return the probe, with
`cwd = /Users/benjaminmatton/Developer/fund-worktrees/standup-probe`. So `--cwd` matches by exact
path, not by repo identity, and worktree sessions are invisible to the scoped query even though the
unscoped roster sees them fine.

Consequence for Phase 1: Phase 1 unions the unscoped listing (`claude agents --json`) with
`git worktree list --porcelain` paths, filtering to rows whose `cwd` is the repo root or under one of
the repo's registered worktree paths, rather than relying on `--cwd` scoping alone.
