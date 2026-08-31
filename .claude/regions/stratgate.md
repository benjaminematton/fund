---
paths:
  - stratgate/
  - tests/test_stratgate*.py
---
# stratgate — standing

Strategy validation gates G1–G4. Pure Python + SQLite: imports no LLM code —
enforced by `scripts/check_purity.py`; validation thresholds change only by
human commit. Pre-built and tested — extend, don't rewrite.
`specs/strategy-contracts.md` is canonical for ids, DDL, state machine, and
tool contracts here and overrides anything conflicting elsewhere; lifecycle
rules (pre-registration, gates, allocation and kill rules) in
`specs/strategy.md`.

# Journal
