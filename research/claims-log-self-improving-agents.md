# Claims log — closed-loop self-improvement with a human commit gate

Working log for `field-brief-self-improving-agents.md` (2026-08-30). Statuses: verified (2+
independent sources read in full, both named) · single-source · contested · inference ·
prior-knowledge. Sources are marked (read) or (search-level); search-level never backs a claim.
A source read in the 2026-08-18 brief (`field-brief-agent-improvement-loops.md`) is
prior-knowledge here unless re-read in this run. Where a WebFetch paraphrase was thin or
wrong, the saved PDF was read page-by-page and that reading is what the log reflects.

## Sources

| Source | Status |
|---|---|
| GEPA, arXiv 2507.19457 — https://arxiv.org/html/2507.19457 | read (HTML; the PDF fetch https://arxiv.org/pdf/2507.19457 was thin) |
| Decagon, Optimizing GEPA for production — https://decagon.ai/blog/optimizing-gepa-for-production | read |
| ETGPO, arXiv 2602.00997 — https://arxiv.org/pdf/2602.00997 | read (PDF pages 1–8 read directly) |
| Agentic Harness Engineering (AHE), arXiv 2604.25850 — https://arxiv.org/pdf/2604.25850 | read (PDF pages 1–10 read directly) |
| Building an Internal Coding Agent at Zup, arXiv 2604.09805 — https://arxiv.org/pdf/2604.09805 | read (PDF pages 1–8 read directly; the fetch paraphrase inverted its finding) |
| When Generic Prompt Improvements Hurt, arXiv 2601.22025 — https://arxiv.org/html/2601.22025 | read (re-read this run) |
| RoboPhD, arXiv 2604.04347 — https://arxiv.org/html/2604.04347v1 | read (HTML; PDF garbled) |
| Data-Prompt Co-Evolution, arXiv 2510.12728 — https://arxiv.org/pdf/2510.12728 | read (paraphrase only; thin — context, not corroboration) |
| Intentmaking and Sensemaking (AlphaEvolve users), arXiv 2605.05921 — https://arxiv.org/pdf/2605.05921 | read |
| AlphaEvolve paper, arXiv 2506.13131 — https://arxiv.org/html/2506.13131 | read (targeted: §2.4, §3.3) |
| AlphaEvolve blog, DeepMind — https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/ | read (no gaming discussion) |
| Alignment Problems With Current Forecasting Platforms, arXiv 2106.11248 — https://arxiv.org/html/2106.11248 | read (HTML; PDF garbled; abs page abstract-only) |
| Incentive-Compatible Forecasting Competitions (ELF), arXiv 2101.01816 — https://arxiv.org/html/2101.01816 | read (HTML; PDF garbled) |
| Who has the best probabilities? Luck vs skill, arXiv 2509.08744 — https://arxiv.org/html/2509.08744 | read (HTML, quoted) |
| Mellers et al. 2015, Identifying and Cultivating Superforecasters — https://web.stanford.edu/~knutson/jdm/mellers15.pdf | read (PDF pages 1–8 read directly) |
| Scoring Strategic Agents, arXiv 1909.01888 — https://arxiv.org/html/1909.01888v6 | read |
| Online Prediction with Selfish Experts, arXiv 1702.03615 — https://arxiv.org/abs/1702.03615 | read, abstract-level only |
| Variance estimation for Brier Score decomposition, arXiv 1303.6182 — https://arxiv.org/abs/1303.6182 | read, abstract only — not citable for numbers |
| Habituation at the Gate, arXiv 2606.22721 — https://arxiv.org/html/2606.22721 | read (HTML; PDF fetch thin) |
| These Aren't the Reviews You're Looking For, arXiv 2605.02273 — https://arxiv.org/html/2605.02273v1 | read |
| Intercom, AI is approving our pull requests — https://www.intercom.com/blog/ai-is-approving-our-pull-requests-heres-how-we-made-it-safe/ | read |
| Design Considerations for Human Oversight of AI, arXiv 2510.19512 — https://arxiv.org/pdf/2510.19512 | read (paraphrase; N not recovered) |
| Bröcker & Smith 2008 (AMS WAF) | fetch FAILED 403 — search-level |
| Parasuraman & Manzey 2010 | fetch FAILED (cookie wall ×2) — search-level / prior-knowledge |
| Good Judgment Project pages, EA Forum, Wikipedia | search-level only |

## Claims

### Q1 — what a proposal must carry; what generalizes

| Claim | Status | Source(s) |
|---|---|---|
| Proposals are derived from execution traces plus evaluator feedback, not from the score alone: GEPA's reflection receives (prompt, trajectory, score, textual feedback) (§3); AHE's evolve agent reads a layered evidence corpus distilled from traces (§3.2); ETGPO collects failed traces and categorises them (§3.1–3.2) | **verified** (three origins) | [GEPA](https://arxiv.org/html/2507.19457) (read); [AHE](https://arxiv.org/pdf/2604.25850) (read); [ETGPO](https://arxiv.org/pdf/2602.00997) (read) |
| AHE change manifest: every edit records failure evidence → inferred root cause → targeted fix → predicted impact (expected fixes + at-risk regressions); next round intersects predictions with observed task deltas → per-edit verdict; ineffective edits reverted at file granularity (§3.3, Alg. 1) | single-source | AHE (read) |
| Iterative/automated prompt optimisation overfits small eval sets: generic prompt rules regressed Qwen 2.5 RAG compliance 26/30 → 9/30; gains are non-monotonic across task contracts; golden sets 50–200 for iteration, 400–600 to detect a 5% absolute difference at 95% | single-source | 2601.22025 (read) |
| In production, unconstrained GEPA over-accumulates: 500 training examples vs 50 grew prompt length ~75% while held-out performance fell; prompts >5,000 chars; a 1,500-char cap in the reflection prompt gave ~4× compression at ~0.8% degradation; 20–100 examples was the useful range | single-source (practitioner) | Decagon (read) |
| Prompt optimisers overfit their optimisation set and more optimisation data/iterations can make held-out performance worse | **verified** (academic ablation + independent practitioner) | [2601.22025](https://arxiv.org/html/2601.22025) (read); [Decagon](https://decagon.ai/blog/optimizing-gepa-for-production) (read) |
| GEPA +13.33% aggregate vs MIPROv2 +5.64% (GPT-4.1 Mini, Table 2); up to 35× fewer rollouts than GRPO; the majority of GEPA's rollout budget is spent on validation (Obs. 1 §4); evolved prompts up to 9.2× shorter than MIPROv2's (Obs. 4); lower generalisation gap (Obs. 2, Fig. 16) | single-source | GEPA (read) |
| ETGPO: taxonomy is LLM-derived from failed traces (optimizer LLM, batched); categories with only one problem are filtered out "to avoid optimizing for error categories that are overly specific to individual problems"; top-G by failure count get guidance; avg 69.08 vs GEPA 67.71 vs MIPROv2 67.20 vs CoT 66.12 (GPT-4.1-mini, Table 1) at ~1/3 of GEPA's optimisation tokens (Table 2); short one-line guidance costs −2.22 vs detailed guidance with examples (Table 6) | single-source | ETGPO (read) |
| Under a fixed budget of 1,500 evaluations, methods reserving 100–200 for a validation split afford only 7–13 candidates; RoboPhD instead spends 3 agents × 20 examples/iteration for ~21 iterations with fresh random samples each iteration and Elo accumulation across iterations | single-source | RoboPhD (read) |
| AHE component ablation (Terminal-Bench 2, 89 tasks): seed 69.7%; +memory only 75.3; +tool only 73.0; +middleware only 71.9; **+system_prompt only 67.4 (regresses)**; full 77.0. Singles sum +11.1 pp vs full +7.3 — components interact non-additively | single-source | AHE (read, Table 3) |
| Frozen AHE harness transfers: SWE-bench-verified 75.6% vs seed 75.2% while prompt-only self-evolvers (ACE, TF-GRPO) regress below seed; cross-model +2.3 to +10.1 pp on five bases | single-source | AHE (read, Table 2, Fig. 3) |

### Q2 — gaming a proper score when an optimiser edits the forecaster

| Claim | Status | Source(s) |
|---|---|---|
| Under Brier with self-selected questions, a forecaster with current average score b should abstain on any question whose true probability lies in (b, 1−b) even knowing it exactly (Theorem 1); the incentive vanishes when the target is the *sum* of scores; fixes: require all questions or impute the community median for abstentions | single-source | Alignment Problems (read) |
| Competing for *rank* under a proper scoring rule induces extremising and modelling of competitors; a lottery with selection probability proportional to relative score (ELF) restores truthful reporting and selects the best forecaster with probability → 1 as events accumulate | single-source | ELF 2101.01816 (read) |
| Ranking forecasters by a proper score creates distortion incentives that propriety alone does not remove | **verified** (Alignment Problems' Metaculus simulations: a perfect predictor raises win-probability 13.46% → 16.2% by distorting; ELF's extremising result) | [Alignment Problems](https://arxiv.org/html/2106.11248) (read); [ELF](https://arxiv.org/html/2101.01816) (read) |
| When agents know the score, optimal score design *underweights* features on which ability to distort is heterogeneous relative to measurement noise; more decision-maker commitment → less feature-sensitivity → less distortion | single-source | Scoring Strategic Agents (read) |
| With selfish experts, incentive-compatible weight updates coincide with proper scoring rules; for absolute loss no algorithm achieves vanishing regret | single-source (abstract-level) | Selfish Experts (read, abstract) |
| AHE fences the optimiser's own surface: the evolve agent writes only inside the harness workspace; runs dir, tracer, verifier and LLM config are read-only; the seed system prompt is non-deletable — explicitly to block "disabling the verifier, swapping the model, or raising the reasoning budget" (§3.3 controllability) | single-source | AHE (read) |
| AlphaEvolve users (8 mathematicians, semi-structured interviews) routinely hit specification gaming — the system found "loopholes in how we specified the problem"; humans iteratively tightened specs; trust needed validity, novelty, intent-alignment, and the *why* of a proposal | single-source | Intentmaking 2605.05921 (read) |
| The AlphaEvolve paper has no section on reward hacking; its mitigations are an evaluation cascade of increasing difficulty (§2.4), rounding/verification steps, and human-expert confirmation of correctness for deployed results (§3.3.3–3.3.4); the DeepMind blog is silent on gaming | single-source (absence + §2.4) | AlphaEvolve paper (read); AlphaEvolve blog (read) |
| Spec/evaluator gaming is a routine, not exotic, failure of LLM-driven proposal loops, and the standing mitigation is human-tightened specifications plus staged verification | **verified** (Intentmaking interviews; AHE's controllability rationale) | [Intentmaking](https://arxiv.org/pdf/2605.05921) (read); [AHE](https://arxiv.org/pdf/2604.25850) (read) |

### Q3 — grading the proposer; the low-data regime

| Claim | Status | Source(s) |
|---|---|---|
| At a 10% forecast error on p=0.5 events, ~100 questions before luck outweighing skill is a 1σ (~16%) event and ~400 before it is 2σ (~2%); two forecasters with RMS forecast difference δ have SD of Brier-difference < δ/√N; a win margin >0.02 Brier ≈ two SD | single-source (quoted) | Luck vs Skill (read) |
| GJP: tournaments posed slightly more than 100 questions (Years 1–2) and ~150 (Year 3); superforecasters = 60 of 2,200–3,900 (top ~2%) selected on Year-1 accuracy; they did **not** regress in Years 2–3 while top-team individuals and all others regressed (Fig. 1); comparison groups required ≥25 questions per tournament; Brier standardised within question to neutralise self-selected difficulty (superforecasters answered 76/116/81 questions/yr) | single-source | Mellers 2015 (read) |
| At n=20 evaluations and a 1% accuracy gap, the better of three candidates wins only ~45% of comparisons (random 33%); Elo across iterations recovers 43.7% ranking accuracy vs 26.7% single-elimination | single-source | RoboPhD (read) |
| Tens of graded outcomes cannot rank candidates; on the order of a hundred is where a large skill gap first separates from luck at 1σ, and hundreds for 2σ; signal at smaller n is usable only accumulated across many rounds | **verified as arithmetic across two origins** (Luck vs Skill's 100/400; RoboPhD's n=20 result) with GJP as domain corroboration | [Luck vs Skill](https://arxiv.org/html/2509.08744) (read); [RoboPhD](https://arxiv.org/html/2604.04347v1) (read); [Mellers 2015](https://web.stanford.edu/~knutson/jdm/mellers15.pdf) (read) |
| AHE's self-attribution: cross-iteration fix precision 33.7% / recall 51.4% (~5× random 6.5%/10.6%); regression precision 11.8% / recall 11.1% (~2× random) — "reliable for fixes but blind to regressions" (§4.4.2, Fig. 4) | single-source | AHE (read) |
| BSS-based tests are more powerful than BS-based; serial correlation inflates variance so uncorrected tests are too liberal; a few hundred pairs for rare events | search-level only — not cited in the brief | Bröcker & Smith (snippet) |

### Q4 — the approval gate

| Claim | Status | Source(s) |
|---|---|---|
| 400 repeat reviewers, 11,429 reviews, 7 months, five agents: approval 27.9% → 42.4% across experience deciles (+14.5 pp, p<10⁻⁶); change-requests 11.2% → 5.6%; median latency 3.9 h → 13.5 h; comments/review 1.01 → 0.79 (−22%), words −28%; in the same repos human-PR approval fell to 29.1% while agent-PR approval rose to 41.7%; median PR size flat (ρ=+0.02); read as reflexive habituation under workload; countermeasures: reviewer rotation, streak audits on consecutive approvals, dashboards pairing a reviewer's approval trajectory with downstream defects | single-source | Habituation (read) |
| AIDev (932k PRs): AI PRs get human-only review 8.08% vs 25.21% for human PRs in the same repos; 71.58% of comments on AI PRs are from agents; human comments are direct assessment 65.53% vs 93.56%; humans on AI PRs mostly issue steering commands | single-source | These Aren't the Reviews (read) |
| Human scrutiny of agent-authored PRs is materially lower than of human PRs and declines with exposure | **verified** (two independent datasets) | [Habituation](https://arxiv.org/html/2606.22721) (read); [These Aren't the Reviews](https://arxiv.org/html/2605.02273v1) (read) |
| Intercom: the AI reviewer refuses large/broad PRs and forces breakdown; every AI approval is labelled/logged/queryable; 19% of PRs auto-approved; revert rate AI-authored 0.53% vs human 5.39% (backend); human review had approved changes later reverted in outages | single-source (vendor) | Intercom (read) |
| Zup: human approval mode (confirm every edit/shell) worked as an onboarding trust-calibration mechanism; developers migrated to autonomous mode organically; open question: adaptive trust models keyed on historical success rate and action reversibility (§4.3, Open Q4) | single-source (practitioner) | Zup (read) |
| Meaningful oversight needs contextualised (not raw) information, bounded workload, genuine authority, and feedback to the overseer on the consequences of their own decisions; decay to rubber-stamping when those are absent | single-source (thin) | Design Considerations 2510.19512 (read) |
| Overseers need feedback on their own decisions' outcomes to stay calibrated | **verified** (Design Considerations' feedback finding; Habituation's dashboard-with-defect-data countermeasure) | [Design Considerations](https://arxiv.org/pdf/2510.19512) (read); [Habituation](https://arxiv.org/html/2606.22721) (read) |
| Complacency and automation bias occur in experts as well as novices and are not overcome by practice (attentional-resource model) | prior-knowledge (fetch failed twice) | Parasuraman & Manzey 2010 |

### Q5 — numeric/config first vs prose charter edits

| Claim | Status | Source(s) |
|---|---|---|
| Zup: "refining tool descriptions, parameter schemas, and error contracts produced more consistent improvements in agent reliability than prompt engineering alone" (abstract; §4.2 first decision); a log-driven feedback loop into tool descriptions and prompts "proven more effective than prompt tuning in isolation" (§3.2) | single-source (practitioner) | Zup (read) |
| Structural components (tools, middleware, memory, schemas) are a more reliable and transferable improvement surface than system-prompt prose; prompt-only edits are the surface most likely to regress or fail to transfer | **verified** (AHE Table 3 + transfer results; Zup's independent practitioner finding) | [AHE](https://arxiv.org/pdf/2604.25850) (read); [Zup](https://arxiv.org/pdf/2604.09805) (read) |
| Prose edits *do* generalise when scoped to prevalent, trace-derived error categories, kept detailed but bounded, and singletons filtered | single-source (ETGPO held-out results), corroborated in direction by GEPA's shorter-prompt/lower-gap observation | ETGPO (read); GEPA (read) |
| For the fund: start tier 2 on targets with one-number attribution (thresholds, seat config, watchlist, tool/output-contract wording) and admit charter prose only as category-scoped, length-capped guidance after a taxonomy exists | **inference** from AHE + Zup + ETGPO + 2601.22025 + the 2026-08-18 design's taxonomy trigger | — |

### Relation to the 2026-08-18 brief and design

| Claim | Status | Source(s) |
|---|---|---|
| The prior design's Class C posture (proposal + evidence + human commit, one change per seat per window, pre-registration, DiD incubation) is consistent with everything read here and is closest to AHE's manifest-and-verdict loop | inference | — |
| Contested: who derives the failure taxonomy — practitioner line says never automate open coding (prior brief, Husain & Shankar); ETGPO derives it with an LLM, filters by prevalence, and matches/exceeds SOTA on held-out sets; AHE distils with an agent debugger | **contested** (named both sides) | prior brief (prior-knowledge); ETGPO (read); AHE (read) |
