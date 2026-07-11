# Agent role survey — what positions do similar projects staff?

Cross-project survey of agent seats/positions in multi-agent LLM systems, finance and beyond. Purpose: sanity-check the fund's 11-seat roster (`specs/design.md` §2) and catalogue roles worth stealing later. Researched 2026-07-10.

---

## 1. Finance / trading firms

| Project | Roles (verbatim where possible) | Notable |
|---|---|---|
| **HedgeAgents** (WWW 2025) | Hedge Fund Manager "Otto"; Bitcoin Analyst "Dave"; Stock Analyst "Bob"; Forex Analyst "Emily" | Coordination via three formal meetings: Budget Allocation Conference, Experience Sharing Conference (cross-agent lesson exchange), **Extreme Market Conference** (crisis committee) |
| **FinCon** (NeurIPS 2024) | Manager Agent (sole decision-maker); analyst agents by modality: news, 10-K/filings, earnings-call audio, market-data, portfolio-selection | Analysts route **upward only** — no peer chat, explicitly to cut token cost. Dual-level risk control: daily CVaR watch + episodic belief-updating self-critique |
| **ContestTrade** (2025) | N Data Analysis Agents + N Research Agents, each seeded with a distinct "Trading Belief" | **Darwinian org chart**: agents continuously scored on realized market feedback; only top-ranked agents' output is adopted |
| **LLM-TradeBot** (2025–26) | DataSync "Oracle", QuantAnalyst "Strategist", Predict "Prophet", Bull "Optimist" vs Bear "Pessimist", DecisionCore "Critic", **RiskAudit "Guardian" (absolute veto)**, Execution "Executor", **Reflection "Philosopher"** (retro every 10 trades), SymbolSelector | Richest explicit roster found; Guardian = closest thing to a compliance seat in open source; full decision-trail auditing |
| **TradingGroup** (2025) | News-Sentiment, Financial-Report, Stock-Forecasting, **Style-Preference**, Trading-Decision agents + dynamic risk module | Style-Preference agent's whole job is picking the firm's current aggression level from P&L history |
| **FinRobot** (AI4Finance) | Market-forecasting / document-analysis / strategy agents; equity-report pipeline of 8 section agents under an agent_manager | **Director Agent** meta-layer: assigns tasks to agents *by their performance metrics* (dispatch/HR analog) |
| **QuantAgent** | Writer Agent vs Judge Agent (inner loop), real backtests refine the judge (outer loop) | Minimal two-seat "developer + reviewer" desk — basically our quant + stratgate |
| **FinMem / FinAgent / TradingGPT** | Single traders with module splits: profiling, layered memory (shallow/deep with decay), low-level + high-level reflection | High-level reflection = built-in self-auditor; TradingGPT differentiated agents by *risk personality*, precursor to bull/bear |
| **StockAgent / ASFM** | 10s–200 heterogeneous trader agents as *market participants* (not a firm), BBS forum for opinion exchange | Different genre: market sims for emergent behavior, not alpha |
| **nof1 Alpha Arena** | Each seat = one frontier model with identical prompts and $10k real capital | Tournament framing; the "position" is the whole trader |
| **Alpha-GPT 2.0** | Alpha mining / alpha modeling / alpha analysis agents assisting a *human* quant | Agents as assistants, not an autonomous firm |

**Absent from the entire finance corpus:** treasurer, compliance officer (nearest: Guardian's veto), and any token/cost-optimization seat. Budget allocation is always folded into the fund-manager role.

## 2. Non-finance organizations

| Project | Roles | Notable |
|---|---|---|
| **ChatDev** (software company) | CEO, CPO, CTO, CHRO, Programmer, Code Reviewer, Tester, Art Designer, **Counselor** | Waterfall of agent *dyads* per phase; Counselor runs a reflection phase with the CEO after each stage |
| **MetaGPT / MGX** | ProductManager, Architect, ProjectManager, Engineer, QaEngineer; MGX adds TeamLeader | `Code = SOP(Team)`: roles hand off **structured documents, never raw chat** — same philosophy as our `submit_signal`/`submit_decision` contracts |
| **CAMEL Workforce** | Coordinator Agent, Task Planner Agent, Worker Nodes | Coordinator can **spawn new workers or decompose tasks on failure** — closest thing to an AI recruiter |
| **Magentic-One** (Microsoft) | Orchestrator + WebSurfer, FileSurfer, Coder, ComputerTerminal | Orchestrator keeps a Task Ledger + Progress Ledger and re-plans on error — our orchestrator, but LLM-driven |
| **Virtual Lab** (Stanford, Zou) | **AI Principal Investigator** (recruits specialists it decides it needs), discipline scientist agents, **Scientific Critic** | Designed real SARS-CoV-2 nanobodies. Critic seat "essential… reduced hallucinations." Team meetings vs 1:1 meetings as interaction modes |
| **Agent Laboratory** | PhD-student, Postdoc, ML-Engineer, Professor (scores output), Reviewer agents (simulated peer review) | Academic hierarchy as QA ladder |
| **Sakana AI Scientist v2** | Idea-generation, Experiment Manager, Coder, Manuscript-writer, **AI Reviewer** + VLM figure-checker | First fully-AI peer-review-accepted paper |
| **Agent Hospital** | Doctor agents (+ MedAgent-Zero), nurse agents, patient agents; shared medical-record library | Doctors improve purely from accumulated case memory — journal-as-training-data at scale |
| **Generative Agents / Project Sid** | Smallville: personas (shopkeeper, barista…) with emergent civic roles (mayoral candidate). Sid (1,000 agents): emergent farmer/guard/merchant/priest professions + an **Election Manager** | Roles *emerged* from memory + social feedback rather than being assigned |
| **Project Vend** (Anthropic) | Claudius the shopkeeper; Phase 2 added **Seymour Cash** — an AI manager above it wielding an OKR tool | Cleanest "boss agent managing another agent by objectives" example |
| **Anthropic multi-agent research** | Lead Researcher (plans, spawns subagents), parallel search subagents, **Citation Agent** | Citation Agent = dedicated claims-must-trace-to-sources QA seat |

## 3. Cross-cutting: the five role archetypes

Every org above staffs some subset of:

1. **Producers** — analysts, engineers, doctors, traders. We have five (analysts + quant).
2. **Judges/critics** — reviewer, tester, professor, Scientific Critic, Citation Agent, Guardian. *The single most consistently load-bearing seat across domains* — every serious project has at least one, and Zou's group credits it with hallucination reduction. We have three (bear researcher, risk officer, plus the deterministic gates).
3. **Coordinators** — orchestrator, PM, PI, TeamLeader. Ours is deliberately **not an LLM** (plain Python scheduler) — rarer and safer than the field norm, where LLM orchestrators (Magentic-One, MGX) re-plan dynamically but add failure modes.
4. **Memory curators** — record libraries, memory streams, Experience Sharing Conference, Memory Engineer. We have journals + calibration; nobody curates/compacts them (see gaps).
5. **Meta/HR** — CHRO, Director Agent, Coordinator-that-spawns-workers, Seymour Cash, contest judges. We assign this to the human CEO — defensible at 11 seats; the automated versions (FinRobot's performance-based dispatch, ContestTrade's survival-of-the-fittest) are the scaling path.

## 4. Gaps vs our roster — candidates, with a verdict each

Judged against the roster in `specs/design.md` §2 (PM, 4 analysts, bull/bear, quant, risk, exec, ops):

**Worth adopting (cheap, high value):**
- **Crisis protocol, not crisis seat** (HedgeAgents' Extreme Market Conference): an orchestrator-triggered off-cycle stage on circuit-breaker/vol-spike events. We already have off-cycle mini-debates; formalize the trigger conditions. No new agent.
- **Style/aggression review** (TradingGroup): fold into the weekly scoreboard — Ops reports whether the book's realized risk matches intent; CEO adjusts charters. No new agent.
- **Experience-sharing ritual** (HedgeAgents ESC): a monthly cross-agent digest where each seat's top reflections are posted firm-wide and injected into everyone's journals. One Ops job.

**Consider at Phase 4+:**
- **Devil's-advocate generalization**: our bear researcher argues tickers; Virtual Lab's Scientific Critic argues *anything* (methodology, process, thesis quality). A "Critic" pass over the PM's decision memo (pre-gate, advisory) is one extra fast-model turn per decision.
- **Journal curator** (memory-engineer analog): as journals grow, a nightly compaction job (summarize, dedupe, index lessons) keeps prompt injection cheap. Start as code, not a seat.
- **Performance-weighted dispatch** (FinRobot Director / ContestTrade): we already weight PM attention by calibration score — the lightweight version. Full contest dynamics (dropping bottom seats) only makes sense with >2 analysts per modality.

**Explicitly rejected:**
- **LLM orchestrator** — our deterministic scheduler is a feature; the field's LLM coordinators trade reliability for flexibility we don't need on a fixed daily cycle.
- **Token-optimization agent** — an LLM burning tokens to think about burning tokens; the `costs` table + a weekly Ops cost section + human config changes covers it.
- **AI manager-of-agents** (Seymour Cash) — the CEO seat is human by design; that's the point of the project.
- **Compliance/treasurer seats** — nothing to comply with on a paper account; budget allocation is the PM/CEO's job. Revisit only if the strategy-sleeve system (Phase 5) grows real capital-allocation complexity.

## 5. Sources

Finance: HedgeAgents (arXiv 2502.13165) · FinCon (2407.06567) · ContestTrade (2508.00554) · LLM-TradeBot (github.com/EthanAlgoX/LLM-TradeBot) · TradingGroup (2508.17565) · FinRobot (2405.14767, 2411.08804) · QuantAgent (2402.03755) · FinMem (2311.13743) · FinAgent (2402.18485) · TradingGPT (2309.03736) · StockAgent (2407.18957) · ASFM (2406.19966) · Alpha-GPT (2308.00016, 2402.09746) · nof1.ai.
Non-finance: ChatDev (2307.07924) · MetaGPT (2308.00352) · CAMEL (NeurIPS 2023) + Workforce · Magentic-One (Microsoft Research) · CrewAI docs · Generative Agents (2304.03442) · Project Sid (2411.00114) · Virtual Lab (Stanford/Zou) · Agent Laboratory (2501.04227) · Sakana AI Scientist v2 · Agent Hospital (2405.02957) · Anthropic Project Vend 1–2 + multi-agent research system blog.
