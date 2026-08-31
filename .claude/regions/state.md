---
paths:
  - state/
  - tests/test_state*.py
---
# state — standing

SQLite DDL + helpers; SQLite is the source of truth — Slack is never where a
decision comes from. Every workflow table is a state machine: apply
transitions only through `state/transition()`; allowed transitions defined in
`specs/contracts.md` (do not invent fields); an illegal transition must raise,
never overwrite. Journals are written only through `state/journal.py`.
Outbound delivery goes through the `events` outbox so a crash or retry can
neither lose nor duplicate a post. Tests: `tests/test_state*.py`.

# Journal
