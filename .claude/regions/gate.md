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
