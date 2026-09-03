# Field brief: resident agents and agent-to-agent coordination

**Date:** 2026-09-03 · **Prepared for:** deciding whether `fund`'s seats should be long-running processes and whether they should talk to each other, before any spec is written · **Depth:** normal-to-deep, 6 searches / 11 fetch-read attempts (2 failed, disclosed below) · **Companion:** `claims-log-resident-agents-peer-coordination.md`

> **Standing caveat.** Nothing in this brief reached the two-independent-origin bar. Every row in
> the claims log is one account, or a labelled inference. The automated landing gate blocked the
> single claim originally marked verified, correctly — it was a synthesis, not a finding. Treat
> direction as well-supported and magnitudes as unconfirmed.

## State of the field *(prose)*

Two literatures that ought to be one are answering our question from opposite ends. The
**always-on / persistent-state** literature has just produced its first serious survey — Ding et
al.'s 435-work coded corpus (arXiv:2606.30306, June 2026) — whose central finding is that the
field has spent its effort on *accumulating and retrieving* state and almost none on *governing,
recovering, or relinquishing* it. Its definition is worth internalising: an always-on agent is one
"whose future behavior depends on durable state accumulated across earlier interactions" — a
property of **durable state, not of a persistent session**. That single move dissolves most of the
"should agents be long-running?" question: residency is about what survives, not about what stays
open.

The **multi-agent coordination** literature is louder and thinner. 2026's most-cited practitioner
texts are two engineering posts that appeared in opposition — Cognition's *Don't Build
Multi-Agents* and Anthropic's *How we built our multi-agent research system* — and the interesting
thing is not their disagreement but what they do identically: neither lets agents talk to each
other. Meanwhile the multi-agent-debate (MAD) survey (arXiv:2607.26212) audits 141 studies and
concludes the field's dominant design choices — fully-connected topology at 72.2%, verbatim message
exchange at 86.1% — were adopted "by convention rather than systematic comparison," and that
negative results are largely unreported. What changed most recently is that the failure modes
became measurable: Hou et al. (arXiv:2607.01641, July 2026) ran a static analyser over 6,549 real
agent repositories and confirmed 68 non-termination failures across 47 projects, with multi-agent
delegation among the named causes.

## Core concepts and vocabulary

- **Always-on / persistent-state system** — an agent whose future behaviour depends on durable state accumulated across earlier interactions. Explicitly *not* a claim about session lifetime.
- **Bounded fresh-context wake** — the dominant resident pattern: a supervised process that stays up, but does one unit of work per invocation with a fresh context seeded from durable state.
- **Context rot** — measurable degradation of model output as input length grows, independent of hitting the window limit (Chroma, 18 models).
- **Controlled compounding** — Ding et al.'s falsifiable criterion: a system may claim it only if a regression traced to an earlier write can have *that specific update* identified, de-authorised, and reverted **together with its derived state**. Most systems demonstrate accumulation instead.
- **The baseline-beats-memory finding** — controlled studies where purpose-built memory underperforms naive recent-history-in-context, and where consolidated memory eventually falls *below* the no-memory baseline.
- **Memory contagion** — evaluator bias or poison in stored trajectories propagating cross-temporally to later agents sharing a memory substrate, with no observed safe contamination threshold.
- **Cross-agent propagation** — the survey's term for corrupt state spreading through a shared substrate faster than per-agent safety checks can contain it, because each agent trusts the substrate.
- **Infinite Agentic Loop (IAL)** — "a structural execution failure where an agentic feedback path repeatedly triggers costly or state-growing actions without an effective stopping bound."
- **Bound coverage** — the IAL paper's key distinction: what matters is not whether a bound exists but whether it *covers the actual feedback path*. Most real failures had bounds placed outside it.
- **CW-POR** — confidence-weighted persuasion override rate: how often, and how confidently, a debate judge is flipped to a wrong answer by a more rhetorically forceful agent.
- **Inter-agent sycophancy** — debate agents reinforcing each other's errors rather than correcting them.
- **Ordering bias / dominance effects** — in MAD, which agent speaks first materially changes the outcome.

## Live debates and open questions *(prose)*

**Does multi-agent architecture help at all?** Cognition (the Devin team) argues no for most
builders: parallel subagents without shared context "make conflicting decisions," and their
prescription is to share *full agent traces* rather than individual messages. Anthropic reports the
opposite result — their multi-agent research system beat single-agent Claude Opus 4 by 90.2% on an
internal eval — but at roughly 15× the token cost of a chat, and they name the exceptions plainly:
multi-agent is unsuitable for coding and for "highly dependent workflows where agents must
coordinate extensively." The reconciliation most commentators reach is domain-shaped — multi-agent
for wide-and-shallow parallel search, single-agent for deep-and-narrow coherent work — but note
that this reconciliation is commentary, not a result either lab published. Cognition also dates its
own critique to 2025-era systems and expects it to age.

**Is peer-to-peer agent communication worth its cost?** This is where the two camps quietly agree
and the finance literature has already voted. Anthropic's subagents cannot coordinate mid-task by
design; Cognition's whole objection is to agents negotiating with each other. In trading
specifically, FinCon's manager-analyst hierarchy exists expressly to eliminate redundant
peer-to-peer messaging on cost grounds — though I could not read the FinCon paper (its NeurIPS PDF
exceeds the fetch size cap), so that stays search-level here and should be re-derived before anyone
leans on it. Against all this, the MAD survey finds sparse topologies and summarised exchange
retain or improve performance at materially lower cost, which suggests the real variable is
*bandwidth discipline* rather than peer contact as such.

**Does memory help, or does it eventually hurt?** This is the debate most likely to surprise, and
it runs against the industry's default assumption. Ding et al. report a controlled continual-learning
benchmark on which dedicated memory systems do not reliably beat naive in-context recent history
(Asawa et al. 2026), and a starker result in which continuously consolidated textual memory traces a
utility curve that rises and then degrades *below the no-memory baseline* as consolidation
accumulates (Zhang et al. 2026a) — harm scaling with total volume written, not with any single bad
write. The survey's own summary is the line to remember: stored state helps only when it is
governed, not when it is simply accumulated, and an ungoverned memory system can be strictly worse
than no memory once the horizon is long enough. These are relayed through the survey; I did not
read the underlying papers.

## Key claims log

Full table with statuses and per-claim scope limits:
`research/claims-log-resident-agents-peer-coordination.md`. The load-bearing rows:

| Claim | Status | Source |
|---|---|---|
| Anthropic's subagents do not directly communicate; they report to a lead agent | single-source | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| 90.2% over single-agent, at ~15× chat token cost; unsuitable for tightly-coupled work | single-source | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Cognition's critique targets peer negotiation specifically; scoped to 2025-era systems | single-source | [Cognition](https://cognition.com/blog/dont-build-multi-agents) |
| Both labs avoid peer-to-peer despite disagreeing on multi-agent generally | **inference** | derived |
| All 18 frontier models degrade with input length; **no onset threshold is stated** | single-source | [Chroma](https://www.trychroma.com/research/context-rot) |
| Ungoverned accumulated memory can fall below the no-memory baseline | single-source, relayed | [Ding et al.](https://arxiv.org/pdf/2606.30306) §6.4.3 |
| Shared memory propagates poisoning more readily than independent memory | single-source, relayed | [Ding et al.](https://arxiv.org/pdf/2606.30306) §6.5 |
| Long-running multi-agent platforms show governance divergence and collapse over weeks–months | single-source, relayed | [Ding et al.](https://arxiv.org/pdf/2606.30306) §6.5 |
| Rollback exposed by only 27 of 435 works; authority by only 72 of 435 | single-source | [Ding et al.](https://arxiv.org/pdf/2606.30306) §8.2.6, §8.3 |
| 68 confirmed non-termination failures across 47 real repos; multi-agent delegation a named cause | single-source | [Hou et al.](https://arxiv.org/pdf/2607.01641) |
| "The key issue is not the presence of a loop, but whether an effective bound covers its feedback path" | single-source | [Hou et al.](https://arxiv.org/pdf/2607.01641) |
| MAD topology and turn-taking are convention, not comparison; negative results under-reported | single-source | [MAD survey](https://arxiv.org/html/2607.26212) |
| A confidently-wrong agent can flip a correct one — but only shown for 3B–14B models, single-turn | single-source, scope-limited | [CW-POR](https://arxiv.org/pdf/2504.00374) |

## Practitioner heuristics *(prose)*

The people doing this well have converged on a shape that is neither "one immortal agent" nor
"stateless function": a **supervised process that stays up, wakes on an event, reads durable state,
does one bounded unit of work, writes state back, and ends the context**. Residency buys
availability and reaction time; the fresh context per wake is what buys reliability. Anyone
proposing a single conversation that runs for days is proposing the configuration the context-rot
and memory-degradation results specifically warn about.

On coordination, the operative discipline is bandwidth, not contact. Sparse topologies beat
fully-connected ones on cost at equal performance; summarised exchange beats verbatim; and the
strongest empirical signal in the debate literature is that *who speaks first* changes the answer,
which means unstructured free-for-all discussion is the worst of the options rather than the most
natural one. The insider move is to bound the conversation in the controller — fixed rounds,
assigned turns, explicit termination — because the IAL work shows the failures come from bounds
that exist but don't cover the feedback path, not from an absence of bounds.

The heuristic that transfers most directly to a trading firm is Ding et al.'s controlled-compounding
test, because it is falsifiable and most systems fail it: if a regression is traced to something
written weeks ago, can you identify that specific write, de-authorise it, and revert it *along with
everything derived from it*? A system that cannot do that has demonstrated accumulation, not
learning, however good its aggregate numbers look.

## Source shelf

- [Ding et al., *Always-On Agents: A Survey of Persistent Memory, State, and Governance in LLM Agents*](https://arxiv.org/pdf/2606.30306) — the canonical survey for the resident half; 435 coded works **(read: abstract, §6.4–6.6, §8.2.3–8.3)**
- [Hou et al., *When Agents Do Not Stop: Uncovering Infinite Agentic Loops in LLM Agents*](https://arxiv.org/pdf/2607.01641) — the only empirical measurement of non-termination in real repos **(read: abstract, §I–III)**
- [Anthropic, *How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system) — the pro-multi-agent production account, with costs and named exceptions **(read)**
- [Cognition, *Don't Build Multi-Agents*](https://cognition.com/blog/dont-build-multi-agents) — the dissenting view, and the sharpest statement of the peer-coordination objection **(read)**
- [*Multi-Agent Debate Strategies: Survey, Taxonomy, and Challenges*](https://arxiv.org/html/2607.26212) — best map of debate mechanics, and honest about the field's evidential weakness **(read)**
- [Chroma, *Context Rot*](https://www.trychroma.com/research/context-rot) — the context-length degradation result everyone cites, including its caveats **(read)**
- [Agarwal & Khanna, *CW-POR*](https://arxiv.org/pdf/2504.00374) — persuasion overriding truth in debate; narrow scope, useful framing **(read: abstract, §1)**
- [FinCon (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/file/f7ae4fe91d96f50abc2211f09b6a7e49-Paper-Conference.pdf) — the trading-specific hierarchy-over-peer-chat argument **(search-level — fetch exceeded size cap, never read)**

## Coverage edges *(prose)*

This brief does not cover durable-execution infrastructure (Temporal-style workflow engines applied
to agents), which is the obvious next place to look for the supervision half, nor the economics of
resident processes at a one-person firm's scale. The trading-specific literature is the thinnest
part: FinCon was never read, so the claim that finance systems deliberately route upward-only rests
on search-level material and the repo's own older survey. Everything attributed to Ding et al.
about Asawa, Zhang, Liu, Men and Akkil is relayed through the survey's prose rather than read at
source — the directions are well-supported, the magnitudes are not. And the CW-POR result should
not be generalised to a frontier-model bull/bear debate without re-testing, since it was
established on 3B–14B open-source models in a single-turn setup.
