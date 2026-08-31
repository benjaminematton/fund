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
