---
paths:
  - agents/
  - charters/
  - tests/test_exec_seat_tool_surface.py
---
# agents — standing

One persistent `ClaudeSDKClient` process per seat; shared runtime in
`agents/runtime.py`, where ALL hooks (PreToolUse order gate, cost recording)
live. Model ids and budgets in `agents/config/*.yaml`, never hardcoded. Only
the Execution Trader seat has the `trading` toolset; order-placing seats get
`setting_sources=[]` and an explicit `tools=[...]` allow-array (MCP globs
only) — `tools` governs availability, `allowed_tools`/`disallowed_tools` only
govern approval and fail open. Pinned by `tests/test_exec_seat_tool_surface.py`
— do not relax. Charters in `charters/` (`_template.md` defines required
sections; `pm.md` and `quant.md` are the quality bar). Agents emit structured
data only through MCP tools, never free text that code parses.

# Journal

## 2026-08-31 · #205 · fund-e2
- The `read_`-prefix exemption ("need not be a registered `@tool`") is one
  filter, in `test_fund_tools.py::test_tool_caps_are_real_registered_tool_names`.
  `test_tool_surface_canon.py`'s two canon checks have no `read_` awareness
  at all (different mechanism — contracts §4, not Alpaca toolsets), so a
  `read_` cap wrongly granted to a seat is caught only by an incidental
  seat-specific test, if one happens to cover it.
- Charter prose is asserted by nothing: `test_charters.py` checks section
  shape/order only ("no test can judge a prompt"); eval tests hash charter
  text for trace provenance, not content. Three false claims reached
  `charters/pm.md` v7 in this lane (83899f1, 7843c11, c4b3036) — all caught
  by review reading, none by a test.
