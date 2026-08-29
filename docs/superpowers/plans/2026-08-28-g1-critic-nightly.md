# G1 Critic Nightly Turn — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close G1 enforcement by scheduling a Critic seat turn on the nightly 16:35 job that consumes the pending-spec queue through the two already-live MCP tools and writes a `strategy_critiques` verdict row.

**Architecture:** A new single-purpose composition root `scripts/critic_g1.py`, modelled on `scripts/reflect_day.py`, added as the **fourth and last** `ExecStart` of `ops/fund-pnl.service` (after `reflect_day.py`). It drives `run_day.make_turn` — inheriting `SEAT_MAX_WALL_S`, cost recording and `seat_turn_failed`/`seat_turn_timeout` alerting — with a new per-turn `tools=[...]` narrowing parameter on `build_seat_options` that cuts the Critic to exactly `get_spec_brief` + `submit_spec_critique` for this turn only. No new table, no new MCP tool, no new process manager, no charter edit, no `run_day.SEATS` entry.

**Tech Stack:** Python 3.12, sqlite3, pydantic v2, pytest, `claude-agent-sdk`, systemd (`Type=oneshot`), GNU make.

## Global Constraints

Every task's requirements implicitly include this section.

- **Invariant 2 (CLAUDE.md):** only the Execution Trader has the `trading` toolset; every other seat gets read-only Alpaca toolsets plus a `disallowed_tools` deny on `mcp__alpaca__place_*`. The per-turn override must never be able to widen a seat's surface — and "widen" is checked against what the seat is actually **served**, not against its yaml globs. See Task 1: a check against `cfg["tools"]` alone would accept `mcp__alpaca__place_stock_order` for the Critic, because `agents/config/critic.yaml` carries `tools: ["mcp__fund__*", "mcp__alpaca__*"]`.
- **Invariant 4 (CLAUDE.md):** default is HOLD. Any error, timeout, malformed input or ambiguity resolves to no action, never a guess. At G1 the *absence* of a row is the not-advancing signal.
- **Invariant 6 (CLAUDE.md):** SQLite is the source of truth. Outbound delivery goes through the `events` outbox.
- **Test invariants (CLAUDE.md):** tests are the spec; a failing test means the implementation is wrong. **NEVER update a golden fixture, expected hash, or expected value to make a test pass. STOP and ask. No "deliberate re-record."**
- **`fixtures/golden-strategy.md` is not touched.** Under the vacuity ruling it stays legal as written.
- **`charters/critic.md` is not touched.** Its header is `# Critic — v3`; `agents/seats.py:_parse_charter_version` reads the version from the first line and `strategy_critiques` CHECK-rejects `'unknown'`. A version bump forces an eval re-record.
- **`agents/config/critic.yaml`'s standing `tools` is not touched.** Leaving it alone keeps `specs/design.md:71`'s `stock-data` row true and keeps `tests/test_exec_seat_tool_surface.py`'s critic parametrization green.
- **`run_day.SEATS` stays at four entries** and `tests/test_run_day.py:724` is not edited.
- **`state/transition.py`'s `EDGES`, `state/schema.sql`, and everything under `specs/`, `charters/`, `evals/`, `stratgate/`, `fundbt/`, `gate/`, `orchestrator/daily.py` are OUT of region.** Needs there go to the Escalations section, not into a diff.
- New alert codes must be a bare `lower_snake` string literal at positional index 2 of `_alert(...)` / index 1 of `append_alert(...)`, matching `^[a-z][a-z0-9_]*$` — enforced by `scripts/check_alert_codes.py` inside `make lint`.
- Never put per-run values (spec ids, timestamps, tmp paths) into prompts.
- `make test` must pass before every commit.
- **This lane runs in a git worktree, which has no `.venv` of its own** — only the main checkout does, and a bare `python3` on macOS is 3.9. Every `Run:` step below is therefore written as `make deps && .venv/bin/python3 …`. `make deps` is the repo's documented bootstrap for exactly this case (`Makefile:7-9`: "plain `make test` works from a clean checkout or a fresh git worktree — `.venv` is created on first run"); it is idempotent and content-hash gated, so it costs a moment on the first invocation and nothing after. `make test` and `make -n` targets need no prefix — they depend on `deps` already.

---

## Design decision: a new sibling script, not a generalized `reflect_day.py`

**Recommendation: Option 1 — a new `scripts/critic_g1.py`.** The parent's assumption is correct, and here is the cost of each option so the CEO can see the arithmetic.

### Option 1 — new sibling script + a new `ExecStart` line (RECOMMENDED)

- **Cost:** ~170 lines of new script, ~35 of which are the env/lock/Slack/clock bootstrap that already exists in three other nightly roots. One new lock file (`critic_g1.lock`), one new test file, two Makefile targets, three ops-file edits.
- **Benefit:** `reflect_day.py`'s 25 existing tests, its 60-line docstring, its four reflect-vocabulary alert codes and its `reflect_and_log(conn, slack, clock, run_turn)` signature are untouched. Its own flock stays its own, so a reflect run that hangs in SDK teardown (the documented residual of `_bounded`) cannot hold the G1 leg out of the *next* night, and a hung G1 leg cannot hold reflect out of one. Two failure domains stay separate.
- **The duplication is the established convention, not new debt.** `close_pnl.py`, `resolve_day.py` and `reflect_day.py` each carry their own `paper_guard` / `require_env` / `connect` bootstrap today (`resolve_day.py:70-82` is the minimal form, `reflect_day.py:main` the full one). A fourth copy follows the repo; extracting a shared helper is Option 3.

### Option 2 — generalize `reflect_day.py` into a two-seat driver (REJECTED)

- **Cost:** `SEAT` and `SEAT_CONFIG` are module-level scalars (`reflect_day.py:88-89`) and `main()` is straight-line with no dispatch structure — a dispatch layer has to be invented. `reflect_and_log`'s `counts` dict (`{"reflected", "failed"}`), its four alert codes and `log()`'s hardcoded `"reflect_day: "` prefix are all reflect vocabulary, inline. 25 tests in `tests/test_reflect_job.py` bind that exact surface. The module docstring is a reflect-specific essay that would become half-false.
- **Benefit:** one bootstrap instead of two; one process start instead of two (saves 1–2 seconds on a 30-minute unit).
- **Verdict:** it buys seconds and pays in blast radius on the fund's memory pipeline. Reject.

### Option 3 — extract a shared `nightly bootstrap` helper and use it from both (DEFER)

- **Cost:** touches `reflect_day.py:main()`, which has **no direct test** (the suite never calls `main()` — it builds real clients). An unpinned refactor of a load-bearing leg, for a lane that does not need it.
- **Verdict:** YAGNI now. Revisit if a fifth nightly leg appears. Noted in Escalations.

### Option 4 — its own systemd unit + timer (ESCAPE HATCH)

- **Cost:** two new ops files, a droplet `systemctl enable` step somebody has to actually perform, and a second 30-minute budget to reason about.
- **Benefit:** its own `TimeoutStartSec`, so the G1 leg never competes with reflect for a shared window.
- **Verdict:** contradicts the CEO's "beside reflect" and adds ops surface for a queue with no live producer yet. And the thing it buys is worth little: a G1 turn that loses the window is re-selected **every** future night (`state/specs.py:specs_awaiting_critique` has no date bound), so losing the window costs a night, never a spec. Keep as the documented escape hatch if the leg is ever observed being cut off repeatedly.

### Leg order: `reflect_day.py` **third**, `critic_g1.py` **fourth and last** — SETTLED BY EVIDENCE

An earlier draft of this plan put `critic_g1.py` third, ahead of reflect, arguing that reflect could otherwise starve it "silently, forever, with no detector". **That argument was false three times over and is withdrawn.** This is no longer a place where two readings are defensible; it is decided by the following facts, each checked against the file named.

1. **Starvation is never silent — the unit alerts on it.** `ops/fund-pnl.service:4` is `OnFailure=fund-alert@%n.service` → `ops/fund-alert@.service` → `ops/notify_failure.sh`. Every starvation path — a `TimeoutStartSec` overrun, a nonzero exit, the guillotine — fails the *unit*, and systemd fires the alert unit, which posts to Slack by `curl` from `/etc/fund/alert-env`, deliberately sharing no dependency with the job. The premise the old ordering rested on — an undetectable absence — does not exist.
2. **The perishability runs the other way, and the old argument had it backwards.** `state/specs.py:specs_awaiting_critique` selects on `c.spec_id IS NULL` with **no date bound at all**: a spec skipped tonight is re-selected tomorrow night and every night after, forever. `scripts/reflect_day.py`'s `_DUE_WHERE` bounds on `resolved_at >= ? AND resolved_at < ?` with `REFLECT_LOOKBACK_DAYS = 7`, and `_AGED_OUT_WHERE` exists precisely to alert on rows that fell below that window and will **never** be written. **G1's misses are recoverable forever; reflect's are permanently destroyed after seven nights.** The old draft put the imperishable leg in front of the perishable one and then cited reflect's bounded retry as the justification — the retry bound is exactly what makes reflect the leg that must not be cut.
3. **The unit's existing comment already states the governing principle, and it points at last.** `ops/fund-pnl.service:27-31` says reflect is last "deliberately: it is the only leg that spends LLM budget and the only one that can fail on a missing `ANTHROPIC_API_KEY` or `SLACK_BOT_TOKEN`, so a failure here cannot cost the fund its P&L line or its calibration record." `critic_g1.py` spends LLM budget and depends on the same two secrets. The principle already committed to this file therefore puts it **after** reflect, not before — and the only change the new leg makes to that sentence is that there are now two such legs, not one.

**And the starvation scenario is not reachable at current volume anyway.** `config/watchlist.yaml` is capped at 3 tickers (spec-P1), so reflect's realistic nightly load is one turn per resolved decision — roughly 3 turns, not 25. `MAX_TURNS_PER_NIGHT = 25` is a backstop, not an expectation.

**Consequence, accepted openly:** the G1 leg is now the one the guillotine lands on. That is the correct place for it, because a cut G1 night costs a night and no spec (fact 2), the cut is alerted (fact 1), and the leg is hard-capped at `MAX_G1_TURNS_PER_NIGHT = 3` turns so it is also the cheapest leg to lose.

**`TimeoutStartSec` stays at 30min.** Raising it was considered and rejected on different grounds than the earlier draft gave. (The earlier draft's stated arithmetic — "the true worst case is 3×240 + 25×240 = 112 min, so no value under two hours makes both legs fit" — refutes itself: 112 minutes *is* under two hours. It is deleted, not repaired.) The reasons to leave the number alone are that 30min is the measured value this repo already uses to bound a hung LLM call in both units; that the realistic load is ~3 reflect turns plus ≤3 G1 turns ≈ 24 minutes of ceiling, not 112; and that raising an ops constant nothing tests, to a number nobody measured, in a lane that does not need it, is the change with the worse expected value. If the unit is ever observed timing out, the number is the lever — with a measurement behind it.

### Where the narrowed tool list lives

`G1_TOOLS` is a module constant in `scripts/critic_g1.py`, not a new key in `agents/config/critic.yaml`. **Two readings are defensible** and this is flagged rather than resolved silently: CLAUDE.md's "model ids and budgets live in `agents/config/*.yaml`, never hardcoded" could be read to cover tool surfaces (reflect's narrowing does live in `reflect.yaml`). The recommendation is the script, because this is a per-**turn** narrowing owned by the caller, not the seat's standing surface — the seat still legitimately holds `stock-data` for trade turns — and because #170's Phase 6 per-kind surfaces will likewise be owned at their call sites, which is what makes the `build_seat_options` parameter reusable rather than a second yaml schema.

### What bounds the turn count

`MAX_G1_TURNS_PER_NIGHT = 3`, **derived, not inherited** from reflect's 25:

1. **Wall clock.** `3 × SEAT_MAX_WALL_S (240s) = 12 min`, ≤ 40% of the unit's `TimeoutStartSec=30min`. This leg runs last, so 12 minutes is what it asks of whatever the three legs ahead of it left — and at the realistic load (two arithmetic legs of seconds each, plus ~3 reflect turns ≈ 12 min) that fits with room. Inheriting reflect's 25 would ask for 100 minutes of a 30-minute unit from the last position, i.e. would guarantee the leg is cut.
2. **Cost.** `3 × critic.yaml max_budget_usd ($0.75) = $2.25` hard backstop per night; against the measured Critic trial max of `$0.1867` (`evals/seats/critic.yaml`), expected ≤ `$0.56`/night. Inheriting 25 would be an `$18.75` backstop against a fund whose whole expected daily spend is under `$0.50`.
3. **Throughput.** `state/specs.py:specs_awaiting_critique`'s docstring fixes the design at one turn per spec (`limit=1` by default, which is what `get_spec_brief` calls). There is no live `submit_strategy_spec` producer today, so steady-state arrival is ≤1 spec/night; 3 drains any realistic backlog in one night and alerts `critic_g1_backlog_capped` naming the remainder when it does not.

### Failure and interrupt semantics

| Interrupt point | State left behind | Recovery |
|---|---|---|
| SIGTERM / crash **before** the seat calls `submit_spec_critique` | No `strategy_critiques` row. No default row is written by anything (`handle_submit_spec_critique` is the only writer; `tests/test_state_specs.py:203` lints `orchestrator/` and this plan adds the same lint for `scripts/critic_g1.py`). | `specs_awaiting_critique`'s predicate is `c.spec_id IS NULL`, so the same spec is re-selected the next night. Row-level idempotency, exactly like reflect's `r.reflection IS NULL`. |
| SIGTERM **during** `submit_spec_critique` | Impossible to tear in half: the `INSERT` + `append_event` + `conn.commit()` are one commit in the handler. Either both landed or neither. | n/a |
| SIGTERM **between** the tool's commit and the job's `has_verdict` re-read | Row exists; `spec_critique` event exists with `posted_at IS NULL`; the counter is lost. | `audit_day`'s undrained-events check has **no date bound**, so it reddens the next audit; the next `drain` posts it. `submit_spec_critique` is PK-write-once so a re-run cannot double-write, and `specs_awaiting_critique` will not re-select that spec. |
| SIGTERM mid-`drain` | Partially posted outbox. | `drain` selects on `posted_at IS NULL` — idempotent. |
| Turn **hangs** | `make_turn`'s `_bounded(SEAT_MAX_WALL_S=240s)` fires ~26 minutes before the unit's SIGTERM, raises `SeatTurnTimeout`, posts `seat_turn_timeout`, writes no row. | Same as row 1. |
| Process dies holding the flock | Kernel releases the flock with the open file description. | Tomorrow's fire is never blocked. |
| Turn returns **without writing** (the likeliest real failure — `make_turn.run()` catches everything and returns normally) | No row; job counts `failed`, appends `critic_g1_turn_wrote_nothing` naming the spec **and the number of specs still pending behind it**, and **breaks the loop**. | See head-of-line note below. Retried next night. |
| Turn writes a verdict for a **different** spec than the one it was shown | A verdict row exists for spec B; spec A still has none. | Detected: the job's post-turn `has_verdict(shown_spec_id)` is False, so the night counts `failed` and alerts. See the binding note below — the *behaviour* is right; only the earlier draft's claim about why was wrong. |

**Head-of-line blocking is structural and must be stated, not hidden.** `get_spec_brief` takes no arguments and always returns the *oldest* unreviewed spec. The job therefore cannot point a turn at spec B while spec A sits uncritiqued. Continuing the loop after a non-write would buy `MAX_G1_TURNS_PER_NIGHT` turns against the *same* spec and fail identically each time. Breaking bounds the spend at one turn; the alert names the blocking spec **and the pending count**, so an operator can see both what is stuck and how much is behind it. Removing the block needs a `spec_id` argument or a skip in `agents/tools/fund_server.py` — out of region, escalated.

**There is no spec-id binding on the write path, and the earlier draft claimed otherwise.** That draft said "the tool's own oldest-first selector is the binding." It is not. `agents/tools/fund_server.py:237` builds `SpecCritique(spec_id=args["spec_id"], ...)` — the spec id on the verdict comes from the **seat's own tool arguments**, and the only checks the handler makes are that the spec is registered (`:243-249`) and that it does not already carry a verdict (`:250-257`). The oldest-first selector binds what the seat is **shown**, not what it **writes**. So a turn shown spec A can submit a verdict for spec B; the write-once rule then makes B permanently unreviewable, `has_verdict(A)` is False, and the job alerts and breaks — reporting the symptom (A got no verdict) rather than the cause (B was overwritten).

The residual risk, stated rather than smoothed over: the job **detects** this (the post-turn `has_verdict(shown_spec_id)` re-read is exactly the instrument) and refuses to count it as success, but it cannot **prevent** it, and the misdirected row is not undoable through any shipped path. The real fix is a binding in the handler — a `spec_id` argument on `get_spec_brief`/`submit_spec_critique` checked against the brief the turn was served, or a server-side check that the submitted id is the current queue head. `agents/tools/fund_server.py` is out of this lane's region; escalated (Escalation 12).

---

## File Structure

**Create**

- `scripts/critic_g1.py` — the nightly G1 composition root. Selects the pending-spec queue head, buys one bounded Critic turn per head, re-reads the verdict, counts, alerts, drains. Never writes a verdict itself. Exits **nonzero** on failure, like `run_day.guarded` and for the same reason (see Task 5): it is the last leg, so nothing downstream can be harmed by a red exit, and `OnFailure=` is the only report path that does not share a failure mode with the job's own Slack client.
- `tests/test_critic_g1_job.py` — the job's decision seams and #169's acceptance bullets.

**Modify**

- `agents/seats.py` — `build_seat_options` gains a keyword-only `tools` override; new private `_turn_tools` helper enforcing narrowing against the seat's **served** surface (`SEAT_CAPS` + the seat's `alpaca_toolsets`), not against its yaml globs.
- `scripts/run_day.py` — `_seat_session` and `make_turn` thread an optional `tools=None` through to `build_seat_options`. Purely additive; `SEATS` and `SEAT_MAX_WALL_S` untouched.
- `scripts/reflect_day.py` — docstring only: reflect stays **third**, but "Third and last" is no longer true, so the oneshot-ordering paragraph names the new fourth leg behind it.
- `ops/fund-pnl.service` — fourth and last `ExecStart`, plus comment updates on the reflect line (it is still third; it is no longer last).
- `ops/README.md` — Units table row; the reflection-job key paragraph (`:142`, which says reflect "is the third and last leg" and "runs last deliberately"); the "Three things … deliberate" list; the Daily-operations block; new "Before the Critic's first live G1 night" checklist.
- `PROGRESS.md` — one row of the timers table (`:440`), which says `fund-pnl.timer` has "three `ExecStart=` lines, in that order (`ops/fund-pnl.service:19,26,32`)". False in the count and in every line number once the fourth leg lands. An orphan this change creates.
- `Makefile` — `critic-g1` and `critic-gate` targets, `.PHONY` entries.
- `tests/test_exec_seat_tool_surface.py` — new tests for the override. **No existing assertion is weakened.**
- `tests/test_run_day.py` — two existing test *fakes* gain a `tools=None` parameter (signature widening of a stub, not weakening of an assertion); new threading tests. **`test_the_seat_wall_clock_ceiling_fires_before_systemd_sigterms_the_unit` is not touched.**
- `tests/test_ops_units.py` — reads `ops/fund-pnl.service` for the first time; pins leg order, `Type=oneshot`, `OnFailure`, `TimeoutStartSec`, no `Restart=`, and the recorded ship-gate precondition.

---

## Task 1: Per-turn `tools` override on `build_seat_options`

**Files:**
- Modify: `agents/seats.py:65-135`
- Test: `tests/test_exec_seat_tool_surface.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_seat_options(cfg: dict, db_path, clock, *, snapshot=None, journals_root=None, expected_decision_id: int | None = None, tools: list[str] | None = None) -> ClaudeAgentOptions`. `tools=None` means "the seat's standing `cfg['tools']`", unchanged. A non-None value must be a non-empty list every entry of which the seat is actually **served** (see below); otherwise `ValueError`.

**What "narrowing" is checked against, and why not `cfg["tools"]` (defect found in review).** The first draft of this task checked each override entry against the seat's standing globs with `fnmatchcase`. That check is **decorative for five of the six seats**: `agents/config/critic.yaml`, and likewise `analyst`, `news`, `pm` and `exec`, all carry `tools: ["mcp__fund__*", "mcp__alpaca__*"]`, so the glob loop **accepts** `tools=["mcp__alpaca__place_stock_order"]` for the Critic — the exact seat this lane narrows, and the exact name invariant 2 exists to keep away from it. It rejects something only for `reflect`, whose standing tools are `["mcp__fund__*"]` — and the draft's test for the property was written against `reflect`, so the one seat where the guard worked was the one that was tested. Verified against both files.

**Decision: option (a) — check against the seat's served surface, in the strongest form the repo can actually express**, rather than demoting the guard to a typo check. Two halves, because the two MCP servers are knowable to different degrees:

- **`mcp__fund__*` — exactly checkable.** `agents/tools/fund_server.py:46`'s `SEAT_CAPS` *is* the registration: `build_fund_server` serves this seat only `{f"mcp__fund__{cap}" for cap in SEAT_CAPS[seat]}`. Anything else is not merely unwanted, it does not exist for that seat. So the check is set membership, and it bites: `mcp__fund__submit_decision` is refused for the Critic.
- **`mcp__alpaca__*` — not enumerable in-repo.** The alpaca surface comes from an external server (`ALPACA_MCP_SPEC = "alpaca-mcp-server@2.2.1"`) whose tool names this repo never lists; only the toolset *string* (`cfg["alpaca_toolsets"]`) is ours. So the check is the standing glob **plus** an invariant-2 rule keyed off the same field the order hooks key off (`agents/seats.py:118`): an `mcp__alpaca__place_*` name is refused unless the seat's `alpaca_toolsets` contains `trading`. That makes invariant 2 a property the override cannot violate for any read-only seat, instead of a claim.
- **Everything else** (a bare `Bash`, a `Read`) matches neither prefix and is refused outright — the preset must never reach a seat through this door.
- **A glob is refused before any of the above.** An entry containing `*`, `?` or `[` is rejected outright: a per-turn override must name concrete tools. This closes the same defect class one level up, because `tools` entries genuinely *are* globs in this repo — `agents/seats.py:92` passes `cfg["tools"]` straight to the SDK and every `agents/config/*.yaml` uses wildcards — so a wildcard override re-widens rather than being inert, and the `place_*` rule above is a *literal* name test that a wildcard walks past. Verified against the real configs: without this rule, seat `critic` **accepted** `['mcp__alpaca__*']`, `['mcp__alpaca__*place*']`, `['mcp__alpaca__pla?e_stock_order']` and `['mcp__alpaca__[pq]lace_stock_order']`. Nothing escalates today — critic's `alpaca_toolsets` is `stock-data` and its `disallowed_tools` denies `place_*`, so no order tool is served whatever the list says — so this closes an **overclaim**, not a live hole. The claim is asserted in three places, so the code has to be what is true.

This is honestly weaker than "provably a subset of the served surface" on the alpaca half, and the docstring says so rather than overselling it. **What #170's Phase 6 per-kind surfaces inherit, stated explicitly:** every entry is a concrete tool name and never a pattern; a per-turn narrowing can never name a `mcp__fund__` tool the fund server does not register for that seat; it can never grant an order-placing alpaca tool to a seat without the `trading` toolset; and it can never admit a builtin. It does **not** guarantee that every alpaca name passed is one the seat's toolsets actually serve — an unserved alpaca name is simply inert (the SDK never surfaces it), not a privilege escalation.

**The two halves remain asymmetric, and that survives the glob fix.** The fund half is exact set membership against `SEAT_CAPS`, so it is case-sensitive by construction and refuses `mcp__fund__SUBMIT_DECISION`. The alpaca half is a glob match plus a case-sensitive literal `place_*` test, so `mcp__alpaca__PLACE_stock_order` is still **accepted** — it matches the standing `mcp__alpaca__*` and not `mcp__alpaca__place_*`. It names no tool any server registers, so it is inert exactly like any other unserved alpaca name, and it is the price of a surface this repo cannot enumerate. Stated here so a reader of the guarantee list does not read symmetry into it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_exec_seat_tool_surface.py`:

```python
def _clock():
    return SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))


G1_TOOLS = ["mcp__fund__get_spec_brief", "mcp__fund__submit_spec_critique"]


def test_a_per_turn_tools_override_narrows_the_seat_for_one_turn(tmp_path):
    """The nightly G1 turn (issue #169) needs the Critic down to two tools for
    ONE turn, without editing agents/config/critic.yaml — the standing config
    is what specs/design.md's seat table describes and what the parametrized
    assertions above pin. #170's Phase 6 per-KIND tool surfaces reuse this
    same parameter rather than building their own."""
    opts = build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite",
                              _clock(), tools=G1_TOOLS)
    assert opts.tools == G1_TOOLS
    assert "mcp__alpaca__*" not in opts.tools
    assert "mcp__fund__*" not in opts.tools


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_the_override_is_absent_by_default_and_changes_no_seat(seat, tmp_path):
    """Additive by construction: every existing call site omits the kwarg."""
    assert _opts(seat, tmp_path).tools == _cfg(seat)["tools"]


@pytest.mark.parametrize("seat,refusal", [("critic", "invariant 2"),
                                          ("reflect", "may only NARROW")])
def test_a_per_turn_override_cannot_grant_an_order_tool(seat, refusal,
                                                        tmp_path):
    """PARAMETRIZED OVER critic, not only reflect — this is the review defect
    that made the first version of this guard decorative. agents/config/
    critic.yaml carries tools: ["mcp__fund__*", "mcp__alpaca__*"], so a check
    against the seat's standing GLOBS accepts mcp__alpaca__place_stock_order
    for the very seat this lane narrows. It rejected only for reflect, whose
    standing tools are ["mcp__fund__*"] — and reflect was the only seat the
    test covered.

    THE TWO SEATS ARE REFUSED FOR DIFFERENT REASONS, and the parametrization
    carries the message each one genuinely raises rather than a substring both
    happen to share. Asserting one message for both is what a first revision of
    this test did, and it shipped RED: agents/config/reflect.yaml:28 is
    `tools: ["mcp__fund__*"]` with no alpaca glob at all, so for reflect the
    name never reaches the invariant-2 branch — it is refused one step earlier,
    as a name the seat is not served. Loosening the pattern until both passed
    would have been the assertion that goes green under any refusal, which is
    exactly the decorative-guard defect this test exists to close.

      critic   IS served the alpaca glob, so the name is admitted by the
               standing surface and refused by the invariant-2 rule keyed off
               alpaca_toolsets — the branch that had to be added.
      reflect  is not served the alpaca surface at all, so the name is refused
               as ungranted. Still a refusal, and still worth pinning, but it
               proves nothing about invariant 2.

    Invariant 2 therefore holds for every seat without `trading` in
    alpaca_toolsets, which is every seat but exec — by the critic case."""
    with pytest.raises(ValueError, match=refusal):
        build_seat_options(_cfg(seat), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__alpaca__place_stock_order"])


def test_a_per_turn_override_cannot_name_a_fund_tool_the_seat_is_not_served(
        tmp_path):
    """SEAT_CAPS is the fund server's registration, so a name outside it does
    not exist for this seat. Checking against it makes the guard bite for the
    Critic, where the yaml glob `mcp__fund__*` admits everything."""
    with pytest.raises(ValueError, match="may only NARROW"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__fund__get_spec_brief",
                                  "mcp__fund__submit_decision"])


def test_a_per_turn_override_cannot_smuggle_in_a_builtin(tmp_path):
    """The whole point of `tools` is that the claude_code preset never reaches
    a seat. An override is not a second door into it."""
    with pytest.raises(ValueError, match="may only NARROW"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=["mcp__fund__get_spec_brief", "Bash"])


@pytest.mark.parametrize("pattern", [
    "mcp__alpaca__*",
    "mcp__alpaca__*place*",
    "mcp__alpaca__pla?e_stock_order",
    "mcp__alpaca__[pq]lace_stock_order",
    "mcp__fund__*",
    "mcp__fund__submit_*",
])
def test_a_per_turn_override_must_name_concrete_tools_never_a_glob(pattern,
                                                                   tmp_path):
    """AN OVERRIDE THAT IS A GLOB RE-WIDENS THE SURFACE, which is the same
    defect class the served-surface check exists to close, one level up.

    `tools` entries in this repo really are globs — agents/seats.py:92 passes
    cfg["tools"] straight to the SDK and every agents/config/*.yaml uses
    wildcards. So an override is not a list of names the SDK looks up; it is a
    list of PATTERNS the SDK expands. Before this rule, all four of these were
    ACCEPTED for the Critic: ['mcp__alpaca__*'], ['mcp__alpaca__*place*'],
    ['mcp__alpaca__pla?e_stock_order'] and ['mcp__alpaca__[pq]lace_...'] — the
    invariant-2 rule is a LITERAL fnmatchcase(name, "mcp__alpaca__place_*")
    test, and a wildcard walks straight past a literal test.

    Nothing escalates today: critic's alpaca_toolsets is `stock-data` and its
    disallowed_tools denies mcp__alpaca__place_*, so no order tool is actually
    served to the seat whatever this list says. This closes an OVERCLAIM, not a
    live hole — but the claim is made in three places, so the code has to be
    the thing that is true.

    Refusing the metacharacter, rather than trying to prove a glob is a subset,
    is deliberate: subset-of-a-glob is undecidable against an external server
    whose tool names this repo never enumerates, and a per-TURN narrowing has
    no legitimate need for a pattern — it names the two tools the turn uses."""
    with pytest.raises(ValueError, match="must name CONCRETE tools"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=[pattern])


def test_the_exec_seat_may_still_be_narrowed_to_its_own_order_tool(tmp_path):
    """The trading rule is keyed off alpaca_toolsets, the same field the order
    hooks key off (agents/seats.py:118) — not off a seat-name allow-list. The
    one seat that legitimately carries `trading` is not blocked from a future
    per-turn narrowing that includes its order tool."""
    opts = build_seat_options(_cfg("exec"), tmp_path / "fund.sqlite", _clock(),
                              tools=["mcp__alpaca__place_stock_order"])
    assert opts.tools == ["mcp__alpaca__place_stock_order"]


def test_an_empty_override_is_refused_not_read_as_no_narrowing(tmp_path):
    """[] and None are different intents and must not collapse. A seat with no
    tools cannot complete any turn; running one and paying for it is worse
    than refusing to build it."""
    with pytest.raises(ValueError, match="empty per-turn surface"):
        build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite", _clock(),
                           tools=[])


def test_a_narrowed_turn_keeps_every_other_guard(tmp_path):
    """A narrowing must not become the place a second guard is quietly
    dropped: the deny-list belt, the absence of order hooks, the empty
    setting_sources and the budget cap are all unchanged."""
    opts = build_seat_options(_cfg("critic"), tmp_path / "fund.sqlite",
                              _clock(), tools=G1_TOOLS)
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    assert opts.hooks in (None, {})
    assert opts.setting_sources == []
    assert opts.permission_mode == "dontAsk"
    assert opts.max_budget_usd == _cfg("critic")["max_budget_usd"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_exec_seat_tool_surface.py -v`
Expected: the nine new tests FAIL with `TypeError: build_seat_options() got an unexpected keyword argument 'tools'` (and `test_the_override_is_absent_by_default_and_changes_no_seat` passes already — it is the regression pin).

**Manufacture the red deliberately, twice — once per defect the guard closes.** A guard test that has never been seen to fail pins nothing, and these two fail against *different* naive versions. Once the implementation is in:

1. Temporarily replace `_turn_tools`'s served-surface check with the naive `any(fnmatchcase(t, glob) for glob in cfg["tools"])` version and re-run. `test_a_per_turn_override_cannot_grant_an_order_tool[critic-invariant 2]` and `test_a_per_turn_override_cannot_name_a_fund_tool_the_seat_is_not_served` must go RED; `[reflect-may only NARROW]` must stay GREEN. That asymmetry is the whole R3 finding — and it is also why the two seats are parametrized with different expected messages.
2. Temporarily delete the `patterned` branch and re-run. All six `test_a_per_turn_override_must_name_concrete_tools_never_a_glob` cases must go RED; every other test stays green — which is the point, since the glob hole was invisible to all of them.

Revert both immediately.

- [ ] **Step 3: Write the implementation**

In `agents/seats.py`, add to the imports at the top (beside `import re`):

```python
from fnmatch import fnmatchcase
```

and extend the existing `agents.tools.fund_server` import (no new module dependency — `build_fund_server` already comes from there):

```python
from agents.tools.fund_server import SEAT_CAPS, build_fund_server
```

Add this function immediately above `build_seat_options`:

```python
def _turn_tools(cfg: dict, override: list[str] | None) -> list[str]:
    """The tool surface for ONE turn: the seat's standing `tools`, unless the
    caller narrows it for this turn only.

    WHY A PARAMETER AND NOT A SECOND YAML KEY. The Critic legitimately carries
    `stock-data` for its trade turns (specs/design.md's seat table), and at G1
    its charter forbids using it — "never at G1, a spec is judged on its
    internal coherence". That is a property of the TURN, not of the seat, so it
    belongs at the call site that knows which turn it is. Issue #170's Phase 6
    per-kind surfaces are the same shape and reuse this parameter.

    NARROWING ONLY — CHECKED AGAINST WHAT THE SEAT IS SERVED, NOT AGAINST ITS
    YAML GLOBS. A check against cfg["tools"] would be decorative: critic.yaml
    (and analyst, news, pm, exec) carry ["mcp__fund__*", "mcp__alpaca__*"], so
    a glob check ACCEPTS mcp__alpaca__place_stock_order for the Critic. Only
    reflect, whose tools are ["mcp__fund__*"], would ever have rejected
    anything. So:

      a glob          REFUSED FIRST, before any of the below. See the next
                      paragraph — this is what makes the rest of the checks
                      mean anything.
      mcp__fund__*    exact membership in SEAT_CAPS[seat], which IS the fund
                      server's registration for this seat (build_fund_server
                      serves nothing else). A name outside it does not exist
                      for this seat, so refusing it is both the invariant-2
                      check and the typo check.
      mcp__alpaca__*  the external alpaca server's tool names are not
                      enumerable in this repo — only the toolset STRING is
                      ours. So: the standing glob must admit it, AND a
                      place_* name is refused unless cfg["alpaca_toolsets"]
                      contains `trading`. That is the same field the order
                      hooks key off (see below), which is what makes invariant
                      2 a property of this function rather than a claim about
                      it.
      anything else   refused. `tools` exists so the claude_code preset never
                      reaches a seat; an override is not a second door in.

    AN OVERRIDE MUST NAME CONCRETE TOOLS. `tools` entries are PATTERNS here —
    agents/seats.py:92 passes cfg["tools"] straight to the SDK and every seat
    yaml uses wildcards — so an override that is itself a glob re-WIDENS rather
    than narrows. Without this rule all of ['mcp__alpaca__*'],
    ['mcp__alpaca__*place*'], ['mcp__alpaca__pla?e_stock_order'] and
    ['mcp__alpaca__[pq]lace_stock_order'] were ACCEPTED for the Critic, because
    the invariant-2 rule below is a literal fnmatchcase(name, "...place_*")
    test and a wildcard walks past a literal test. Refusing the metacharacter
    is the fix rather than trying to prove a glob is a subset: subset-of-a-glob
    is undecidable against a server whose names this repo never enumerates, and
    a per-TURN narrowing has no legitimate use for a pattern.

    HONEST LIMIT, stated rather than oversold: on the alpaca half this is NOT a
    proof of subset. A read-only alpaca name the seat's toolsets do not
    actually serve would pass this check — and be inert, because the SDK never
    surfaces it.

    AND THE TWO HALVES ARE NOT EQUALLY STRONG, which survives the glob fix and
    is worth naming. The fund half is exact set membership, so it is
    case-sensitive by construction: `mcp__fund__SUBMIT_DECISION` is refused.
    The alpaca half is a glob match plus a case-sensitive literal test, so
    `mcp__alpaca__PLACE_stock_order` is ACCEPTED — it matches the standing
    `mcp__alpaca__*` and not `mcp__alpaca__place_*`. It names no tool any
    server registers, so it is inert for the same reason as any other unserved
    alpaca name; it is not an escalation, and it is the price of a surface this
    repo cannot enumerate. fnmatchcase, not fnmatch, is what keeps this a
    documented asymmetry instead of a platform-dependent one.

    WHAT IS ACTUALLY GUARANTEED, and it is what #170's Phase 6 per-kind
    surfaces inherit — no more: (i) every entry is a concrete tool name, never
    a pattern; (ii) no fund tool the seat is not served, exactly; (iii) no
    order tool for a seat without `trading` in alpaca_toolsets; (iv) no
    builtin. NOT guaranteed: that every alpaca name passed is one the seat's
    toolsets serve.

    fnmatchcase, not fnmatch: fnmatch normalises case on some platforms, and a
    tool surface that means something different on macOS than on the droplet is
    not a lock.

    [] is refused rather than read as "no narrowing". [] and None are different
    intents; collapsing them would buy a turn the seat cannot possibly
    complete, which costs money and writes nothing."""
    standing = cfg["tools"]
    if override is None:
        return standing
    seat = cfg["seat"]
    if not override:
        raise ValueError(
            f"build_seat_options: empty per-turn surface for seat"
            f" {seat!r} — a seat with no tools cannot complete a turn."
            " Pass tools=None to keep the seat's standing surface.")

    trades = "trading" in [t.strip()
                           for t in cfg["alpaca_toolsets"].split(",")]
    served_fund = {f"mcp__fund__{cap}" for cap in SEAT_CAPS.get(seat, ())}
    patterned, ungranted, escalating = [], [], []
    for name in override:
        if any(ch in name for ch in "*?["):
            patterned.append(name)
        elif name.startswith("mcp__fund__"):
            if name not in served_fund:
                ungranted.append(name)
        elif name.startswith("mcp__alpaca__"):
            if not any(fnmatchcase(name, glob) for glob in standing):
                ungranted.append(name)
            elif fnmatchcase(name, "mcp__alpaca__place_*") and not trades:
                escalating.append(name)
        else:
            ungranted.append(name)

    if patterned:
        raise ValueError(
            f"build_seat_options: a per-turn override must name CONCRETE"
            f" tools — {patterned} contain glob metacharacters. `tools`"
            " entries are PATTERNS in this repo (agents/seats.py:92 passes"
            " cfg['tools'] straight to the SDK), so a wildcard override"
            " re-widens the surface instead of narrowing it, and the"
            " invariant-2 place_* rule below is a literal name test a"
            " wildcard walks past. A per-turn narrowing names the tools the"
            " turn uses; widen the seat's yaml, where it is reviewed.")
    if escalating:
        raise ValueError(
            f"build_seat_options: a per-turn override may not grant"
            f" {escalating} to seat {seat!r}, whose alpaca_toolsets"
            f" ({cfg['alpaca_toolsets']!r}) do not include `trading` —"
            " invariant 2: only the Execution Trader places orders.")
    if ungranted:
        raise ValueError(
            f"build_seat_options: a per-turn override may only NARROW —"
            f" {ungranted} are not served to seat {seat!r} (fund tools:"
            f" {sorted(served_fund)}; alpaca globs: {standing}). Widen the"
            " seat's yaml, or SEAT_CAPS, where the change is reviewed.")
    return list(override)
```

**Note for the implementer:** `SEAT_CAPS` is imported from `agents.tools.fund_server`, which `agents/seats.py` already imports `build_fund_server` from — so this adds no new edge in the import graph and cannot create a cycle. Do not copy the capability names into `seats.py`; a second copy is exactly the drift `test_the_g1_surface_is_exactly_the_seats_two_g1_capabilities` exists to catch.

Change `build_seat_options`'s signature to:

```python
def build_seat_options(cfg: dict, db_path: str | Path, clock: Clock, *,
                       snapshot=None, journals_root=None,
                       expected_decision_id: int | None = None,
                       tools: list[str] | None = None
                       ) -> ClaudeAgentOptions:
```

Append to its docstring, after the `expected_decision_id` paragraph:

```
    `tools` narrows this ONE turn's surface below the seat's standing
    `cfg['tools']`. None (the default) means no narrowing — every existing
    call site is unaffected. It may only narrow, never widen, and "widen" is
    measured against what the seat is SERVED (SEAT_CAPS plus its
    alpaca_toolsets), not against its yaml globs; see _turn_tools.
```

Replace the `tools=cfg["tools"],` line in the `options = dict(...)` literal with:

```python
        tools=_turn_tools(cfg, tools),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_exec_seat_tool_surface.py -v`
Expected: PASS, all tests including the 30+ pre-existing parametrized ones.

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/seats.py tests/test_exec_seat_tool_surface.py
git commit -m "feat(seats): per-turn tools override on build_seat_options, narrowing-only

The nightly G1 Critic turn (#169) needs the seat down to get_spec_brief +
submit_spec_critique for one turn without editing critic.yaml's standing
surface.

Narrowing is checked against what the seat is SERVED, not against its yaml
globs: critic/analyst/news/pm/exec all carry [\"mcp__fund__*\",
\"mcp__alpaca__*\"], so a glob check would accept mcp__alpaca__place_stock_order
for the Critic. Fund names must be in SEAT_CAPS[seat]; an alpaca place_* name
needs `trading` in alpaca_toolsets, the same field the order hooks key off; and
every entry must be a CONCRETE name, since `tools` entries are patterns
(seats.py:92) and a glob override re-widens rather than narrows.
#170's Phase 6 per-kind surfaces inherit exactly those guarantees."
```

---

## Task 2: Thread `tools` through `make_turn` → `_seat_session`

**Files:**
- Modify: `scripts/run_day.py:251-265` (`_seat_session`), `scripts/run_day.py:359-413` (`make_turn`)
- Test: `tests/test_run_day.py`

**Interfaces:**
- Consumes: `build_seat_options(..., tools=...)` from Task 1.
- Produces: `make_turn(seat, cfg, db_path, clock, conn, run_date, prompt, snapshot=None, journals_root=None, trace_sink=None, turn_seq=None, expected_decision_id=None, tools=None) -> Callable[[], None]`. `tools` is forwarded verbatim to `build_seat_options`.

**Region note (flagged, not resolved silently):** `scripts/run_day.py` is shared trading-day code. The stated region is "the nightly job script(s) under `scripts/`", and `reflect_day.py` already drives its turns through `run_day.make_turn` — so the nightly path *is* this call chain. This change is additive with a `None` default and cannot alter any of the four daily turns. If the region is read more narrowly, the alternative is for `critic_g1.py` to build its own SDK session — which would forfeit `SEAT_MAX_WALL_S` bounding, cost recording and the `seat_turn_failed`/`seat_turn_timeout` alerts, and is not worth it. Flagged for the CEO.

- [ ] **Step 1: Widen the two existing test fakes**

This is a stub-signature widening, **not** a weakened assertion — both fakes will otherwise `TypeError` once `make_turn` forwards the kwarg unconditionally.

In `tests/test_run_day.py`, in `test_seat_session_threads_the_bound_id_to_build_seat_options`, change:

```python
    def _fake_build_seat_options(cfg, db_path, clock, *, snapshot=None,
                                 journals_root=None,
                                 expected_decision_id=None):
```

to:

```python
    def _fake_build_seat_options(cfg, db_path, clock, *, snapshot=None,
                                 journals_root=None,
                                 expected_decision_id=None, tools=None):
```

In `test_make_turn_threads_the_bound_id_to_seat_session`, change:

```python
    async def _fake_seat_session(cfg, db_path, clk, prompt, snapshot,
                                 journals_root, expected_decision_id=None):
```

to:

```python
    async def _fake_seat_session(cfg, db_path, clk, prompt, snapshot,
                                 journals_root, expected_decision_id=None,
                                 tools=None):
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_run_day.py`, immediately after `test_make_turn_threads_the_bound_id_to_seat_session`:

```python
def test_make_turn_threads_a_per_turn_tool_surface_to_seat_session(
        wired, monkeypatch):
    """The nightly G1 leg (#169) narrows the Critic to two tools for one turn.
    That narrowing is inert unless make_turn actually carries it down — the
    same class of hole test_read_only_seats_cannot_trade closes for the yaml
    value, which is why the id-binding chain above is pinned leg by leg."""
    conn, _, clock = wired
    seen = {}

    async def _fake_seat_session(cfg, db_path, clk, prompt, snapshot,
                                 journals_root, expected_decision_id=None,
                                 tools=None):
        seen["tools"] = tools
        return ([], _Result(turns=1))

    monkeypatch.setattr(run_day_script, "_seat_session", _fake_seat_session)

    _turn(conn, clock, seat="critic",
          tools=["mcp__fund__get_spec_brief"])()

    assert seen["tools"] == ["mcp__fund__get_spec_brief"]


def test_the_four_daily_turns_keep_their_standing_tool_surface(
        wired, monkeypatch):
    """Additive by construction: `tools` defaults to None and every trading-day
    call site omits it, so SEATS' four turns are byte-identical to before."""
    conn, _, clock = wired
    seen = {}

    async def _fake_seat_session(cfg, db_path, clk, prompt, snapshot,
                                 journals_root, expected_decision_id=None,
                                 tools=None):
        seen["tools"] = tools
        return ([], _Result(turns=1))

    monkeypatch.setattr(run_day_script, "_seat_session", _fake_seat_session)

    _turn(conn, clock, seat="analyst")()

    assert seen["tools"] is None


def test_seat_session_forwards_the_tool_surface_to_build_seat_options(
        monkeypatch):
    """The leg below make_turn: accepting the parameter and dropping it is the
    exact defect test_seat_session_threads_the_bound_id_to_build_seat_options
    exists to catch for expected_decision_id."""
    import asyncio

    import claude_agent_sdk

    seen = {}

    def _fake_build_seat_options(cfg, db_path, clock, *, snapshot=None,
                                 journals_root=None,
                                 expected_decision_id=None, tools=None):
        seen["tools"] = tools
        return object()

    class _FakeClient:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _fake_run_seat_turn(client, prompt, required):
        return ([], None)

    monkeypatch.setattr(run_day_script, "build_seat_options",
                        _fake_build_seat_options)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(run_day_script, "run_seat_turn", _fake_run_seat_turn)

    asyncio.run(run_day_script._seat_session(
        {"seat": "critic"}, ":memory:", SimClock(START), "p", None, None,
        tools=["mcp__fund__get_spec_brief"]))

    assert seen["tools"] == ["mcp__fund__get_spec_brief"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_run_day.py -k "tool_surface or forwards_the_tool" -v`
Expected: FAIL with `TypeError: make_turn() got an unexpected keyword argument 'tools'` and `_seat_session() got an unexpected keyword argument 'tools'`.

- [ ] **Step 4: Write the implementation**

In `scripts/run_day.py`, change `_seat_session`'s signature and its `build_seat_options` call:

```python
async def _seat_session(cfg: dict, db_path: str, clock, prompt: str,
                        snapshot, journals_root, expected_decision_id=None,
                        tools=None):
    """One seat's live SDK session. Options ALWAYS via build_seat_options —
    the tool surface, settings isolation and order hooks are decided there,
    never here (tests/test_exec_seat_tool_surface.py pins them).

    `expected_decision_id` is None for every seat but reflect; see
    build_seat_options.

    `tools` is None for every trading-day turn. scripts/critic_g1.py passes the
    G1 pair to narrow the Critic for that one nightly turn; build_seat_options
    refuses anything the seat's yaml does not already grant."""
    from claude_agent_sdk import ClaudeSDKClient

    options = build_seat_options(cfg, db_path, clock, snapshot=snapshot,
                                 journals_root=journals_root,
                                 expected_decision_id=expected_decision_id,
                                 tools=tools)
    async with ClaudeSDKClient(options=options) as client:
        return await run_seat_turn(client, prompt, REQUIRED_SERVERS)
```

Change `make_turn`'s signature:

```python
def make_turn(seat: str, cfg: dict, db_path: str, clock, conn, run_date: str,
              prompt: str, snapshot=None, journals_root=None,
              trace_sink=None, turn_seq=None, expected_decision_id=None,
              tools=None):
```

Add to its docstring, after the `expected_decision_id` line:

```
    `tools` is scripts/critic_g1.py's per-turn narrowing of the Critic's
    surface to the two G1 tools; every other caller leaves it None.
```

And in its `run()` body, change the `_seat_session(...)` call to:

```python
            names, result = asyncio.run(_bounded(
                _seat_session(cfg, db_path, clock, prompt, snapshot,
                              journals_root,
                              expected_decision_id=expected_decision_id,
                              tools=tools),
                SEAT_MAX_WALL_S))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_run_day.py -v`
Expected: PASS, including `test_the_seat_wall_clock_ceiling_fires_before_systemd_sigterms_the_unit` (unmodified — `SEATS` still has four entries).

- [ ] **Step 6: Commit**

```bash
git add scripts/run_day.py tests/test_run_day.py
git commit -m "feat(run_day): thread an optional per-turn tools surface into make_turn

Additive with a None default: SEATS' four daily turns are unchanged and
test_the_seat_wall_clock_ceiling... is untouched. Two existing test fakes gain
the kwarg in their signatures (stub widening, no assertion weakened)."
```

---

## Task 3: `scripts/critic_g1.py` — the queue loop and its happy path

**Files:**
- Create: `scripts/critic_g1.py`
- Test: `tests/test_critic_g1_job.py`

**Interfaces:**
- Consumes: `state.specs.specs_awaiting_critique(conn, *, limit=1)`; `run_day._alert`, `run_day.paper_guard`, `run_day.require_env`, `run_day.acquire_lock`, `run_day.parse_channel_overrides`, `run_day.RemappedSlack`, `run_day.make_turn`; `slackkit.outbox.drain`; `orchestrator.clock.et_run_date`, `iso`; `state.db.connect`.
- Produces:
  - `critic_g1.SEAT = "critic"`, `SEAT_CONFIG`, `LOCK_NAME = "critic_g1.lock"`, `REQUIRED_ENV`
  - `critic_g1.G1_TOOLS: list[str]`, `critic_g1.G1_PROMPT: str`, `critic_g1.MAX_G1_TURNS_PER_NIGHT: int`
  - `critic_g1.next_pending_spec(conn) -> str | None`
  - `critic_g1.pending_count(conn) -> int` (saturating at `PENDING_REPORT_LIMIT`)
  - `critic_g1.has_verdict(conn, spec_id: str) -> bool`
  - `critic_g1.critique_and_log(conn, slack, clock, run_turn) -> dict` returning `{"critiqued": int, "failed": int}`
  - `critic_g1._make_run_turn(seat, cfg, db_path, clock, conn, run_date) -> Callable[[dict], None]` — the callable takes `{"spec_id": str}`
  - `critic_g1._guarded(conn, slack, clock, body) -> int` — body's own code, or **1** on failure
  - `critic_g1._build_slack(env, environ)` — the patchable Slack seam `main()` builds before the guard
  - `critic_g1.main(argv=None) -> int`

- [ ] **Step 1: Write the failing tests (happy path + queue semantics)**

Create `tests/test_critic_g1_job.py`:

```python
"""Offline tests for the nightly G1 job's decision seams (issue #169).

scripts/critic_g1.py is a composition root like reflect_day.py, so main() is
never called here — it builds real clients. What is pinned is what it SELECTS,
what it does when a turn misbehaves, and that it writes no verdict of its own,
because every turn it runs costs real money and every row it touches is the
gate a strategy passes through.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.tools.fund_server import handle_submit_spec_critique
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state.db import connect
from state.models import StrategySpec
from state.specs import insert_strategy_spec, specs_awaiting_critique

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "critic_g1.py"

# 2026-08-25 16:35 ET == 20:35 UTC (EDT) — the scheduled fire.
NIGHTLY = datetime(2026, 8, 25, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("critic_g1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


critic_g1 = _load()

# Copied from tests/test_state_specs.py — the same shape state.specs already
# pins, so a spec this fixture can build is a spec insert_strategy_spec can.
SPEC = dict(
    family="F1", seat="quant",
    hypothesis="Reversal pays for absorbing forced selling.",
    mechanism_class="liquidity_provision",
    universe={"index": "Russell 1000", "pit_constituents": True, "filters": []},
    liquidity_bucket="mega_large",
    signal_rule={"entry": "5d return below -1.5 sigma"},
    param_ranges={"sigma": [1.0, 2.5, 0.25]},
    search_budget=24, holding_period_d=5, rebalance="daily",
    expected_turnover=42.0, exit_rule="close at 5 trading days",
    invalidation="12m low-turnover spread negative for two quarters.",
    capacity_usd=4000000.0,
    predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
    llm_in_loop=0)


def _spec(conn, *, family="F1", created_at="2026-08-25T18:00:00+00:00") -> str:
    """One registered spec with NO critique row — the G1 precondition. `family`
    varies the content because spec_id is the hash of the FIELDS: two specs
    that differ only in created_at collide on the primary key and the second
    insert is silently ignored."""
    return insert_strategy_spec(conn, StrategySpec(**dict(SPEC, family=family)),
                                created_at)


def _verdict(conn, spec_id: str, verdict: str = "clear",
             objections=()) -> None:
    """Write a verdict exactly the way a real turn does — through the handler,
    with attribution bound by the caller (strategy_critiques forbids
    'unknown'). Never a raw INSERT: a fixture that can write a row the handler
    would refuse is a fixture that tests nothing."""
    result = handle_submit_spec_critique(
        conn, seat="critic",
        args={"spec_id": spec_id, "verdict": verdict,
              "objections": list(objections)},
        now_iso=iso(NIGHTLY), charter_version="v3",
        model_id="claude-sonnet-5")
    assert result["ok"], result


def _alert_texts(conn) -> list[str]:
    return [r["payload"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def _undrained(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


# --- #169 bullet 1a: a registered spec gets a critique row that night --------

def test_a_pending_spec_gets_a_verdict_row_the_same_night(db):
    sid = _spec(db)

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": 1, "failed": 0}
    rows = [dict(r) for r in db.execute(
        "SELECT spec_id, verdict, seat, charter_version, model_id"
        " FROM strategy_critiques")]
    assert rows == [{"spec_id": sid, "verdict": "clear", "seat": "critic",
                     "charter_version": "v3", "model_id": "claude-sonnet-5"}]
    assert _undrained(db) == 0          # the spec_critique event reached Slack


def test_the_queue_is_taken_oldest_first(db):
    """get_spec_brief's selector is ORDER BY created_at, spec_id — the job must
    not impose its own order, or the seat would be shown a different spec than
    the job re-reads."""
    old = _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    new = _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    seen = []

    def _turn(job):
        seen.append(job["spec_id"])
        _verdict(db, job["spec_id"], "clear")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _turn)

    assert seen == [old, new]
    assert counts == {"critiqued": 2, "failed": 0}


def test_a_night_with_nothing_pending_runs_no_turn_and_says_so(db, capsys):
    """An empty queue is the normal state today — there is no live
    submit_strategy_spec producer yet. Spending nothing is correct, and this
    leg costs $0 on such a night."""
    ran = []

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        lambda job: ran.append(job))

    assert ran == [] and counts == {"critiqued": 0, "failed": 0}
    assert "critic_g1:" in capsys.readouterr().out
    assert _alert_texts(db) == []


def test_a_spec_that_already_carries_a_verdict_is_never_bought_again(db):
    """Row-level idempotency, the only kind on this path: there are no
    checkpoints on the nightly job. A re-fire pays only for what is still
    pending — the same predicate that makes a SIGTERM'd night retryable."""
    _spec(db)
    critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                               lambda job: _verdict(db, job["spec_id"]))

    bought = []
    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY), lambda job: bought.append(job))

    assert bought == []
    assert counts == {"critiqued": 0, "failed": 0}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 1


def test_the_job_never_writes_a_verdict_of_its_own(db):
    """strategy-contracts.md §3.4: no default row, ever. The job SELECTS the
    queue and RE-READS the result; the only INSERT is the seat's own tool call.
    Same instrument tests/test_state_specs.py:203 points at orchestrator/ — a
    lint, not a comment, because prose cannot hold this."""
    source = SCRIPT.read_text()
    for verb in ("INSERT INTO strategy_critiques",
                 "UPDATE strategy_critiques",
                 "DELETE FROM strategy_critiques",
                 "insert_strategy_spec"):
        assert verb not in source, f"{SCRIPT.name} writes G1 state: {verb!r}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -v`
Expected: FAIL at collection — `FileNotFoundError: scripts/critic_g1.py`.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/critic_g1.py`:

```python
#!/usr/bin/env python3
"""Nightly G1 enforcement — gives `strategy_critiques` its producer (#169).

    make critic-g1         # == python scripts/critic_g1.py

strategy-contracts.md §2 records that nothing yet READS strategy_critiques.
The two MCP tools have shipped since 2026-08-20 and nothing has ever opened a
Critic session for them. This is the caller.

WHY IT RIDES THE 16:35 TIMER — not the trading day. The G1 queue holds
nothing time-sensitive (a spec's worst case is 30d-idle staleness), and a
fifth scheduled seat turn does not fit the day's wall-clock budget:
tests/test_run_day.py's ceiling test derives turns_per_day from run_day.SEATS
and 5 x SEAT_MAX_WALL_S = 1200s exceeds the 0.6 x 30min = 1080s it allows.
reflect_day.py is the precedent — a seat turn on the nightly job, outside
SEATS and outside design.md §3.

WHY FOURTH AND LAST, i.e. AFTER reflect. Three reasons, each checkable:

  1. This unit's own comment already states the principle: reflect is last
     because "it is the only leg that spends LLM budget and the only one that
     can fail on a missing ANTHROPIC_API_KEY or SLACK_BOT_TOKEN, so a failure
     here cannot cost the fund its P&L line or its calibration record." This
     leg spends LLM budget and needs the same two secrets. The principle the
     file already committed to therefore puts it behind reflect, not in front.
  2. PERISHABILITY. state/specs.py:specs_awaiting_critique selects on
     `c.spec_id IS NULL` with NO date bound: a spec skipped tonight is
     re-selected tomorrow night and every night after, forever. reflect's
     _DUE_WHERE bounds on resolved_at within REFLECT_LOOKBACK_DAYS=7, and
     _AGED_OUT_WHERE exists to alert on rows that fell below that window and
     will NEVER be written. G1 misses are recoverable; reflect misses are
     destroyed. The perishable leg goes first.
  3. Losing the window is NOT silent, so there is nothing to protect against
     by going early. ops/fund-pnl.service:4 is OnFailure=fund-alert@%n.service
     -> ops/notify_failure.sh, which posts by curl from /etc/fund/alert-env and
     deliberately shares no dependency with this job. An overrun, a nonzero
     exit and the guillotine all fail the UNIT and all alert.

(At current volume the question is close to moot: config/watchlist.yaml is
capped at 3 tickers, so reflect's realistic load is ~3 turns, not its
MAX_TURNS_PER_NIGHT=25 backstop.)

WHY THIS LEG EXITS NONZERO ON FAILURE, unlike an earlier design. Nothing runs
after it, so a red exit cannot cost the fund anything downstream — and going
quiet costs a lot: an alert appended to `events` is only visible once it
DRAINS, and if the failure IS Slack, the drain fails too and the night is
invisible. OnFailure= is the one report path that does not share a failure
mode with this job's own Slack client. So: alert + drain (best effort) AND
return 1, the same posture as run_day.guarded. See _guarded.

Posture (invariant 4: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'  -> exit 1 before a client is built
  * a missing env var             -> exit 1 naming every missing var
  * another critic_g1 running     -> exit 0 rather than double-spend (not a
                                     failure: the other process is doing the
                                     work)
  * a turn that raises            -> one alert, NO row, night continues
                                     (defence in depth — not reachable through
                                     run_day.make_turn today)
  * a turn that writes nothing    -> one alert naming the spec AND how many
                                     specs are still pending, and the loop
                                     STOPS (see head-of-line, below)
  * more than MAX_G1_TURNS_PER_NIGHT pending -> take the cap, alert how many
                                     were left, the night continues
  * anything else after connect() -> one alert, drained best-effort, EXIT 1 so
                                     systemd's OnFailure reports it even if
                                     Slack is what broke

NEVER A DEFAULT ROW. This module SELECTS the queue and RE-READS the result;
the only INSERT into strategy_critiques anywhere is the seat's own
submit_spec_critique call. At G1 the absence of a row IS the not-advancing
signal (specs/strategy.md invariant 7), so a default row would silently
advance a spec nobody reviewed. Pinned by a source lint in
tests/test_critic_g1_job.py, the same instrument tests/test_state_specs.py:203
points at orchestrator/.

HEAD-OF-LINE BLOCKING IS STRUCTURAL, and the loop is shaped around it.
get_spec_brief takes NO arguments and always returns the OLDEST unreviewed
spec, so this job cannot point a turn at spec B while spec A sits uncritiqued.
Continuing after a turn that wrote nothing would buy MAX_G1_TURNS_PER_NIGHT
turns against the SAME spec and fail identically each time. So the loop
breaks, the spend is bounded at one turn, and the alert names the spec that is
blocking AND the number still pending behind it — a blocked head with four
specs queued behind it is a different operator problem from a blocked head
that is the whole queue, and one alert must be able to tell them apart.
Removing the block needs a spec_id argument in agents/tools/fund_server.py,
which is out of this lane's region.

THE VERDICT IS NOT BOUND TO THE SPEC THE TURN WAS SHOWN. handle_submit_spec_
critique builds SpecCritique(spec_id=args["spec_id"], ...) — the id comes from
the SEAT's tool arguments, and the handler only checks that the spec is
registered and unreviewed. The oldest-first selector binds what the seat is
SHOWN, never what it WRITES, so a turn shown spec A can write a verdict for
spec B; B then becomes permanently unreviewable (write-once) and A is still
pending. This job DETECTS that — has_verdict(shown_spec_id) is False, so the
night counts it as a failure and alerts — but it cannot PREVENT it. The fix is
a binding in fund_server.py; escalated, out of region.

INTERRUPT SEMANTICS. There are no checkpoints on the nightly path;
idempotency is row-level, exactly like reflect's `reflection IS NULL`.
  * killed before the tool call        -> no row; specs_awaiting_critique's
                                          `c.spec_id IS NULL` re-selects the
                                          same spec the next night
  * killed DURING the tool call        -> impossible to tear in half: the
                                          INSERT + append_event + commit are
                                          one commit inside the handler
  * killed between that commit and     -> the row and the event stand, only
    this job's re-read                    the counter is lost; the undrained
                                          event reddens the next audit (that
                                          check has no date bound) and the
                                          next drain posts it. A re-run
                                          cannot double-write: the verdict is
                                          PK-write-once and the spec is no
                                          longer selected.
  * killed mid-drain                   -> drain selects posted_at IS NULL;
                                          idempotent
  * a HUNG turn                        -> run_day.make_turn's _bounded fires
                                          at SEAT_MAX_WALL_S (240s), ~26min
                                          before the unit's SIGTERM, posts
                                          seat_turn_timeout, writes no row
  * the process dying with the flock   -> the kernel releases it with the open
                                          file description; tomorrow is never
                                          blocked

ALERT ARITHMETIC, so an operator is not surprised by the count.

  * run_day.make_turn posts its OWN seat_turn_failed/seat_turn_timeout, one per
    failing turn, before this job ever sees the turn return — from here that
    turn just looks like "wrote nothing". So a night whose single turn crashes
    posts TWO alert messages (one seat_turn_failed, one
    critic_g1_turn_wrote_nothing), not one.
  * A SUCCESSFUL turn also posts: the spec_critique event renders to #research
    (slackkit/render.py).
  * And expect a model_fallback_used post to #risk on top of that, roughly one
    per turn. agents/config/critic.yaml pins model: claude-sonnet-5, and this
    event is a KNOWN FALSE POSITIVE for Sonnet-configured seats — an SDK
    auxiliary haiku call shows up in model_usage and record_turn_result reads
    the extra key as a fallback. It is not a real fallback and does not mean
    the verdict was written by a model other than the configured one. It is
    deliberately not an `alert` kind (audit_day fails the day on any alert),
    so it does not redden the audit; it does cost a Slack post.

So the realistic count for a clean night with one pending spec is TWO posts
(#research verdict + #risk fallback), and for a crashed turn TWO alerts plus
whatever make_turn already posted.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/critic_g1.py` anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.clock import et_run_date, iso       # noqa: E402
from slackkit.outbox import drain                     # noqa: E402
from state.db import connect                          # noqa: E402
from state.specs import specs_awaiting_critique       # noqa: E402

# Identical to reflect_day's, and for the same reasons: this job runs a seat
# (ANTHROPIC_API_KEY) and drains (SLACK_BOT_TOKEN), and build_seat_options
# wires the alpaca MCP server unconditionally for every seat — which
# run_seat_turn then requires to be CONNECTED, even though the narrowed G1
# surface can reach none of its tools. That coupling is issue #108, not a
# property of this seat.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
                "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY")

# Its own lock, NOT reflect's. A shared one would let a G1 turn hanging in SDK
# teardown (the documented residual of run_day._bounded) hold reflect out of
# its own night, and a hung reflect hold G1 out of the next one.
LOCK_NAME = "critic_g1.lock"

SEAT = "critic"
SEAT_CONFIG = ROOT / "agents" / "config" / f"{SEAT}.yaml"

# The Critic's surface for THIS turn only. agents/config/critic.yaml keeps its
# standing ["mcp__fund__*", "mcp__alpaca__*"] — that is what specs/design.md's
# seat table describes and what tests/test_exec_seat_tool_surface.py pins, and
# the seat legitimately needs stock-data for a trade turn. At G1 its charter
# says the opposite ("never at G1 — a spec is judged on its internal
# coherence"), so the narrowing belongs to the TURN. build_seat_options refuses
# any name critic.yaml does not already grant, so this can only subtract.
#
# Exactly the two capabilities SEAT_CAPS["critic"] carries: the two locks agree
# by test, not by comment.
G1_TOOLS = ["mcp__fund__get_spec_brief", "mcp__fund__submit_spec_critique"]

# Byte-identical to evals/prompts.py's "critic" template, and pinned to it by
# test. That file's own drift guard derives its seat list from run_day.SEATS,
# where the Critic deliberately is not — so nothing else would catch a prompt
# this job sends that the eval rig does not evaluate.
#
# It names NO spec. get_spec_brief's own oldest-first selector is what binds a
# turn to a spec; a spec id in the prompt would be a per-run value in prompt
# text, which breaks replay (CLAUDE.md).
G1_PROMPT = ("G1 review turn. Start by calling get_spec_brief, then follow"
             " your charter and end by calling submit_spec_critique exactly"
             " once, for the spec in your brief.")

# DERIVED, not inherited. reflect_day's MAX_TURNS_PER_NIGHT=25 is sized for one
# turn per resolved decision; taking that number here would ask for 25 x 240s =
# 100 minutes and 25 x $0.75 = $18.75 of worst case from the LAST position on a
# unit whose whole budget is 30 minutes, for a fund whose whole expected daily
# spend is under $0.50 — i.e. would guarantee this leg is cut. Three, because:
#   wall clock  3 x run_day.SEAT_MAX_WALL_S (240s) = 12 min, <= 40% of the
#               unit's TimeoutStartSec=30min, which fits behind two arithmetic
#               legs (seconds each) and reflect's realistic ~3 turns
#   cost        3 x critic.yaml max_budget_usd ($0.75) = $2.25 hard backstop;
#               against the measured Critic trial max of $0.1867
#               (evals/seats/critic.yaml) the expectation is <= $0.56/night
#   throughput  state/specs.py fixes the design at ONE turn per spec, and
#               there is no live submit_strategy_spec producer yet, so
#               steady-state arrival is <= 1 spec/night. Three drains any
#               realistic backlog in one night.
# Exceeding it is never silent — see critique_and_log's critic_g1_backlog_capped.
MAX_G1_TURNS_PER_NIGHT = 3

# How many pending specs an alert will count before it says "N+". The canonical
# selector defaults to limit=1 (state/specs.py), so a count needs a limit
# argument rather than a second query carrying its own copy of the predicate —
# a duplicated selector is how the job and the tool come to disagree about what
# "pending" means.
PENDING_REPORT_LIMIT = 50


def log(msg: str) -> None:
    print(f"critic_g1: {msg}", flush=True)


def pending_count(conn) -> int:
    """How many specs still await a verdict, saturating at
    PENDING_REPORT_LIMIT.

    Reported in the blocking and cap alerts. Without it, a blocked head with
    four specs queued behind it produces exactly the same Slack message as a
    blocked head that is the entire queue — and those are different operator
    problems with different urgency."""
    return len(specs_awaiting_critique(conn, limit=PENDING_REPORT_LIMIT))


def _count_text(n: int) -> str:
    """'4' or '50+' — never a number an operator would read as exact when it
    is a saturating count."""
    return f"{n}+" if n >= PENDING_REPORT_LIMIT else str(n)


def next_pending_spec(conn) -> str | None:
    """The spec id at the head of the G1 queue, or None if it is empty.

    Deliberately the SAME selector handle_get_spec_brief uses, with the same
    default limit=1: the seat is shown the head, so the job must re-read the
    head, or the row it checks is not the row the turn reviewed.

    DOES NOT DEGRADE TO None ON ERROR. A read failure raising is what makes it
    distinguishable from an empty queue — the same posture handle_get_spec_brief
    documents at length. _guarded turns the raise into an alert."""
    pending = specs_awaiting_critique(conn)
    return pending[0]["spec_id"] if pending else None


def has_verdict(conn, spec_id: str) -> bool:
    """Did the turn actually write? Success is never inferred from the absence
    of an exception: run_day.make_turn's own run() catches every exception and
    returns normally, so the likeliest real failure — a seat that never calls
    submit_spec_critique, or calls it and gives up on {"ok": false} — would
    raise nothing here either."""
    return conn.execute(
        "SELECT 1 FROM strategy_critiques WHERE spec_id = ?",
        (spec_id,)).fetchone() is not None


def critique_and_log(conn, slack, clock, run_turn) -> dict:
    """One turn per queue head, up to MAX_G1_TURNS_PER_NIGHT, then drain.
    Returns the counts.

    `run_turn` takes {"spec_id": ...}. That id is carried for the post-turn
    re-read and the log line ONLY — it never reaches the prompt.

    THERE IS NO BINDING, and that is a gap, not a design. Unlike reflect's
    expected_decision_id, nothing ties the verdict the seat writes to the spec
    it was shown: handle_submit_spec_critique takes spec_id from the seat's own
    tool arguments and only checks that it is registered and unreviewed. The
    oldest-first selector binds the SHOW, not the WRITE. The has_verdict()
    re-read below is therefore load-bearing — it is what turns "the turn wrote
    a verdict for some other spec" into a counted failure with an alert instead
    of a silent success. Adding the real binding is a fund_server change, out
    of region, escalated.

    The alerts and the drain both run in `finally`, for reflect_day's N1
    reason: a DB error on a LATER iteration must not skip either. Appending
    only after the loop meant such a raise never QUEUED the alert at all — not
    merely left it undrained — so Slack learned nothing. And draining alone
    was not enough: a freshly-appended alert with posted_at IS NULL has no date
    bound on the audit check that catches it, so it would redden every audit
    until the next drain."""
    counts = {"critiqued": 0, "failed": 0}
    stalled: dict | None = None
    capped = False
    remaining = 0
    try:
        for _ in range(MAX_G1_TURNS_PER_NIGHT):
            head = next_pending_spec(conn)
            if head is None:
                break
            try:
                run_turn({"spec_id": head})
            except Exception as exc:
                log(f"spec {head} — turn raised {type(exc).__name__}: {exc};"
                    " no verdict written")
                stalled = {"spec_id": head, "why": "raised",
                           "detail": f"{type(exc).__name__}: {exc}"}
                counts["failed"] += 1
                break
            if has_verdict(conn, head):
                counts["critiqued"] += 1
                continue
            log(f"spec {head} wrote no verdict — the turn returned without"
                " calling submit_spec_critique (or the call was refused, or it"
                " wrote a verdict for a DIFFERENT spec); stopping, since the"
                " next turn would be shown the same spec")
            # Counted HERE, not in `finally`: the head is still pending at this
            # moment, and a read in `finally` could raise on a broken DB and
            # mask the failure it is trying to describe.
            stalled = {"spec_id": head, "why": "wrote_nothing", "detail": "",
                       "pending": pending_count(conn)}
            counts["failed"] += 1
            break
        else:
            capped = next_pending_spec(conn) is not None
            remaining = pending_count(conn) if capped else 0
        log(f"critiqued {counts['critiqued']} · failed {counts['failed']}")
    finally:
        if stalled and stalled["why"] == "raised":
            run_day._alert(conn, clock, "critic_g1_turn_failed",
                           f"critic_g1_turn_failed spec"
                           f" {stalled['spec_id']} — {stalled['detail']};"
                           f" no verdict written, no default row, the spec"
                           f" stays pending for the next night")
        elif stalled:
            run_day._alert(conn, clock, "critic_g1_turn_wrote_nothing",
                           f"critic_g1_turn_wrote_nothing — spec"
                           f" {stalled['spec_id']} got a turn and no verdict;"
                           f" the G1 queue is oldest-first with no skip, so"
                           f" this spec blocks the queue until it clears."
                           f" {_count_text(stalled['pending'])} spec(s) are now"
                           f" pending, including this one. They stay pending"
                           f" for the next night")
        if capped:
            run_day._alert(conn, clock, "critic_g1_backlog_capped",
                           f"critic_g1_backlog_capped — the G1 queue still has"
                           f" {_count_text(remaining)} pending spec(s) after"
                           f" tonight's {MAX_G1_TURNS_PER_NIGHT}-turn cap"
                           f" (MAX_G1_TURNS_PER_NIGHT={MAX_G1_TURNS_PER_NIGHT});"
                           f" the rest stay pending for the next night")
        drain(conn, slack, iso(clock.now()))
    return counts
```

(The remaining functions — `_make_run_turn`, `_guarded`, `main` — land in Task 5. This task stops at a module that imports and whose queue loop is green.)

Append a temporary `if __name__ == "__main__":` guard only in Task 5; do not add one yet.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -v`
Expected: PASS — six tests.

- [ ] **Step 5: Run the alert-code lint**

Run: `make deps && .venv/bin/python3 scripts/check_alert_codes.py`
Expected: exit 0. The three new codes are bare `lower_snake` literals at positional index 2 of `run_day._alert(...)`.

- [ ] **Step 6: Commit**

```bash
git add scripts/critic_g1.py tests/test_critic_g1_job.py
git commit -m "feat(critic_g1): nightly G1 queue loop over specs_awaiting_critique

One bounded Critic turn per queue head, up to a DERIVED cap of 3 (12 min /
\$2.25 worst case), oldest-first, re-reading the verdict after each turn.
Writes no verdict itself — pinned by a source lint."
```

---

## Task 4: Failure, cap and interrupt semantics

**Files:**
- Test: `tests/test_critic_g1_job.py` (append)
- Modify: `scripts/critic_g1.py` only if a test finds the Task-3 implementation wrong.

**Interfaces:**
- Consumes: everything Task 3 produced.
- Produces: no new symbols. This task proves the semantics Task 3's code claims.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_critic_g1_job.py`:

```python
# --- #169 bullet 2: a crashed turn writes nothing and the night completes ---

def test_a_turn_that_writes_no_verdict_stops_the_night_and_alerts(db):
    """The likeliest real failure: run_day.make_turn's run() catches every
    exception and returns normally, so a seat that never calls
    submit_spec_critique never raises here. Counting on the absence of an
    exception would report that turn as critiqued.

    STOPPING, not continuing: get_spec_brief takes no arguments and always
    returns the OLDEST unreviewed spec, so the next turn would be shown the
    SAME spec and fail the same way. Breaking bounds the spend at one turn
    instead of MAX_G1_TURNS_PER_NIGHT.

    THE ALERT MUST CARRY THE PENDING COUNT. Because the loop breaks, the
    for...else never runs and `capped` stays False — so without the count this
    is the ONLY message the operator gets, and a head blocking four specs looks
    exactly like a head that is the whole queue."""
    blocking = _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    behind = _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    ran = []

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: ran.append(job["spec_id"]))

    assert ran == [blocking]                       # `behind` was never bought
    assert counts == {"critiqued": 0, "failed": 1}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 0       # NO DEFAULT ROW, EVER
    texts = _alert_texts(db)
    assert len(texts) == 1                         # no cap alert on this path
    assert "critic_g1_turn_wrote_nothing" in texts[0]
    assert blocking in texts[0]
    assert behind not in texts[0]                  # the id, not the count
    assert "2 spec(s) are now pending" in texts[0]
    assert _undrained(db) == 0


def test_the_blocking_alert_counts_everything_still_queued(db):
    """Five pending, the head blocking: one alert, and it must say five. The
    count is what tells an operator whether this is one stuck spec or a stalled
    pipeline."""
    for i in range(5):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        lambda job: None)

    assert counts == {"critiqued": 0, "failed": 1}
    texts = _alert_texts(db)
    assert len(texts) == 1
    assert "5 spec(s) are now pending" in texts[0]


def test_a_turn_that_raises_leaves_no_row_and_the_spec_still_pending(db):
    """Defence in depth for a run_turn that DOES raise — not reachable through
    make_turn today, and costs nothing to keep. It is also the exact shape a
    turn abandoned at SEAT_MAX_WALL_S leaves behind."""
    sid = _spec(db)

    def _boom(job):
        raise TimeoutError("no result after 240s (SEAT_MAX_WALL_S ceiling)")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _boom)

    assert counts == {"critiqued": 0, "failed": 1}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 0
    # the whole recovery story, asserted rather than asserted-in-prose:
    assert [s["spec_id"] for s in specs_awaiting_critique(db)] == [sid]
    texts = _alert_texts(db)
    assert len(texts) == 1 and "critic_g1_turn_failed" in texts[0]
    assert _undrained(db) == 0


def test_an_interrupted_night_is_retried_not_lost(db):
    """The systemd-SIGTERM story, modelled: night one buys a turn that never
    writes; night two re-selects the same spec and completes it. There are no
    checkpoints on this path — the `c.spec_id IS NULL` predicate is the whole
    recovery mechanism."""
    sid = _spec(db)

    critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                               lambda job: None)
    assert [s["spec_id"] for s in specs_awaiting_critique(db)] == [sid]

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": 1, "failed": 0}
    assert specs_awaiting_critique(db) == []


def test_a_verdict_is_written_once_so_a_re_fire_cannot_double_it(db):
    """The window a SIGTERM between the tool's commit and this job's re-read
    leaves open. submit_spec_critique is PK-write-once and refuses a second
    verdict with the first intact, so re-running the night is safe."""
    sid = _spec(db)
    _verdict(db, sid, "objections", ["the entry clause ignores funding cost"])

    second = handle_submit_spec_critique(
        db, seat="critic",
        args={"spec_id": sid, "verdict": "clear", "objections": []},
        now_iso=iso(NIGHTLY), charter_version="v3", model_id="claude-sonnet-5")

    assert second["ok"] is False and "written once" in second["error"]
    assert db.execute("SELECT verdict FROM strategy_critiques WHERE spec_id = ?",
                      (sid,)).fetchone()["verdict"] == "objections"


# --- the cap: what bounds the number of turns per night ---------------------

def test_the_night_is_capped_and_a_silent_cap_is_alerted(db):
    n = critic_g1.MAX_G1_TURNS_PER_NIGHT
    for i in range(n + 2):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")
    ran = []

    def _turn(job):
        ran.append(job["spec_id"])
        _verdict(db, job["spec_id"], "clear")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _turn)

    assert len(ran) == n
    assert counts == {"critiqued": n, "failed": 0}
    texts = _alert_texts(db)
    assert len(texts) == 1
    assert "critic_g1_backlog_capped" in texts[0]
    assert "2 pending spec(s)" in texts[0]     # n+2 registered, n critiqued
    assert "stay pending for the next night" in texts[0]
    assert _undrained(db) == 0


def test_a_cap_that_exactly_drains_the_queue_raises_no_alert(db):
    """The cap alert must mean "there is a backlog", not "we hit the number"."""
    n = critic_g1.MAX_G1_TURNS_PER_NIGHT
    for i in range(n):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": n, "failed": 0}
    assert _alert_texts(db) == []


def test_the_nightly_cap_is_derived_not_inherited_from_reflect(db):
    """reflect's MAX_TURNS_PER_NIGHT=25 is sized for one turn per resolved
    decision. Inheriting it here would ask for 100 minutes and \$18.75 of worst
    case from the LAST leg of a unit whose whole budget is 30 minutes — i.e.
    would guarantee this leg is cut by the guillotine.

    A guard-rail on the constants, not on behaviour — the behaviour is pinned
    by the cap test above. This exists so a later re-tune cannot silently
    cross the unit's budget or the fund's daily spend."""
    import yaml

    cap = critic_g1.MAX_G1_TURNS_PER_NIGHT
    critic = yaml.safe_load((ROOT / "agents/config/critic.yaml").read_text())

    assert cap >= 1
    # at most 40% of the nightly unit's TimeoutStartSec=30min, leaving the rest
    # for two arithmetic legs and reflect
    assert cap * critic_g1.run_day.SEAT_MAX_WALL_S <= 0.4 * 30 * 60
    # and a hard cost backstop an operator would not be shocked by
    assert cap * critic["max_budget_usd"] <= 2.5


# --- the drain is in `finally` ---------------------------------------------

def test_a_db_error_mid_queue_still_drains_what_the_night_produced(
        db, monkeypatch):
    """reflect_day's N1, borrowed: audit_day's undrained-events check has NO
    date bound, so an appended-but-undrained event reddens every audit until
    the next drain. The drain lives in `finally` for exactly that reason."""
    _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    calls = {"n": 0}
    real = critic_g1.next_pending_spec

    def _flaky(conn):
        calls["n"] += 1
        if calls["n"] > 1:
            raise sqlite3.OperationalError("database is locked")
        return real(conn)

    monkeypatch.setattr(critic_g1, "next_pending_spec", _flaky)

    with pytest.raises(sqlite3.OperationalError):
        critic_g1.critique_and_log(
            db, FakeSlack(), SimClock(NIGHTLY),
            lambda job: _verdict(db, job["spec_id"], "clear"))

    assert _undrained(db) == 0
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 1


def test_the_verdict_reaches_research_through_the_outbox(db):
    """Invariant 6: outbound delivery goes through the events outbox, so a
    crash or retry can neither lose nor duplicate a post. slackkit/render.py
    projects a spec_critique event to #research."""
    sid = _spec(db)
    slack = FakeSlack()

    critic_g1.critique_and_log(
        db, slack, SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "objections",
                             ["the entry clause ignores the funding-cost"
                              " condition the hypothesis calls essential"]))

    kinds = [r["kind"] for r in db.execute(
        "SELECT kind FROM events WHERE posted_at IS NOT NULL")]
    assert "spec_critique" in kinds
    # slackkit/fake.py:13 — `posts` is dict[str, list[dict]] KEYED BY CHANNEL,
    # so iterating it yields channel-name strings, not posts. Index the channel
    # instead, which also asserts the thing the docstring claims: the verdict
    # reached #research, not merely somewhere.
    research = slack.posts.get("#research", [])
    assert any(sid in str(post) for post in research), slack.posts
```

**Verified, not assumed:** `slackkit/fake.py:13` declares `self.posts: dict[str, list[dict]] = {}` and `:24` does `self.posts.setdefault(channel, []).append(...)`. `slackkit/render.py:272-284` routes `spec_critique` to `#research` ("at G1 nothing has been risked yet"). The earlier draft's `any(sid in str(post) for post in slack.posts)` iterated the dict's keys — channel names — and would have failed; the fallback it suggested (dropping the line) would also have dropped the channel check.

- [ ] **Step 2: Run the tests to verify they fail (or reveal a Task-3 defect)**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -v`
Expected: PASS if Task 3's implementation is correct. **If any fail, the implementation is wrong, not the test** (CLAUDE.md: tests are the spec). Fix `scripts/critic_g1.py`; never relax an assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/test_critic_g1_job.py scripts/critic_g1.py
git commit -m "test(critic_g1): failure, cap and interrupt semantics

#169 bullet 2: a crashed or silent turn leaves no critique row, no default
row, one alert, and a night that completes. Plus the derived-cap guard rail
and the finally-drain property."
```

---

## Task 5: The turn seam, the guard, and `main()`

**Files:**
- Modify: `scripts/critic_g1.py` (append `_make_run_turn`, `_guarded`, `main`, `__main__` guard)
- Test: `tests/test_critic_g1_job.py` (append)

**Interfaces:**
- Consumes: `run_day.make_turn(..., tools=...)` from Task 2; `G1_TOOLS`, `G1_PROMPT` from Task 3.
- Produces: `_make_run_turn`, `_guarded`, `_build_slack`, `main` as declared in Task 3's interface block. `_guarded` returns the body's own code on success and **1** on failure; `main` returns 0 on a clean night or a held lock, 1 on any failure after `connect()`, and exits nonzero before that on `paper_guard`/`require_env`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_critic_g1_job.py`:

```python
# --- #169 bullet 3: the turn's tool surface ---------------------------------

def test_the_g1_surface_is_exactly_the_seats_two_g1_capabilities(db):
    """Two locks, one surface. SEAT_CAPS decides which tools the fund MCP
    server REGISTERS for this seat; G1_TOOLS decides which names the SDK makes
    AVAILABLE for this turn. If they ever disagree, one of them is decorative
    — and the decorative one is always the one somebody trusts."""
    from agents.tools.fund_server import SEAT_CAPS

    assert set(critic_g1.G1_TOOLS) == {
        f"mcp__fund__{cap}" for cap in SEAT_CAPS["critic"]}


def test_the_g1_turn_can_reach_no_broker_tool_and_no_other_submit(db, tmp_path):
    """#169: "Critic turn cannot call any other submit_* or broker tool."
    `tools` governs AVAILABILITY — it is the real lock; allowed_tools and
    disallowed_tools only govern approval and fail open."""
    from agents.seats import build_seat_options, load_seat_config

    opts = build_seat_options(
        load_seat_config(ROOT / "agents/config/critic.yaml"),
        tmp_path / "fund.sqlite", SimClock(NIGHTLY), tools=critic_g1.G1_TOOLS)

    assert opts.tools == ["mcp__fund__get_spec_brief",
                          "mcp__fund__submit_spec_critique"]
    assert not any(t.startswith("mcp__alpaca__") for t in opts.tools)
    for forbidden in ("mcp__fund__submit_decision", "mcp__fund__submit_signal",
                      "mcp__fund__submit_reflection", "mcp__fund__submit_critique",
                      "mcp__fund__get_stage_brief", "mcp__fund__list_open_tickets",
                      "mcp__fund__*", "Bash", "Write", "Task", "Read"):
        assert forbidden not in opts.tools
    # the belt stays on even though the brace already holds
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    assert opts.hooks in (None, {})     # no order gate on a read-only seat
    assert opts.setting_sources == []   # no CLAUDE.md, no dev settings


def test_the_turn_is_built_with_the_narrowed_surface(db, monkeypatch):
    """The narrowing is inert unless _make_run_turn actually passes it."""
    seen = {}

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen.update(kwargs)
        seen["seat"] = seat
        return lambda: None

    monkeypatch.setattr(critic_g1.run_day, "make_turn", _fake_make_turn)

    run_turn = critic_g1._make_run_turn(
        "critic", {}, ":memory:", SimClock(NIGHTLY), db, "2026-08-25")
    run_turn({"spec_id": "0123456789abcdef"})

    assert seen["seat"] == "critic"
    assert seen["tools"] == critic_g1.G1_TOOLS


# --- #169 bullet 4: the turn is replayable ---------------------------------

def test_the_g1_prompt_is_byte_identical_to_the_one_the_eval_rig_sends(db):
    """evals/prompts.py's drift guard derives its seat list from run_day.SEATS,
    where the Critic deliberately is not — so nothing else catches a prompt
    this job sends that the rig does not evaluate, and a rig evaluating a
    prompt production no longer sends measures nothing."""
    from evals.prompts import PROMPT_TEMPLATES

    assert critic_g1.G1_PROMPT == PROMPT_TEMPLATES["critic"]


def test_the_prompt_carries_no_per_run_value(db, monkeypatch):
    """CLAUDE.md: per-run values reach a seat through TOOLS, never through
    prompt text — a baked-in value breaks replay. The brief is where every
    per-run fact lives, and get_spec_brief's own oldest-first selector is what
    binds this turn to a spec. Two different heads, one identical prompt."""
    seen = []

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen.append(prompt)
        return lambda: None

    monkeypatch.setattr(critic_g1.run_day, "make_turn", _fake_make_turn)

    run_turn = critic_g1._make_run_turn(
        "critic", {}, ":memory:", SimClock(NIGHTLY), db, "2026-08-25")
    run_turn({"spec_id": "0123456789abcdef"})
    run_turn({"spec_id": "fedcba9876543210"})

    assert set(seen) == {critic_g1.G1_PROMPT}
    assert "0123456789abcdef" not in critic_g1.G1_PROMPT
    assert "2026-08-25" not in critic_g1.G1_PROMPT


def test_the_turn_emits_no_live_trace(db, monkeypatch):
    """evals/live.py:64-80 deliberately skips strategy_critiques in its
    rows_written scan, and says whoever adds the Critic stage must add a
    `WHERE seat = ?` scan or live traces grade differently from eval traces of
    the same turn. evals/ is out of this lane's region, so this job emits NO
    live trace at all rather than a divergent one. Escalated in the plan."""
    seen = {}

    def _fake_make_turn(seat, cfg, db_path, clock, conn, run_date, prompt,
                        **kwargs):
        seen.update(kwargs)
        return lambda: None

    monkeypatch.setattr(critic_g1.run_day, "make_turn", _fake_make_turn)

    critic_g1._make_run_turn("critic", {}, ":memory:", SimClock(NIGHTLY), db,
                             "2026-08-25")({"spec_id": "abc"})

    assert seen.get("trace_sink") is None


# --- the leg is last, so a failure goes RED ---------------------------------

def test_a_failure_in_this_leg_exits_nonzero_so_systemd_reports_it(db):
    """This leg is LAST on ops/fund-pnl.service, so a nonzero exit cannot cost
    any other leg its night — close_pnl, resolve_day and reflect_day have all
    already committed. That removes the entire reason to swallow a failure into
    exit 0, and swallowing has a real cost: an appended alert is only visible
    once it DRAINS, and if Slack is what broke, the drain fails too and the
    night is invisible.

    OnFailure=fund-alert@%n.service is the report path that does NOT share a
    failure mode with this job: ops/notify_failure.sh posts by curl using
    /etc/fund/alert-env, a different env file, no DB, no python, no fund
    imports. It only fires if the unit fails, which requires this exit code.

    Same posture as run_day.guarded, which also returns 1 — the earlier draft
    had this backwards and called the inversion deliberate."""
    slack = FakeSlack()

    def _boom():
        raise sqlite3.OperationalError("database is locked")

    rc = critic_g1._guarded(db, slack, SimClock(NIGHTLY), _boom)

    assert rc == 1
    texts = _alert_texts(db)
    assert len(texts) == 1 and "critic_g1_failed" in texts[0]
    assert _undrained(db) == 0


def test_a_hard_stop_inside_the_body_is_still_alerted_and_still_red(db):
    """SystemExit alongside Exception, for run_day.guarded's reason: a config
    hard stop must still say so in Slack rather than exiting silently — and
    must still fail the unit."""
    slack = FakeSlack()

    def _stop():
        raise SystemExit("critic_g1: something refused to start")

    assert critic_g1._guarded(db, slack, SimClock(NIGHTLY), _stop) == 1
    assert "critic_g1_failed" in _alert_texts(db)[0]


def test_a_failure_is_still_red_when_the_recovery_drain_also_fails(db,
                                                                  monkeypatch):
    """The case that decides the whole exit-code question. If Slack is what
    broke, the alert cannot be delivered — the events row sits undrained and
    nobody sees it until the next audit. The exit code is then the ONLY signal
    that leaves the box, so it must not be 0."""
    def _boom():
        raise RuntimeError("slack_sdk.errors.SlackApiError: invalid_auth")

    def _drain_explodes(*a, **k):
        raise RuntimeError("invalid_auth")

    monkeypatch.setattr(critic_g1, "drain", _drain_explodes)

    assert critic_g1._guarded(db, FakeSlack(), SimClock(NIGHTLY), _boom) == 1
    assert _undrained(db) == 1        # the alert is recorded but undelivered


def test_a_clean_run_returns_the_bodys_own_code(db):
    """A NONZERO sentinel, deliberately. `lambda: 0` asserted against 0 cannot
    tell pass-through from a swallow — it is the assertion that would have gone
    green under either implementation."""
    assert critic_g1._guarded(db, FakeSlack(), SimClock(NIGHTLY),
                              lambda: 7) == 7
    assert _alert_texts(db) == []


# --- main()'s own exit codes ------------------------------------------------
#
# The earlier draft claimed critic_g1 "returns 0 from every failure path from
# connect() onward", pinned by a test. It was not pinned: the test called
# _guarded directly and never saw main() at all, and connect(),
# load_seat_config, RealSlack, parse_channel_overrides and acquire_lock all sat
# OUTSIDE _guarded. These tests exercise main().

def test_main_exits_one_when_the_guarded_body_fails(db, tmp_path, monkeypatch):
    """The end-to-end code, not just _guarded's. Everything main() builds is
    faked except the decision under test: what integer reaches sys.exit."""
    monkeypatch.setattr(critic_g1.run_day, "paper_guard", lambda env: None)
    monkeypatch.setattr(critic_g1.run_day, "require_env",
                        lambda names, env: {n: "x" for n in names}
                        | {"FUND_DB": str(tmp_path / "fund.sqlite")})
    monkeypatch.setattr(critic_g1.run_day, "acquire_lock", lambda p: object())
    monkeypatch.setattr(critic_g1, "connect", lambda p: db)
    monkeypatch.setattr(critic_g1, "_build_slack", lambda env, environ:
                        FakeSlack())
    monkeypatch.setattr(critic_g1, "critique_and_log",
                        lambda *a, **k: (_ for _ in ()).throw(
                            sqlite3.OperationalError("database is locked")))

    assert critic_g1.main([]) == 1
    assert "critic_g1_failed" in _alert_texts(db)[0]


def test_main_exits_zero_on_a_clean_night(db, tmp_path, monkeypatch):
    monkeypatch.setattr(critic_g1.run_day, "paper_guard", lambda env: None)
    monkeypatch.setattr(critic_g1.run_day, "require_env",
                        lambda names, env: {n: "x" for n in names}
                        | {"FUND_DB": str(tmp_path / "fund.sqlite")})
    monkeypatch.setattr(critic_g1.run_day, "acquire_lock", lambda p: object())
    monkeypatch.setattr(critic_g1, "connect", lambda p: db)
    monkeypatch.setattr(critic_g1, "_build_slack", lambda env, environ:
                        FakeSlack())
    monkeypatch.setattr(critic_g1, "critique_and_log",
                        lambda *a, **k: {"critiqued": 0, "failed": 0})

    assert critic_g1.main([]) == 0
    assert _alert_texts(db) == []


def test_main_exits_zero_when_another_run_holds_the_lock(db, tmp_path,
                                                         monkeypatch):
    """NOT a failure: the other process is doing the work, and a red unit here
    would page a human about a race that resolved itself correctly. This is the
    one path that returns 0 without doing anything."""
    monkeypatch.setattr(critic_g1.run_day, "paper_guard", lambda env: None)
    monkeypatch.setattr(critic_g1.run_day, "require_env",
                        lambda names, env: {n: "x" for n in names}
                        | {"FUND_DB": str(tmp_path / "fund.sqlite")})
    monkeypatch.setattr(critic_g1.run_day, "acquire_lock", lambda p: None)
    ran = []
    monkeypatch.setattr(critic_g1, "connect", lambda p: ran.append(p) or db)

    assert critic_g1.main([]) == 0
    assert ran == []             # it never even opened the DB


def test_a_bad_seat_config_fails_the_unit_rather_than_passing_silently(
        db, tmp_path, monkeypatch):
    """load_seat_config reads agents/config/critic.yaml — a failure reflect does
    NOT share, which is exactly why the earlier draft's "reflect would have
    failed on the same var anyway" argument did not cover it. It is inside
    _guarded, so it alerts with a code and exits 1."""
    monkeypatch.setattr(critic_g1.run_day, "paper_guard", lambda env: None)
    monkeypatch.setattr(critic_g1.run_day, "require_env",
                        lambda names, env: {n: "x" for n in names}
                        | {"FUND_DB": str(tmp_path / "fund.sqlite")})
    monkeypatch.setattr(critic_g1.run_day, "acquire_lock", lambda p: object())
    monkeypatch.setattr(critic_g1, "connect", lambda p: db)
    monkeypatch.setattr(critic_g1, "_build_slack", lambda env, environ:
                        FakeSlack())
    monkeypatch.setattr(critic_g1, "load_seat_config",
                        lambda p: (_ for _ in ()).throw(
                            FileNotFoundError("agents/config/critic.yaml")))

    assert critic_g1.main([]) == 1
    assert "critic_g1_failed" in _alert_texts(db)[0]
    assert "FileNotFoundError" in _alert_texts(db)[0]


# --- environment and single-instance ---------------------------------------

def test_the_job_needs_the_same_env_as_its_reflect_sibling():
    """It runs a seat (ANTHROPIC_API_KEY) and drains (SLACK_BOT_TOKEN), for the
    same reasons reflect_day does — and build_seat_options wires the alpaca MCP
    server unconditionally, which run_seat_turn then requires to be CONNECTED
    even though the narrowed surface can reach none of its tools (issue #108)."""
    assert set(critic_g1.REQUIRED_ENV) == {
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
        "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY"}


def test_the_job_takes_its_own_lock_not_reflects():
    """A shared lock would let a G1 turn hanging in SDK teardown hold reflect
    out of its own night, and a hung reflect hold G1 out of the next one."""
    assert critic_g1.LOCK_NAME == "critic_g1.lock"
    assert critic_g1.LOCK_NAME != "reflect_day.lock"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -v`
Expected: the new tests FAIL with `AttributeError: module 'critic_g1' has no attribute '_make_run_turn'` / `'_guarded'`.

- [ ] **Step 3: Write the implementation**

Append to `scripts/critic_g1.py`:

```python
def _make_run_turn(seat: str, cfg: dict, db_path: str, clock, conn,
                   run_date: str):
    """Build the per-spec `run_turn` callable `critique_and_log` drives.

    A named factory rather than a closure inline in main() so this seam — a
    narrowed tool surface reaching run_day.make_turn — is unit-testable without
    calling main(), which builds real clients.

    The prompt names NO spec, and job['spec_id'] never reaches it — a per-run
    value in prompt text breaks replay (CLAUDE.md). The id is carried on the
    job dict purely so critique_and_log can re-read the right row afterwards.

    NOTHING IS BOUND HERE, and that is a known gap rather than a design.
    reflect passes expected_decision_id, which handle_submit_reflection checks.
    There is no equivalent for G1: handle_submit_spec_critique takes spec_id
    from the seat's own arguments, so get_spec_brief's oldest-first selector
    binds only what the seat is SHOWN. critique_and_log's post-turn
    has_verdict() re-read is what catches a verdict written for the wrong spec.
    Adding a real binding is a fund_server.py change, out of region, escalated.

    NO trace_sink, deliberately. evals/live.py's rows_written scan skips
    strategy_critiques and documents that adding the Critic stage requires a
    `WHERE seat = ?` scan there, or live traces grade differently from eval
    traces of the same turn. evals/ is out of this lane's region, so this turn
    emits no live trace at all rather than a divergent one."""
    def run_turn(job: dict) -> None:
        turn = run_day.make_turn(seat, cfg, db_path, clock, conn, run_date,
                                 G1_PROMPT, tools=G1_TOOLS)
        turn()
    return run_turn


def _guarded(conn, slack, clock, body) -> int:
    """Run `body`; make sure a failure is never silent — in Slack OR to systemd.

    RETURNS 1 ON FAILURE, the same posture as run_day.guarded. An earlier
    design returned 0 here, on the theory that this leg ran BEFORE reflect on a
    Type=oneshot unit and must never stop it. This leg is now LAST
    (ops/fund-pnl.service; see the module docstring), so there is nothing after
    it to protect and the entire motivation is gone.

    AND RETURNING 0 WAS ACTIVELY WORSE, independent of ordering. The alert this
    function appends is an `events` row: it is visible only once it DRAINS. If
    the thing that broke IS Slack, the drain in the recovery path fails too,
    the alert sits with posted_at IS NULL until the next audit, and a leg that
    exited 0 looks to systemd exactly like a leg that succeeded. Exit 1 makes
    OnFailure=fund-alert@%n.service fire, and ops/notify_failure.sh reaches
    Slack by curl from /etc/fund/alert-env — a different env file, no DB, no
    python, no fund imports — so it is the one report path that does not share
    a failure mode with this job.

    So a failure is reported twice, by two independent mechanisms: the drained
    alert when Slack works, and the unit's own OnFailure when it does not.

    A G1 leg that could not run leaves every pending spec pending, which is the
    correct default (invariant 4) and costs nothing: specs_awaiting_critique
    has no date bound, so tomorrow night re-selects all of it.

    SystemExit alongside Exception for run_day.guarded's reason: a config hard
    stop must still say so in Slack. The recovery is itself guarded — if the DB
    is what broke, the original failure is the one that matters."""
    try:
        return body()
    except (Exception, SystemExit) as exc:
        text = (f"critic_g1_failed — {type(exc).__name__}: {exc}. The G1 leg"
                " stopped here; no verdict was written, no default row exists,"
                " and every pending spec stays pending for the next night.")
        log(f"ALERT {text}")
        try:
            run_day._alert(conn, clock, "critic_g1_failed", text)
            drain(conn, slack, iso(clock.now()))
        except Exception as inner:
            log(f"could not record/post that alert ({type(inner).__name__}:"
                f" {inner}) — the failure above is the one that matters."
                " systemd's OnFailure is what carries it out of the box now")
        return 1


def _build_slack(env: dict, environ):
    """The Slack client _guarded needs in order to report anything, plus this
    run's channel remapping.

    A named seam so tests can drive main() without a network client, and so the
    ONE thing that must exist before the guard can report is built in one
    place."""
    from slackkit.real import RealSlack

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)
    return slack


def main(argv: list[str] | None = None) -> int:
    """WHAT SITS OUTSIDE _guarded, and why each one earns it.

    The earlier draft left connect(), load_seat_config(), RealSlack(),
    parse_channel_overrides() and acquire_lock() outside the guard while
    claiming every failure "from connect() onward" returned 0. That claim was
    false — load_seat_config reads agents/config/critic.yaml, a failure reflect
    does not share — and nothing tested main() at all. The rule now is: each
    thing outside the guard is listed with the reason it is outside.

      paper_guard    invariant 1. Must exit 1 before any client exists; there
                     is nothing to report through yet and nothing should be.
      require_env    same. Also: reflect checks the identical REQUIRED_ENV
                     tuple, so a missing var has already failed the leg ahead.
      acquire_lock   it runs BEFORE connect, so there is no conn for _guarded's
                     first argument yet. (NOT because contention would be
                     mislabelled: contention is a None RETURN, not an
                     exception, so the guard would never see it. An earlier
                     draft gave that reason and it was wrong.)
      connect        _guarded's first argument. A guard cannot alert through a
                     connection that does not exist.
      _build_slack   _guarded's second argument: the recovery path ends in
                     drain(conn, slack, ...), so a guard built without `slack`
                     could RECORD an alert but never DELIVER it.

                     THIS ONE IS A CHOICE, NOT A STRUCTURAL IMPOSSIBILITY, and
                     saying otherwise would be the same overclaim this docstring
                     was rewritten to remove. conn already exists here, so the
                     append half — run_day._alert — IS coverable; only the drain
                     is not. It stays outside because a guard that records
                     without delivering is half a guard, and the half it drops
                     is the one an operator sees.

                     CONSEQUENCE, stated so nobody discovers it in an incident:
                     _build_slack calls run_day.parse_channel_overrides, which
                     raises SystemExit on a malformed SLACK_CHANNEL_OVERRIDES
                     (scripts/run_day.py:189-207) — a config hard stop exactly
                     parallel to load_seat_config's, which DID move inside. So
                     that one failure exits 1, the unit goes red and OnFailure
                     fires with the journal tail, but NO critic_g1_failed row is
                     ever written, so audit_day sees nothing and the events
                     outbox is empty. systemd is the only witness. Accepted
                     rather than worked around because the variable is a
                     staging affordance: .env.example:35 ships it EMPTY and
                     ops/staging-env.example is the only file that populates it
                     (ops/README.md:540). A production night cannot reach this
                     path; a staging night that does gets a red unit and a
                     journal line naming the malformed entry.

    Everything else — load_seat_config, run_date, the turn factory and the
    queue loop — is INSIDE, and pinned by
    test_a_bad_seat_config_fails_the_unit_rather_than_passing_silently."""
    import os

    from agents.wallclock import WallClock

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    db_path = env["FUND_DB"]
    lock_path = Path(db_path).parent / LOCK_NAME
    lock = run_day.acquire_lock(lock_path)   # must outlive the run; kept in scope
    if lock is None:
        log(f"another critic_g1 holds {lock_path} — exiting 0 rather than"
            " racing it (two overlapping runs = two paid turns per spec)")
        return 0

    clock = WallClock()
    conn = connect(db_path)
    slack = _build_slack(env, environ)

    def _body() -> int:
        cfg = load_seat_config(SEAT_CONFIG)
        run_date = et_run_date(clock.now())  # cost lands on the day it ran
        run_turn = _make_run_turn(SEAT, cfg, db_path, clock, conn, run_date)
        critique_and_log(conn, slack, clock, run_turn)
        return 0

    return _guarded(conn, slack, clock, _body)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

**Also move `load_seat_config` to a module-level import** (beside the existing `run_day` / `state.db` imports), so `critic_g1.load_seat_config` is a patchable name and the failure it can raise is visible at the top of the file:

```python
from agents.seats import load_seat_config                # noqa: E402
```

This adds no new weight: `run_day` is already imported at module level and itself imports `agents.seats`, so `claude_agent_sdk` is loaded either way. `RealSlack` stays a function-level import inside `_build_slack` — that one *is* a new dependency at import time, and the test file execs this module.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -v`
Expected: PASS.

- [ ] **Step 5: Run lint and the full suite**

Run: `make test`
Expected: PASS. `scripts/check_purity.py` does not cover `scripts/`, and `scripts/check_alert_codes.py` sees four literal codes.

- [ ] **Step 6: Commit**

```bash
git add scripts/critic_g1.py tests/test_critic_g1_job.py
git commit -m "feat(critic_g1): turn seam, fail-red guard, and main()

The turn is built with tools=G1_TOOLS and a prompt byte-identical to the eval
rig's, carrying no per-run value.

_guarded returns 1 on failure, like run_day.guarded. The leg is last on
fund-pnl.service, so a red exit costs nothing downstream, and an alert that
only lives in the events outbox is invisible when Slack is the thing that
broke — OnFailure=/notify_failure.sh is the report path that shares no
dependency with this job. main()'s own exit codes are tested; only
paper_guard, require_env, acquire_lock, connect and the Slack build sit
outside the guard, each with its reason stated — including that the Slack
build is a choice (its drain needs the client) rather than an impossibility,
and what that costs on a malformed SLACK_CHANNEL_OVERRIDES."
```

---

## Task 6: The vacuity — `objections` advances nothing because nothing can advance

**Files:**
- Test: `tests/test_critic_g1_job.py` (append)

**Interfaces:** consumes everything above; produces no new symbols.

This is #169's second acceptance bullet, as amended by the CEO ruling: implemented as a **demonstrated vacuity**, not a simulation. No transition edge, no status column, no `strategies` table is added.

- [ ] **Step 1: Write the test**

Append to `tests/test_critic_g1_job.py`:

```python
# --- #169 bullet 1b: "objections -> the spec does not advance" -------------

def test_an_objections_verdict_advances_nothing_because_nothing_can_advance(db):
    """#169's bullet reads "verdict `objections` -> spec does not advance".
    The CEO ruling of 2026-08-28 accepts this as a demonstrated VACUITY, not
    something to simulate: there is no legal transition to withhold.

      * state/transition.py's EDGES covers decisions, tickets, orders and
        checkpoints — nothing strategy-side, and try_transition RAISES
        IllegalTransition for a table with no machine
      * strategy_specs has no state/status column (it is immutable
        pre-registration; supersede via lineage, never UPDATE)
      * no `strategies` lifecycle table exists — state/schema.sql:136 says so
        deliberately
      * specs/strategy-contracts.md §4's transition table has no G1 edge at all

    So this asserts the ABSENCE. Inventing the edge would be this lane
    exceeding its region into canonical schema.

    WHAT THIS DOES AND DOES NOT CATCH — stated, because "the day someone adds
    an advance path this test reddens" is more than it can promise. It reddens
    on exactly three shapes: a new key in state/transition.py's EDGES, a
    `strategies` table, and a `state`/`status` column on strategy_specs. It
    would NOT catch an advance path expressed some other way — a verdict-gated
    call into stratgate, a lifecycle column under a different name (`phase`,
    `stage`, `g1`), a row in another table keyed by spec_id, or a scheduler
    that reads strategy_critiques directly. Those are the shapes to look for by
    hand when Phase 5's registration lane lands; this test is a tripwire on the
    three most likely ones, not a proof of vacuity."""
    from state.transition import EDGES

    sid = _spec(db)
    before = dict(db.execute("SELECT * FROM strategy_specs WHERE spec_id = ?",
                             (sid,)).fetchone())

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "objections",
                             ["the entry clause ignores the funding-cost"
                              " condition the hypothesis calls essential"]))

    # 1. the verdict IS written — the turn's whole deliverable
    assert counts == {"critiqued": 1, "failed": 0}
    row = db.execute("SELECT verdict, objections FROM strategy_critiques"
                     " WHERE spec_id = ?", (sid,)).fetchone()
    assert row["verdict"] == "objections"
    assert "funding-cost" in row["objections"]

    # 2. and NOTHING else moved: the spec row is byte-identical
    after = dict(db.execute("SELECT * FROM strategy_specs WHERE spec_id = ?",
                            (sid,)).fetchone())
    assert after == before

    # 3. because there is no advance path to withhold
    assert "strategy_specs" not in EDGES
    assert "strategies" not in EDGES
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "strategies" not in tables
    columns = {r["name"] for r in db.execute("PRAGMA table_info(strategy_specs)")}
    assert not ({"state", "status"} & columns), columns


def test_a_clear_verdict_and_an_objecting_one_have_identical_side_effects(db):
    """The other half of the vacuity: if a future edit ever made `clear` DO
    something that `objections` does not, "objections does not advance" would
    start carrying content this lane never implemented. This reddens first."""
    cleared = _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    objected = _spec(db, family="F2", created_at="2026-08-21T18:00:00+00:00")

    def _turn(job):
        if job["spec_id"] == cleared:
            _verdict(db, job["spec_id"], "clear")
        else:
            _verdict(db, job["spec_id"], "objections", ["mechanism mismatch"])

    critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY), _turn)

    a = dict(db.execute("SELECT * FROM strategy_specs WHERE spec_id = ?",
                        (cleared,)).fetchone())
    b = dict(db.execute("SELECT * FROM strategy_specs WHERE spec_id = ?",
                        (objected,)).fetchone())
    ignore = {"spec_id", "family", "created_at"}
    assert {k: v for k, v in a.items() if k not in ignore} == \
           {k: v for k, v in b.items() if k not in ignore}
    assert specs_awaiting_critique(db) == []      # both are reviewed, neither moved
```

- [ ] **Step 2: Run the test**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_critic_g1_job.py -k vacuit -v` then the whole file.
Expected: PASS. If it fails, the codebase already has an advance path recon missed — **STOP and escalate**, do not add or remove one.

- [ ] **Step 3: Commit**

```bash
git add tests/test_critic_g1_job.py
git commit -m "test(critic_g1): objections advances nothing, as a demonstrated vacuity

Per the CEO ruling on #169: assert that no advance path EXISTS rather than
simulate withholding one. No transition edge, no status column, no strategies
table is added. The test reddens the day one appears."
```

---

## Task 7: Ops wiring, the ship-gate precondition, and the Makefile

**Files:**
- Modify: `ops/fund-pnl.service`; `ops/README.md` — the units table (`:33`), the "Three things … deliberate" list (`:37`), **the reflection-job key paragraph (`:142`)**, and the Daily-operations block (`:569-583`); **`PROGRESS.md:440`**; `Makefile:3-5` and the target block; `scripts/reflect_day.py:9-16` (docstring only)
- Test: `tests/test_ops_units.py`

**Two live files assert the three-leg shape and become false the moment the fourth leg lands.** Both are orphans this change creates, so both are in scope (Steps 5a and 5b): `ops/README.md:142` says reflect "is the third and last leg of `fund-pnl.timer`" and "runs last deliberately for this reason", and `PROGRESS.md:440` says the unit has "three `ExecStart=` lines, in that order (`ops/fund-pnl.service:19,26,32`)" — wrong in the count *and* in every line number, since the new comment block shifts them all.

**Interfaces:** consumes `scripts/critic_g1.py` from Tasks 3–5. Produces no Python symbols; produces the `critic-g1` and `critic-gate` make targets and the `fund-pnl.service` fourth `ExecStart`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ops_units.py` (and add `PNL`, `MAKEFILE`, `OPS_README` beside the existing `DAILY` constant at the top of the file — those three names, spelled exactly as the code block below spells them):

```python
PNL = (ROOT / "ops" / "fund-pnl.service").read_text()
MAKEFILE = (ROOT / "Makefile").read_text()
OPS_README = (ROOT / "ops" / "README.md").read_text()


def _exec_starts(unit: str) -> list[str]:
    return [line.split("=", 1)[1].strip()
            for line in unit.splitlines() if line.startswith("ExecStart=")]


def test_the_nightly_unit_runs_its_four_legs_in_the_committed_order():
    """Type=oneshot runs ExecStart lines IN ORDER and stops at the first one
    that exits nonzero, so this order is behaviour, not formatting. Until
    2026-08-28 no test read this file at all.

      close_pnl, resolve_day  first: arithmetic, no LLM budget, and nothing is
                                     reflectable until resolutions exist
      reflect_day             third: PERISHABLE. reflect_day's _DUE_WHERE
                                     bounds on resolved_at within
                                     REFLECT_LOOKBACK_DAYS=7 and _AGED_OUT_WHERE
                                     alerts on rows that fell below the window
                                     and will NEVER be written. A reflection
                                     lost for seven nights is lost for good
      critic_g1                last: IMPERISHABLE and cheap to lose.
                                     state/specs.py:specs_awaiting_critique
                                     selects on `c.spec_id IS NULL` with NO
                                     date bound, so a skipped spec is
                                     re-selected every future night, forever.
                                     It also spends LLM budget and needs
                                     ANTHROPIC_API_KEY/SLACK_BOT_TOKEN — the
                                     property this file's own comment already
                                     gives as the reason reflect went last, now
                                     true of two legs

    Losing the window is not silent either way: OnFailure=fund-alert@%n.service
    fires on an overrun, a nonzero exit, or the guillotine.
    """
    assert [Path(cmd.split()[-1]).name for cmd in _exec_starts(PNL)] == [
        "close_pnl.py", "resolve_day.py", "reflect_day.py", "critic_g1.py"]


def test_the_nightly_unit_still_bounds_and_alerts_itself():
    assert "Type=oneshot" in PNL
    assert "OnFailure=fund-alert@%n.service" in PNL
    assert "TimeoutStartSec=30min" in PNL


def test_no_restart_directive_on_the_nightly_unit():
    """Invariant 4: a failed night waits for a human. Directive lines only —
    the file's comments discuss Restart= without setting it."""
    directives = [l for l in PNL.splitlines() if l and not l.startswith("#")]
    assert not any(l.startswith("Restart=") for l in directives), directives


def test_the_g1_leg_is_run_from_the_deployed_venv_like_its_siblings():
    """A bare `python3` would pick the host interpreter, not /opt/fund/.venv,
    and the seat would import nothing."""
    g1 = next(c for c in _exec_starts(PNL) if c.endswith("critic_g1.py"))
    assert g1.startswith("/opt/fund/.venv/bin/python3 ")
    assert g1.endswith("/opt/fund/scripts/critic_g1.py")


def test_the_critic_ship_gate_is_recorded_as_a_runnable_precondition():
    """scripts/critic_gate.py "decides whether the G1 gate ships, and the
    holdout it reads can only be spent once" — and until this lane it was
    invoked by NOTHING: not CI, not the Makefile, not systemd. An orphaned gate
    is how the stop-leg class of incident happens, in eval form. The CEO ruled
    it a recorded precondition for the Critic's first LIVE run: a make target
    plus an ops checklist line. It is deliberately NOT a `make test`
    prerequisite — it grades real recorded LLM trials."""
    assert "critic-gate:" in MAKEFILE
    assert "scripts/critic_gate.py" in MAKEFILE
    assert "make critic-gate" in OPS_README
    assert "critic_g1.py" in OPS_README        # the leg the units table lists


def test_the_g1_leg_has_a_by_hand_target_like_every_other_nightly_leg():
    """close-pnl, resolve and reflect each have one; an operator re-running a
    missed night by hand must not have to remember a path."""
    assert "critic-g1:" in MAKEFILE
    assert "scripts/critic_g1.py" in MAKEFILE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_ops_units.py -v`
Expected: FAIL — `NameError`/`AssertionError` on the new constants and assertions.

- [ ] **Step 3: Edit `ops/fund-pnl.service`**

Insert a fourth `ExecStart` **after** the `reflect_day.py` line, and amend the reflect comment (reflect stays third; it is no longer *last*). The `[Service]` block's tail becomes:

```
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/resolve_day.py
# Third: the reflection turns. Runs only if both legs above succeeded, which is
# correct — if nothing resolved, nothing is reflectable. It is ahead of the G1
# leg deliberately, and the reason is PERISHABILITY: reflect_day selects on
# resolved_at inside a REFLECT_LOOKBACK_DAYS=7 window and its _AGED_OUT_WHERE
# exists to alert on rows that fell below that window and will NEVER be
# written. A reflection this unit fails to buy for seven nights is destroyed.
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/reflect_day.py
# Fourth and last: nightly G1 enforcement (issue #169). One Critic turn per
# pending strategy spec, hard-capped at scripts/critic_g1.py's
# MAX_G1_TURNS_PER_NIGHT.
#
# LAST, not before reflect, and the ordering is behaviour. Three reasons:
#   1. The rule this file already committed to. reflect went last because "it
#      is the only leg that spends LLM budget and the only one that can fail on
#      a missing ANTHROPIC_API_KEY or SLACK_BOT_TOKEN". That is now true of two
#      legs, and this is the newer, more optional one.
#   2. This leg's misses are recoverable and reflect's are not.
#      state/specs.py:specs_awaiting_critique selects on `c.spec_id IS NULL`
#      with NO date bound: a spec skipped tonight is re-selected every future
#      night, forever. Losing this leg's window costs a night, never a spec.
#   3. Losing it is not silent. OnFailure= above fires on an overrun, a nonzero
#      exit, or the guillotine — via ops/notify_failure.sh, which shares no
#      dependency with the job it watches.
#
# critic_g1.py exits NONZERO on failure, on purpose: nothing runs after it, and
# an alert that only reaches the `events` outbox is invisible when Slack is the
# thing that broke. Do not "tidy" that into a return 0 — the exit code is what
# makes the OnFailure= line above cover this leg.
ExecStart=/opt/fund/.venv/bin/python3 /opt/fund/scripts/critic_g1.py
# 30min, matching fund-daily.service: this budget now has to cover N seat
# turns across TWO seat-running legs, not two arithmetic jobs. Deliberately NOT
# raised for the G1 leg. Realistic load is ~3 reflect turns (config/watchlist
# .yaml caps the universe at 3 tickers) plus <=3 G1 turns, ~24 min of ceiling
# against a 30 min budget; MAX_TURNS_PER_NIGHT=25 is a backstop, not a forecast.
# This is a guillotine, not a schedule; which leg it lands on is decided by the
# ORDER above, not by this number. Raise it only with a measurement.
TimeoutStartSec=30min
```

Also update the `Description=` line to mention the G1 leg:

```
Description=fund — post-close jobs (P&L $ and %% vs SPY; nightly resolutions; G1 critiques; reflections)
```

- [ ] **Step 4: Edit `scripts/reflect_day.py`'s docstring (comment-only)**

Reflect stays the **third** leg, so the `WHY IT RIDES THE 16:35 TIMER, THIRD.` heading (`reflect_day.py:10-15`) is unchanged. What is no longer true is `:23`'s "It runs last, so a missing token here cannot stop close_pnl or resolve_day, both of which have already committed." Append one sentence to that paragraph:

```
It is no longer the LAST leg: scripts/critic_g1.py (issue #169) runs after it,
placed there because a G1 miss is re-selected every future night
(state/specs.py has no date bound) while a reflection miss is destroyed after
REFLECT_LOOKBACK_DAYS. Nothing about this leg's own posture changes — it still
runs after everything that must not be blocked by a missing token.
```

No code in `reflect_day.py` changes, and no heading is renumbered.

- [ ] **Step 5: Edit `ops/README.md`**

Change the Units table row for `fund-pnl.timer` to:

```
| `fund-pnl.timer` | 16:35 ET Mon–Fri | `scripts/close_pnl.py`, then `scripts/resolve_day.py`, then `scripts/reflect_day.py`, then `scripts/critic_g1.py` |
```

Add a fourth bullet to the "Three things about these are deliberate" list (and change "Three" to "Four"):

```markdown
- **The nightly unit's leg order is behaviour, not formatting.** `Type=oneshot`
  shares one `TimeoutStartSec` across all four legs, so the last leg is the one
  the guillotine lands on. `critic_g1.py` is last **because its misses are
  recoverable**: `specs_awaiting_critique` has no date bound, so a spec skipped
  tonight is re-selected every future night. `reflect_day.py` is ahead of it
  because its `_DUE_WHERE` window is seven nights wide and a reflection that
  falls out of it is destroyed. It is also the older of the two LLM-spending
  legs, and this unit's stated rule is that LLM-spending legs go behind the
  arithmetic ones. Losing a leg is never silent — `OnFailure=` fires on an
  overrun, a nonzero exit or the guillotine. Pinned by
  `tests/test_ops_units.py`.
```

Add a new section immediately before `## Daily operations`:

```markdown
## Before the Critic's first live G1 night

`scripts/critic_g1.py` is on the 16:35 unit from the moment it is deployed, and
it spends real LLM budget the first night a strategy spec is pending. Do these
once, in order, before that night.

`scripts/critic_gate.py` says of itself that it "decides whether the G1 gate
ships, and the holdout it reads can only be spent once" — and until issue #169
nothing invoked it: not CI, not the Makefile, not systemd. An orphaned gate is
how the stop-leg class of incident happens, in eval form. This checklist is the
closure of that gap.

```bash
make eval-critic-holdout LABEL=<label>   # ~$0.81, ~7 min, hits the network
make critic-gate LABEL=<label>           # must print GATE PASS
```

1. `make eval-critic-holdout` records the holdout trials. Run it **once**: the
   holdout must never inform the charter afterwards (`specs/strategy.md`
   invariant 6).
2. `make critic-gate` is nonzero unless detection ≥ 8/9, false alarm ≤ 1/9,
   containment clean and trial counts clean. A red gate means the verdicts this
   job writes are not trustworthy.
3. **If the gate is red**, comment out the `critic_g1.py` `ExecStart` line in
   `ops/fund-pnl.service`, `systemctl daemon-reload`, and file the failure.
   Leaving a red gate wired means a spec gets cleared by nobody.

The gate is deliberately not in `make test` and not on a timer: `make test` is
free and offline, and this grades real recorded trials against a one-shot
holdout.
```

In the "Daily operations" code block, **amend the line that is already there** — do not add a second one. `ops/README.md:576` currently reads:

```bash
systemctl start fund-pnl.service               # P&L digest + resolutions by hand
```

Replace that single line with:

```bash
systemctl start fund-pnl.service               # P&L + resolutions + reflections + G1 by hand
journalctl -u fund-pnl -n 200 --no-pager       # what the four nightly legs did
```

(Adding rather than replacing would leave two identical `systemctl start fund-pnl.service` commands with different comments — an operator reading the block would have no way to tell which is current.)

- [ ] **Step 5a: Fix `ops/README.md:142` — the reflection-job key paragraph**

That paragraph explains why `ANTHROPIC_API_KEY`/`SLACK_BOT_TOKEN` being missing is survivable, and its whole argument is positional. It currently ends:

> It is the third and last leg of `fund-pnl.timer`, so a missing key does not block P&L or resolution posts — only this job's own alerting. It runs last deliberately for this reason.

Reflect is still third and its argument still holds — only "and last" is false. Replace those two sentences with:

```markdown
It is the third leg of `fund-pnl.timer`, so a missing key does not block P&L or
resolution posts — only this job's own alerting. It runs behind the arithmetic
legs deliberately for this reason. It is no longer the *last* leg:
`scripts/critic_g1.py` (issue #169) runs fourth, and needs the same two keys
for the same reasons. Both LLM-spending legs sit behind both arithmetic ones;
`ops/fund-pnl.service` explains the order between the two of them.
```

- [ ] **Step 5b: Fix `PROGRESS.md:440` — the timers table**

The `fund-pnl.timer` row asserts a leg count and three line numbers, all of which the new `ExecStart` and its comment block falsify. Replace the row's `does` cell:

```markdown
| `fund-pnl.timer` | 16:35 ET Mon–Fri | posts P&L $ / % vs SPY, **then writes the nightly `resolutions`, then reflects, then runs the G1 critique turn** — four `ExecStart=` lines, in that order (`ops/fund-pnl.service`) |
```

The explicit line numbers are dropped rather than re-derived: they were already a maintenance trap, and the next comment edit to that unit breaks them again. `tests/test_ops_units.py`'s `test_the_nightly_unit_runs_its_four_legs_in_the_committed_order` is what actually pins the order now, and it reads the file rather than quoting offsets into it.

- [ ] **Step 6: Edit the `Makefile`**

Change the second `.PHONY` line to include the new targets:

```make
.PHONY: staging-day staging-reset eval eval-report critic-g1 critic-gate
```

Add, immediately after the `reflect:` target:

```make
# Nightly G1 enforcement: registered strategy specs with no verdict -> one
# Critic turn each (issue #169). Rides the same 16:35 fire, FOURTH and last,
# after reflect — ops/fund-pnl.service explains why the leg whose misses are
# recoverable goes behind the leg whose misses are not.
# Safe to re-run and cheap to re-run: a spec that already carries a verdict is
# not selected again, so a re-fire pays only for what is still pending. Costs
# $0 on a night with an empty queue, which is every night until a
# submit_strategy_spec producer exists.
critic-g1: deps
	$(PYTHON) scripts/critic_g1.py

# The G1 SHIP GATE. Scores a recorded Critic eval run per class: nonzero
# unless detection >= 8/9 and false alarm <= 1/9, with clean containment and
# clean trial counts.
#
# NEVER in `make test` and never on a timer. It grades REAL recorded LLM
# trials, and `--split holdout` reads a holdout that can only be spent once
# (specs/strategy.md invariant 6). `make test` stays free and offline.
#
# This is the recorded precondition for the Critic's FIRST live G1 night —
# ops/README.md § "Before the Critic's first live G1 night" is the checklist.
# Until issue #169 nothing invoked this script at all; an orphaned gate that
# decides whether G1 ships is how the stop-leg class of incident happens.
#
# LABEL is required, and required LOUDLY: traces are keyed by git sha, so an
# unlabelled run silently overwrites the control baseline (scripts/eval_suite.py).
critic-gate: deps
	@test -n "$(LABEL)" || { echo "critic-gate: LABEL=<run-label> is required (see ops/README.md)" >&2; exit 2; }
	$(PYTHON) scripts/critic_gate.py $(LABEL) --split holdout
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `make deps && .venv/bin/python3 -m pytest tests/test_ops_units.py -v`
Expected: PASS.

- [ ] **Step 8: Verify the make targets parse**

Run: `make -n critic-g1` and `make -n critic-gate LABEL=probe`
Expected: both print their recipe lines without executing anything network-bound. Then run `make critic-gate` with **no** `LABEL` and confirm it exits 2 with the message (it must not reach `critic_gate.py`).

- [ ] **Step 9: Run the full suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add ops/fund-pnl.service ops/README.md PROGRESS.md Makefile \
        scripts/reflect_day.py tests/test_ops_units.py
git commit -m "ops(critic_g1): schedule the G1 leg last on fund-pnl, record the ship gate

Last, behind reflect, for three reasons: this unit's existing rule puts
LLM-spending legs behind the arithmetic ones and the G1 leg spends the same
budget on the same two secrets; specs_awaiting_critique has no date bound so a
skipped spec is re-selected forever, while reflect's _DUE_WHERE window is seven
nights and a miss past it is destroyed; and OnFailure= already alerts on every
starvation path, so nothing here is silent.

TimeoutStartSec unchanged. First tests in the repo to read fund-pnl.service.
critic_gate.py green is now a make target and an ops checklist line for the
Critic's first live night (CEO ruling on #169).

Also corrects the two live files that asserted the three-leg shape:
ops/README.md's reflection-key paragraph (\"third and last leg\") and
PROGRESS.md's timers row (\"three ExecStart= lines … :19,26,32\")."
```

---

## Acceptance: #169's four bullets, as amended

| #169 bullet (amended) | Test | File |
|---|---|---|
| Registered spec → critique row **within that night's run** | `test_a_pending_spec_gets_a_verdict_row_the_same_night`, `test_the_queue_is_taken_oldest_first` | `tests/test_critic_g1_job.py` |
| Verdict `objections` → spec does not advance (**demonstrated vacuity**) | `test_an_objections_verdict_advances_nothing_because_nothing_can_advance`, `test_a_clear_verdict_and_an_objecting_one_have_identical_side_effects` | `tests/test_critic_g1_job.py` |
| Critic turn crash → no critique row, no default row, alert exists, night completes | `test_a_turn_that_writes_no_verdict_stops_the_night_and_alerts`, `test_the_blocking_alert_counts_everything_still_queued`, `test_a_turn_that_raises_leaves_no_row_and_the_spec_still_pending`, `test_the_job_never_writes_a_verdict_of_its_own` | `tests/test_critic_g1_job.py` |
| **The leg's own exit codes** (plan-added, replaces an untested claim) | `test_a_failure_in_this_leg_exits_nonzero_so_systemd_reports_it`, `test_a_failure_is_still_red_when_the_recovery_drain_also_fails`, `test_a_clean_run_returns_the_bodys_own_code`, `test_main_exits_one_when_the_guarded_body_fails`, `test_main_exits_zero_on_a_clean_night`, `test_main_exits_zero_when_another_run_holds_the_lock`, `test_a_bad_seat_config_fails_the_unit_rather_than_passing_silently` | `tests/test_critic_g1_job.py` |
| Tool surface: cannot call any other `submit_*` or broker tool | `test_the_g1_turn_can_reach_no_broker_tool_and_no_other_submit`, `test_the_g1_surface_is_exactly_the_seats_two_g1_capabilities`, `test_the_turn_is_built_with_the_narrowed_surface`, plus Task 1's nine override tests (including `test_a_per_turn_override_cannot_grant_an_order_tool` parametrized over **critic** and reflect with the message each genuinely raises, and `test_a_per_turn_override_must_name_concrete_tools_never_a_glob`) | `tests/test_critic_g1_job.py`, `tests/test_exec_seat_tool_surface.py` |
| Replay of a recorded turn passes (brief carries no per-run values) | `test_the_g1_prompt_is_byte_identical_to_the_one_the_eval_rig_sends`, `test_the_prompt_carries_no_per_run_value` | `tests/test_critic_g1_job.py` |
| **What bounds the turn count** (plan-added) | `test_the_night_is_capped_and_a_silent_cap_is_alerted`, `test_a_cap_that_exactly_drains_the_queue_raises_no_alert`, `test_the_nightly_cap_is_derived_not_inherited_from_reflect` | `tests/test_critic_g1_job.py` |
| **Interrupt semantics** (plan-added) | `test_an_interrupted_night_is_retried_not_lost`, `test_a_verdict_is_written_once_so_a_re_fire_cannot_double_it`, `test_a_db_error_mid_queue_still_drains_what_the_night_produced` | `tests/test_critic_g1_job.py` |
| **The ship-gate precondition** (CEO ruling) | `test_the_critic_ship_gate_is_recorded_as_a_runnable_precondition` | `tests/test_ops_units.py` |

**Bullet 4 caveat, stated rather than papered over:** the repo has **no replay harness** — `make replay` exits 2 with "not implemented yet". The bullet's testable content today is prompt invariance (the property that would make a recorded turn replayable), which is what the two tests above assert. A genuine replay of a recorded Critic turn needs the recorder/replayer from `specs/acceptance.md` §0 and is not in this lane.

---

## Escalations

Everything here is **out of the stated region**. None of it is planned as an edit.

1. **`agents/tools/fund_server.py` — head-of-line blocking in the G1 queue.** `get_spec_brief` takes no arguments and always returns the oldest unreviewed spec, so a spec the seat cannot critique blocks every spec behind it, indefinitely. The job's mitigation is to stop after one turn and alert naming the blocking spec and the pending count. A real fix needs a `spec_id` argument (or a skip list) on the handler. **Ask:** should a follow-up issue add one? *(Same file and plausibly the same change as Escalation 12 — a `spec_id` argument fixes both the skip and the binding.)*
2. **`evals/live.py:64-80` — the G1 turn emits no live trace.** That file's `rows_written` scan deliberately skips `strategy_critiques` and states that whoever adds the Critic stage must add a `WHERE seat = ?` scan plus JSON-decoding of `objections`, or live traces grade differently from eval traces of the same turn. This plan therefore passes **no** `trace_sink`, producing no divergent trace — but also no production evidence of what the seat did beyond the row and the `spec_critique` event. **Ask:** file a follow-up to trace the nightly G1 turn.
3. **`specs/acceptance.md` has no criterion for the Critic's G1 turn.** Phase 5's fifteen bullets never mention `strategy_critiques`, `submit_spec_critique` or `get_spec_brief`. `CLAUDE.md` says to implement acceptance tests first; there was nothing there to implement, so `tests/test_critic_g1_job.py` is the de-facto criterion. **Ask:** a human should land the criterion.
4. **`specs/strategy.md` §2 and §7 contradict the Critic's G1 role.** §2 defines G1 as field-completeness at registration and never mentions the Critic; §7's division-of-labor table has no Critic row and assigns qualitative review to the Risk Officer. `specs/INDEX.md:29` makes `strategy-contracts.md` the winner, which sides with the Critic — but `strategy.md` is stale on this and only a human commit fixes it.
5. **`state/schema.sql:167`'s "`stratgate.evaluate_g1()` is the G1 gate plan" is stale.** `stratgate/gate.py` has `evaluate_g2`/`evaluate_g3` only; `evaluate_g1` does not exist. Now that a real G1 producer ships, the comment misleads.
6. **There is still no live `submit_strategy_spec` producer** (recon F2: zero MCP tool, no `quant` seat, `insert_strategy_spec`'s only callers are `evals/fixtures.py` and tests). This lane ships the consumer; in production it will run on an empty queue and cost `$0`/night until #49's registration lane lands. Shipping the consumer early is free, but its acceptance value is latent — worth saying out loud so nobody reads a quiet `#research` channel as G1 working.
7. **`specs/design.md` §3's daily-cycle table has no nightly Critic row.** Same class as reflect, which is also absent from §3.
8. **`evals/prompts.py`'s drift guard is blind to this prompt.** `tests/test_evals_runner.py`'s guard derives its seat list from `run_day.SEATS`, where the Critic deliberately is not. This plan pins the byte-equality from the new test file instead. A cleaner fix generalizes the guard to scan every production prompt source, which touches a test about `evals/` — borderline in-region; not planned here.
9. **No *age* detector on the G1 queue.** To be precise about what is and is not covered, since an earlier draft overstated this gap: a night on which the script does not run at all — an overrun, a nonzero exit, the guillotine — **is** detected, by `OnFailure=fund-alert@%n.service`. What is not detected is a spec that stays pending across many nights while every night looks individually fine: the in-script alerts describe tonight (`critic_g1_backlog_capped`, `critic_g1_turn_wrote_nothing`) and carry a count, but nothing says "this spec has been pending for nine nights". A "specs pending N nights" check belongs in `scripts/audit_day.py`, which is trading-day code. **Ask:** should audit_day gain that check?
10. **Option 3 (a shared nightly bootstrap helper).** `close_pnl.py`, `resolve_day.py`, `reflect_day.py` and now `critic_g1.py` each carry their own `paper_guard`/`require_env`/`connect`/lock/Slack bootstrap. Extracting it would touch `reflect_day.py:main()`, which has no direct test. Deferred; revisit at a fifth leg.
11. **Four new alert codes** (`critic_g1_failed`, `critic_g1_turn_failed`, `critic_g1_turn_wrote_nothing`, `critic_g1_backlog_capped`). The earlier draft said these would automatically open up to four GitHub issues via `scripts/file_alert_issues.py`. **That is stale.** As of `f496da5` ("docs: the alert filer is hand-run, so an alert becomes an issue only when a human runs it", #179) the filer is invoked by nothing — not CI, not the Makefile, not a timer. So a new code costs a Slack alert and an `events` row, and becomes an issue only when someone runs the filer by hand. The cost is smaller than stated; the *risk* is the other way round — an alert nobody files is an alert nobody tracks.

12. **`agents/tools/fund_server.py` — the G1 verdict is not bound to the spec the turn was shown (found in review).** `:237` builds `SpecCritique(spec_id=args["spec_id"], ...)`: the id comes from the seat's own tool arguments, and `:243-257` checks only that the spec is registered and unreviewed. `get_spec_brief`'s oldest-first selector binds what the seat is *shown*, never what it *writes*. A turn shown spec A can write a verdict for spec B; B is then permanently unreviewable (write-once) and A is still pending. This job **detects** it — the post-turn `has_verdict(shown_spec_id)` re-read is False, so the night alerts and breaks — but cannot prevent it, and the misdirected row cannot be undone through any shipped path. **Ask:** a follow-up adding a real binding — a `spec_id` argument threaded from `get_spec_brief`'s brief and checked in `handle_submit_spec_critique`, or a server-side check that the submitted id is the current queue head. Same shape as reflect's `expected_decision_id`, which already exists in `build_seat_options`.

13. **`ops/notify_failure.sh:46-51` headlines every `fund-pnl*` failure "No P&L was posted for today" — a PRE-EXISTING defect this lane widens, not one it creates.** The mapping is `fund-pnl*) HEADLINE='No P&L was posted for today'`, and the headline exists precisely so an operator reads the *cost* rather than a unit-status line. It is **already false today**, before this lane: `scripts/reflect_day.py:313-347`'s `main()` has no guard at all, so anything raising inside `reflect_and_log` propagates and the leg exits nonzero — with `close_pnl` long since committed and the P&L line posted. A reflect-only failure has always produced that headline. What this lane changes is the *odds*: it adds a second LLM-spending leg behind reflect, and that leg is deliberately fail-red (Task 5), so the share of `fund-pnl` failures for which the headline is wrong goes up rather than starting from zero. Nothing is lost either way — the alert still names the unit, exit code and journal tail. Changing the mapping affects the alert path for all four legs, so it is not done here. **Ask:** a per-leg headline, or a neutral `fund-pnl` headline.

    It compounds with the two other unit-state readers named in R2: `scripts/dev_status.py` renders `fund-pnl` red on the devops status report, and `systemctl --failed` keeps showing it until someone runs `reset-failed`. So a G1-only failure is reported in three places and all three point at the P&L leg.

---

## Points where two readings are both defensible

These were decided, not hidden. Each is a place the CEO may overrule.

**Removed from this list because it is now settled by evidence, not judgment:** *leg order*. The earlier draft listed "third vs. fourth" here and called it "the one genuinely two-sided call in the plan". It is not two-sided. `ops/fund-pnl.service:4`'s `OnFailure=` means starvation is always alerted; `state/specs.py`'s unbounded selector versus `reflect_day.py`'s seven-night `_DUE_WHERE` means G1's misses are recoverable and reflect's are destroyed; and this unit's own comment already states the rule that puts LLM-spending legs last. All three point the same way. `critic_g1.py` is **fourth and last**. See the Design Decision section.

1. **The exit code: 1 on failure, not 0.** Decided (see Task 5), and worth stating as a choice rather than an inevitability. Returning 0 would keep the unit green and rely on the drained `events` alert; returning 1 makes systemd's `OnFailure` cover this leg at the cost of a red unit for a leg whose failure costs a night and no data. Chosen because the drained alert and the failure share a dependency (Slack) and the `OnFailure` path does not. The red is read in three places, not one — the `OnFailure` Slack headline, `scripts/dev_status.py`'s status report (`UNITS` includes `fund-pnl`), and `systemctl --failed`, which keeps showing it until `reset-failed`; see R2. If that proves too noisy, the levers are Escalation 13's headline and a per-leg signal in `dev_status.py`, not the exit code.
2. **`G1_TOOLS` lives in `scripts/critic_g1.py`, not in `agents/config/critic.yaml`.** CLAUDE.md's "never hardcoded" rule names model ids and budgets; reflect's narrowing does live in yaml. Recommended the script, because this is a per-*turn* narrowing and #170's per-*kind* surfaces will be call-site owned too.
3. **`scripts/run_day.py`'s `make_turn`/`_seat_session` counted as in-region.** They are the nightly job's own call chain (`reflect_day.py` already drives them), and the change is additive with a `None` default. A narrower reading would force `critic_g1.py` to hand-roll a session and lose `SEAT_MAX_WALL_S` bounding, cost recording and the turn-failure alerts.
4. **The override is narrowing-only, checked against the seat's *served* surface, and must name concrete tools** — my addition, not part of the CEO's ruling, and the first two versions of it were decorative (see Task 1: a glob check accepted `place_stock_order` for the Critic; a literal `place_*` rule then accepted `mcp__alpaca__*`). The check now bites for the Critic. Its honest limits are on the alpaca half: the external server's tool names are not enumerable here, so an unserved read-only alpaca name passes and is inert, and a case-variant `mcp__alpaca__PLACE_stock_order` is accepted for the same reason while the fund half's exact membership refuses the equivalent. If the `SEAT_CAPS` half ever blocks a legitimate #170 use case, the fix is to add the capability to `SEAT_CAPS` — where it is reviewed — not to loosen the check. If a #170 surface ever genuinely wants a pattern, that is a signal it belongs in the seat's yaml, not in a per-turn override.
5. **`tools=[]` raises rather than meaning "no tools".** Defensible either way; refusing fails closed at build time.
6. **The loop breaks on the first non-writing turn** rather than skipping to the next spec. Skipping is impossible without a `fund_server.py` change (escalation 1), so this is the only coherent shape — but it does mean one bad spec pauses the queue. The alert now carries the pending count so the operator can see how much is behind it.
7. **`TimeoutStartSec` left at 30min.** Raising it was considered. (The earlier draft's justification — "no value under two hours makes both legs fit", from a computed worst case of 112 minutes — was self-refuting and is deleted.) Left alone because 30min is the measured number this repo already uses in both units, the realistic load is ~24 minutes of ceiling, and raising an untested ops constant to an unmeasured number is the change with the worse expected value. It is a guillotine position, and the order decides which leg it lands on.
8. **Two existing test fakes in `tests/test_run_day.py` gain a `tools=None` parameter.** This is stub-signature widening, not a weakened assertion — but it *is* an edit to an existing test file, so it is called out explicitly rather than slipped in.
9. **`main()` is now partly tested, against this file's own convention.** `reflect_day.py:main()` has no direct test and the earlier draft copied that. But the draft also made a claim about `main()`'s exit codes that only `main()` could have falsified, and nothing tested it. The tests added in Task 5 fake every client `main()` builds and assert only the integer it returns. If that proves brittle, delete the tests — but then delete the claims too.

---

## Self-Review

- **Spec coverage:** all four #169 bullets map to named tests (table above), plus the three plan-mandated additions (turn bound, interrupt semantics, ship-gate precondition). Both CEO rulings on the body's stale points are implemented: the turn is on the nightly job (Tasks 3–5, 7) and the objections bullet is a demonstrated vacuity (Task 6). Ruling 3 (per-turn override in `build_seat_options`, not `critic.yaml`) is Task 1; ruling 4 (`critic_gate.py` recorded) is Task 7; ruling 5 (phrasing) is reflected in every test name and docstring. `run_day.SEATS`, `tests/test_run_day.py:724`, `charters/critic.md`, `agents/config/critic.yaml`'s `tools`, `state/transition.py`, `state/schema.sql` and `fixtures/golden-strategy.md` are all untouched.
- **Placeholder scan:** every step carries real code or a real command. No verification steps are left to the implementer: the `FakeSlack` attribute shape (`slackkit/fake.py:13`, `dict[str, list[dict]]` keyed by channel), the `spec_critique` → `#research` route (`slackkit/render.py:272-284`), `build_seat_options`'s line range (`agents/seats.py:65-135`), `SEAT_CAPS`'s contents (`agents/tools/fund_server.py:46-64`), and every seat yaml's `tools` value were all read and are quoted rather than guessed.
- **Type consistency:** `critique_and_log` returns `{"critiqued", "failed"}` in every test and in the implementation; `run_turn` takes `{"spec_id": str}` in the factory, the loop, and all fakes; `G1_TOOLS`, `G1_PROMPT`, `MAX_G1_TURNS_PER_NIGHT`, `PENDING_REPORT_LIMIT`, `next_pending_spec`, `pending_count`, `has_verdict`, `_make_run_turn`, `_guarded`, `_build_slack`, `main` are spelled identically in the interface blocks, the implementation and the tests. `build_seat_options`'s new kwarg is `tools` in all four places it appears (`seats.py`, `_seat_session`, `make_turn`, and every fake). `_guarded` returns `int` and its failure value is `1` everywhere it is described.
- **Claims that were withdrawn rather than repaired**, so a later reader does not resurrect them: (a) "reflect could starve this leg silently, forever, with no detector"; (b) "the true worst case is 3×240 + 25×240 = 112 min, so no value under two hours makes both legs fit"; (c) "returns 0 from every failure path from `connect()` onward … pinned by a test"; (d) "an override may only narrow" as enforced by a glob check against `cfg["tools"]`; (e) "the tool's own oldest-first selector is the binding"; (f) "four new alert codes will open four GitHub issues". Each is contradicted by a file cited in the Revision log.

---

### Critical Files for Implementation

- `/Users/benjaminmatton/Developer/fund-wt/g1-critic-nightly/scripts/critic_g1.py` (create)
- `/Users/benjaminmatton/Developer/fund-wt/g1-critic-nightly/tests/test_critic_g1_job.py` (create)
- `/Users/benjaminmatton/Developer/fund-wt/g1-critic-nightly/agents/seats.py`
- `/Users/benjaminmatton/Developer/fund-wt/g1-critic-nightly/scripts/run_day.py`
- `/Users/benjaminmatton/Developer/fund-wt/g1-critic-nightly/ops/fund-pnl.service`

---

**Two notes on the brief:**

- **Your assumption holds.** A new sibling script is the cheaper and safer option, and generalizing `reflect_day.py` is clearly worse — not marginally. The decisive facts are that the bootstrap you'd be deduplicating is *already* duplicated three times (`close_pnl.py`, `resolve_day.py:70-82`, `reflect_day.py:main`), so a fourth copy follows the convention rather than creating debt; and that `tests/test_reflect_job.py` binds `reflect_and_log(conn, slack, clock, run_turn)` across 25 tests. You do not need to take anything back to the CEO on this one.
- **Your framing was right about leg order and an earlier draft of this plan was wrong.** You assumed a **4th** `ExecStart`; that draft argued for 3rd, before reflect, on a starvation asymmetry. Two independent adversarial reviews demonstrated the argument false on all three of its supports: `OnFailure=` means starvation is never silent, `specs_awaiting_critique`'s unbounded selector means G1's misses are the *recoverable* ones while reflect's age out after seven nights, and this unit's own comment already gives "spends LLM budget, needs the two secrets" as the reason a leg goes last — a description of `critic_g1.py`. The plan now schedules it **fourth and last**, and the exit-code design that only existed to make going first safe is replaced by a plain fail-red. Nothing remains of that draft's reasoning; see the Revision log.

---

## Revision log

Revised 2026-08-28 after two independent adversarial reviews. Every item below was **demonstrated** against the file named, not argued.

- **R1 — Leg order reversed: `critic_g1.py` is now FOURTH and last, behind `reflect_day.py`.** The old "reflect starves G1 silently, forever, with no detector" rationale is deleted, not softened: `ops/fund-pnl.service:4`'s `OnFailure=fund-alert@%n.service` alerts on every starvation path; `state/specs.py:specs_awaiting_critique` has **no** date bound so G1 misses are re-selected forever while `scripts/reflect_day.py`'s `_DUE_WHERE`/`_AGED_OUT_WHERE` destroy reflect's after `REFLECT_LOOKBACK_DAYS=7`; and `ops/fund-pnl.service:27-31` already states that LLM-budget legs needing `ANTHROPIC_API_KEY`/`SLACK_BOT_TOKEN` go last, which describes `critic_g1.py`. The self-refuting "3×240 + 25×240 = 112 min, so no value under two hours makes both legs fit" sentence is removed (112 *is* under two hours), and the scenario is noted unreachable at current volume anyway (`config/watchlist.yaml` caps the universe at 3 tickers, so reflect's realistic load is ~3 turns). Rewritten in: the Architecture line, the Design Decision section, Options 1 and 4, the turn-count derivation, `critic_g1.py`'s module docstring, the `ops/fund-pnl.service` comment block, `scripts/reflect_day.py`'s docstring note, the `ops/README.md` units row and deliberate-choices bullet, the `Makefile` comment, `test_the_nightly_unit_runs_its_four_legs_in_the_committed_order`, and the Task 7 commit message. Moved out of "Points where two readings are both defensible" and marked settled.
- **R2 — `_guarded` now returns 1 on failure, not 0, and `main()`'s exit codes are tested.** The old claim ("returns 0 from every failure path from `connect()` onward … pinned by a test") was already false — `connect`, `load_seat_config`, `RealSlack`, `parse_channel_overrides` and `acquire_lock` all sat outside `_guarded`, and the test called `_guarded` directly. With the leg last there is nothing downstream to protect, and exit 0 hides a failure whose only other signal is an `events` alert that must drain — impossible when Slack is what broke. `main()` is restructured so only `paper_guard`, `require_env`, `acquire_lock`, `connect` and a new `_build_slack` seam sit outside the guard, each with its reason stated; `load_seat_config` (which reads `agents/config/critic.yaml`, a failure reflect does not share) moved inside. Four new `main()` tests plus a drain-also-fails test.

    **"A red exit costs nothing downstream" is about *legs*, and two readers of unit state were not named. Both are arguably correct behaviour, but they are consequences of this decision and belong in the argument rather than outside it.** (a) `scripts/dev_status.py:52` has `UNITS = ("fund-daily", "fund-pnl")` and `:203` runs `systemctl show fund-pnl.service -p Result`, so a G1-only failure renders **`fund-pnl` red on the devops status report** — a report about the P&L unit, reddened by the leg with the least to do with P&L. (b) `ops/README.md:575` tells operators to run `systemctl --failed`, and a failed `Type=oneshot` unit **stays failed until someone runs `systemctl reset-failed`** — so the red persists across days, outliving the night that caused it. Neither loses data and neither blocks a leg, which is why the decision stands. But they **compound Escalation 13**: the Slack headline says "No P&L was posted for today" and the status report says `fund-pnl` is red, so all three signals point an operator at the P&L leg for a failure that has nothing to do with it. If that noise proves unacceptable, the levers are Escalation 13's headline and a per-leg signal in `dev_status.py` — not the exit code, which is the only thing that gets a failure out of the box when Slack is what broke.
- **R3 — the narrowing guard was decorative for five of six seats; fixed, not demoted.** `agents/config/critic.yaml:16` (and analyst, news, pm, exec) carry `tools: ["mcp__fund__*", "mcp__alpaca__*"]`, so the old `fnmatchcase`-against-`cfg["tools"]` loop **accepted** `mcp__alpaca__place_stock_order` for the Critic and rejected only for `reflect` — the one seat the old test covered. `_turn_tools` now checks `mcp__fund__` names against `SEAT_CAPS[seat]` (the fund server's actual registration) and refuses `mcp__alpaca__place_*` unless `cfg["alpaca_toolsets"]` contains `trading`, the same field the order hooks key off. The narrowing test is parametrized over **critic** and reflect, a step manufactures the red against the naive version, and the docstring states the honest limit (the external alpaca surface is not enumerable in-repo) plus exactly what #170's Phase 6 per-kind surfaces inherit.
- **R4 — the "the selector is the binding" claim is corrected and the real gap escalated.** `agents/tools/fund_server.py:237` builds `SpecCritique(spec_id=args["spec_id"], ...)`, so the spec id comes from the seat's own tool arguments; the oldest-first selector binds what the seat is *shown*, never what it *writes*. A turn shown spec A can write a verdict for spec B, making B permanently unreviewable. The post-turn `has_verdict()` re-read is kept and re-labelled load-bearing (it detects this), the residual risk is stated in the failure table, the module docstring and `_make_run_turn`, and the missing binding is added as **Escalation 12**.
- **R5 — three test corrections.** `test_the_verdict_reaches_research_through_the_outbox` now indexes `slack.posts.get("#research", [])` (`slackkit/fake.py:13` — `posts` is `dict[str, list[dict]]` keyed by channel, so the old `for post in slack.posts` iterated channel names), which also preserves the `#research` routing check. `test_a_clean_run_returns_the_bodys_own_code` uses a nonzero sentinel (`lambda: 7`), since `0` could not distinguish pass-through from a swallow. And because the `wrote_nothing` path `break`s — so the `for…else` never runs and `capped` stays `False` — the blocking alert now carries a pending count (`pending_count`, `PENDING_REPORT_LIMIT`, `_count_text`), with a new test at five pending specs.
- **B1 — the parametrized guard test shipped RED and is fixed by splitting the expected message per seat, not by loosening the match.** `agents/config/reflect.yaml:28` is `tools: ["mcp__fund__*"]` with no alpaca glob, so for `reflect` the name is refused one branch earlier as ungranted (`may only NARROW`) and never reaches the invariant-2 branch — `pytest.raises(match="invariant 2")` failed on `[reflect]`. Verified by running the proposed `_turn_tools` body against all six real seat configs. The parametrization now carries `(seat, refusal)` pairs — `("critic", "invariant 2")`, `("reflect", "may only NARROW")` — because a substring both messages share would be the assertion that passes under any refusal, i.e. the decorative-guard defect R3 exists to close. The docstring states that only the critic case proves anything about invariant 2.
- **B2 — the narrowing guard accepted glob overrides, the same defect class as R3, one level up.** `agents/seats.py:92` passes `cfg["tools"]` straight to the SDK and every seat yaml uses wildcards, so `tools` entries genuinely are patterns and a wildcard override re-widens rather than being inert; the `place_*` rule is a literal `fnmatchcase` test a wildcard walks past. Verified accepted for seat `critic`: `['mcp__alpaca__*']`, `['mcp__alpaca__*place*']`, `['mcp__alpaca__pla?e_stock_order']`, `['mcp__alpaca__[pq]lace_stock_order']`. Nothing escalates today (critic's `alpaca_toolsets` is `stock-data`, `disallowed_tools` denies `place_*`), so this was an **overclaim**, not a live hole — but the property was asserted in three places. `_turn_tools` now refuses any entry containing `*`, `?` or `[` before every other check, with a six-case parametrized test and a second manufactured red; and all three claim sites now state four guarantees (concrete names, exact `SEAT_CAPS` membership, no `place_*` without `trading`, no builtin) instead of three. The surviving asymmetry is documented rather than dropped: the fund half's exact set membership refuses `mcp__fund__SUBMIT_DECISION`, while the alpaca half's glob-plus-literal test still accepts `mcp__alpaca__PLACE_stock_order` — inert, since it names no registered tool, and the price of a surface this repo cannot enumerate.
- **B3 — two live files asserting the three-leg shape are now in the edit list.** `ops/README.md:142` ("It is the third and last leg of `fund-pnl.timer` … It runs last deliberately for this reason") becomes Task 7 Step 5a: reflect stays third and its argument stands, only "and last" goes. `PROGRESS.md:440` ("three `ExecStart=` lines, in that order (`ops/fund-pnl.service:19,26,32`)") becomes Step 5b — false in the count and in every line number, since the new comment block shifts them; the offsets are dropped rather than re-derived, because `test_the_nightly_unit_runs_its_four_legs_in_the_committed_order` reads the file instead of quoting into it. Both are orphans this change creates. Added to the File Structure list, Task 7's Files block, `git add` and the commit message.
- **B4 — Task 7 Step 5 replaces the Daily-operations line instead of adding a duplicate.** `ops/README.md:576` already carries `systemctl start fund-pnl.service   # P&L digest + resolutions by hand`; followed literally the old step produced two identical commands with different comments and no way to tell which was current. The step now quotes the existing line and replaces it.
- **B5 — two stated "structural reasons" were false; placement kept, justification corrected.** (a) `_build_slack` was claimed to be something `_guarded` "structurally CANNOT cover". It is not: `_guarded` uses `slack` only for the `drain()` in its recovery path and `conn` exists by then, so the `run_day._alert` half IS coverable. It stays outside as a **choice** — a guard that records without delivering is half a guard — and the consequence is now stated: `_build_slack` calls `run_day.parse_channel_overrides`, which raises `SystemExit` on a malformed `SLACK_CHANNEL_OVERRIDES` (`scripts/run_day.py:189-207`), a config hard stop exactly parallel to the `load_seat_config` one that moved inside, so that failure exits 1 with a red unit and `OnFailure`, but no `critic_g1_failed` row for `audit_day` to see. Bounded by the variable being a staging affordance (`.env.example:35` ships it empty; only `ops/staging-env.example` populates it). (b) `acquire_lock`'s reason was "returns 0 on contention, guarding it would mislabel it" — contention is a `None` **return**, not an exception, so the guard would never see it and could mislabel nothing. The real reason is that it runs before `connect`.
- **B6 — R2's "a red exit costs nothing downstream" now names the two readers of unit state it omitted.** `scripts/dev_status.py:52`'s `UNITS = ("fund-daily", "fund-pnl")` plus `:203`'s `systemctl show fund-pnl.service -p Result` render `fund-pnl` red on the devops status report for a G1-only failure; and `ops/README.md:575` tells operators to run `systemctl --failed`, where a failed `Type=oneshot` unit persists until `systemctl reset-failed`, so the red outlives the night. Both are arguably correct behaviour and neither changes the decision, but they compound Escalation 13 — Slack headline, status report and `systemctl --failed` all point at the P&L leg for a failure with nothing to do with it. Added to R2 and to "defensible readings" #1, which previously named only the headline as the lever.
- **B7 — Escalation 13 reworded as a pre-existing defect this lane widens.** `ops/notify_failure.sh:46-51` does headline every `fund-pnl*` failure `'No P&L was posted for today'`, but it is already false today for a reflect-only failure: `scripts/reflect_day.py:313-347`'s `main()` has no guard, so anything raising inside `reflect_and_log` propagates and exits nonzero with the P&L line long since posted. What this lane changes is the odds — a second LLM-spending leg, deliberately fail-red — not the existence of the defect.
- **B8 — every `Run:` step now works from the worktree.** They named `.venv/bin/python3`, and a fresh git worktree has no `.venv` — only the main checkout does, and a bare `python3` on macOS is 3.9. Each is now `make deps && .venv/bin/python3 …`; `make deps` is the repo's own documented bootstrap for exactly this case (`Makefile:7-9`), idempotent and content-hash gated. A Global Constraints bullet states it once, since that section is implicitly part of every task.
- **R6 — smaller factual corrections.** `build_seat_options` is `agents/seats.py:65-135` (was 66-137). Task 7 Step 1's prose now names `PNL`, `MAKEFILE`, `OPS_README`, matching its code block. Escalation 11 corrected: as of `f496da5` the alert filer is hand-run (#179), so new alert codes do **not** automatically open GitHub issues. The alert arithmetic gains `model_fallback_used` — `agents/config/critic.yaml:5` pins `claude-sonnet-5`, so expect roughly one extra `#risk` post per successful turn, flagged as a known false positive rather than a real fallback. Task 6's vacuity tests are kept but the "the day someone adds an edge, a column or the table, this test reddens" overclaim is replaced by an explicit statement of the three shapes it catches and the ones it does not (a verdict-gated `stratgate` call, a lifecycle column under another name, a side table keyed by `spec_id`). Escalation 9 also narrowed to what is genuinely undetected — an *age* signal — since `OnFailure=` covers a leg that does not run.