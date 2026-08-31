---
paths:
  - calibration/
  - tests/test_calibration*.py
---
# calibration — standing

Analyst scoring (Brier/BSS, shrinkage) → deterministic PM weights; spec in
`specs/calibration.md`. Pure Python + SQLite: imports no LLM code — enforced
by `scripts/check_purity.py`. Pre-built and tested — extend, don't rewrite.
Tests: `tests/test_calibration*.py`.

# Journal
