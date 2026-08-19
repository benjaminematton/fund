# Acceptance criteria — per phase, all checkable

Write these tests BEFORE the code they verify. A phase is done when its whole checklist passes. Never weaken a test to pass it.

## 0. Test infrastructure (built in Phase 1, used everywhere)

Testing splits LLM **decisions** (expensive, non-deterministic) from tool **execution** (cheap, deterministic — the part we actually test):

- `Clock` protocol with `SimClock` (settable, acceleratable) — injected into orchestrator, gate expiry, checkpoints. Zero `datetime.now()` in business logic (enforced by `scripts/check_purity.py` in CI and `make test`).
- `FakeSlack` — in-memory slackkit implementation recording posts per channel; queryable in asserts.
- **Recorder/replayer for agent turns**: record mode logs each seat's tool-call decisions (tool name + arguments) per stage to `recordings/*.jsonl`; replay mode feeds recorded decisions to the runtime while **executing real tools** against the real (temp) DB and FakeSlack. Prompts must contain no per-run values (see CLAUDE.md) so recordings stay valid.
- Pytest markers: default = offline; `@pytest.mark.live` = real APIs, excluded from `make test`, run manually.
- Fixtures: temp SQLite from `specs/contracts.md` DDL; frozen market data for the golden-day tickers (`fixtures/golden-day.md`).

## Phase 1 — Plumbing (Execution Trader alone)

- [x] `make test` green with no network and no keys.
- [x] DDL applies cleanly; `state.transition()` rejects illegal edges (parameterized test over every non-edge).
- [x] Sim: seed an `open` ticket → fire execution stage → exactly one `orders` row, `client_order_id == ticket.id`, FakeSlack `#trade-log` has one fill message.
- [x] **Idempotency**: fire the execution stage twice with the same ticket → still exactly one order row, one Slack message.
- [x] **Hook**: replayed trader turn attempting `place_order` with no ticket / expired ticket / qty > max_qty / wrong symbol / stop leg ≠ ticket `stop_price` → `PreToolUse` deny in all five cases; zero order rows.
- [x] **Stop-exit orders**: ticket with `stop_price` set → trader submits an `oto` order with that stop leg; ticket with `stop_price` NULL → plain order, no stop leg.
- [x] Expiry: `SimClock` past `expires_at` → ticket `expired`, order attempt denied.
- [x] Crash resume: kill the stage after ticket consumption, restart → checkpoint prevents re-execution.
- [ ] `@live` smoke: 1-share paper order round-trips (submitted → filled/canceled), fill lands in real Slack.

## Phase 2 — The desk (PM + 2 analysts + real gate)

- [ ] Gate unit tests (pure, exhaustive): vol tiers (14.9/15/49.9/50%), correlation multipliers at boundaries, cash cap, max position count, sector weight, daily-loss circuit breaker, and **any malformed/NaN/missing input → REJECT `gate_error`**.
- [ ] Golden-day vector: fixture inputs produce the exact ticket in `fixtures/golden-day.md` (max_qty **66** — 105 is the pre-sector-cap intermediate; assert both step values).
- [ ] Pre-gate (advisory mode): same fixture at 08:45 yields allowed actions `{buy: 66, sell: 0}` for NVDA (full computation incl. sector cap — same code path as enforcement, no ticket); a fixture ticker with no cash headroom and no held shares yields `{buy: 0, sell: 0}` and is dropped from the active set.
- [ ] Sim full day (replayed decisions, real execution): both analysts' `submit_signal` rows present → PM `submit_decision` row → gate ticket → order → all checkpoints `done`.
- [ ] Missing-signal default: one analyst recording omits a ticker → `neutral/0` row auto-inserted, day completes.
- [ ] PM timeout: no decision recorded for a ticker → decision row `hold/0`, `pm_timeout` event exists.
- [ ] HOLD-only skip: ticker where the 08:45 pre-gate computes `{buy:0, sell:0}` never reaches the LLM pipeline (assert zero agent turns for it).
- [ ] PM inputs: the decision-stage prompt context includes the allowed-actions snapshot for every active ticker (assert on the rendered stage input, not the prompt text — no per-run values in prompts).
- [ ] Journals: after sim day each participating seat's journal has an entry via `state/journal.py`; reflection job at `SimClock`+5 trading days writes `resolutions` with correct realized return & alpha from fixture prices.
- [ ] Cost rows recorded per seat per session.

## Phase 3 — The firm (debates, risk persona, macro, ops, CEO gate)

- [ ] Debate: orchestrator assigns turns bull→bear→bull→bear (+1 risk question each side); transcript in one FakeSlack thread; ≤5 replies per agent enforced; termination after 2 rounds even if agents would continue.
- [ ] Debate trigger logic: fires only on signal disagreement or contemplated position change (parameterized).
- [ ] Event overlay: injected breaking-news event → news analyst flags → PM mini-debate off-cycle → normal pipeline (gate still mandatory).
- [ ] CEO approval: order above threshold blocks on `can_use_tool`; sim approve → executes; sim reject / 15-min timeout → decision `failed`, no order.
- [ ] Ops: standup + EOD digest posted with fields per contracts §8; scoreboard math (hit rate, avg alpha, calibration) verified against hand-computed fixture.
- [ ] Self-filter: FakeSlack loops each agent's own messages back → no agent responds to itself (bot_id filter test).
- [ ] Critique flow: PM draft → critic `submit_critique` → PM final; `submit_decision` before a critique row exists → tool error; after → accepted (ordering test).
- [ ] Critique defaults: critic timeout → orchestrator inserts `clear`/`critic_timeout`, PM proceeds, day completes; no-critic-seat config (Phase-2 mode) → `clear`/`no_critic_seat` rows at stage start, Decision runs as one turn.
- [ ] Critique is advisory: replayed critic turn with 3 objections → pipeline unchanged (same ticket, same order); objections recorded, PM acknowledgment present in FakeSlack thread.
- [ ] `submit_critique` seat-restricted (non-critic caller → error); schema rejects >3 objections, >200-char objection, and `objections` non-empty with verdict `clear`.
- [ ] Scoreboard includes critic objection hit-rate, verified against a hand-computed fixture (objection on a decision that resolved badly counts as a hit).

## Phase 4 — Running it

- [ ] Chaos tests: Alpaca MCP down at execution (retry→expire→alert), Slack down mid-day (outbox drains after recovery), agent container kill mid-debate (session resume, turn re-assigned).
- [ ] Full-week sim under `SimClock` acceleration completes: 5 days, ≥1 resolution cycle, scoreboard posted.
- [ ] Budget: any seat hitting `max_budget_usd` degrades gracefully (stage default applies, day completes, alert posted).
- [ ] 30-day paper burn-in (manual, not CI): zero invariant violations in DB audit query; est. cost within budget.

## Phase 5 — The lab (strategy platform, per `specs/strategy.md`)

Note: `fundbt/`, `stratgate/`, and `calibration/` arrive from the starter kit with 32 passing offline tests — merged at repo root; keep them green. The criteria below are the integration bar on top.

- [ ] Purity lint covers `stratgate/`, `fundbt/`, and `calibration/`: free of LLM imports and wall-clock calls (`scripts/check_purity.py`).
- [ ] Starter-kit test suite green inside the fund repo (`make test` runs it).
- [ ] Only `run_backtest` is exposed to seats via MCP; `evaluate_holdout` and G2/G3/G4 evaluators are orchestrator-invoked only (test: seat toolbelt contains no evaluator tools).
- [ ] Trial registry unified: `fundbt/registry.py` writes to the fund DB; schema matches `specs/strategy-contracts.md` §2 (single source of truth).
- [ ] Spec enforcement: `run_backtest` without a registered `spec_id`, with config outside `param_ranges`, or beyond `search_budget` → refused, no stats, no trial row.
- [ ] Trial registry: every successful/errored run inserts exactly one `trials` row; identical `(config, data, engine, seed)` returns the cached result with **no** new row (assert N unchanged).
- [ ] Cost floors: sub-floor cost config refused; every result contains 2× and 3× cost reruns.
- [ ] Holdout quarantine: `run_backtest` cannot read the reserved months (data-slice test); G3 evaluator runs once — second attempt hits the `holdout_evaluations` PRIMARY KEY and resolves to REJECT `holdout_already_consumed`; the row is written on pass AND fail.
- [ ] Golden-strategy vector (`fixtures/golden-strategy.md`, from starter kit — canonical PASS + FAIL numbers): stratgate reproduces every frozen value and verdict; G2 boundary tests parameterized at each threshold (trades 99/100, Sharpe 0.49/0.50, WFE 0.29/0.30/0.50, DSR 0.94/0.95, drawdown 25%, cost share 50%, param cliff 40%).
- [ ] Lifecycle: illegal strategy transitions raise (state machine per `specs/strategy-contracts.md` §4); `REJECTED`/`RETIRED` terminal; revival requires new spec with `lineage_parent` and inherits family N in the DSR computation.
- [ ] Calibration: `calibration/` scoring per `specs/calibration.md` — abstains graded at p=0.5, ranking by total skill (shrunk BSS × n), PM weight floor at 0.5× mean, scoreboard job crash leaves last good board standing.
- [ ] Self-judging block: proposing seat attempting any gate-evaluator invocation or review of its own strategy → denied and logged.
- [ ] Allocation/kill under `SimClock`: 5% initial sleeve, ×1.5 per clean month capped at 20%; correlated sleeves (ρ>0.6) share a cap; −10% drawdown → halved + `probation`; −15% → `retired`; 60-day shadow Sharpe < 0 after ramp → `probation`, second 60 → `retired` — all automatic, same cycle.
- [ ] Shadow P&L: every paper fill reconciled against quoted spread at order time → `shadow_fills` row; G4 evaluation reads shadow P&L, never raw paper P&L (assert on fixture where they diverge).
- [ ] Contamination guard: a `llm_in_loop=1` spec's historical backtest runs only the coded rule (no agent turns in the run path); its G4 evidence window starts post-incubation.

## Definition of done, globally

`make test` green · `make sim-day` completes with all checkpoints `done` · no TODOs referencing live trading · purity lint clean (`gate/`, `stratgate/`, `fundbt/`, `calibration/`, `state/`, and `orchestrator/` free of LLM imports and wall-clock calls — `scripts/check_purity.py`).
