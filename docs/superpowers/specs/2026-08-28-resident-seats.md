# Resident seats — the 9-5 phase (Phase 6 sketch, v2)

**Date:** 2026-08-28 · **Status:** derived design, not canonical. Defers to `specs/design.md`,
`specs/contracts.md`, and `CLAUDE.md`; where this document appears to extend them, the canonical
file must be edited first (by human commit). Research grounding:
`field-brief-long-running-agents.md`. **v2** after an adversarial fresh-context review
(verdict on v1: NOT-READY; all blockers and majors addressed below — the review's findings
are folded in where they changed the design).

## What "9-5" means here

The seats become *resident for the trading day*: reachable by @mention, reactive to events within
seconds, doing unscheduled work between the scheduled stages — without giving up the three
properties the per-turn design paid for: replayable turns, invariant 6 (Slack is never a trigger
for workflow state), and degrade-to-HOLD.

The research consensus shapes everything below: **resident does not mean one immortal context.**
Anthropic's harness guidance, 12-factor agents, and the always-on community converge on bounded
sessions that read durable state on wake, do one unit of work, write state back, and end. Chroma's
context-rot results — and Alpha Arena's in-domain "context fatigue" — are the empirical reason. A
resident seat is a supervised *process* that is always up, running *bounded fresh-context wakes*
fed by a work queue that only code can write.

## The two lanes

**Lane A — workflow-critical (unchanged).** Research, decision, gate, execution stages stay
orchestrator-assigned, recorded, and replayable. **Decision-recording turns always run
fresh-context**: a recorded Lane A turn must be a pure function of its brief, and that is now an
asserted property, not a hope — see "The test that guards the split" below.

**Lane B — ambient (new).** Unscheduled work — spec reviews, alert triage, @mention answers —
arriving as rows in a SQLite work queue, consumed in bounded fresh-context wakes.

**Lane B authority is a tool surface, not a promise.** v1 said Lane B has "no decision
authority" while handing wakes the seat's full toolset; that was a contradiction, and this repo
does not enforce authority by charter prose (invariant 2's pattern: enforced allow-arrays, pinned
by test). Each work kind gets an explicit `tools=[...]` allow-array built in
`build_seat_options()`:

| kind | tool surface |
|---|---|
| `spec_review` | `get_spec_brief`, `submit_spec_critique` only |
| `mention` | read-only fund/journal tools + Slack prose; **no `submit_*` with workflow effect** |
| `alert_triage` | read-only + Slack prose + `gh` issue tools per devops doctrine |

Pinned by a per-kind tool-surface test, same pattern as `tests/test_exec_seat_tool_surface.py`.
Default on any wake error: do nothing, alert `#risk`, row `failed`.

### The work queue

```sql
CREATE TABLE IF NOT EXISTS worklist (
  work_id       TEXT PRIMARY KEY,            -- "wk_" + hash(kind, subject, dedupe_key)
  kind          TEXT NOT NULL,               -- 'spec_review' | 'mention' | 'alert_triage' | ...
  producer      TEXT NOT NULL,               -- which code path wrote it ('orchestrator',
                                             --  'alert_filer', 'slack_listener', 'nightly')
  seat          TEXT NOT NULL,               -- the one seat that may consume it
  subject       TEXT NOT NULL,               -- id of the thing (spec_id, alert id, slack ts)
  payload       TEXT NOT NULL DEFAULT '{}',  -- JSON, small; the wake re-reads truth from DB
  state         TEXT NOT NULL CHECK(state IN ('open','claimed','done','failed','expired')),
  state_version INTEGER NOT NULL DEFAULT 0,  -- CAS, same transition() helper
  attempts      INTEGER NOT NULL DEFAULT 0,
  not_before    TEXT,                        -- injected-Clock ISO; debounce/backoff
  expires_at    TEXT NOT NULL,
  claim_expires_at TEXT,                     -- lease; set at claim from injected Clock
  created_at    TEXT NOT NULL,
  claimed_at    TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_worklist_dispatch ON worklist(seat, state, not_before);
```

Rules, each closing a reviewed hole:

- **Producers are deterministic code only**, and the dispatcher enforces a *kind-by-producer
  allow-list*: a `slack_listener`-produced row may only carry prose-capable kinds. An agent never
  enqueues work; `producer` makes provenance auditable and branchable.
- **Mentions enqueue only for human-authored messages.** The `message.channels` listener sees
  bot-authored mentions too; those never produce rows — otherwise seat A's prose "@B should weigh
  in" wakes B, whose answer wakes A: the self-amplifying loop, laundered through Slack.
  Acceptance: bot-authored mention → zero rows.
- **`worklist` is scheduling intent, never truth.** Truth stays where it lives
  (`strategy_critiques`, `alerts`, …). Row finalization reconciles against truth: a wake that
  crashed *after* `submit_spec_critique` lands → row `done` on reconcile, exactly one critique
  row, no double review.
- **No silent states.** A deterministic sweep (with the orchestrator's jobs, injected Clock)
  transitions over-lease `claimed` → `failed` and past-due `open` → `expired`, **both with a
  `#risk` alert**. LLM wakes are never auto-requeued; re-enqueue is a human action and increments
  `attempts` (the `dedupe_key` incorporates it, so a failed row is not a permanent tombstone).
- **Budget exhaustion is loud**: per-seat daily cap hit → dispatcher stops claiming for that
  seat, one alert; remaining rows expire on schedule (alerting per the sweep rule).

### The wake

The dispatcher (plain Python, no LLM, lives with the orchestrator's process group under systemd
with a watchdog — the dispatcher is the fund's first resident process and is itself supervised;
its death alerts) claims a row by CAS + lease and runs one bounded seat turn: fresh context,
brief = current durable state + the row's subject re-read from DB, under `SEAT_MAX_WALL_S`, a
per-wake `max_budget_usd`, and the per-day caps. Wakes are recorded to `recordings/` — recorded
and auditable (replay of a prose-only wake is vacuous; the claim is audit, not replay).

Cost invariants of this phase:

1. **No polling heartbeats, no LLM liveness checks.** Deterministic code watches for change; the
   model runs only when a row exists. Liveness = systemd watchdog + a deterministic daily
   self-check row that never invokes a model.
2. **Budgets:** $0.25/day/seat Lane B cap and a $1/day firm-wide Lane B cap to start (baseline
   firm spend is ~$0.20/day; caps must be able to bind while the cost curve is unknown). Raise
   only from cost-row evidence.
3. **Tiered triage** (cheap model classifies a wake before the seat's model runs) — not built
   until measured volume says so.

### The test that guards the split

New acceptance property, Lane A: **recorded brief == the turn's entire model input**, asserted
for every decision-recording turn. This is the property "replay passes" cannot check (replay
replays recorded decisions; it never re-runs the model), and it is what makes the two-lane split
real rather than declared.

### The day session (R4, rescoped and conditional)

v1 let a seat hold one `ClaudeSDKClient` session across Lane A stages and Lane B wakes; the
review killed both halves (decision turns must stay pure functions of their brief; ambient wakes
sharing a day session reverses the isolated-session cost finding and pollutes decision context —
the Alpha Arena failure). What survives, if anything does:

- **Ops only, non-decision stages only**: standup → invalidation watch → EOD digest as one
  continuous session, born at pre-open, dead at close, journal written before close. No decision
  turn ever runs inside it; no Lane B wake shares it.
- Compaction configured (CLAUDE.md summary instructions + `PreCompact` archive), crash →
  `resume=session_id`, unresumable → fresh-context fallback for the rest of the day.
- **Build it only if it buys something the journal-summary brief does not.** The claimed benefit
  of v1 ("standup context available at the decision") is already delivered by the existing
  journal mechanism. If a measurable benefit can't be named at build time, R4 is cut, and the
  firm is resident without any persistent session — which the research says is the normal case.

## Build order

**P5 (pulled out of this phase entirely) — G1 enforcement, no queue.** The Critic turn is
orchestrator-assigned inside `run_day.py`: after spec registration, run the Critic until
`get_spec_brief` is empty. `get_spec_brief` already implements oldest-unreviewed queue semantics;
G1 needs zero new infrastructure and belongs to Phase 5's acceptance items. Phase 6 no longer
gates the lab.

**R1 — queue + dispatcher, no LLM.** DDL + `transition()` edges + dispatcher + sweep + systemd
supervision. Producers: alert filer and orchestrator only. Acceptance: CAS under concurrent
claim; lease reap alerts; expiry alerts; sim-day with empty queue → zero wakes, zero cost rows
(idle-cost test); dispatcher kill mid-claim → reap + alert, no lost row.

**R2 — migrate the Critic onto the queue.** The P5 scheduled turn becomes a `spec_review`
consumer with its two-tool surface. Acceptance: spec registered → verdict within one dispatcher
cycle in sim; crash-after-submit → reconcile to `done`, exactly one critique row; queue
unreadable → error, never silently empty.

**R3 — @mentions.** Precondition: the CLAUDE.md/invariant-6 amendment below is committed by
human hand first. Listener writes `mention` rows for human-authored messages only; wakes answer
from journal + DB, prose-only surface. Acceptance: human mention → threaded answer in FakeSlack;
bot-authored mention → zero rows; mention asking for a trade → no workflow tool available (tool-
surface test, not charter test).

**R4 — the Ops day session**, conditional as above.

**R5 — the event overlay `design.md` §3 already promises.** News flags and big-move triggers
enqueue rows **whose consumer is the orchestrator, not a seat**: consumption = the orchestrator
assigns ordinary Lane A turns (off-cycle mini-debate, gate mandatory), recorded and replayable
like any stage. Sim fixture injects the triggering event under the injected Clock. A market-data
watcher, not Slack, is the producer.

## Canonical-text precondition (R3)

Invariant 6's "never let a Slack event produce a decision, an order, or a state transition" is
strained by a listener inserting rows that produce paid agent turns. Before R3, CLAUDE.md must be
amended by human commit to draw the line explicitly — proposed text: *"A Slack event may produce
at most a prose reply: the listener may enqueue prose-capable ambient work from human-authored
messages only; no Slack-produced row may reach a tool surface that writes workflow state."* If
the CEO won't ratify that sentence, R3 does not get built.

## What this phase refuses to do

No agent-to-agent queue writes (enforced by producer allow-list + human-only mentions). No
autonomous overnight residency. No second checker loop. No new decision paths — every order still
walks signal → decision → gate → ticket → trader. No heartbeats, no LLM liveness. No live-trading
anything (invariant 1).

## CEO decisions (answered 2026-08-28, in-session; ratification = the CLAUDE.md commit itself)

1. R3 invariant-6 amendment: **approved as written** — pending Benjamin's own CLAUDE.md commit,
   which is the act that ratifies it.
2. Lane B budgets: **confirmed** — $0.25/day/seat + $1/day firm-wide; raise only from cost rows.
3. R4 cut condition: **confirmed** — no named measurable benefit at build time → R4 is cut.
