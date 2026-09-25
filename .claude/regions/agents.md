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

## 2026-09-25 · #128 #209 #125 · fund-fe (overseer)
- **The MCP `env` dict is an overlay, not a scope** (PR #252): the SDK spawns the CLI with
  `{**os.environ, **options.env}` and the CLI spawns a stdio server with
  `{...process.env, ...config.env}`, so an allow-list *dict* would have been decorative — and
  copying secrets into it puts them in the CLI's `--mcp-config` argv (`ps`-visible). The
  Alpaca server now launches as `/bin/sh -c <ALPACA_MCP_LAUNCH> sh uvx <spec>`: the prologue
  unsets every name not in `ALPACA_MCP_ENV_KEEP`, exports `ALPACA_PAPER_TRADE=true` itself,
  and `exec`s uvx (process tree unchanged). Demonstrated locally 2026-09-25 against the real
  server: initialize + tools/list answered, 17 tools under `stock-data`, no `place_*`.
  **Run `make preflight` on the droplet before the first trading morning after this deploys** —
  no offline test proves `alpaca-mcp-server@2.3.1` needs nothing beyond these names.
- `tests/test_trader_wiring.py`'s spec pin moved from `args == [SPEC]` to
  `command == "/bin/sh"` and `args[-2:] == ["uvx", SPEC]` — same claim, relocated.
- `disallowed_tools: ["mcp__alpaca__place_*"]` is now pinned on the STANDING options of
  `quant` and `reflect` (#209); before, dropping it for both left nothing red.
- `agents/config/pm.yaml`: `claude-sonnet-5` is the complete model id; there is no dated
  Sonnet-5 snapshot to pin to (#125, comment in the file). `fallback_model` in the six
  configs and `critic.yaml` carry the same alias with no note.
