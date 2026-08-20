# Addendum — agent-driven strategy discovery (August 2026)

Supplement to `research/strategy-research-report.md`, which remains the canonical evidence base. This file carries **only** what the main report does not already cover. Where the two touch the same topic, the main report wins.

Scope: how LLM agents are being used to *generate* strategies, and two validation questions the main report raises but does not resolve.

---

## 1. The LLM alpha-mining landscape, and its shared blind spot

A dozen named frameworks appeared over 2025–2026 for LLM-driven alpha mining. They converge on one architecture — hypothesis generation → code implementation → evaluation, with evaluation feedback steering the next hypothesis. AlphaAgent (KDD '25) names the roles Idea/Factor/Eval; Man Group's production AlphaGPT names them Idea Person/Implementer/Evaluator. Differentiation lies in what wraps the loop: regularization penalizing factor complexity and rewarding novelty (AlphaAgent, QuantaAlpha), structured semantic spaces searched by surrogate model (AlphaSchema), evolutionary operators at the trajectory level (QuantaAlpha), and persistent memory of the search process including failures (AlphaMemo, FactorMiner).

This confirms §3.3 mitigation 5 — the Alpha-GPT pattern of an LLM proposing behind a deterministic gate is now the field's default architecture, not one option among several.

**The blind spot is the finding.** An agent proposing hundreds of factors is running hundreds of trials, and **not one of these frameworks applies a multiple-testing correction.** AlphaAgent runs 20 trials × 5 evolutionary rounds and never discusses selection bias. The constrained-crypto work (arXiv 2604.26747) states outright it has no treatment of multiple hypothesis testing. Alpha-R1 exists specifically to screen thousands of candidates and still omits it. Several omit transaction costs entirely — QuantaAlpha reports 27.75% annualized on CSI 300 with no cost model. A trial registry feeding a deflated Sharpe, as `specs/strategy.md` invariant 3 requires, is therefore **ahead of every published framework** on the axis that matters most.

Practitioner counterweight: Man Group publishes no performance data, calls results "early," and states AlphaGPT cannot operate unsupervised. Zerve's Phily Hayes is blunter — "LLMs do not generate alpha… suggestions rarely produce signal that survives validation" — though he cites no evidence either. Neither side is arguing from data. What tips it is that the papers' numbers are uncorrected for trial count and often uncosted, so the disagreement may be less about capability than about whether those numbers survive corrections nobody applied.

**Design consequence:** treat agent-generated candidates as trials *including abandoned ones*, and note that generation volume is the lever that most raises the bar the survivor must clear.

## 2. Adaptive search — the mechanism §1.5 lacks

§1.5 establishes the problem: "the more an author backtested, the bigger their IS/OOS gap — iteration itself is the contaminant." The main report offers no mechanism for it, because DSR does not model it. DSR assumes N draws from a fixed distribution; an agent conditioning each spec on prior results is running a *targeted* search, so realized max Sharpe exceeds what N blind draws produce.

Two biases in fact point opposite ways: correlated configs mean effective N is below the literal count (literal counting over-penalizes), while adaptivity means literal counting under-penalizes. They partially offset. That is reassuring, not principled.

**The correctly-shaped tool is online FDR control** — alpha-investing (Foster & Stine 2008) and its descendants LORD++ and SAFFRON (Ramdas et al. 2017/2018). SAFFRON's own framing is a setting where hypotheses arrive indefinitely and future nulls may depend on the outcome of the current test — an agent proposing specs conditioned on its own results, exactly. The mechanism is an alpha-wealth budget spent per test and partly refunded, (1−λ)α on every rejection but the first.

Translated to this fund: **family-wide alpha-wealth in place of a flat family-wide trial cap.** A validated strategy refunds budget to its family; a run of failures starves it. Better economics than the fixed ~45-config ceiling from MinBTL, and self-tightening in the right direction.

Two costs. It needs a **p-value per test**, which `run_backtest` does not produce (it returns Sharpe/DSR/WFE). And strict FDR control requires independence or monotone thresholds; trials sharing data and overlapping windows get the weaker **mFDR** guarantee.

**On holdout reuse:** Dwork et al.'s Thresholdout shows a differentially-private holdout can answer exponentially many adaptive queries in holdout size *n*, provided the overfitting budget stays subquadratic in *n*. So "touch the holdout once, ever" is a *choice*, not a necessity. But the guarantee scales with *n*, and an 18-month daily-bar holdout is ~375 observations — far below where that promise means anything, with added noise on top. **At this data scale the one-shot rule is correct, not merely cautious.** Worth having the reason on record so nobody "improves" it later.

## 3. Crowding decomposes — and it lands on F1

§1.5 has McLean & Pontiff's −58% post-publication decay as an aggregate. It decomposes, and the decomposition is not symmetric across the registered families.

"Not All Factors Crowd Equally" (arXiv 2512.11913) fits a hyperbolic crowding model α(t)=K/(1+λt) to eight Fama-French factors over 1963–2024. **Mechanical** factors — momentum and reversal, signals with one unambiguous reading — fit at mean R²=0.37. **Judgment** factors — value, quality, profitability — fit at R²=0.04, a tenfold difference. Crowding is a mechanical-factor phenomenon.

The tail risk cuts opposite ways for the two mechanical families:

| Family | Signal type | Crowded crash probability |
|---|---|---|
| **F1** short-term mean reversion | mechanical, contrarian | **1.84× higher** (16.9% vs 9.2%) — bets against prevailing momentum, so crowding means synchronized wrong-way liquidation |
| **F4** vol-managed momentum | mechanical, trend-following | **0.38×**, i.e. lower (10.9% vs 28.2%, p=0.006) — crowding is trend confirmation |

Their crowding-*timing* strategy fails outright (Sharpe 0.22 vs a 0.39 factor-momentum benchmark), so the usable conclusion is explicit: **crowding informs position sizing and stop calibration, not factor selection.** This is a direct input to `specs/strategy.md` §6 sleeve sizing and kill rules for F1 — which is the build-first family and the exposed one. It is not a reason to drop F1.

Caveats: eight factors, US equities only, individual-factor tail significance marginal (p≈0.08–0.10). **Whether the mechanical/judgment split transfers to event-driven signals (F2, PEAD) is untested** — see gaps.

## 4. Parametric look-ahead — sharpening §3.3

§3.3 establishes that pretraining contains the future and that prompting cannot suppress it. Two 2026 papers now *measure* it and propose a weight-level correction.

Look-Ahead-Bench (arXiv 2601.13770) and FinCAD (arXiv 2605.24564) independently document what FinCAD calls **parametric look-ahead bias** — future knowledge encoded in model weights rather than in the data pipeline, invisible to any pipeline audit. FinCAD probes it by completion ("After [date], [stock] went…"), separating stable brand priors from date-specific memorization via date-variance, then subtracts the memorized prior at decode time.

The numbers are the useful part. Subtracting memorization cut in-sample returns **45.4% on average and 78.2% on NVDA** for Qwen2.5-14B, while out-of-sample the correction did essentially nothing. That asymmetry is the signature of contamination rather than skill, and it is consistent with the 51–62% Profit Mirage decay already in §3.3. FinCAD also improved in-sample→out-of-sample Sharpe rank correlation from +0.779 to +0.846 across 11 LLMs, beating anonymization (+0.547) — which is §3.3 mitigation 3, so **anonymization is now measurably the weakest of the listed defenses**.

Limits: 7B–14B open models only, US equities, and the authors call the in-sample drop a lower bound. FinCAD is a partial correction; §3.3 mitigation 1 (post-cutoff evaluation only) remains strictly stronger and stays the fund's primary defense.

---

## Already covered — do not re-research

The following were investigated in August 2026 and found to be **already answered, better, in the main report**. Recorded so nobody spends budget again:

- **Data stack / whether price+news caps the ceiling** → §3.2. EDGAR XBRL gives free PIT fundamentals and exact 8-K timestamps; Finnhub free gives the earnings calendar; Norgate (~$630/yr) fixes survivorship and PIT constituents.
- **Capacity and cost at small-account size** → §1.4. Retail orders at ≪0.1% of ADV pay effectively zero market impact; spread and timing slippage dominate. The bps/side floors are the right primitive.
- **CPCV vs purged walk-forward** → §1.2 already frames CPCV as *what enables PBO computation*, not a walk-forward replacement. That remains the right reading; the pro-CPCV comparison paper could not be obtained (see gaps).
- **The overfitting apparatus generally** → §1.2, including MinBTL as the source of the ~45-trial figure.

## Correction to an earlier August 2026 note

An earlier draft of this addendum claimed F2 (small-cap PEAD) was not implementable for lack of fundamentals data. **That was wrong** — it scoped the question to the Alpaca surface only. §3.2 already documents free EDGAR XBRL and Finnhub sources, and `specs/strategy.md` F2 already specifies EDGAR 8-K timestamp validation. There is no data gap.

## Gaps

1. **What is a backtest's p-value?** Blocks alpha-wealth (§2) entirely. Unresearched.
2. **Does the mechanical/judgment crowding split transfer to event-driven signals?** 2512.11913 covers eight Fama-French factors; F2 is PEAD. Unknown.
3. **Can an LLM reviewer reliably catch mechanism-vs-rule misalignment?** No source found. This is the load-bearing assumption under any G1 alignment gate; an eval set would have to establish it locally.
4. **Two primary sources unread:** the pro-CPCV comparison (Arian/Norouzi/Seco — SSRN 403, ScienceDirect paywalled) and the Bailey–López de Prado DSR paper itself (both mirrors returned unreadable PDFs). §1.2 covers the latter adequately.

## Evidence log

Statuses: **verified** = two independently read sources from different origins, both named · **single-source** · **contested** · **inference** · **prior-knowledge**.

| Claim | Status | Source(s) |
|---|---|---|
| LLM alpha-mining converges on hypothesis → code → evaluation with feedback | verified | [AlphaAgent 2502.16789](https://arxiv.org/html/2502.16789v2), [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha) |
| Published frameworks apply no multiple-testing correction despite many candidates | verified | [AlphaAgent](https://arxiv.org/html/2502.16789v2), [Constrained LLM Agents in Crypto 2604.26747](https://arxiv.org/html/2604.26747v1) |
| Parametric look-ahead bias materially inflates LLM backtest returns | verified | [Look-Ahead-Bench 2601.13770](https://arxiv.org/pdf/2601.13770), [FinCAD 2605.24564](https://arxiv.org/html/2605.24564) |
| Backtest-return-only evaluation of mined alphas is insufficient | verified | [AlphaEval 2508.13174](https://arxiv.org/pdf/2508.13174), [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha) |
| Whether LLM agents generate genuine alpha | contested | Papers report strong backtest numbers; [Zerve](https://www.zerve.ai/blog/llms-in-quantitative-research) says flatly no; Man Group says early, no data, human-in-the-loop mandatory. Neither side cites evidence |
| Mechanical factors crowd (R²=0.37) and judgment factors do not (R²=0.04); crowded reversal 1.84× crash probability, crowded momentum 0.38× | single-source | [2512.11913](https://arxiv.org/html/2512.11913v1) — read in full |
| Online FDR (alpha-investing/LORD++/SAFFRON) targets exactly the adaptive-arrival setting; alpha-wealth refunded (1−λ)α per rejection; mFDR only under dependence | single-source | [SAFFRON 1802.09098](https://ar5iv.labs.arxiv.org/html/1802.09098) |
| Thresholdout answers exponentially many adaptive queries in holdout size n given subquadratic overfitting budget | single-source | [Dwork et al. 1506.02629](https://ar5iv.labs.arxiv.org/html/1506.02629) |
| FinCAD: −45.4% mean / −78.2% NVDA in-sample, ~0 out-of-sample; rank correlation +0.779→+0.846; anonymization +0.547 | single-source | [FinCAD](https://arxiv.org/html/2605.24564) |
| AlphaAgent results: CSI 500 IC 0.0212 / AR 11.00%; S&P 500 IC 0.0056 / AR 8.74%, 2021-01→2025-01, OHLCV only | single-source | [AlphaAgent](https://arxiv.org/html/2502.16789v2) — ACM version 403, no second origin |
| QuantaAlpha: IC 0.1501 / ARR 27.75% CSI 300; no costs, slippage, or multiple-testing correction | single-source | [emergentmind relay](https://www.emergentmind.com/papers/2602.07085) — third-party summary, not the paper |
| Agent-scale generation with no trial accounting is the literature's central hole; a trial registry + DSR already mechanizes it, so the open design work is on the generation side | inference | Derived from AlphaAgent + 2604.26747 + Alpha-R1 all lacking N accounting |
| DSR / PBO / MinBTL / Harvey-Liu mechanics | prior-knowledge | Confirmed at search level only this session; §1.2 of the main report is the authority |

**Not read (search-level only, never load-bearing):** the FITEE survey on LLM-based alpha mining (paywalled), RD-Agent(Q)'s "2× ARR with 70% fewer factors," and the "insider purchases → 4–8% annual alpha" figure.
