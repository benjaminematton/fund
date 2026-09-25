---
paths:
  - gate/
  - tests/test_gate*.py
---
# gate — standing

Deterministic risk gate and ticket store between LLM decisions and Alpaca
orders. Pure Python + SQLite: imports no LLM code, no `claude_agent_sdk`, no
`anthropic`, no prompt strings — enforced in CI by `scripts/check_purity.py`.
Gate thresholds change only by human commit, never by an agent. Default is
HOLD: any error, timeout, or malformed input resolves to no action. Orders are
idempotent: `client_order_id` = gate ticket id, always; never mint a new id on
retry. Never import from `agents/` here. Tests: `tests/test_gate*.py`; gate
math worked through in `specs/design.md` and `fixtures/golden-day.md`.

# Journal

# Journal

## 2026-09-25 · #137 #138 · fund-fe (overseer)
- `size(inputs)` has no `mode` parameter (PR #243): it was dead — the body never read it —
  so `test_advisory_equals_enforcement_on_identical_inputs` could not fail. The parity pin
  now runs the two REAL orchestrator paths (`allowed_actions` vs the ticket `run_gate`
  mints) on identical inputs; a haircut or clamp on either side alone trips it. Still
  single-ticker; a joint/stateful gate would need a multi-ticker vector (#38's question).
- There is no "invariant §3.9" in any canonical spec. The property is `specs/design.md` §5
  "Deterministic risk gate": "advisory and enforcement share one code path; they may differ
  only via price/account drift between runs." Cite that, not a number.
