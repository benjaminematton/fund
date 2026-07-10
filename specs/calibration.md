# Calibration Scoreboard — `specs/calibration.md`

How analyst signals are scored, how scores become PM weights, and why agents
can't game it. Extends `specs/contracts.md` (resolutions table) and closes the
feedback loop design.md §2 gestures at ("the PM's charter weights analyst
signals by calibration"). Code: `calibration/` — pure numpy + stdlib, no LLM
imports, CI-enforced like `gate/` and `stratgate/`.

Evidence base: proper scoring rules (Gneiting & Raftery 2007), Murphy (1973)
decomposition, Good Judgment Project aggregation findings, empirical-Bayes
shrinkage practice, and the forecasting-platform incentive literature
(arXiv:2106.11248). Full citations in the research appendix below.

---

## 0. Invariants

1. **Scoring is code.** No agent — including the PM — computes, adjusts, or
   waives a score. Ops runs the scoreboard job; agents read it.
2. **Every signal is graded.** Abstains (neutral) score as p = 0.5. Skipping
   them would let agents protect their averages by abstaining on hard calls —
   the documented failure mode of self-selected scoring.
3. **The ranking metric is TOTAL skill, not average.** total = shrunk BSS ×
   n_graded. An agent that hides earns zero, not a protected 0.25 Brier.
4. **One event convention, fixed:** "ticker beats SPY over the signal's
   horizon (default 5 trading days)." All probabilities refer to this event.
5. **Malformed rows are dropped and counted, never guessed** (invariant 7).

## 1. Scoring pipeline

```
signal (direction, confidence 0-100)
  -> p = conf/100 (long) | 1 - conf/100 (short) | 0.5 (neutral)
resolution at horizon (nightly job, contracts §2)
  -> o = 1 if alpha_vs_SPY > 0 else 0
per agent, chronological, recency-weighted (exp decay, half-life 75 calls):
  Brier            mean (p - o)^2                       [proper: honesty optimal]
  BSS              1 - Brier/Brier(base rate)            [>0 = beats climatology]
  Murphy           reliability - resolution + uncertainty = Brier
                   (exact when confidences land on discrete steps; agents are
                   prompted to 5-point steps partly for this reason)
  ECE              equal-mass bins, <=10, >=20 obs/bin    [descriptive, not ranked]
  batting/slugging hit rate / (avg win / avg loss)        [Brier ignores magnitude]
  shrunk BSS       w*BSS + (1-w)*pool_BSS, w = n/(n+30)   [streaks can't buy weight]
  total skill      shrunk BSS * n_graded                  [the ranking column]
```

## 2. PM weighting rule (deterministic; the PM sizes conviction within it)

```
weight_i ~ max(shrunk_BSS_i, 0)
  agents with < 50 graded calls  -> mean weight (no track record = pool average)
  floor: no agent below 0.5 x mean weight   [equal weights are hard to beat;
                                             weights drift from equality only
                                             as evidence accumulates]
  normalize to sum 1
```

The PM's charter says: treat analyst signals as evidence weighted by the
scoreboard, not by persuasiveness of prose. When aggregating multiple signals
on one ticker, pool in log-odds space with these weights (geometric mean of
odds — empirically beats linear pooling), **no extremizing**: our agents share
training data, so their errors correlate and extremizing amplifies shared bias.

## 3. Reading the decomposition (for charters and post-mortems)

- **reliability high** (miscalibrated): agent's 80s hit like 60s → charter fix:
  confidence-language anchors, show the agent its own reliability curve.
- **resolution low** (non-discriminating): all calls hover at 55 → the agent
  adds nothing over the base rate; consider narrowing its coverage or retiring
  the seat.
- **batting < 0.5 with slugging > 1.5**: fine — right-tail seeking is a valid
  style (IR 0.3 is achievable at 30% batting / 2.6 slugging). Judge the pair.

## 4. Statistical honesty rules

- **Overlapping horizons:** 5-day outcomes sampled daily overlap 5x; for any
  significance claim use N_eff ≈ N/5, or non-overlapping calls only. The
  scoreboard displays raw N and N_eff both.
- **Horizon bucketing:** signals at different horizons are scored in separate
  buckets — never compare a 1-day caller to a 20-day caller on one table.
- **Minimums:** < 50 graded calls → provisional (mean weight); trust
  approaches data-driven weights around 150–300 calls. GJP's superforecaster
  designation used a full season of ~100 questions and still saw 30% churn.
- **ECE is descriptive.** It's gameable (always forecast the base rate → ECE 0)
  and discontinuous; it never enters the weight.

## 5. Failure semantics

| failure | resolution |
|---|---|
| malformed signal row (bad direction/confidence) | drop, count, weekly `malformed_signals` line in #risk |
| missing/NaN alpha at resolution | signal stays ungraded; > 5% ungraded → data-pipeline alarm |
| degenerate outcomes (all same) in a window | BSS undefined → agent keeps prior weight, flag on board |
| scoreboard job crash | last good scoreboard stands; PM weights never silently reset to equal |
| agent disputes a score | tough — scores are code; disputes go to threshold-change proposals via Risk Officer, human commit |

## 6. Slack projection

Weekly to `#pnl` via the events outbox: the markdown table (see
`calibration/scoreboard.py::render_markdown`) sorted by total skill, plus
reading guide. Per-agent reliability curves attached monthly. The scoreboard
is also injected into each analyst's morning context — seeing your own
calibration is the cheapest charter tune-up there is.

## 7. Research appendix (key sources)

Proper scoring / decomposition: Gneiting & Raftery 2007 (JASA); Murphy 1973;
Siegert 2017 (QJRMS, decomposition bias). Calibration measurement: Guo et al.
2017; Błasiok & Nakkiran ICLR 2024 (smECE — adopt later if one-number
calibration is wanted). Shrinkage: Robinson, empirical-Bayes batting averages.
Aggregation/weighting: GJP (performance + recency weighting, log-odds pooling;
extremizing contested for correlated forecasters — Satopää 2014, Baron 2014).
Incentives: "Alignment Problems With Current Forecasting Platforms"
(arXiv:2106.11248) — the abstain rule; incentive-compatible competitions
(arXiv:2101.01816). Finance: Mauboussin, "Dispersion and Alpha Conversion"
(batting/slugging); Marshall Wace TOPS (alpha capture precedent — contributors
scored on simulated P&L of their calls); Britten-Jones et al. / Boudoukh et
al. FAJ 2019 (overlapping-horizon inference).
