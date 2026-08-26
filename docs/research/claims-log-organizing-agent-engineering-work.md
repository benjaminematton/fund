# Claims log — organizing engineering work under parallel AI agents

Run date 2026-08-26. 6 searches, 7 fetch attempts, 6 successful reads, 1 failed.

| Claim | Status | Source(s) |
|---|---|---|
| Review/verification capacity, not code generation, is the binding constraint when agents write code | **verified** | (read) addyosmani.com/blog/code-agent-orchestra — *"The bottleneck is no longer generation. It's verification."*; (read) blog.codacy.com/ai-agents-are-turning-developers-into-engineering-orchestrators — risk shifts from implementation to review; (read) dora.dev/insights/balancing-ai-tensions — "Velocity Paradox": time saved in creation is reallocated to verification. Three distinct origins. |
| Higher AI adoption correlates with **increased throughput AND increased delivery instability simultaneously** | **single-source** | (read) dora.dev/insights/balancing-ai-tensions. mstone.ai relays the same DORA finding, so it inherits the origin and does not corroborate. No independent replication read. |
| AI is an **amplifier**: orgs with strong platforms/testing gain; orgs with fragmented tooling or fragile infrastructure "simply generate technical debt faster" | **single-source** | (read) dora.dev/insights/balancing-ai-tensions |
| 3–5 parallel agents is the practical oversight ceiling; "don't run more agents than you can meaningfully review" | **single-source** | (read) addyosmani.com/blog/code-agent-orchestra |
| Named multi-agent failure mode **"Spec Amplification"** — ambiguous requirements propagate through parallel runs, each going wrong differently | **single-source** | (read) addyosmani.com/blog/code-agent-orchestra |
| Other named multi-agent failure modes: context overload, stuck agents (recommends MAX_ITERATIONS=8, kill after 3+ stuck iterations), architectural drift | **single-source** | (read) addyosmani.com/blog/code-agent-orchestra |
| Error budget policy: exceeding the budget over a 4-week window **halts all changes and releases** except P0/security until back within SLO | **single-source (primary)** | (read) sre.google/workbook/error-budget-policy — Google documenting its own practice; strongest available source for what Google does, but not independently corroborated |
| Error budget binding force comes from **public quarterly planning + accountability**, not from the metric; disputes escalate to the CTO | **single-source (primary)** | (read) sre.google/workbook/error-budget-policy |
| Error budget distinguishes **mandatory** reliability work (internal cause: bugs, procedure, dependencies) from **discretionary** (external cause) | **single-source (primary)** | (read) sre.google/workbook/error-budget-policy |
| ~38% of developers say reviewing AI-generated code takes more effort than reviewing human code | **single-source** | (read) mstone.ai/blog/ai-code-review-bottleneck cites Sonar 2026 survey; (read) blog.codacy.com cites The Register 2026 for the same figure — a relay of the same underlying survey, so ONE origin |
| GitHub May 2026: Copilot code review passed 60M reviews; >1 in 5 GitHub code reviews involved an agent | **single-source** | (read) mstone.ai/blog/ai-code-review-bottleneck citing GitHub |
| July 2026 GitHub study of 1.02M PRs: agent-involved review was faster but **efficiency gains did not translate into better review quality** | **single-source** | (read) mstone.ai/blog/ai-code-review-bottleneck |
| Spec-driven development in 2026 is "a discipline, not a tool"; EARS is the de-facto acceptance-criteria syntax; tasks should name the files likely to change and the acceptance criteria they satisfy | **search-level — NOT cited as support** | Search results only (dev.to, amux.io, thebcms.com). Not fetched. Directionally consistent with the fund's existing `specs/acceptance.md` practice but unverified. |
| Capacity-allocation benchmarks — GitLab 60/40 feature-vs-maintenance, "20% rule" per sprint, tech-debt ratio <10% | **search-level — NOT cited as support** | GitLab handbook fetch returned a navigation index only, never the content (rule 4: permanently search-level). All percentage figures come from search summaries. **Declared hole.** |
| "80% of internal developer platforms fail" | **rejected** | (read) platformengineering.org/blog/golden-cage-syndrome — fetched and read; the figure is in the headline with **no study, survey, or data** behind it. Not usable. |
| LinearB 2026: agentic PRs have 5.3× longer pickup time | **rejected as unsupported** | Attributed by a search summary to mstone.ai; on reading that article it contains **no mention of LinearB**. Never verified. |
| The fund's own shape — 34 issues opened / 8 closed in one 9-hour run, 28 of 45 open issues being tooling, 0 of 41 acceptance criteria on the board | **prior-knowledge (measured this session)** | Direct `gh` and `git` measurement in this repo, not a literature claim |

## Coverage edges

- **Allocation ratios are the declared hole.** The one primary source I tried (GitLab's handbook) failed to fetch. Every percentage benchmark stayed search-level, so no ratio is asserted in the readout or brief.
- **No source read addresses tooling investment growing out of proportion to the product it serves.** The platform-engineering literature covers *adoption* failure, not *proportion*. That specific concern is inference from the DORA amplifier finding, and is labelled as such.
- **Nothing read covers multi-agent work organization at the level of a work-board convention** — the closest is Osmani on team size and decomposition. The board/lane design questions remain unaddressed by literature.
