# Phase 2 — The desk (PM + 2 analysts + real gate) — design

Status: **validated** (brainstorming, 2026-07-12). Next: `writing-plans`.
Scope owner made every design call in-session; this doc records the decisions, not options.

Companion canon (authoritative where they overlap): `specs/design.md` §5 (gate math),
`specs/contracts.md` (schemas, state machines, §4 tools, §5–6 failure semantics),
`specs/acceptance.md` Phase 2, `fixtures/golden-day.md` (frozen test vector), `CLAUDE.md`
(7 invariants). This doc adds nothing that contradicts those; where it extends them it says so
and names the canonical file that must be edited (by human commit) before the code.

## 0. The five decided forks (why this design looks the way it does)

1. **Risk math is the tiered gate, no Kelly.** `grep` confirms no `kelly`/`edge` input exists in
   the repo or spec. Sizing is decision-independent given ticker+side (that is what lets advisory
   and enforcement share one code path). The invariant set (§3) is about tiers, multipliers, caps,
   and the REJECT-on-malformed default — not Kelly.
2. **Async fills: FakeAlpaca models `accepted→filled`, and a dedicated reconciliation stage runs
   the same code in sim and live.** Highest fidelity; closes the exact sim≠live bug class.
3. **Conformance offline oracle = captured cassettes.** The `@live` arm captures real responses;
   offline, FakeAlpaca + `mcp_envelope` must reproduce them. A divergent re-capture is the alarm.
4. **Sim/live invariant = order-request identity at the gate→broker boundary.** Fills/timing may
   diverge; the request payload may not.
5. **Tool surface = one canonical `contracts.md` §4, written before the handlers**; calibration in
   Phase 2 stops at resolution *data* (scoring→PM-weights is Phase 5).

## 1. Scope & non-goals

**In scope.** Deterministic risk gate (`gate/risk.py`, tiered) + market-features provider
(`market/features.py`); PM + fundamentals + technical seats; canonical §4 tool surface;
async-fill `FakeAlpaca` + reconciliation stage; FakeAlpaca conformance suite (cassette oracle);
sim/live order-request-identity harness; `state/journal.py` + reflection→`resolutions`; full
sim-day; carried-forward repair sweep (conn-leak, drain-ordering guard).

**Out of scope (Phase 3+).** Debate mechanics; Critic seat and `submit_critique`;
news/macro/ops/CEO/risk-persona seats; CEO approval gate; calibration scoring → PM weights
(Phase 2 produces the *input* `resolutions` data only). The Decision stage runs as a **single
turn**: with no critic seat configured, the orchestrator inserts `clear`/`no_critic_seat` rows at
stage start (contracts §4), and `submit_decision`'s draft→critique ordering guard is satisfied by
that inserted row.

**Unchanged invariants.** All 7 in `CLAUDE.md` bind every task. Notably: paper only; only the
Execution Trader seat holds `trading`; `gate/` imports no LLM code and no wall-clock; default is
HOLD/REJECT on any error; orders idempotent on `client_order_id`; SQLite is truth, Slack a
projection; agents emit structured data only via strict-schema MCP tools.

## 2. Risk gate architecture

### 2.1 Purity boundary (two modules)

- **`gate/risk.py` — pure.** A pure function over a validated inputs object:
  `size(inputs: GateInputs, mode: Literal["advisory","enforce"]) -> Sizing`, where `Sizing` is
  either an allowed-actions result `{buy, sell, hold}` (advisory) / a mintable ticket spec
  (enforce), or a `Reject(reason)`. No LLM import, no `datetime.now()`, no `time.sleep()`, no I/O,
  no RNG. Purity-linted by `scripts/check_purity.py` (add `gate/risk.py` to its coverage).
- **`market/features.py` — non-pure, isolated.** Computes 60d annualized vol and average
  correlation-vs-book from price history and assembles `GateInputs` (equity, cash, positions,
  price, daily P&L, per-sector weights, held_qty, vol, avg_corr) from Alpaca (live) or frozen
  fixtures (sim). The gate never reaches a data source; the provider never sizes. This split is
  what keeps `gate/` purity-clean while the numbers it needs come from the network.

`market/` is a **new top-level package**. It may import Alpaca read clients; it must not import
from `agents/` or from `gate/` (one-way dependency: `market/` → data; `gate/` ← plain `GateInputs`).

### 2.2 Sector map

Static committed `config/sectors.yaml` maps `ticker → sector`, injected into the gate as plain
data. Human-changed only, exactly like gate thresholds (never by an agent). A ticker with **no**
sector entry → REJECT `gate_error` (fail-closed; never treated as "unknown / 0% weight"). Sim and
live read the same file, so the sector cap is replay-stable and deterministic.

### 2.3 One computation, two modes

The same `size()` runs in **advisory** mode (08:45 pre-gate and the 11:00 PM-input snapshot —
produces `{buy: max_qty, sell: held_qty}` per ticker, mints no ticket) and **enforcement** mode
(11:15 — identical computation on live data, mints the ticket with a +45-min `expires_at`). Given
identical `GateInputs`, the two modes return an identical `max_qty` (pinned as invariant §3.9).
They may differ across the day only through genuine price/account drift between snapshots.

### 2.4 The four steps (boundaries pinned)

Per `specs/design.md` §5 and the `fixtures/golden-day.md` worked math, with boundary inclusivity
made explicit (the spec's prose ranges are ambiguous at the edges; these are the pinned rules and
match the golden-day and the acceptance boundary tests 14.9/15/49.9/50):

```
step 1  60d annualized vol → base position limit (% of equity)
        vol < 15%           → 25%
        15% ≤ vol ≤ 50%     → 20%      (golden-day: 42% → 20%)
        vol > 50%           → 10%
step 2  avg correlation vs existing positions → multiplier
        corr ≥ 0.8          → 0.70×
        0.6 ≤ corr < 0.8    → 0.85×
        0.4 ≤ corr < 0.6    → 0.95×    (golden-day: 0.55 → 0.95×)
        0.2 ≤ corr < 0.4    → 1.00×
        corr < 0.2          → 1.10×
step 3  price → max shares = floor(dollar_limit / price); cash cap → {buy, sell, hold}
step 4  firm limits:
        position count ≤ 8                         (hard breach → REJECT)
        sector weight ≤ 60% POST-TRADE             (soft breach → resize down to cap)
        daily-loss circuit breaker ≤ −3%           (hard breach → REJECT all buys, firm-wide)
output  advisory: {buy, sell, hold}   |   enforce: APPROVED ticket | REJECTED {reason}
default on ANY error / NaN / inf / missing / wrong-type input: REJECT (reason gate_error) → HOLD
```

Soft-cap breach (sector, vol dollar-limit) → the gate itself computes the capped qty (the golden
"one resize retry", 105→67 shares), no LLM round-trip. Hard breach (position count, circuit
breaker) → REJECT.

### 2.5 HOLD-only skip

The 08:45 pre-gate computes advisory allowed-actions for every watchlist/position ticker. Any
ticker whose result is `{buy:0, sell:0}` is **dropped from the day's active set before any research
turn** — acceptance asserts **zero agent turns** are spent on it (cost control + removes temptation).

## 3. Risk-math invariant set (property tests via `hypothesis`)

`hypothesis` is added as a **test-only** dependency in `pyproject.toml`. Strategies generate over
the full input space (vols, correlations, prices, equity, cash, positions, sector weights, daily
P&L, held_qty). The Kelly-framed invariants from the kickoff (monotonicity in edge, zero-edge
limit, fractional-vs-full Kelly ordering) **do not apply** — the gate has no edge input. The set
below is the tiered-gate replacement; items marked ★ were **not** in the kickoff list and are
argued for.

1. **Vol-tier non-increasing.** Higher vol ⟹ base-limit % never rises. Boundary cases 14.9 / 15 /
   49.9 / 50 / 50.1.
2. **Correlation-multiplier non-increasing** in correlation. Boundaries 0.2 / 0.4 / 0.6 / 0.8
   (lower-inclusive per §2.4).
3. **Caps hold under ALL inputs (master property).** For every generated input, the output
   `max_qty·price` simultaneously respects the vol dollar-limit, cash, post-trade sector cap, and
   position count — or the gate REJECTs. No input yields a qty that breaches any cap.
4. **Cash never over-spent.** A buy's `max_qty·price ≤ available cash`.
5. **Scale homogeneity. ★** Tier and multiplier selection depend only on unitless vol% and corr,
   so they are scale-free; dollar exposure is homogeneous degree 1 in equity. Scaling equity and
   price by a common k leaves the integer share count unchanged (modulo the floor); scaling equity
   alone by k scales the dollar limit by k. A violation means a unit/absolute-threshold bug.
6. **Integer floor, rounded DOWN. ★** `max_qty` is always a non-negative integer, floored not
   rounded. One share over a boundary tips the 60% sector cap or spends cash the account lacks
   (67 vs 68 is a money bug). The kickoff's "caps hold" implies this but the rounding *direction*
   must be asserted independently.
7. **Fail-closed on malformed / NaN / inf / missing / wrong-type. ★** Explicit **NaN and inf**
   cases, not just missing keys: `NaN < 0.15` is `False`, so a NaN vol silently slips into a tier
   instead of rejecting — the archetypal wrong-but-green. Any NaN/inf/negative-price/wrong-type ⟹
   REJECT `gate_error`, never a guess or a partial size (invariant 4).
8. **Sell ≤ held (long-only). ★** The gate never authorizes a sell exceeding the held position;
   an accidental over-sell is a short, violating the long-only invariant. The kickoff's Kelly
   framing omitted the sell side entirely.
9. **Advisory ≡ enforcement. ★** Identical inputs ⟹ identical `max_qty` across the two modes.
   Load-bearing: HOLD-skip and the PM-input snapshot both trust that the 08:45/11:00 advisory
   numbers equal what enforcement would compute; a divergence spends LLM turns on a ticker that
   will be rejected, or drops one that would have been live.
10. **Monotone in headroom. ★** Adding cash or equity, all else equal, never *decreases* `max_qty`.
    A sign/logic-bug detector.
11. **Circuit breaker is firm-wide. ★** Daily P&L ≤ −3% ⟹ **no** buy ticket mints for *any*
    ticker — a property over the whole active set, not a per-ticker check.
12. **Post-trade cap evaluation. ★** Caps are computed on the *resulting* book; the resize solves
    for the largest qty keeping post-trade sector weight ≤ cap (golden-day 105→67). Evaluating the
    *pre-trade* book would pass 52% and place the order that pushes the book to 62.4%.
13. **Determinism / purity.** Same inputs → same output; no clock, no RNG, no I/O (purity lint
    plus a determinism property that calls `size()` twice and asserts equality).

Example-based anchors alongside the properties: the **golden-day vector** (max_qty **67**, with the
105 pre-sector-cap intermediate asserted as a distinct step value), and the acceptance boundary
grid (vol tiers, correlation multipliers, cash cap, position count, sector cap, breaker).

## 4. Async fills + reconciliation stage

### 4.1 FakeAlpaca async model

`FakeAlpaca.place_order` acks with `status="accepted"`, `filled_qty="0"`, `filled_avg_price=null`
(matching the real market-order ack). A `tick()` / poll advances an accepted order to `filled`
(and can express `partially_filled` for the conformance suite). The instant-`filled` fiction is
removed from the default path. `mcp_envelope` continues to wrap the broker dict as the real
alpaca-mcp-server does (JSON string, `data`, `_alpaca_mcp_security`, string numerics).

### 4.2 Reconciliation stage

A **new `reconciliation` checkpoint stage runs immediately after execution.** For each order in
`submitted`, it re-queries the broker via `get_order_by_client_order_id` and drives
`submitted → filled` (with the fill event and `decision → approved→executed`) or
`submitted → partially_filled → filled`. This is where the fill-side recorder logic now lives; the
PostToolUse ack turn only records the `submitted` order row and consumes the ticket (as today).
**The same reconciliation code runs in sim and live** — that is the fidelity guarantee. Idempotent
under retry via CAS, like every stage.

### 4.3 Golden-day fixture edit — 🔏 human-authored

Moving the fill to the reconciliation stage changes the golden-day fill-event timing (currently the
11:30 ack fills instantly). The frozen fixture must be updated to show the fill landing at the
reconciliation stage rather than the ack. **Per the test invariant ("never update a golden fixture
to make a test pass — STOP and ask"), the planning/execution agents will not touch
`fixtures/golden-day.md`;** the exact lines to change will be flagged and the human authors the edit.

## 5. FakeAlpaca conformance suite

One test file, parameterized over `[FakeAlpaca + mcp_envelope, real paper Alpaca]`, running the
**same assertions** on both arms; the real arm is marked `@pytest.mark.live` and excluded from
`make test`. The **offline oracle is captured cassettes** in `fixtures/alpaca/` (extending the
existing `place_stock_order.json`): the `@live` arm captures real responses; offline, FakeAlpaca +
`mcp_envelope` output must reproduce the captured shape. A `@live` re-capture whose shape diverges
from the cassette is the drift alarm — the guard all five live bugs slipped past.

Behaviors covered (each an assertion that runs identically on both arms):

1. **Place-ack envelope shape** — JSON string wrapping `data` under `_alpaca_mcp_security`;
   `qty`/`filled_qty` string-typed; `filled_avg_price` null until fill; `order_class` `""` for a
   simple order, `"oto"` for a stop exit. (BUG A/B origin.)
2. **Accepted-not-filled** — a fresh market order acks `status="accepted"`, `filled_qty="0"`,
   `filled_avg_price=null` (NOT filled). (BUG B / async.)
3. **Async full fill** — after `tick()`/poll, `get_order_by_client_order_id` reports
   `status="filled"`, `filled_qty==qty`, `filled_avg_price` set.
4. **Partial fill** — `status="partially_filled"`, `0 < filled_qty < qty`; shape + recorder
   handling. (Unverified today.)
5. **Duplicate `client_order_id` → 422** — Alpaca `{code, message}` with **no** `error` key;
   recorder skips it (`_extract_order` → None); the ticket is not consumed a second time
   (contracts §5.1 idempotency).
6. **Other rejections** — insufficient buying power, unknown symbol → `{code, message}`; recorder
   records nothing; pipeline defaults to HOLD. (Shapes currently unverified.)
7. **oto stop order** — `order_class="oto"` with a single stop leg places (parent + held stop leg);
   long sell-stop min-distance rule `stop_price ≤ base_price − 0.01`. (BUG D.)
8. **Bracket-without-take_profit → 422** — the fiction stays rejected offline and live. (BUG D
   guard.)
9. **qty-as-string on input** — the place tool sends `qty` as a string ("1"); gate and recorder
   accept it. (BUG C.)
10. **filled_avg_price typing** — string on the wire, null before fill; recorder coerces. (BUG A/B.)

This suite is *response* fidelity; §6 is *request* determinism. Together they bound the exact
divergence surface the five bugs lived on.

## 6. Sim/live divergence harness

**Invariant: order-request identity at the gate→broker boundary.** The exact validated `place_*`
`tool_input` — `client_order_id`, `symbol`, `side`, `qty`, `order_class`, and the stop leg — must
be **byte-identical** between a sim-day run and a `@live` run over the **same recorded decisions and
same frozen market data**. Fills, order status, and timing may legitimately diverge (that is §4/§5's
territory); the request the gate authorizes may not.

**Mechanism.** A tap at the PreToolUse boundary records each validated request payload. A sim-day
writes a request manifest; the `@live` run asserts its captured requests equal the manifest. All
five live bugs were response-side — the request-side identity is the invariant proving the
gate/decision→request path produces the same order regardless of how the broker answers.

## 7. PM/analyst tools — canonical §4, written first — 🔏 spec edit before code

`contracts.md` §4 becomes the **single canonical enumeration of every fund-server MCP tool**, each
with a strict schema, **committed before the handlers exist**. It gains:

- `submit_signal` (analyst seats only) — already specified.
- `submit_decision` (PM only) — already specified.
- `list_open_tickets` — retroactively added (shipped in Phase 1 without a §4 entry — the gap this
  decision closes).
- Read tool(s) Phase 2 needs (the allowed-actions snapshot query).

A test asserts **the served tool set equals the §4 enumeration** — no tool exists without a
canonical entry. Seat-restriction is enforced per §4 (wrong-seat caller → tool error). The PM's
allowed-actions snapshot is delivered as **stage input**, not prompt text: acceptance asserts on the
rendered stage input, and no per-run values go into prompts (replay-stability, CLAUDE.md).

## 8. Journals + reflection → resolutions (calibration = data only)

- **`state/journal.py` (new)** — the single sanctioned writer of per-agent markdown journals
  (`CLAUDE.md`: "Do not write journals except through `state/journal.py`"; it is referenced but does
  not yet exist). Each participating seat writes one entry per sim day.
- **Reflection job at `SimClock` + 5 trading days** — writes `resolutions` rows (`realized_return`,
  `alpha_vs_spy`) from frozen fixture prices (golden-day: NVDA $191.20, SPY +1.1% over the window →
  +6.14% realized, +5.04pp alpha). This is the calibration *input*; scoring → PM weights is Phase 5.
- **Cost rows** per seat per session via `record_cost` (the `costs` table exists;
  `ResultMessage.total_cost_usd` labeled "est.").

## 9. Expiry guard test (Task-5 non-atomicity — argument settled)

**Argument (safe).** `gate/tickets.py:validate_order` denies on wall-clock `_expired(expires_at,
now_iso)` **independently of the ticket status flag**. A ticket past `expires_at` is denied even
while still `status='open'`, so no order is ever authorized against a dead ticket. The Task-5
two-CAS window (ticket `open→expired` committed, decision `approved→expired` not yet) only strands a
`decision` row at `approved` — a cosmetic/projection wart, not an authorization gap, because
authorization gates on time, not on the sweep having run. The **only** way the window becomes
exploitable is if a future change "optimizes" `validate_order` to trust the status flag and drops
the independent time-check.

**Action.** Add a **load-bearing guard test**: a ticket whose `expires_at` is in the past but whose
`status` is still `'open'` → `validate_order` denies; a mutation that removes the `_expired` check
turns the test red. **No atomicity fix** — the non-atomicity is documented safe (matches the opus
final-review verdict: trading safety unaffected).

## 10. Repair sweep (carried-forward Phase-1b Minors)

- **Conn-leak.** `conn_factory` currently opens a never-closed SQLite connection per hook call — a
  handle leak that compounds over a full sim/live day in a long-lived seat. Cache and close the
  connection per turn.
- **Drain-before-done ordering guard.** The current `test_execution_stage.py` drain assertion would
  pass under the old (buggy) done→drain ordering too. Add a white-box regression guard that fails
  under the old ordering (crash injected in the done→drain window), making inv-6's projection-flush
  guarantee actually load-bearing.

## 11. Orchestrator daily cycle

New/changed checkpoint stages (each a state machine; transitions via `state.transition` CAS; clock
injected):

```
08:45  pre-gate       advisory sizing for every watchlist/position ticker;
                      {buy:0, sell:0} tickers DROPPED from the active set (zero agent turns)
09:00  research       fundamentals + technical submit_signal;
                      analyst that misses the deadline → neutral/0 "no report" row auto-inserted
11:00  decision       single turn (clear/no_critic_seat inserted at stage start);
                      PM submit_decision; no call by deadline → hold/0 + pm_timeout event to #risk
11:15  gate           enforcement mode; mint APPROVED ticket (+45-min expiry) | REJECT reason to #risk
11:30  execution      existing: gate-hooked place, idempotent on client_order_id
       reconciliation  NEW: re-query broker per submitted order; submitted→(partially_)filled,
                      fill event, decision approved→executed; same code sim + live
T+5    reflection     journals + resolutions (realized_return, alpha_vs_spy)
```

All checkpoints reach `done` in a full sim-day. Cost rows recorded per seat per session.

🔏 **acceptance.md delta (human-approved wording).** Phase 2's checklist gains explicit lines for:
the FakeAlpaca conformance suite (§5), the sim/live order-request-identity harness (§6), and the
reconciliation stage terminal-state assertions (§4). These are **additions**; no existing
acceptance line is weakened or removed.

## 12. Testing strategy → acceptance mapping

- Gate unit + property tests (`hypothesis`) → acceptance "Gate unit tests (pure, exhaustive)" +
  "any malformed/NaN/missing input → REJECT gate_error".
- Golden-day vector (67, with 105 intermediate) → "Golden-day vector".
- Advisory-mode + HOLD-skip tests → "Pre-gate (advisory mode)" + "HOLD-only skip".
- Sim full day (replayed decisions, real execution incl. reconciliation) → "Sim full day".
- Missing-signal / PM-timeout defaults → their acceptance lines.
- PM stage-input snapshot assertion → "PM inputs".
- Journals + reflection resolutions + cost rows → their acceptance lines.
- Conformance suite (cassettes; `@live` re-capture) → new acceptance line (§11 delta).
- Order-request-identity harness → new acceptance line (§11 delta).
- Expiry guard, conn-leak, drain-ordering guard → repair-sweep tests (regression guards).

Global done: `make test` green offline (no network, no keys); `make sim-day` completes with all
checkpoints `done`; purity lint clean with `gate/risk.py` added to its coverage (LLM-free +
wall-clock-free). `market/` sits **outside** the purity-linted gate boundary because it performs
data I/O; it is nonetheless constrained to import no LLM code and to take its "as-of" date from the
injected `Clock` (never `datetime.now()`), so it stays replay-stable. Whether `market/` is also
added to the LLM-import lint is a plan-level call; it is never added to the pure-gate boundary.
