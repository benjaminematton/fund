# Handoff — MVF build (written 2026-08-12, Cowork scoping session)

For: the Claude Code terminal session executing `plans/mvf.md`. Everything you
need is in-repo; this file exists so you never re-litigate the scoping session.

## What you are building and why

A 3-day "Minimum Viable Firm" slice: analyst + PM agents making REAL daily
decisions from fresh market data via tools, inside the deterministic gate
envelope, live on Alpaca paper + one Slack app, scheduled, unattended,
<$0.50/day. Purpose: a shipped, live, genuinely-agentic system for Benjamin's
resume within 2–3 days. The agency lives in the analyst/PM seats (their own
tool choices, HOLD-is-a-real-answer); the gate/exec layer is deterministic BY
DESIGN — that contrast is the story, not a compromise.

## Binding documents (read in this order, nothing else needed)

1. `CLAUDE.md` — 7 invariants + test invariants. Unchanged, all bind.
2. `docs/superpowers/specs/2026-08-12-mvf-scope.md` — the scope. §6 holds 13
   adversarial-review decisions (A1–A4, C1–C4, T1–T4, P1–P4). BINDING.
3. `plans/mvf.md` — 16 TDD tasks with code, tests, commands, commits.
   Execute with subagent-driven-development, task by task, in order.

## State of the repo at handoff

- Phase 1 complete on `master` (exec plumbing, hooks, idempotency, ~50 offline
  tests green; 5 real live-integration bugs already found/fixed — see git log).
  Only unchecked Phase-1 box: the manual `@live` smoke.
- Uncommitted (commit these first, two commits):
  - `docs: MVF scope + review decisions + handoff` → the spec, this file
  - `docs: MVF implementation plan` → `plans/mvf.md`
  - (`research/improvement-loops.md` is unrelated draft work — leave it.)
- Offline suite verified green 2026-08-12 (33/34 in a pandas-3 sandbox; the one
  "failure" was the data_snapshot_hash env artifact — see Gotchas).

## Open human decisions (do NOT proceed past these silently)

1. **Golden-day 66 vs 67 (plan Decisions #14) — blocks Task 4 Step 1.**
   fixtures/golden-day.md step 4 says max tech add $12,160 → 67 shares, but
   0.60×$100,000 − (120×$232 + 40×$505 = $48,040) = $11,960 → floor(/180) = 66.
   Benjamin must re-record the fixture (deliberate re-record commit) or
   document a convention that yields 67. Scoping-session read: fixture slip;
   re-record to 66. NEVER code to 67 to make the test pass.
2. Slack channel names live vs test — decide at Task 16 (.env override exists).

## Environment facts (verified in the scoping session)

- Accounts in hand: Alpaca paper + keys, Slack workspace (admin), Console API
  key. Runtime MUST use the API key — subscription OAuth is Claude Code/
  Claude.ai-only (Anthropic policy, Feb 2026). Dev work = this session = sub.
- Cost bounds are in the spec (P1): watchlist ≤3, analyst max_turns 12, Haiku
  tier for analyst/exec, budgets in yaml. Target <$0.50/day live.
- Python: repo needs ≥3.12; dev venv is 3.14. `make test` bootstraps it.

## Gotchas inherited from Phase 1 (do not re-discover these)

- Hook `matcher` is a FULL match — prefix matchers silently never fire. Use
  `matcher=None` + self-filter (already the pattern in agents/trader.py).
- alpaca-mcp-server returns a JSON STRING envelope: order under `data`,
  string numerics, `_alpaca_mcp_security` key. `_extract_order` handles it;
  FakeAlpaca's `mcp_envelope` reproduces it — keep tests routed through it.
- Stop exits are `order_class='oto'`; `bracket` 422s without take_profit.
- `tools=[...]` is the capability lock; `allowed_tools` only pre-approves.
  Order-placing seats: `setting_sources=[]`, explicit MCP-glob tools array.
- tests/test_golden.py's `data_snapshot_hash` is numpy/pandas-version
  sensitive (CSV fingerprint). Pin pandas <3.0 (already pinned). A hash-only
  mismatch on a different stack is an env artifact, not a bug.
- `ResultMessage.total_cost_usd` is a client-side estimate — label "est.".

## Execution protocol

Per task: RED (write the failing test exactly as planned) → GREEN (minimal
implementation) → `make test` → conventional commit. Review checkpoint after
each task. Deviations from spec/plan → STOP and ask, never silently redesign.
Definition of done for the slice = spec §4 acceptance list, including one full
unattended live market day with `scripts/audit_day.py` clean.

## Day map (target)

- Day 1: commit docs → Tasks 1–3 → Slack app + .env setup (human, ~30 min
  dashboard work) → `@live` smoke green (evidence: fill JSON + Slack link)
  → Task 4 up to the STOP, get the 66/67 ruling.
- Day 2: Tasks 4–12 (the decision agents exist by end of day; first
  supervised live decision if market is open).
- Day 3: Tasks 13–16, full live day, schedule it, README + demo recording.

## Kickoff prompt (paste into Claude Code in the repo)

    Read docs/superpowers/handoffs/2026-08-12-mvf-handoff.md, then
    docs/superpowers/specs/2026-08-12-mvf-scope.md (§6 decisions are binding),
    then plans/mvf.md. Do not re-open scoping questions. First commit the
    uncommitted MVF docs as described in the handoff. Then execute plans/mvf.md
    with your subagent-driven-development skill, task by task, TDD, review
    checkpoint after each task. Report BLOCKED at Task 4 Step 1 per plan
    Decisions #14 (66-vs-67 golden fixture) and wait for my ruling. Never
    update a golden fixture or weaken a test to go green — STOP and ask.
