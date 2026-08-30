# Index — canonical specs, and where the derived designs live

**This file is a map, not a source.** It carries no rules and no schemas.

## Authority tiers

Three tiers. A document's tier is decided by where it lives and whether its filename
carries a date — never by how confident it sounds.

| Tier | Where | Dated? | What it means |
|---|---|---|---|
| **Canonical** | `specs/`, `charters/`, `fixtures/`, `CLAUDE.md` | no | The contract. Changes only by deliberate human commit. |
| **Derived** | `docs/`, `plans/`, `research/` | yes | One moment's reasoning, snapshotted. Never current state. |
| **Live state** | GitHub board #49, open issues, PRs, `git log` | — | The only current truth. Not in this repo. |

**The rule that makes it work: nothing in the repo may claim to be live state.** A derived
document describes the moment it was written. When it disagrees with `git log` or board #49,
those win and the document is stale — not wrong, stale. Say so in a status header rather than
rewriting it, because its staleness is often itself the evidence.

## Canonical — read these before implementing

| File | What it settles |
|---|---|
| `design.md` | Seats, daily cycle, gate math, infrastructure, build order |
| `contracts.md` | DDL, pydantic models, tool schemas, state machines, failure semantics. **Do not invent fields.** |
| **`acceptance.md`** | **The build order.** Per-phase done-criteria; write these tests first |
| `strategy.md` | Strategy lifecycle, backtest rules, gates G1–G4, allocation and kill rules |
| `strategy-contracts.md` | Canonical ids/DDL/state machine for the strategy pipeline; overrides conflicts elsewhere |
| `calibration.md` | Analyst scoring → deterministic PM weights |
| `improvement.md` | The improvement loop: tier 1 (weights, narrowing, lessons — code) and tier 2 (the Proposer — proposals a human commits); `weights`/`lessons`/`proposals` DDL and tool schemas |
| `../charters/` | Seat system prompts; `_template.md` defines required sections |
| `../fixtures/golden-day.md` | Worked example of one full day; its numbers are test vectors |

## Derived designs — `docs/superpowers/specs/`

Skill-generated (`brainstorming` writes here by default), so the directory holds fund designs
and tooling designs side by side, ordered by date. That mixture is correct: the folder means
*skill output*, not *one subject*. This index is how you find a fund design without reading
fourteen filenames.

**Fund — how a part of the system was designed**

| Date | Design |
|---|---|
| 2026-07-12 | Phase 2 — the desk (PM + 2 analysts + real gate) |
| 2026-08-12 | MVF — minimum viable firm, 3-day resume slice |
| 2026-08-17 | Move the daily run to a Linux VM |
| 2026-08-18 | G1 alignment gate — design, and its measured result |
| 2026-08-18 | Improvement loops — the buildable half |
| 2026-08-19 | Closing the missing-stop class |
| 2026-08-20 | Account precondition drift detection |
| 2026-08-21 | Day bookends — morning standup and EOD digest |
| 2026-08-24 | Alert identity and the alert → issue filer |
| 2026-08-30 | Species Two — reframing `design.md` around the improvement loop (v2, reconciled); promoted to `improvement.md` |

**Tooling — how the agents working this repo are organized**

| Date | Design |
|---|---|
| 2026-08-24 | `morning-standup` ends in lanes owned |
| 2026-08-25 | `owning-a-lane` — the shared overseer role |
| 2026-08-26 | `morning-standup` dispatches a mix — remediate, decide, land (+ four competing candidates) |
| — | Field brief + claims log: coordinating parallel agent sessions on one repo |

**Every one of these defers to the canonical files above.** The Phase 2 desk design states it
outright — *"companion canon (authoritative where they overlap)"*. A derived design that
appears to extend a canonical file names the canonical file that must be edited first.

## Other derived locations

- `plans/` — the four hand-written foundational plans (`phase-1a`, `phase-1b`, `mvf`,
  `evals-1`). Referenced by path in `CLAUDE.md`; kept separate from skill-generated plans.
- `docs/superpowers/plans/` — skill-generated implementation plans.
- `docs/adr/` — architecture decisions; immutable once accepted.
- `docs/agents/` — how agents operate this repo (issue tracker, domain docs, devops).
- `research/` — evidence base. Not loaded by agents by default.

## Where current state actually lives

- **What to work on** — GitHub issue **#49** (`wayfinder:map`) and its children.
- **What is in flight** — open issues and PRs.
- **What shipped** — `git log` on `master`.

`PROGRESS.md`, `HANDOFF-LIVE.md`, and `KICKOFF.md` at the repo root are historical snapshots
despite their names. `PROGRESS.md` carries a staleness header saying so.
