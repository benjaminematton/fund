# Building Production-Grade Trading Strategies for an AI Hedge Fund

**Research report — July 2026.** Deep research across ~80 sources: academic papers (SSRN/arXiv/JF/RFS/JFE), practitioner research (AQR, Robeco, Alpha Architect, Quantpedia, Robot Wealth, Quantitativo), framework repos/docs, and vendor pricing pages. Companion deliverable: `specs/strategy.md` (the agent-facing playbook distilled from this report).

**Context:** US equities, daily/swing horizon (days–weeks), Alpaca paper trading, retail-scale capital (<$1M), long-running LLM agents as the research staff.

---

## Executive summary

1. **The backtest is the enemy, not the product.** The best empirical study on this — 888 real algorithms with live out-of-sample data — found backtested Sharpe predicts live performance with **R² < 0.025**, i.e. essentially not at all [Wiecki et al.]. Published anomalies decay **26% out-of-sample and 58% post-publication** [McLean & Pontiff]. With 5 years of daily data, trying just **~45 strategy configurations virtually guarantees** finding an in-sample Sharpe ≈ 1 whose true expectation is zero [Bailey et al.]. Everything in the playbook follows from these three numbers.

2. **LLM agents have a unique, disqualifying bias if unmanaged: their training data contains the backtest period.** "Profit Mirage" (2025) re-evaluated the flagship LLM trading agents (FinMem, FinAgent, QuantAgent, FinCON, TradingAgents — the systems your design doc builds on) and found **Sharpe decays of 51–62% and return decays of 50–72%** once tested beyond the LLM's knowledge cutoff. Most reported LLM-agent alpha is memorization, not skill. Live tests confirm: in Alpha Arena (2025), 4 of 6 frontier LLMs lost 30–63% of capital in weeks. **Mitigation is architectural: the LLM proposes; deterministic code evaluates, on post-cutoff or leakage-controlled data; and the LLM that designed a strategy never judges it.**

3. **What still plausibly works at your size and horizon**, ranked: short-term mean reversion in liquid names (best cost/edge trade-off); small/micro-cap event drift — PEAD and news/LLM-sentiment (small capital is a structural advantage here, but execution is the gate); vol-managed small-cap momentum; turn-of-month and trend filters as overlays. Overnight anomaly and low-vol/quality are real phenomena but the wrong tools at this horizon/cost structure.

4. **Infrastructure recommendation:** vectorbt as the agent's fast backtest oracle (deterministic, sub-second, headless) wrapped in a config-schema tool with **non-negotiable cost floors and a trial registry**; Norgate or Sharadar data to kill survivorship bias; Alpaca paper treated as an integration test, never a performance estimate (its fills have no slippage, no NBBO size checks, and random partial fills).

---

## Pillar 1 — How real quant shops develop and validate strategies

### 1.1 The lifecycle

Professional pipeline: **economic hypothesis → data → signal research → in-sample backtest → out-of-sample/walk-forward validation → paper incubation → small live allocation → ramped scaling with decay monitoring.**

The load-bearing rule is the *ordering*: the hypothesis comes **before** the data mining. A strategy must state, in advance, why the inefficiency exists, who is on the other side of the trade, and why they don't arbitrage it away (behavioral bias, institutional constraint, risk premium, liquidity provision). Arnott, Harvey & Markowitz's "A Backtesting Protocol in the Era of Machine Learning" (JFDS 2019) is the closest thing quant has to pre-registration and is directly adaptable to agents: specify the hypothesis before testing, log all trials and variants, cross-validate honestly, and treat data snooping at the *group* level — a strategy "discovered" after colleagues tried 100 variants inherits their trial count. For a fleet of LLM agents generating variants, this last point is critical: **the fund's trial count is the sum across all agents, forever.**

### 1.2 The overfitting apparatus (the numbers that matter)

| Tool | What it does | Key threshold |
|---|---|---|
| **Deflated Sharpe Ratio** (Bailey & López de Prado 2014) | Corrects observed Sharpe for selection among N trials + non-normality | DSR ≥ 0.95 to accept; requires logging every trial |
| **Haircut Sharpe** (Harvey & Liu 2015) | Multiple-testing haircut; nonlinear — marginal Sharpes → ~0 | Industry default: halve any backtested Sharpe |
| **t-stat hurdle** (Harvey, Liu & Zhu 2016) | 316 published factors; most likely false discoveries | t > 3.0, not 1.96 |
| **MinBTL** (Bailey et al., AMS 2014) | Min backtest length as a function of trials attempted | 5 yrs daily data supports ~45 independent trials before max noise-Sharpe ≈ 1 |
| **PBO via CSCV** (Bailey et al. 2015) | Probability the IS-best config underperforms OOS median | PBO < 0.2 preferred |
| **PSR / MinTRL** | Confidence that true SR > benchmark given sample size | ≥ ~100 trades before metrics mean anything |
| **Walk-Forward Efficiency** | OOS ÷ IS performance across rolling windows | > 0.5 pass, > 0.7 strong, < 0.3 = overfit |

Purged/embargoed k-fold CV (López de Prado, *Advances in Financial ML*) replaces standard k-fold for time series: purge training points whose label windows overlap the test window, then embargo ~1% of the sample after each test block. Combinatorial purged CV (CPCV) generates many backtest paths and is what enables PBO computation.

### 1.3 Biases and their mechanical prevention

- **Look-ahead:** signal uses data unavailable at decision time (e.g., trading on close with same-day close in the signal; using Q1 financials before the ~Apr 25 filing date). Prevention: point-in-time data keyed on *release* date; engine exposes only data timestamped ≤ decision time; lag fundamentals 90+ days if PIT data unavailable.
- **Survivorship:** delisted/bankrupt names vanish, inflating returns by roughly **1–4%/yr**, worst in small caps — exactly where your edge is supposed to live. Prevention: survivorship-bias-free data with delisting returns (Norgate, Sharadar) and point-in-time index constituents.
- **Data snooping:** tuning until it looks good. Prevention: pre-registered spec, trial registry, one-touch holdout, few parameters, and a smooth parameter surface (performance must not collapse when a parameter moves one step).
- **Restatement bias:** vendor fundamentals are silently corrected after the fact; backtests "know" numbers before they existed. Prevention: as-reported/PIT fundamentals (Sharadar SF1, SimFin publish-date indexing, or SEC EDGAR filing timestamps).

### 1.4 Transaction costs at retail scale

Good news: real costs are far below old academic assumptions. Frazzini, Israel & Moskowitz, using $1.7T of live AQR executions, measured a median of **~6 bps per rebalance** in large caps; retail orders (≪0.1% of ADV) pay effectively zero market impact — but the full spread and timing slippage are always payable.

Reasonable backtest floors at daily frequency (synthesized — no single canonical table exists):

| Universe | Per-side cost floor |
|---|---|
| Mega/large caps (top ~500) | 5 bps |
| Mid caps | 15 bps |
| Small caps | 40 bps |
| Micro caps | 100+ bps (spreads can exceed 500 bps) |

Standard stress test: rerun every backtest at **2–3× assumed costs**. A strategy that dies at 30 bps round-trip in liquid names was never real. Cost modeling is *the* deciding factor for the small-cap strategies in Pillar 2.

### 1.5 The backtest–live gap

- Wiecki et al. (Quantopian, 888 algorithms): backtest Sharpe → OOS performance **R² < 0.025**; and the more an author backtested, the *bigger* their IS/OOS gap — iteration itself is the contaminant.
- McLean & Pontiff (JF 2016): **–26% OOS** (pure statistical bias) and **–58% post-publication** (crowding) across 97 published predictors. Decay persists least in high-idiosyncratic-risk, low-liquidity stocks. Nuance: Jacobs & Müller find the US is the only market with reliable post-publication decay — US large caps are the most arbitraged pond on earth.
- Recent live-vs-backtest fund studies: volatility-adjusted returns drop **2–3 percentage points** from backtest to live.
- The widely repeated "95% of backtested strategies fail live" has **no traceable primary source** — treat as folklore; the verifiable evidence above is damning enough.

---

## Pillar 2 — Strategy families: what still works in 2025–2026 at your size

The cross-cutting finding: anomaly decay is real but concentrated in cheap-to-trade large caps. **Residual returns concentrate in small/micro caps and high-limits-to-arbitrage names** — institutions can't scale there, which is the strongest published support for "small capital is an advantage." The same illiquidity that preserves the anomaly imposes wide spreads, so execution skill is the gating factor throughout.

### Ranked for your constraints (daily/swing, <$1M, long-biased-practical)

**1. Short-term mean reversion in liquid names — ALIVE; best cost/edge trade-off.**
Naive academic reversal dies on costs ("excessive trading in small caps"), but with a large/mid-cap universe and turnover-aware construction it still nets **30–50 bps/week** (de Groot, Huij & Zhou, Robeco), and Robeco lists short-term reversal among factors that *kept working* 2010–2019. Returns are largely liquidity-provision compensation — best in high-VIX turmoil. Critical nuance (Medhat & Schmeling, RFS 2022): reversal holds in **low-turnover** stocks; **high-turnover** stocks exhibit short-term *momentum* — condition on turnover or the effects cancel. Practitioner variants (e.g., Quantitativo's dip-buying above the 200-day MA, ~5-day holds) report Sharpe ~1.1 with 21/22 positive years, but these are unrefereed blog backtests — haircut accordingly. **Realistic net expectation: Sharpe ~0.8–1.2 for a well-built long-biased dip-buyer in liquid names.**

**2. Small/micro-cap event drift (PEAD + news/LLM sentiment) — CONTESTED; the clearest small-capital advantage and the biggest execution trap.**
PEAD vanished from non-microcaps ~2001–2006 (Martineau, "Rest in Peace PEAD"). Two 2025 papers claim revival; Subrahmanyam's reconciliation (data through Dec 2024): earnings-drift factor **t = 2.18 with all stocks, t = 1.43 excluding microcaps** — the drift survives only in bottom-quintile-cap, analyst-neglected names. Expected drift ~1–3% over weeks must clear microcap spreads; long-side only (microcap borrow is impractical). LLM sentiment: Lopez-Lira & Tang's post-cutoff result (GPT headline scores predict next-day drift, strongest in small caps and negative news) is the one credible LLM edge — and its replication shows Sharpe decaying **6.5 (2021) → 1.2 (2024)** as adoption spread. Headline claims of Sharpe ≥ 3 in LLM-sentiment papers are unreliable (lookahead/memorization literature). **Realistic: modest positive alpha on long small-cap positive-surprise/news drift, decaying, execution-gated.**

**3. Cross-sectional momentum, vol-managed, small-cap tilt — ALIVE at ~half historical strength.**
Post-publication decay ~50%; US momentum earned ~+3.5%/yr in 2010–2019 vs ~8% long-run. Raw long-short momentum is crash-prone (–82% in 2009); volatility-managed momentum roughly **doubles the Sharpe** (Daniel & Moskowitz). Spreads are largest in small caps. Weeks-to-months rebalance makes this a slower sleeve. **Realistic: net Sharpe ~0.4–0.7 for a long-only vol-managed small-cap momentum tilt.**

**4. Overlays (not standalone edges): time-series momentum / trend filter, turn-of-month.**
TSMOM/dual momentum: real as risk management (truncates left tail), contested as alpha (Newfound showed hundreds of bps sensitivity to tiny spec changes in GEM); unlimited capacity means no small-capital edge. Turn-of-month persists in 19/20 countries, concentrated in the last ~4 + first ~3 trading days; modest (~0.5–0.8 Sharpe as an index-timing overlay) but nearly free to harvest. **Use both to condition entries and regime-gate the core strategies.**

**5. Wrong tools at this horizon (know why):**
- **Overnight anomaly:** robust gross (SPY 2020–2025: +47% overnight vs +30% intraday), but daily round-trips hand the entire edge to the spread — Alpha Architect: "Trading Costs Wipe Out the Overnight Return Anomaly." Use as an execution overlay (hold swing positions overnight; use auctions; avoid paying the open).
- **Low-vol/quality:** months-to-years signals; at swing horizon they add nothing directional. Use as **universe filters** (only buy dips in profitable, non-junk names).
- **Pairs/stat arb:** decayed post-2002 and again post-2016; survives only in adaptive, regime-aware forms, and microcap shorting costs bite. Lower priority.

---

## Pillar 3 — Infrastructure: backtesting for autonomous agents

### 3.1 Framework choice

| Framework | Verdict |
|---|---|
| **vectorbt** (OSS; PRO paid) | **Primary pick.** Active (last push Apr 2026). Vectorized/Numba, ~1000× faster than backtrader in benchmarks — sub-second daily-bar backtests let agents run hundreds of variants. Deterministic given pinned data. Headless, pure function calls. PRO adds purged walk-forward CV. You supply survivorship-bias-free data and enforce costs in the wrapper. |
| **backtesting.py** | Runner-up for simplicity — active again (v0.6.x 2025+), tiny API, one stats object out. Single-instrument only; AGPL. |
| **QuantConnect LEAN** | Best reality modeling (PIT corporate actions, delisted names, borrow costs, first-class walk-forward) but heavy and slow — use as a **final-validation stage**, not the agent's inner loop. |
| **NautilusTrader** | Explicitly deterministic event-driven engine; overkill for daily equities, strong if you ever go intraday. |
| **qlib** (Microsoft) | Only OSS option with a true point-in-time DB; what RD-Agent(Q) uses as its backtest oracle. Choose if agents pivot to ML factor mining. |
| **backtrader / zipline-reloaded / bt** | Avoid as foundations: unmaintained since 2024 / decayed data ecosystem / niche. |

**Alpaca paper fidelity — treat as integration test only:** paper fills simulate against real-time quotes without routing; quantity is *not* checked against NBBO size; partial fills injected randomly ~10% of the time; effectively zero slippage; documented fill delays of 50–260s on marketable limit orders. Paper P&L will systematically flatter any strategy, worst for small caps. Do slippage stress-testing in the backtester, and reconcile paper fills against quoted spreads.

### 3.2 Data stack (verified pricing, July 2026)

| Budget | Stack |
|---|---|
| **$0/mo** | Alpaca free (SIP-sourced historical daily bars ~7 yrs, corporate actions API, Benzinga news archive to 2015) + Tiingo Starter (30+ yr EOD, 500 symbols/mo) + SEC EDGAR XBRL APIs (free PIT fundamentals + exact 8-K timestamps, 10 req/s) + Finnhub free (earnings calendar, 60 calls/min). Accept: survivorship bias, ~7-yr window. |
| **~$50/mo** | Add **Norgate US Stocks Platinum (~$630/yr)**: survivorship-bias-free daily prices to 1990 **+ historical index constituents** — this single purchase fixes the two worst backtest biases. |
| **~$200/mo** | Add Alpaca Algo Trader Plus ($99/mo, full SIP) + Sharadar Core US Bundle via Nasdaq Data Link (PIT fundamentals SF1 to 1990, delisted-inclusive prices; price login-gated, ballpark $40–60/mo) or FMP Premium ($59/mo, earnings calendar; restated-data caveat). |

For PEAD specifically: budget vendors' historical BMO/AMC flags are often wrong — validate announcement timing against SEC EDGAR 8-K acceptance timestamps (free, exact to the second). yfinance: exploratory cross-checks only — rate-limited, survivorship-biased, no API contract.

### 3.3 The LLM contamination problem and its mitigations

This is the section your fund lives or dies on.

**Evidence:** "Profit Mirage" (arXiv 2510.07920): flagship LLM agents' Sharpe decays 51–62% beyond knowledge cutoff. Sarkar & Vafa (ICML 2025): prompted about Sept–Nov 2019 earnings calls, Llama 2 mentions Covid-19 in >25% of cases — pretraining contains the future and prompting cannot suppress it. "The Memorization Problem" (arXiv 2504.14765): memorized outcomes make forecasting ability non-identified. TradingAgents' own release notes patch "backtesting date fidelity" issues; its claim of "no look-ahead bias" addresses prompts, not weights. Live: Alpha Arena Season 1 — 4/6 frontier LLMs lost 30–63% in 2.5 weeks, P&L dominated by overtrading costs. Agent Market Arena: framework risk-style drives outcomes more than model intelligence.

**Converged mitigations:**
1. **Post-cutoff evaluation only** — any LLM-designed strategy's decision-quality is measured only on data after the model's knowledge cutoff (paper trading is post-cutoff by construction — your forward-running design is accidentally the correct instrument).
2. **Mechanical signals for history** — backtests over pre-cutoff periods may use only deterministic, coded rules (the LLM writes the rule; code replays it; the LLM never "decides" on historical days it may have memorized).
3. **Anonymization** as defense-in-depth (mask tickers/dates when LLMs analyze historical episodes) — imperfect, models can re-identify.
4. **Separate judge** — self-preference bias is documented (arXiv 2410.21819); the agent that designed a strategy never evaluates it.
5. **Deterministic gates in code the LLM cannot edit** — the WorldQuant/Alpha-GPT pattern: LLM proposes alpha expressions; a deterministic backtester computes stats and pass/fail. The strongest verified use of LLMs in quant is as *idea generators behind gates* (Alpha-GPT top-10-of-41,000-teams result; Microsoft RD-Agent using qlib as oracle), not as autonomous traders.

### 3.4 Determinism and reproducibility for the backtest tool

Pin data snapshots as immutable Parquet with recorded SHA256; seed any probabilistic component explicitly; inject the clock (your design already does this); no network during a run except a frozen read-only cache; key results by `hash(strategy_config + data_snapshot_hash + engine_version + seed)` — which also gives agents idempotent backtest calls for free; log a full artifact (trade ledger, engine version, code hash) with every run.

### 3.5 Allocation discipline (what multi-strategy funds do, translated)

Pod shops size capital sleeves by track record and cut them on drawdown breaches (widely reported convention: ~–5% halves the sleeve, ~–7.5–10% terminates it), and PMs need multi-year track records to get hired. Translation: each strategy is a "pod"; the platform's incubation clocks, allocation ramps, and kill-switches are deterministic code, never LLM judgment. New strategies incubate on paper (your 30-day burn-in is the floor, not the target), deploy small, and earn size only as live behavior matches backtest expectations (Sharpe, turnover, slippage vs modeled).

### 3.6 Base rates (calibrate expectations)

Chague et al.: of Brazilian day traders persisting 300+ days, **97% lost money**. Barber & Odean: active retail underperforms ~6 pp/yr. Short-horizon US equity alpha is the most institutionally crowded space in finance. Your realistic advantages are exactly two: **capacity-constrained niches** (small/micro caps institutions can't touch) and **discipline** (the deterministic validation gate). Model access is commoditized. A portfolio of 2–3 modest, uncorrelated, net-Sharpe-0.5–1.0 sleeves that survive honest validation is a *good* outcome; expect the large majority of agent-proposed strategies to die at the gate — that is the gate working.

---

## Sources (primary)

**Validation methodology:** Bailey & López de Prado, Deflated Sharpe (SSRN 2460551) · Bailey, Borwein, López de Prado & Zhu, PBO/CSCV (SSRN 2326253) · Bailey et al., "Pseudo-Mathematics and Financial Charlatanism," MinBTL (AMS Notices 2014: ams.org/notices/201405/rnoti-p458.pdf) · Harvey, Liu & Zhu, "...Cross-Section of Expected Returns" (SSRN 2249314) · Harvey & Liu, "Backtesting" (SSRN 2345489) · Arnott, Harvey & Markowitz, "Backtesting Protocol in the Era of ML" (SSRN 3275654) · Wiecki et al., "All That Glitters Is Not Gold" (SSRN 2745220) · McLean & Pontiff (SSRN 2156623) · Frazzini, Israel & Moskowitz, "Trading Costs" (SSRN 3229719) · López de Prado, *Advances in Financial Machine Learning* (purged CV).

**Strategy families:** de Groot, Huij & Zhou, reversal costs (SSRN 1605049) · Medhat & Schmeling, short-term momentum (SSRN 3795253) · Martineau, "Rest in Peace PEAD" (SSRN 3111607) · Subrahmanyam, PEAD reconciliation (SSRN 5930255; UCLA Anderson Review) · Daniel & Moskowitz, momentum crashes (JFE 2016) · Blitz/Robeco, "Factor Performance 2010–2019" (SSRN 3562242) · Lopez-Lira & Tang (arXiv 2304.07619) + Modern Finance replication (mf-journal.com/article/view/327) · Alpha Architect on overnight-anomaly costs · Quantpedia (turn-of-month, reversal, PEAD, TSMOM) · Newfound, GEM fragility · Quantitativo mean-reversion posts (unrefereed) · Jacobs & Müller, global decay (JFE).

**LLM agents & contamination:** "Profit Mirage" (arXiv 2510.07920) · Sarkar & Vafa, lookahead bias in LLMs (SSRN 4754678, ICML 2025) · "The Memorization Problem" (arXiv 2504.14765) · TradingAgents (arXiv 2412.20138) · AlphaAgent (arXiv 2502.16789) · Alpha-GPT (arXiv 2308.00016, 2402.09746) · Microsoft RD-Agent (github.com/microsoft/RD-Agent) · Agent Market Arena (arXiv 2510.11695) · Alpha Arena (nof1.ai; protos.com) · Self-preference bias (arXiv 2410.21819) · Chague et al., day trading (SSRN 3423101).

**Infrastructure:** vectorbt (github.com/polakowo/vectorbt; vectorbt.pro) · backtesting.py (github.com/kernc/backtesting.py) · QuantConnect LEAN reality-modeling docs · NautilusTrader backtesting docs · qlib PIT docs (qlib.readthedocs.io) · Alpaca paper-trading docs (docs.alpaca.markets/us/docs/paper-trading) + forum fill-delay threads · Norgate (norgatedata.com) · Sharadar via Nasdaq Data Link / QuantRocket · Tiingo, EODHD, FMP, Alpha Vantage, Databento pricing pages (fetched July 2026) · SEC EDGAR developer docs.

**Flagged as contested/unverifiable:** the "95% fail live" figure (folklore); PEAD outside microcaps (Martineau vs Dickerson et al. vs Subrahmanyam); LLM-sentiment Sharpes ≥ 3 (contamination); dual momentum's OOS record; institutional "Sharpe < 0.5 never deployed" (blog claim); synthesized retail cost bands (composite, no canonical table); Massive/Polygon and Sharadar exact current prices (JS/login-gated).
