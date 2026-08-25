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

**Finding the map issue.** It is the issue labelled `wayfinder:map` — the convention already written in
`docs/agents/issue-tracker.md`, reused rather than duplicated with a second one of our own. Zero matches
and several matches are not the same failure. Several means the board convention is in use and
something is broken — a duplicate map — worth raising to the human. Zero needs one more check first:
`gh label list`, for a `wayfinder:*` label anywhere in the repo. None at all means the repo has simply
never adopted the convention — say so once, plainly, never as a daily question (below). A
`wayfinder:*` label that exists with no open issue wearing it is the real ambiguity: the board cannot be
read deterministically, and degrades to human-named lanes rather than to a guess.

```bash
gh issue list --state open --label wayfinder:map --json number,title,body
```

List the map's children — GitHub sub-issues where the repo has them enabled:

```bash
gh api repos/{owner}/{repo}/issues/<n>/sub_issues --jq '.[] | {number, title, state}'
```

Where sub-issues are not enabled, that call returns nothing usable: fall back to the task list in the
map issue's body, whose children each carry a `Part of #<n>` line at the top of their own body — the
convention documented in `docs/agents/issue-tracker.md`.

Frontier query: for each open child, check its own blockers —

```bash
gh api repos/{owner}/{repo}/issues/<child> --jq '.issue_dependencies_summary.blocked_by'
```

`blocked_by` comes back `null` on an issue with no dependency data recorded at all, not on one with zero
blockers — treat `null` as dependency data unavailable and report it as such (`null == 0` is false, so
reading `null` as unblocked silently drops the child from the board, and reading it as blocked is just
as much a guess).

— and keep those open children that are:

- **unblocked** — `blocked_by == 0`
- **unclaimed** — no row in `map.md` whose `sessionId` appears in the unfiltered `claude agents --json`
  listing, the same listing Phase 6's release check uses. Liveness, not row status — and not
  `get-aligned`'s own `live/parked/held/prospective` vocabulary for that file. This skill reads only
  rows in a shape it can interpret; a row written by `get-aligned` or `split-the-plan` is left alone,
  never reinterpreted.
- **region-declared** — see below; a lane with no declared region is excluded here, not carried forward

in map order. These are the **candidate lanes**.

**Each candidate lane carries a region**, read from its issue body — Phase 4's collision check compares
that region against what each session says it owns, so a lane without one cannot be reconciled. An
issue that names no region is never a candidate lane: it is excluded from what Phase 2 polls on, not
merely deprioritized. It is not dropped from the run — Phase 3 still surfaces it as a flag.

Read a plan file only when a candidate lane names one, and only as the how.

**Degradation is mandatory, and it has two shapes.** A repo with no `wayfinder:*` label at all is not on
the convention: state that once, flatly, as a fact — never re-litigate it as a question, morning to
morning. That fact is not a reason to stop: it folds into the same open-issues report and lane-naming
offer as the other shape, below. A repo that uses the convention but currently shows zero-with-label or
several map-issue matches: say so in one line, skip the frontier query, and ask the human to name the
lanes. **That ask does not block the run, and the run does not invent lanes to fill the gap.** It reports
the board, states plainly that lanes are unnamed and why, and continues through Phase 1's roster and
Phase 3's digest exactly as written. The run ends having asked; an answer that never arrives is a
reported state, not a failure. With no map issue, there are no sub-issues and therefore no declared
regions at all: Phase 4's collision check has nothing to run against, and lane-to-session matching is the
human's call that morning. Never invent a priority order from an unordered issue list; never read a
plan checkbox as state.

**Neither shape of degradation is "no work," and neither forecloses naming lanes.** A repo with open
issues always has work to report, whether or not a map issue exists, and whether or not it has ever
adopted the convention. Both shapes list the open issues, name the ones nobody is assigned to, and rank
them by label, matched case-insensitively against this fixed sequence, most severe first: **critical,
high, medium, low**. Issues carrying none of those four come last, in issue-number order — a label that
is a status rather than a severity does not enter the ranking, and an issue marked resolved or refuted
is not surfaced as work at all. **If no label in the repo matches any of the four tiers, there is no
severity ordering available:** present the issues in issue-number order and say in one line that no
severity labels were found, rather than deriving an order from titles, age, or anything else. Let the
human name today's lanes from that list if they choose to — non-blocking, same rule as above: silence
continues the run with lanes reported unnamed, never invented. A morning is "quiet" only when there are
no open issues
and no live sessions (§6) — a repo that is off the board convention, or between maps, can still be loud.

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
session**. Self-identification is a set difference between the two listings this phase already needs,
**computed over the rows already filtered to this repo**: `claude agents --json` includes the calling
session, and the `ListAgents` tool excludes it, so the caller's row is whichever row the former reports
that the latter does not. **Exactly one row must survive that difference.** Zero or more than one means
self-identification failed — a listing was partial, or the repo filter under-selected — and the run
stops rather than poll a set that might include the caller or drop a real peer. Exactly one row: that
row is **excluded from the poll** (this seat does not poll itself). This is why both listings are
called, not one: the old
rule, "you are the one `ListAgents` omits," is unreliable taken alone — it cannot distinguish "absent
because it is me" from "absent because the listing was partial" — and diffing against `claude agents
--json`, a listing known to contain everyone, is what makes the identification exact. The roster itself
replaces the previous three-source join (`git worktree list` ∪ `~/.claude/sessions/*.json` ∪
`ListAgents`) with a two-source union (`claude agents --json` ∪ `git worktree list --porcelain`);
`ListAgents` stays in play only for this self-identification check.

Filter to sessions active since the window start — the **mtime** of the newest-by-filename file in
`~/.claude/align/<repo-basename>/standups/` (filenames are `YYYY-MM-DD.md`, so newest-by-filename is a
plain lexicographic max), else the last 24 hours if no file exists yet — by transcript mtime at
`~/.claude/projects/<escaped-cwd>/<sessionId>.jsonl`, where `<escaped-cwd>` is the cwd with every `/`
and `.` turned into `-`.

Retained from the current skill because they are still true: a session's `cwd` is where it sits, not
what it edits, so the roster is a floor; a session that entered a worktree writes to a worktree-scoped
projects directory and its old path goes quiet, so check there before calling anyone idle; re-check the
roster immediately before dispatch, and state coverage ("polled 3 of 11").

### Phase 2 — Poll

One `SendMessage` per rostered peer: the existing four fields (**did / doing / next / blocked**), plus
two more — six fields in total:

- **capacity** — could you take **ownership** of one of these lanes at your next boundary, or are you
  too deep in what you have? Owning a lane means running it — fanning out to subagents, or splitting
  it — not typing it yourself. The candidate lane list is inlined. The message states plainly that
  this is not an assignment and nothing has been decided.
- **owns** — regions, not filenames (`parse_config` and its callers; the retry block in `send()`).
  "nothing" is a complete answer. This is the field Phase 4's collision check compares each lane's
  region against.

**No candidate lanes today?** The poll still goes out, with the other five fields — the capacity field
and its inlined lane list are omitted from the send entirely, never sent empty.

Deadline: the poll's own `SendMessage` call carries `notify_when_idle` — one send per peer, not the poll
followed by a second subscribe call — rather than `Bash(run_in_background: true): sleep 300`. It is
one-shot, costs no tokens in the watched session, and expires after 12 hours.

**An idle notice is not a reply.** It fires when the watched session next finishes a turn — and fires
immediately if that session is already idle, which can happen before it has read the poll at all. The
notice bounds the wait; the six fields are what count. Collection ends on a full house, on a **2-hour
wall-clock cap** from when the poll went out, or when every subscribed session has both notified and had
a turn since the poll landed — whichever comes first. A session that notifies without answering, or
never notifies before the cap, is silent, and is treated as silent.

### Phase 3 — Digest

Unchanged in shape: one block per session, silent sessions named as silent, findings surfaced not
resolved, each flag marked reported or verified with the method named, and the absence rule — a session
can report an absence, never verify one.

One addition: each **capacity** answer prints beside that session's age (`startedAt`) and transcript
size. A self-report is the routing input; the numbers sit next to it so a "yes, I'm free" from a
six-hour session is not read blind. Compaction count is not observable, so age and size are proxies and
must be labelled as proxies.

This phase builds the digest and stops — it does not write it to disk, broadcast it, or report to the
human. Phase 6 does all three, exactly once each.

### Phase 4 — Reconcile

**One lane, one overseer** — the seat that owns a lane runs it, and never types it itself. A lane is
*owned*, never *worked*. The seat that takes it runs it — fans out to subagents, or fires
`/split-the-plan` if it is big enough — and does not implement it directly. This is uniform: a seat
that was already running and a chat opened this morning take a lane on exactly the same terms, so
"covered" means the same thing in every row of the digest.

Each candidate lane resolves to exactly one of:

- **covered** — a live session answered capacity yes **naming this lane**, and its stated region does
  not collide
- **needs a chat** — nobody free for this lane, or the only session that answered yes names a region
  that collides

A candidate lane becomes a **lane** once it resolves to either state above — every phase from here on
says "lane," never "candidate."

**Region**, throughout, means what `get-aligned` means by it: an area of code, not a filename
(`parse_config` and its callers; the retry block in `send()`). One file routinely has three owners, so
filename matching produces false conflicts and hides real ones.

A capacity yes whose region collides is a flag, not an assignment: → `/get-aligned`. A capacity yes that
names no lane, or names a lane a session already covers, is a flag of a different kind — no region
collision to reconcile, just an answer this phase cannot bind — so it goes to the human, not to
`/get-aligned`.

Never bind two lanes to one session to make the numbers work, and never bind a lane to a session that
did not name it.

### Phase 5 — Dispatch

Record `T = now` (epoch ms). **N is the count of lanes Phase 4 resolved to `needs a chat`; N = 0 means
skip this message entirely** — there is no chat to open. Otherwise tell the human:

> Open **N** chats in this repo. Nothing else — I'll take it from there.

List what each will own. No prompts to paste, no session ids to copy. **This ask does not block the
run — the same non-blocking rule Phase 0 gives its own ask.** Read the roster again, using Phase 1's
exact-path-or-containment-under-`path + "/"` rule, never a bare prefix test, and take every session with
`startedAt > T`. **If fewer appear than asked, or none do before the run ends**, bind what exists,
record the remaining lanes as unbound with the reason, and wait no further: Phase 6 writes the digest
and broadcasts regardless of whether confirmation ever arrived — a run always produces its digest.

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

Then brief each bound session as an **overseer**, not an implementer — a `covered` session gets this
same briefing, not a lighter one: Phase 4 already holds that a session already running and a chat opened
this morning take a lane on identical terms.

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
  assigned. A lane row states, at minimum: the lane (issue number or human-named lane), its region, the
  owning `sessionId`, its status, and which skill wrote it. Update rows keyed on `sessionId`; never
  rewrite the file wholesale; stamp which skill touched it last. This skill reads only rows in a shape
  it can interpret; a row written by `get-aligned` or `split-the-plan`, in that skill's own shape, is
  left alone rather than guessed at.

**Claim release.** Before writing new rows, check existing ones: a row whose `sessionId` is no longer
live, or whose issue is closed, is released and struck. The measured failure in this class of system is
not races over work — it is agents finishing and never letting go of the slot. Liveness is checked
against the full, unfiltered `claude agents --json` listing — the same listing Phase 0 checks
`unclaimed` against — never against the Phase 1 roster, which was already filtered to sessions active
since the last standup. That window governs who gets polled; it never governs who still exists. Release
is checked observationally (is the session live, is the issue closed), never by asking.

**Broadcast, once.** Send the digest to every rostered peer, including the silent ones — that is the
part that makes it a standup. Give each copy a personalized tail naming what concerns them, never what
they should do next: who is blocked on them, who shares their region, and which lane they now own, if
any. For a `covered` session, that last line **confirms** the briefing it already got in Phase 5 rather
than announcing something new. A tail is a relevance filter, not an instruction slot — a peer cannot
assign work to a peer, including by naming a lane "theirs by authorship." Unowned work goes to the human
as a flag and stays unowned until they say otherwise.

Report to the human last, leading with the unclaimed lanes and the flags — those are the parts needing
a decision. This is the single broadcast and the single report for the run; Phase 3 does neither.

## 5. What does not change

The skill stays daily, cheap, one round, no arbitration. Flags are surfaced, never resolved. The digest
goes to every rostered peer including the silent ones, with a personalized tail naming what concerns
them and never what they should do next. A peer cannot assign work to a peer; unowned work goes to the
human. Its sibling table row changes from *"Nothing happens afterwards"* to *"Ends in lanes owned."*

## 6. Failure modes

| Situation | Behavior |
|---|---|
| No sessions, board has work | Skip the poll entirely. Report the lanes, say "open N". This is now the normal empty-roster path, not a degenerate one |
| No sessions, no open issues at all | Say so, write nothing, stop. The only "quiet" run: no work and no one active |
| No map issue in this repo, or several | Say so in one line, fall back to human-named lanes, continue without blocking, reconcile normally. Still report open issues; never call this quiet |
| No `wayfinder:*` label in this repo at all | Say so once, flatly — not a daily question. Report open issues, ranked critical/high/medium/low as Phase 0 defines (unlabelled last), and let the human name today's lanes from them if they want to |
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

## 10. Not yet exercised

Two cold runs (2026-08-25) executed the skill end to end against `fund`. Both landed in the
degradation path, because `fund` has no `wayfinder:*` label and had no live peer sessions besides the
runner. The following therefore have NEVER run, and nothing about them should be read as verified:

- **Phase 2 (poll)** — no `SendMessage` round, no `notify_when_idle` subscription, no six-field
  collection, no silent-vs-answered handling.
- **Phase 4 (reconcile)** — no candidate lane ever resolved to covered or needs-a-chat; the
  capacity-collides-with-region rule has never fired.
- **Phase 5 (dispatch)** — never reached. The "open N chats", the `startedAt > T` binding, and the
  overseer briefing are entirely untested.
- **Phase 6's broadcast** — no peer existed to receive a digest or a "concerns you" tail.
- **Phase 0's harder branches** — several `wayfinder:map` matches, and a label that exists but no
  issue wears it.
- **Self-identification under ambiguity** — the set-difference method worked, but only one
  repo-rooted row existed, so it never had to tell "absent because it is me" from "absent because the
  listing was partial".

The first real dispatch is the first true test of Phases 4, 5, and 6, and should be run with a human
watching. Artifact B (the `fund` board migration) is what would let Phase 0 take its main path at all.
