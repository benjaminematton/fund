# Day bookends — morning standup and EOD digest, with a repo health descriptor

Status: design, awaiting review (2026-08-21)

A pair of developer-facing coordination skills that bracket the working day, plus a
per-repo descriptor that tells them what "healthy" means here.

This is **developer tooling**. It adds no stage, seat, table, MCP tool, or charter, and
it cannot move any arc of the research → market run → analysis flywheel. That flywheel is
specced in `specs/design.md` §3/§7 and phased in `specs/acceptance.md`; nothing here
reorders it.

---

## 0. Why

**Thirteen sessions could not agree on production state.** On 2026-08-21 four quoted four
different droplet SHAs. None had read the broker. The fund's own alert — *"NVDA 80 was
ticketed with a stop at 215.0 but the broker covers only 40 of 80 shares"* — was raised at
13:38:02Z, projected to Slack the same second, and sat unactioned for eight hours.

**Every wrong fact that day came from an agent re-deriving a check by hand.** A two-dot
`git diff` read as a branch's own changes. A transcript mtime read as activity, when the
transcript had moved to a worktree-scoped directory. A timer *specification* read as an
observation of its *state*. `resolutions` being empty read as a dead job, when the first
decision does not reach its horizon until 2026-08-24. Eight instances in one day of one
shape: **a signal that changes meaning without changing appearance.**

The design consequence is the whole spec: **the reads are a tested command, not prose.**
Prose instructions re-derived each morning reproduce exactly the failures above.

## 1. Non-goals

- Not a replacement for the fund's own Ops standup/EOD digest (`specs/design.md` §3,
  08:30 and 16:15, posted to Slack by the Ops seat in Phase 3). **Deliberately not named
  standup/EOD-digest at the fund level** — two things by that name in one repo is a bug.
- Not a cron job. These are attended; they require Benjamin present.
- Not a notifier. The fund already alerts correctly and the alert was seen. This is
  context for developers, not an escalation channel.
- No mutation. Read-only against droplet, broker and DB. Every action stays with a human.

## 2. Components

| Artifact | Location | Generic? |
|---|---|---|
| `/eod-digest` skill | `~/.claude/skills/eod-digest/` | yes |
| `morning-standup` — gains intents write + health step | `~/.claude/skills/morning-standup/` | yes |
| `.claude/health.md` | each repo | per-repo |
| `scripts/dev_status.py` + `make dev-status` | fund repo | fund-only |
| `~/.claude/align/<repo>/standups/YYYY-MM-DD.md` | outside every repo | generic path |

**Naming.** The descriptor is `.claude/health.md`, named for its subject, not its reader.
"Overseer" is a role word already in circulation in handoff and coordination docs; a file
named for a role invites every session doing role-adjacent reading to pull it in, and this
file should be read by exactly two skills at exactly two moments.

### 2.1 `/eod-digest` — four steps

1. **Did each agent do what it was supposed to?** Poll live sessions (roster mechanics
   from `get-aligned` Phase 1: join `git worktree list`, `~/.claude/sessions/*.json` on
   `cwd`, and `ListAgents`; re-check `ListAgents` immediately before broadcast). Diff each
   reply against that session's morning intent.
2. **Close out what is done.** Before any close-out, scan for **local branches that are
   unpushed and unmerged** — the refined form (§2.4), not bare local-only. That is the
   loss-on-close check, and on 2026-08-21 it was the only thing that would have caught
   `bdac89b` and `docs/adr-stop-amend`, both living in worktrees. Sessions reporting zero
   bearing then print a large `CLOSE ME` banner so the human can close tabs by sight.
   **No session closes itself.**
3. **Consolidate.** Name overlaps, name the survivor, record releases as explicitly as
   claims.
4. **Tomorrow's todos.** Written to the dated standup file, which becomes the morning's
   baseline.

### 2.2 The intents file

Step 1 needs a baseline. Without one, "did you do what you were supposed to" degrades into
"what did you do" — which is what happened on 2026-08-21 and is why nothing could be
checked.

`morning-standup` writes `~/.claude/align/<repo>/standups/YYYY-MM-DD.md` with a per-session
intent line. `/eod-digest` reads it and diffs. The two skills are **a pair sharing one
artifact**, not two independent skills.

The file lives outside every repo, so no working tree is dirtied — the same rule
`get-aligned` already follows for `map.md`.

### 2.3 `.claude/health.md` — the per-repo descriptor

```markdown
---
health_command: make dev-status
---

# What healthy means in this repo

`make dev-status` runs `scripts/dev_status.py` — read-only, no mutation.

## Interpreting the output
- `model_fallback_used` is a KNOWN FALSE POSITIVE — an SDK auxiliary Haiku
  call on Sonnet-configured seats. Ignore unless the seat is Haiku-configured.
- `resolutions` empty is correct until a decision passes its 5-day horizon.

## Escalate, never act
Broker mutations, droplet deploys, gate thresholds.
```

**The split is the point.** `health_command` is the deterministic half — one tested
command, identical reads every time. The prose is the judgment half — how to read the
output. Determinism where drift is fatal; prose where interpretation is needed.

**The known-false-positive section is load-bearing, not decoration.** `model_fallback_used`
fires at severity 3 every day. With nowhere to write "this one is noise," a reader learns
to skip the scorecard within a week, and the next real severity-3 goes past.

**Behaviour:** file present → run `health_command`, fold in output, interpret with the
prose. File absent → skip the step silently. Other repos supply their own and get the same
treatment.

### 2.4 `scripts/dev_status.py`

Pure read. No LLM imports, no mutation, no wall-clock call — time via the injected `Clock`
protocol. Emits markdown to stdout. Exit code 0 always; a failed check renders as a
finding, never as a crash that hides the other checks.

**Derivation rule — read before adding a check.** Every check answers *"is a stated
invariant or a phase acceptance criterion still true in production?"* Incidents are used
to **validate** the list, never to source it. A check justified only by "this bit us once"
is overfitting to one day; a check justified by an invariant keeps earning its place after
the incident is forgotten. When those disagree, the invariant wins and the incident-only
check is dropped.

Applying that rule to 2026-08-21's incidents: it keeps most of them, and it adds the
paper-trading check, which no incident suggested and which guards the most important
invariant in the repo.

| Check | Derived from |
|---|---|
| `ALPACA_PAPER_TRADE=true` on the droplet | invariant 1 — paper only |
| exec seat is the only one with `trading`; non-exec deny rules intact | invariant 2 |
| `gate_error` / `pm_timeout` / missing-signal defaults for the run | invariant 4 — default is HOLD |
| every order's `client_order_id` equals its ticket id | invariant 5 — idempotency |
| `events` where `posted_at IS NULL`; `orders` vs broker fills | invariant 6 — SQLite is truth, Slack is a projection |
| all checkpoints `done`; journals written for participating seats | Phase 2 acceptance |
| decisions past horizon with a `resolutions` row | Phase 2 acceptance — reflection |
| positions, open orders, **coverage per position** | `specs/design.md` §5 — the gate's stop contract |
| droplet HEAD vs `origin/master`; last result of `fund-daily` and `fund-pnl` | deployment state — is the code under test the code running |

**Dropped as incident-only:** "open PRs and unowned issues." It is a `gh` one-liner the
skill can run directly, it answers no invariant, and it grows without bound.

**Moved, not dropped: local branches unpushed and unmerged.** It answers no invariant and
is dev-side, so by the derivation rule it does not belong in a production health script.
But it is the right question at close-out — *is anything lost if this chat closes?* — so
it lives in `/eod-digest` step 2 instead. It caught two real losses on 2026-08-21
(`bdac89b`, then `docs/adr-stop-amend`), both in worktrees, both invisible to every other
check.

**It must be the refined form.** Local-only alone returns 18 rows on this repo, nearly all
merged branches whose remote was deleted — noise. Local-only **and** carrying commits not
on master returns 2, both self-labelled `backup/pre-rebase-*`. Signal.

## 3. Data flow

```
morning-standup ──writes──> align/<repo>/standups/YYYY-MM-DD.md (intents)
       │                                    │
       └──runs──> health_command            │ read as baseline
                       │                    ▼
                       └──────────────> /eod-digest ──> same file (outcomes + tomorrow)
                                             │
                                             └──runs──> health_command
```

## 4. Error handling

Default is **report, never act** — the developer-tooling twin of invariant 4.

| Failure | Behaviour |
|---|---|
| droplet unreachable | render "droplet: unreachable"; every other check still runs |
| broker call fails | render the failure as a finding; never infer position state from the DB |
| `health_command` missing or non-zero | render stderr; the session poll still proceeds |
| no `.claude/health.md` | skip the health step silently |
| no intents file for today | say so, and fall back to "what did you do" — never fabricate a baseline |
| peer never replies | named silent in the digest; still receives the outcome |

## 5. Testing

`scripts/dev_status.py` runs in `make test` — offline, no network, no keys. Every reader
(droplet, broker, DB) is injected, so the suite exercises it against fakes.

Required cases:

- Each check's negative control fails. **A test whose negative control also passes is not
  a test** — three instances of that on 2026-08-21, two caught by luck.
- `ALPACA_PAPER_TRADE` absent or not `true` → finding. Present and true → silent.
- Unpushed-and-unmerged: a merged local-only branch is **not** reported; an unmerged one is.
- `resolutions` empty **before** any horizon → no finding. Empty **after** one passes →
  finding. Same DB state, opposite verdicts.
- A known false positive in the descriptor is suppressed; the same code from a
  non-suppressed seat is reported.
- Droplet unreachable → other checks still render.

## 6. Sequencing

`/eod-digest` and `dev_status.py` first; EOD writes tomorrow's todos. Only then does
`morning-standup` learn to write intents.

Reason: `morning-standup` is shared with other repos and is the most-used coordination
skill. Nothing may depend on the morning file until the file exists.

## 7. Open

- `CONTEXT.md` at the fund repo root is empty (0 bytes) while `CLAUDE.md` names it as half
  the domain docs. Out of scope here; recorded because it was found while reading.
