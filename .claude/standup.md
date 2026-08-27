---
intent_sources:
  - docs/superpowers/specs/2026-08-26-devops-loop-design.md
  - specs/acceptance.md
  - PROGRESS.md
---

# Intent sources for morning-standup Phase 0c

Read only what each source marks as next or open:

- A spec's **Landing order** section: the first step not yet landed.
- `specs/acceptance.md`: unticked `- [ ]` items in the current phase (Phase 2) only.
- `PROGRESS.md`: entries under the **Open items** heading.

These feed the standup's Today proposal as flagged intent items. They are not
lanes and are not dispatched; an off-board item enters work only when the human
confirms it in the Today reply or files it as an issue.

## Keeping this list current

The spec entry is a dated file and goes stale by design — a dated filename is a
snapshot, never current state. When a newer design spec becomes the one being
landed, replace that line; do not accumulate them. `specs/acceptance.md` and
`PROGRESS.md` are standing sources and stay. When the current phase advances
past Phase 2, update the phase named above — the standup reads this file, not
the board, to know which phase's boxes matter.
