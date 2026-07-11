# fund — AI hedge fund run by long-lived agents

Multi-agent paper-trading firm on the Claude Agent SDK (Python). Agents communicate in Slack; a deterministic Python gate sits between LLM decisions and Alpaca orders.

## INVARIANTS — never violate, no exceptions

1. **Paper only.** `ALPACA_PAPER_TRADE=true` everywhere. Never add live-trading code paths, config flags, or TODOs pointing at live trading.
2. **Only the Execution Trader seat has the `trading` toolset.** Every other seat: read-only Alpaca toolsets, plus SDK-level `disallowed_tools` deny on `mcp__alpaca__place_*`.
3. **`gate/`, `stratgate/`, and `calibration/` import no LLM code.** No `claude_agent_sdk`, no `anthropic`, no prompt strings. Pure Python + SQLite. Enforced in CI by `scripts/check_purity.py` (AST lint: forbidden imports + wall-clock calls; runs in `make test`). Gate thresholds (risk and strategy-validation) change only by human commit — never by an agent.
4. **Default is HOLD.** Any error, timeout, malformed tool input, or ambiguity anywhere in the pipeline resolves to no action — never to a guess.
5. **Orders are idempotent.** `client_order_id` = gate ticket id, always. Alpaca 422-rejects duplicates; never mint a new id on retry.
6. **SQLite is the source of truth; Slack is a projection.** Never read workflow state from Slack; never trigger execution from a Slack event.
7. **Agents emit structured data only via MCP tools** (`submit_signal`, `submit_decision`, strict schemas) — never as free text that code parses.

## Commands

- `make test` — full offline suite. No network, no API keys. Must pass before every commit.
- `make sim-day` — full simulated trading day: injected clock, FakeSlack, recorded LLM decisions, **real** tool/gate/DB execution.
- `make replay REC=<recording>` — replay a recorded day's LLM decisions against current code.
- `make live-paper` — real Slack + Alpaca paper + real LLM calls (needs `.env`).
- `docker compose up` — one service per seat + orchestrator.

## Architecture map

`orchestrator/` (no LLM: clock, stage scheduler, turn assignment, checkpoints) → `agents/` (one persistent `ClaudeSDKClient` process per seat; shared runtime in `agents/runtime.py`) → `gate/` (deterministic risk, ticket store) → Alpaca MCP (paper). Research side: `fundbt/` (`run_backtest` engine + trial registry) → `stratgate/` (strategy validation G1–G4) → `calibration/` (analyst scoring → PM weights) — all pre-built and tested (32 offline tests); extend, don't rewrite. State: `state/` (SQLite DDL + helpers), `journals/` (per-agent markdown). Slack via `slackkit/` in-process MCP server.

## Conventions

- Python 3.12, `pydantic` v2, `pytest`; `claude-agent-sdk` and `slack_bolt` pinned in `pyproject.toml`. Model ids and budgets live in `agents/config/*.yaml`, never hardcoded.
- Every workflow table is a state machine. Allowed transitions are defined in `specs/contracts.md`; apply them only through `state/transition()`. Illegal transition = raise, never overwrite.
- Time comes from an injected `Clock` protocol. Never call `datetime.now()` or `time.sleep()` in business logic — this is what makes `sim-day` possible.
- Never put per-run values (timestamps, uuids, tmp paths) into prompts; pass them to tools out-of-band. Baked-in values break replay tests.
- All hooks (`PreToolUse` order gate, cost recording) are defined in `agents/runtime.py` only.
- Set `setting_sources=["project"]` in `ClaudeAgentOptions` so this file is loaded for every seat.
- `ResultMessage.total_cost_usd` is a client-side estimate — label it "est." in digests.

## Specs — read before implementing; schemas there are canonical

- `specs/design.md` — full design doc (seats, daily cycle, gate math)
- `specs/contracts.md` — DDL, pydantic models, tool schemas, state machines, failure semantics. **Do not invent fields.**
- `specs/acceptance.md` — per-phase done-criteria. Implement its tests FIRST, then code until green.
- `specs/strategy.md` — strategy lifecycle: pre-registration, backtest tool rules, gates G1–G4, allocation & kill rules. Evidence behind its numbers lives in `research/strategy-research-report.md` — consult on demand, do NOT load by default.
- `specs/strategy-contracts.md` — canonical ids/DDL/state machine/tool contracts for the strategy pipeline; matches the tested code in `fundbt/` + `stratgate/`. Overrides anything conflicting elsewhere.
- `specs/calibration.md` — analyst scoring (Brier/BSS, shrinkage) → deterministic PM weights; implemented in `calibration/`.
- `charters/` — seat system prompts. `_template.md` defines required sections; `pm.md` and `quant.md` are the quality bar.
- `fixtures/golden-day.md` — worked example of one full day; use its numbers as test vectors.

## Do NOT

- Do not parse tickers/actions/sizes out of free text anywhere.
- Do not give a seat a new Alpaca toolset without updating the seat table in `specs/design.md`.
- Do not write journals except through `state/journal.py`.
- Do not weaken or delete a red acceptance test to make it pass.
- Do not run a backtest except through the `run_backtest` tool (it auto-logs the trial registry; an unlogged trial corrupts the deflated-Sharpe correction fund-wide).
- Do not import from `agents/` inside `gate/` or `orchestrator/`.

## Test invariants

- Tests are the spec. A failing test means the implementation is wrong.
- NEVER update a golden fixture, expected hash, or expected value to make a test pass. If you believe an expectation is genuinely wrong, STOP and ask. No exceptions, no "deliberate re-record."
- Run: `make test`

## Compact instructions

When compacting, preserve verbatim: the test invariants above; which plan tasks are complete with commit shas; any deviation from `plans/phase-1.md` and why. Discard file reads, test output, and exploration traces.
