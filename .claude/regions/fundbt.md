---
paths:
  - fundbt/
  - tests/test_fundbt*.py
---
# fundbt — standing

Backtest engine (`run_backtest`) + trial registry. Never run a backtest except
through the `run_backtest` tool, which auto-logs the trial registry — an
unlogged trial corrupts the deflated-Sharpe correction fund-wide.
`specs/strategy-contracts.md` is canonical for the pipeline's contracts;
evidence behind `specs/strategy.md`'s numbers lives in
`research/strategy-research-report.md` (consult on demand, don't load by
default). Pre-built and tested — extend, don't rewrite.

# Journal
