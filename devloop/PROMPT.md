<!-- Fixed worker prompt. Same allocation every iteration (Ralph pattern: fresh
     process = fresh context; the loop, not the conversation, carries state). -->

Read CLAUDE.md, specs/contracts.md, and specs/acceptance.md (§0 and Phase 1).

Pick the SINGLE most important unchecked `- [ ]` item in the Phase 1 section of
specs/acceptance.md, skipping any item marked `@live`. One item per iteration —
do not touch other items even if you notice easy wins. If §0 infrastructure
(Clock/SimClock, FakeSlack, recorder/replayer, markers, fixtures) is needed by
your item and missing, building that infrastructure IS the item.

Rules:

1. Search the codebase before writing anything — do not assume something is
   unimplemented. Existing code in fundbt/, stratgate/, calibration/ is prebuilt
   and tested: extend, never rewrite.
2. Test-driven: write the acceptance test first, watch it fail, then implement
   until `make test` is green. Schemas come from specs/contracts.md verbatim —
   invent zero fields.
3. Full implementations only. No placeholders, no stubs, no TODO-later. If your
   item cannot be completed without touching a protected path (specs/, fixtures/,
   charters/, scripts/, Makefile, pyproject.toml, CLAUDE.md, fundbt/, stratgate/,
   calibration/, devloop/) or without a human decision, STOP: write what you
   need and why to devloop/NOTES.md and end your turn without changes.
4. Never modify or delete an existing test, fixture, or spec to get green. The
   checker gate rejects the iteration if you do.
5. The 7 invariants at the top of CLAUDE.md bind every line you write. In
   particular: injected Clock only (no datetime.now/time.sleep in business
   logic), state transitions only through state/transition(), tool-structured
   outputs only.
6. When `make test` is green and your item's criteria are met, edit its checkbox
   in specs/acceptance.md from `- [ ]` to `- [x]` — this is the ONLY edit to
   specs/ you are permitted, and only for the one item you implemented.
7. Do NOT run git commit — the harness commits on green.
8. Record durable learnings (build quirks, spec ambiguities, discovered bugs
   outside your item) in devloop/NOTES.md, briefly. No status reports there.
