# Claims log — resident agents & peer coordination

**Run date:** 2026-09-03 · **For:** whether `fund`'s seats should be long-running and whether they
should talk to each other · **Budget:** 6 searches, 11 fetch/read attempts (2 failed, logged below)

Statuses: **verified** (2+ independent origins, both read in full) · **single-source** ·
**contested** · **inference** (derived here) · **prior-knowledge** (unconfirmed by this run's sources)

## Source shelf

| # | Source | URL | Status |
|---|---|---|---|
| S1 | Chroma, *Context Rot: How Increasing Input Tokens Impacts LLM Performance* | https://www.trychroma.com/research/context-rot | **read** |
| S2 | Ding, Nannapaneni, Liu, Zhang — *Always-On Agents: A Survey of Persistent Memory, State, and Governance in LLM Agents* | https://arxiv.org/pdf/2606.30306 | **read** (abstract, §6.4–6.6, §8.2.3–8.3) |
| S3 | Anthropic — *How we built our multi-agent research system* | https://www.anthropic.com/engineering/multi-agent-research-system | **read** |
| S4 | Cognition — *Don't Build Multi-Agents* | https://cognition.com/blog/dont-build-multi-agents | **read** |
| S5 | *Multi-Agent Debate Strategies: Survey, Taxonomy, and Challenges* | https://arxiv.org/html/2607.26212 | **read** |
| S6 | Agarwal & Khanna — *When Persuasion Overrides Truth in Multi-Agent LLM Debates (CW-POR)* | https://arxiv.org/pdf/2504.00374 | **read** (abstract + §1 only) |
| S7 | Hou, Wang, Zhao, Wang — *When Agents Do Not Stop: Uncovering Infinite Agentic Loops in LLM Agents* | https://arxiv.org/pdf/2607.01641 | **read** (abstract + §I–III) |
| S8 | FinCon (NeurIPS 2024) | https://proceedings.neurips.cc/paper_files/paper/2024/file/f7ae4fe91d96f50abc2211f09b6a7e49-Paper-Conference.pdf | **search-level** — fetch failed (>10MB cap) |
| S9 | Assorted "why multi-agent systems fail" vendor blogs | various | **search-level** |

**Failed fetches, permanently search-level:** S8 (size cap). `cognition.ai` 301→`cognition.com`;
the redirect target was fetched and read, so S4 stands. Two arXiv PDFs (S6, S7) returned encoded
bytes to the fetcher and were read from the saved PDF instead — those count as read.

## Claims

| # | Claim | Status | Source(s) |
|---|---|---|---|
| C1a | In Anthropic's production research system, subagents do not directly communicate with each other; each gets a task description and output format from a lead agent and returns findings to it. | single-source | https://www.anthropic.com/engineering/multi-agent-research-system |
| C1b | Cognition's argument against multi-agent systems targets the peer case specifically: dispersed decision-making, agents negotiating with each other without human-level communication efficiency, and subagents unable to see each other's work producing conflicting results. Their prescription is to share full agent traces rather than individual messages. Scope limit they state: the critique is aimed at 2025-era systems. | single-source | https://cognition.com/blog/dont-build-multi-agents |
| C1c | Two labs that publicly disagree about whether to build multi-agent systems at all nonetheless both avoid peer-to-peer agent communication — Anthropic by architecture, Cognition by prohibition. **This convergence is my synthesis across two sources, not a claim either source makes.** | **inference** | derived from C1a, C1b |
| C2 | Anthropic's multi-agent research system beat single-agent Claude Opus 4 by **90.2%** on their internal research eval, and multi-agent uses **~15× more tokens than chat** (single agent ~4×). | single-source | S3 |
| C3 | Anthropic names multi-agent as **unsuitable** for coding and for "highly dependent workflows where agents must coordinate extensively" / "all agents need to share the same context". | single-source | S3 |
| C4 | Every one of 18 frontier models tested degrades as input length grows, non-uniformly, on tasks as simple as retrieval and text replication. | single-source (18 models internally) | S1 |
| C5 | Chroma states **no explicit degradation-onset token threshold**, and cautions its minimal tasks likely *understate* real degradation. Any citation of Chroma for a specific threshold is unsupported. | single-source | S1 |
| C6 | Ungoverned accumulated memory can be **worse than no memory**: continuously LLM-consolidated textual memory rises then degrades *below the no-memory baseline*, and harm scales with volume written, not with any single bad write. Survey's summary: "stored state helps only when it is governed, not when it is simply accumulated." | single-source (survey over 435 coded works) | S2 §6.4.3 |
| C7 | Naive in-context use of recent history **outperforms several purpose-built memory architectures** on a continual-learning benchmark (Asawa et al. 2026, via survey). | single-source, relayed | S2 §6.4.3 |
| C8 | Shared memory between agents **propagates poisoning more readily than independent memory**; evaluator bias propagates to future agents with "no safe contamination threshold even under oracle consolidation" (named *memory contagion*). Per-agent safety checks are insufficient when the substrate carries state across the scope boundary. | single-source, relayed | S2 §6.5 |
| C9 | Continuously running multi-agent platforms show "governance divergence and outright collapse under identical setups over weeks to months" (Akkil et al. 2026, via survey). The survey adds the field "cannot yet explain or repair it mechanistically." | single-source, relayed | S2 §6.5 |
| C10 | The survey's **controlled-compounding criterion**: a system may claim it only if a regression traced to an earlier write can have that specific update identified, de-authorized and reverted *with its derived state*. "By that standard most current mechanisms have not demonstrated controlled compounding; they have demonstrated accumulation." | single-source | S2 §6.6 |
| C11 | Rollback is itself a fresh attack surface, and is the least-studied operation: **only 27 of 435 coded works expose any rollback mechanism**. Authority is the rarest axis (**72 of 435**). | single-source | S2 §8.2.6, §8.3 |
| C12 | Infinite Agentic Loops are **observed in the wild, not theorized**: static analysis over **6,549 real-world agent repositories** yielded 74 findings, **68 confirmed IAL failures across 47 projects**, 91.9% precision. Named causes include **multi-agent delegation**; impacts include cost exhaustion and model denial of service. | single-source | S7 |
| C13 | The IAL paper's framing: "**the key issue is not the presence of a loop, but whether an effective bound covers its feedback path**." Frameworks already ship bounds (`max_iterations`, `recursion_limit`, `max_turns`); failures come from omitting them, misconfiguring them, or placing them *outside* the actual feedback path. | single-source | S7 |
| C14 | Multi-agent debate improves accuracy in the reported literature, but the MAD survey states the field **lacks systematic negative-result reporting** and that topology/turn-taking are "design pattern by convention rather than systematic comparison" — fully-connected topology 72.2%, verbatim exchange 86.1%, both adopted by convention. No consensus on round count. | single-source (survey of 141 studies) | S5 |
| C15 | Named MAD failure modes: inter-agent **sycophancy**, premature convergence / echo chamber under homogeneous personas, **ordering bias and dominance effects**, consensus collapse on open-ended tasks. | single-source | S5 |
| C16 | A confidently-wrong agent can flip a correct answer in debate; even 3B–14B models "craft persuasive arguments that override truthful answers—often with high confidence." **Scope limit:** five open-source models 3B–14B, single-turn, TruthfulQA, judge from same model family. Does not establish the effect for frontier models in multi-round debate. | single-source, scope-limited | S6 |
| C17 | Sparse topologies and summarised (rather than verbatim) exchange retain or improve performance at significantly lower cost. | single-source | S5 |
| C18 | FinCon uses a manager-analyst hierarchy that routes messages only to workers who need them, "eliminating redundant peer-to-peer communication and saving communication costs." | **search-level — do not cite as established** | S8, S9 |
| C19 | The three failure classes that would bite `fund` hardest if seats were given a shared conversational substrate are memory contagion (C8), long-horizon governance divergence (C9), and unbounded delegation loops (C12) — none of which are addressed by charter prose, and all of which are substrate-level. | **inference** | derived from C8, C9, C12 |
| C20 | `fund`'s existing constraints — SQLite as sole truth, structured-tool-only emission, orchestrator-assigned turns, pre-registered proposals with `trial_id`, one-change-per-incubation-window, human-commit-only charter edits — are close to a working implementation of the survey's controlled-compounding criterion, which it says most systems fail. | **inference** | derived from C10, C11 + repo `specs/improvement.md` §0 |
| C21 | An orchestrator-bounded debate (fixed round count, code-assigned turns, prose passed through the brief) sits outside the IAL failure class by construction, because the bound is in the controller and covers the feedback path. | **inference** | derived from C13 |

## Coverage edges

- **Nothing in this run reaches `verified`.** The landing gate blocked the one claim I had marked
  so (the Anthropic/Cognition convergence) and the block was correct on substance, not merely
  mechanical: the convergence was my synthesis across two sources, not something either source
  asserts. It is now split into two single-source claims plus one labelled inference. Readers
  should treat every row here as one account unless they re-derive it.
- **C2's 90.2% is a vendor's internal eval**, not independently reproduced; treat as directional.
- **FinCon (S8) was never read** — its NeurIPS PDF exceeds the fetch size cap. The repo's existing
  `agent-roles-survey.md` claim about upward-only routing therefore remains uncorroborated by this
  run. Re-derive from the PDF directly if it becomes load-bearing.
- **C6–C9 are relayed through S2**, a survey. I read the survey's prose, not the underlying papers
  (Asawa, Zhang, Liu, Men, Akkil). Direction is well-supported; specific magnitudes are not.
- **C16's scope is narrow** and its authors are independent researchers, not a lab. Do not
  generalise CW-POR to a frontier-model bull/bear debate without re-testing.
- **The 69.1% / 95.6% figures that a search summary attributed to S7 were not present in the
  paper's abstract or §I–III and are deliberately excluded.** They may exist deeper in the paper;
  they were not verified and must not be cited.
- Not covered: durable-execution engines (Temporal-style) for agent supervision; the economics of
  resident processes at `fund`'s scale; anything specific to trading-domain multi-agent debate.
