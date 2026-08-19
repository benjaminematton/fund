# Strategy Development Playbook — `specs/strategy.md`

How agents propose, test, validate, and earn capital for trading strategies. Companion to `specs/design.md` (architecture) and `specs/strategy-contracts.md` (schemas — canonical for this pipeline; trade-pipeline schemas stay in `specs/contracts.md`). The evidence behind every number here: `research/strategy-research-report.md`, with `research/strategy-research-addendum-2026-08.md` covering agent-driven discovery, adaptive-search correction, and the crowding decomposition. Where they overlap, the main report wins.

The design doc's core principle extends to research: **LLMs opine; code executes.** Here: **LLMs hypothesize; code validates.** The strategy gate below is the research-side twin of the risk gate in design.md §5 — pure Python, no LLM imports, CI-enforced.

---

## 0. Invariants — never violate

1. **The strategy gate is deterministic code.** `stratgate/` imports no LLM code. An agent cannot approve, score, or waive any check — including its own.
2. **No self-judging.** The seat that proposed a strategy never evaluates it. Validation verdicts come from the gate; qualitative review comes from a different seat (Risk Officer).
3. **Every trial is logged, forever, fund-wide.** Every backtest run by any agent — including abandoned ones — appends to the trial registry. N (the trial count) feeds the deflated Sharpe correction. An unlogged backtest is a firing offense; the backtest tool logs automatically so this cannot happen.
4. **Cost floors are not parameters.** The backtest tool imposes minimum per-side costs by liquidity bucket (§4). Agents cannot set costs below the floor. Any strategy pitched on sub-floor costs is invalid by construction.
5. **Contamination rule: LLM judgment is only evaluated on post-knowledge-cutoff data.** Historical backtests may only replay *deterministic coded rules*. An agent may write the rule; it may never "decide" on historical days its training data may contain. Live paper trading is post-cutoff by construction and is the primary evidence for any LLM-in-the-loop step.
6. **The holdout is touched once.** Each strategy's reserved holdout period is evaluated exactly one time, at Gate G3. A holdout evaluation is recorded in the registry whether it passes or fails; there is no re-roll.
7. **Default is REJECT.** Any error, missing data, or ambiguity in validation resolves to the strategy not advancing. (Twin of design.md invariant 4.)
8. **Allocation, ramps, and kill-switches are code.** Strategies earn and lose capital by rule (§6), never by argument.

---

## 1. Strategy lifecycle state machine

```
IDEA → SPEC → BACKTEST → VALIDATED → INCUBATING → ALLOCATED → { SCALED | PROBATION → RETIRED }
         │        │           │            │
         └────────┴───────────┴────────────┴──→ REJECTED (one-way; idea may be resubmitted as new SPEC with lineage link)
```

All transitions via the same compare-and-swap `transition()` helper as the trade pipeline. Each state's exit criteria are a gate (G1–G4 below). A strategy is a row in `strategies`; its full config is content-addressed (config hash = identity).

| State | Owner seat | Exit gate |
|---|---|---|
| IDEA | any analyst/researcher | PM sponsors → SPEC |
| SPEC | proposing seat | G1: pre-registration complete |
| BACKTEST | proposing seat (via backtest tool) | G2: statistical gate |
| VALIDATED | — | G3: holdout + robustness |
| INCUBATING | Ops (monitoring) | G4: paper-trading gate |
| ALLOCATED / SCALED | PM (sizing within rules) | continuous monitoring (§6) |
| PROBATION / RETIRED | gate (automatic) | §6 kill rules |

---

## 2. Gate G1 — Pre-registration (the spec)

No backtest may run without a registered spec. The spec is immutable once registered; changes create a new spec with a lineage link (and the old spec's trials still count toward family N).

Required fields (`submit_strategy_spec`, strict schema like `submit_signal`):

- **hypothesis** — the economic mechanism, ≤500 chars. Must answer: *why does this inefficiency exist, who is on the other side, and why don't they arbitrage it away?* Acceptable mechanism classes: behavioral bias, institutional constraint, risk premium, liquidity provision. "The backtest looks good" is not a mechanism.
- **family** — one of the registered families (§3) or a petition for a new one.
- **universe** — exact, point-in-time-constituent-based (e.g., "Russell 1000 members as of each rebalance date"), with liquidity bucket for cost floors.
- **signal rule** — deterministic, coded, parameterized. List every parameter and its *pre-declared* search range. The parameter search budget is declared here (max # of configurations).
- **holding period, rebalance frequency, expected turnover.**
- **exit + invalidation** — what closes a position; what observation would falsify the hypothesis.
- **capacity estimate** — at what AUM does this stop working?
- **predicted metrics** — expected net Sharpe, max drawdown, hit rate. (Prediction vs realization feeds the proposing agent's calibration score, same as signal confidence.)
- **LLM-in-the-loop?** — flag whether live decisions require LLM judgment (e.g., news scoring) vs pure code. If yes, invariant 5 applies: history may only be used for the coded parts; the LLM component's evidence must come from incubation.

---

## 3. Registered strategy families — the starting menu

Ranked by evidence quality × fit to our constraints (US equities, days–weeks holds, <$1M, Alpaca, long-bias practical). Realistic expectations already include the ~50% haircut on published/backtested figures.

### F1. Short-term mean reversion, liquid universe — **build first**
- **Mechanism:** liquidity provision — compensation for absorbing short-term selling pressure; strongest in turmoil.
- **Shape:** long oversold dips (e.g., short-run drawdown or RSI-style trigger) in Russell 1000-ish names **above a long-term trend filter** (200d MA), 1–7 day holds, limit-order entries.
- **Known edges/traps:** condition on turnover — reversal lives in LOW-turnover names; HIGH-turnover names show short-term momentum (Medhat–Schmeling). Naive versions die on costs in small caps; stay liquid. Reversal is a *mechanical* factor and crowds; crowded reversal carries ~1.84× the crash probability of uncrowded (16.9% vs 9.2%) because it bets against prevailing momentum. Crowding informs sizing and stops, never selection — see the addendum §3.
- **Realistic net:** Sharpe 0.6–1.0. **Cost floor bucket:** large (5 bps/side).

### F2. Small-cap event drift: earnings (PEAD) — **build second, carefully**
- **Mechanism:** underreaction in analyst-neglected small/micro caps; institutions can't deploy size here — our structural advantage.
- **Shape:** long positive-surprise small caps at next open after announcement, hold 2–6 weeks. Long-only (microcap borrow impractical). Earnings timestamps validated against SEC EDGAR 8-K acceptance times, never vendor BMO/AMC flags alone.
- **Known edges/traps:** contested academically — drift exists only in the bottom cap quintile (t≈1.4 without microcaps); expected drift 1–3% must clear 40–100+ bps/side spreads. Execution (patient limit orders, spread-aware sizing) is the whole game.
- **Realistic net:** Sharpe 0.3–0.7, small capacity. **Cost floor bucket:** small/micro (40–100 bps/side).

### F3. News/LLM-sentiment drift, small caps — **the LLM-native family; incubation-only evidence**
- **Mechanism:** slow diffusion of news into small-cap prices; negative news underreaction strongest.
- **Shape:** score fresh headlines (Alpaca/Benzinga feed) with a fixed prompt + fixed model version; long strong-positive small caps, days-scale holds. The scoring prompt/model is part of the config hash — model upgrades create a new strategy.
- **Known edges/traps:** invariant 5 in full force — historical "backtests" of LLM scoring are contaminated (published agents lose 51–72% of performance past cutoff). Validate the *pipeline* on history with a mechanical sentiment proxy if needed; validate the *LLM's* edge only in incubation. The one credible published edge here decayed from Sharpe 6.5→1.2 in three years — expect continued decay, monitor hard.
- **Realistic net:** unknown until incubated; assume modest. **Cost floor bucket:** small (40 bps/side).

### F4. Vol-managed momentum tilt, small caps — **slow sleeve**
- **Mechanism:** underreaction/herding; crash risk is the premium's cost — vol management roughly doubles realized Sharpe.
- **Shape:** long-only top-decile 12-1 momentum within small caps, monthly rebalance, position vol-scaling, market-state filter to sidestep rebound crashes.
- **Realistic net:** Sharpe 0.4–0.7 above market. **Cost floor bucket:** small (40 bps/side), but ~monthly turnover keeps cost drag low.

### F5. Overlays — not standalone strategies; free conditioning for F1–F4
- **Trend/regime filter** (TSMOM on index): gates F1 dip-buying and F4 exposure. **Turn-of-month:** schedule rebalances/entries into the last 4 + first 3 trading days tailwind. **Overnight tilt:** hold swing positions overnight; enter near close / via auctions; never pay the open on high-attention names.

### Deprecated at our horizon (agents should not spend trial budget here without new evidence)
Standalone overnight round-trips (spread eats the edge) · low-vol/quality as swing signals (use as universe filters only) · classic static pairs trading (decayed; borrow costs) · intraday anything (architecture and cost structure wrong for it).

---

## 4. The backtest tool — `run_backtest` (strict MCP tool)

Pandas reference engine by default (optional vectorbt adapter — same semantics), wrapped exactly like `submit_signal`: strict schema in, deterministic stats out. Sub-second daily-bar runs so research iterates fast; LEAN reserved for optional final validation of a VALIDATED strategy.

**The wrapper enforces (agent cannot override):**

1. **Registered spec required** — `spec_id` mandatory; config must lie within the spec's pre-declared parameter ranges; runs exceeding the spec's declared search budget are rejected.
2. **Cost floors by liquidity bucket** (per side): mega/large 5 bps · mid 15 bps · small 40 bps · micro 100 bps. Every result is also computed at 2× and 3× costs automatically.
3. **Pinned data snapshots** — immutable Parquet, SHA256-verified, survivorship-bias-free with PIT index constituents (Norgate/Sharadar). No network during a run.
4. **Purged walk-forward** — rolling IS/OOS (70/30) with embargo; single-period "one big backtest" results are reported but never gate-eligible.
5. **Holdout quarantine** — the most recent N months (default 18) are invisible to `run_backtest`; only the gate's one-shot G3 evaluator can touch them.
6. **Auto-logged trial registry row** — `{spec_id, config_hash, data_snapshot_hash, engine_version, seed, full stats, timestamp, seat}`. Idempotent: identical config hash returns the cached result without incrementing N.
7. **Determinism** — seeded, clock-injected, `hash(config + data + engine + seed)` keyed. Same call, same answer, forever.

**Returns:** net Sharpe (at floor / 2× / 3× costs), deflated Sharpe given family-wide N, walk-forward efficiency, max drawdown, # trades, turnover, cost share of gross P&L, parameter-neighborhood stats (±1 step on each parameter), exposure profile, trade ledger artifact.

---

## 5. Gate G2/G3 — the statistical validation gate (`stratgate/`)

Pure code. Numbers tunable by human commit only; defaults below.

```
G2 — statistical gate (runs on walk-forward results):
  trades          ≥ 100                          (else REJECT: insufficient_sample)
  span            ≥ 8 years or max data          (short-history specs need CEO waiver)
  net Sharpe      ≥ 0.5 at floor costs           (at 2x costs: ≥ 0.3, must stay positive at 3x)
  deflated Sharpe ≥ 0.95, N = family-wide trials (the multiple-testing kill shot)
  WFE             ≥ 0.5 (OOS/IS across windows)  (< 0.3 auto-REJECT: overfit)
  max drawdown    ≤ 25%
  cost share      ≤ 50% of gross P&L
  param surface   no neighbor config loses > 40% of Sharpe (cliff = curve-fit)

G3 — one-shot holdout + robustness (only for G2 passers):
  holdout net Sharpe > 0 AND within 50% of walk-forward Sharpe
  regime check: profitable (or flat) in ≥ 2 of 3 regime buckets (bull/bear/chop)
  Risk Officer hostile review (qualitative, logged; can flag but only CEO can waive a flag)
  → VALIDATED. Holdout result recorded regardless of outcome; no re-evaluation ever.

default on ANY error/missing data: REJECT (reason gate_error)
```

**Expectation-setting (enforced in reporting):** the PM plans capital using **half** the gate-passing Sharpe. If halved-Sharpe ≥ 0.4 still justifies a sleeve, proceed; otherwise the strategy is a paper tiger even having passed.

**Trial budget discipline:** ~45 independent configs is the most 5 years of daily data can support before max noise-Sharpe ≈ 1.0. Longer data buys more budget; the registry tracks family-wide N and the DSR check prices it in automatically. Agents should treat trials as scarce ammunition: fewer, hypothesis-driven configs beat sweeps — *more search mechanically raises your own bar.* Note the known limit: DSR assumes N draws from a fixed distribution, but an agent conditioning each spec on prior results is running a targeted search that DSR does not model. Online FDR (alpha-wealth per family) is the correctly-shaped replacement and is not yet implemented — addendum §2.

---

## 6. Gate G4 — incubation, allocation, and kill rules

**Incubation (paper, post-cutoff by construction):**
- Minimum **60 trading days** AND **30 closed positions** (whichever is later) per strategy.
- Pass criteria: live paper Sharpe within 1 std-error band of (halved) backtest expectation; realized slippage vs quoted spreads consistent with modeled costs; turnover within 25% of predicted; no invariant breaches.
- Remember Alpaca paper flatters fills (no slippage, no NBBO size check, random partials): Ops reconciles every fill against the quoted spread at order time and books *estimated true cost* in a shadow P&L. **The shadow P&L, not raw paper P&L, is the G4 evidence.**

**Allocation (pod-shop rules, deterministic):**
- Initial sleeve: 5% of equity. Ramp ×1.5 per clean month, cap 20% per strategy (interacts with design.md §5 position caps).
- Sleeve sizing rebalanced monthly by realized (shadow) Sharpe; correlated strategies (ρ > 0.6 of daily returns) share a combined cap.

**Kill rules (automatic, no debate):**
- Sleeve drawdown −10% from high-water: halve sleeve, PROBATION.
- Sleeve drawdown −15%: RETIRED. Post-mortem written by a *non-proposing* seat; lessons → journals.
- 60-day rolling shadow Sharpe < 0 after full ramp: PROBATION; another 60 days < 0: RETIRED.
- Invalidation event from the spec observed: immediate PROBATION + forced review.
- Retired strategies stay retired; a revival is a new spec with lineage (and inherited family N).

**Decay monitoring (Ops, weekly scoreboard):** rolling live-vs-expected Sharpe per strategy, cost-share trend, crowding proxies (family-relevant: e.g., LLM-sentiment edge decays with adoption). Published-anomaly base rate: −26% out-of-sample, −58% post-publication — decay is the default assumption, persistence the surprise.

---

## 7. Division of labor (maps to design.md seats)

| Seat | Role in strategy development |
|---|---|
| Analysts | Propose IDEAs/SPECs in their domain; run `run_backtest` within spec budgets |
| Bull/Bear researchers | Debate G2-passing strategies before G3 (same mechanics as ticker debates) |
| Risk Officer | Hostile review at G3; owns `stratgate/` parameter change proposals (human-committed) |
| PM | Sponsors SPECs, plans sleeves at halved Sharpe, sizes within §6 rules |
| Execution Trader | Unchanged — trades the *positions* the allocated strategies emit through the §5 risk gate |
| Ops | Incubation monitoring, shadow P&L, decay scoreboard, post-mortems |
| CEO (human) | Waivers, `stratgate/` threshold commits, new-family approvals, capital policy |

## 8. Honest priors (read before proposing anything)

Backtested Sharpe predicts live performance with R² < 0.025 across 888 real algorithms. 97% of persistent day traders lose money. Frontier LLMs trading live lost 30–63% in weeks in open competition, and published LLM-agent alpha mostly evaporates past the knowledge cutoff. We are not exempt. Our only real edges: capacity-constrained niches institutions can't touch, and a validation gate we never argue with. A fund running 2–3 uncorrelated sleeves at true net Sharpe 0.5–1.0 is a success. Most proposals should die at G2 — **that is the gate working, not failing.** The goal is not to pass the gate; the goal is to find the rare strategy that deserves to.
