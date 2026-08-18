# Domain docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read the domain docs

Before you explore the codebase, read the following files:

- **`CONTEXT.md`** at the repo root.
- **`CONTEXT-MAP.md`** — if this file exists at the repo root, read it instead of `CONTEXT.md`. It points at one `CONTEXT.md` per context; read each one relevant to the topic.
- **`docs/adr/`** — read the architecture decision records (ADRs) that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached through `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (most repos):

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

Multi-context repo (presence of `CONTEXT-MAP.md` at the root):

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, or a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms that the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal. Either you're inventing language that the project doesn't use — in that case, reconsider the term — or there's a real gap, so note it for `/domain-modeling`.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface the conflict explicitly rather than silently overriding the ADR:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_

## This repo: `specs/` stays canonical

`specs/contracts.md`, `specs/strategy-contracts.md`, and the rest of `specs/` remain the
canonical source for DDL, schemas, tool contracts, and state machines — an ADR never
overrides them. ADRs record *why* a decision was made; `specs/` records *what* the
contract is. `CONTEXT.md` is the glossary layer above both.
