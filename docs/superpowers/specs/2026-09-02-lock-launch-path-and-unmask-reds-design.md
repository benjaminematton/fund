# Lock the launch path, and stop a standing red absorbing new failures

**Status:** design, awaiting implementation plan
**Date:** 2026-09-02
**Origin:** the 2026-08-31 → 09-02 outage. Fix for the trigger shipped as PR #222 (`f902c3a`); this
addresses the two conditions that let it happen and let it stand for three days.
**Not in scope:** #108 (blast radius — one MCP server down stops every seat). Own issue, own design.
**Goal:** dependency maintenance that runs itself. Parts 1–2 make the signal trustworthy; Part 3
hands the bumping to a bot. Automating on today's signal would have caused this outage, not caught
it — see Part 3.

## The incident, in one paragraph

`uvx` launches the Alpaca MCP server. `alpaca-mcp-server@2.2.1` declares `fastmcp>=3.1.0` with no
upper bound, so `uvx` resolved fastmcp 4.0.2, which moved `fastmcp.tools.tool` —
imported at `alpaca_mcp_server/security.py:14`. The server raised `ModuleNotFoundError` at
**startup**. Every seat turn hit `required MCP server(s) not connected after 30.0s: {'alpaca':
'failed'}` and defaulted to HOLD. Invariant 4 behaved exactly as designed; the firm placed no order
after 2026-08-31, lost the 09-01 and 09-02 sessions, and failed all 9 reflection turns on 09-02.

## The theme

**Every guard involved proved something adjacent to what it appeared to prove.**

| Guard | Appears to prove | Actually proves |
|---|---|---|
| `requirements.lock` | the dependency tree is pinned | the *fund venv's* tree is pinned — the `uvx` launch path is not in it |
| `uvx <spec> --help` (ops/README pre-warm) | the server works | click can parse argv; it exits before importing `.server` |
| `services` 🔴 | the day's health | one exit code — a new failure under an old red is invisible |
| `ALPACA_MCP_SPEC` exact pin | the launch is reproducible | the *app* is pinned; its dependencies resolve fresh at 09:35 |

Two changes, each making one guard cover its actual claim. The `--help` row was already fixed in
#222.

---

## Part 1 — lock the launch path

### Decision: `--exclude-newer` with a fixed timestamp

```python
# agents/seats.py
MCP_RESOLUTION_DATE = "2026-09-03T00:00:00Z"
ALPACA_MCP_ARGS = ["--exclude-newer", MCP_RESOLUTION_DATE, ALPACA_MCP_SPEC]
```

### Why, and what was rejected

`uv`'s own documentation is explicit that this is the reproducibility mechanism:

> "uv supports an `--exclude-newer` option to limit resolution to distributions uploaded before a
> specific date, allowing reproduction of installations regardless of new package releases."

The `uv tool` / `uvx` documentation says **nothing** about reproducibility. There is no tool
lockfile. That absence is the finding: this is unsolved upstream, so we are choosing between
imperfect mechanisms rather than failing to find the standard one.

**Rejected — a committed constraints file** (`--constraints ops/mcp-constraints.txt`). It was the
first choice, and uv's docs rule it out for the case that actually bit us:

> "being listed as a constraint alone will not cause a package to be included to the resolution.
> Instead, constraints only take effect if a requested package is already pulled in."

A constraints file is structurally blind to a dependency that does not exist yet. `--exclude-newer`
bounds those too, because it filters by upload date rather than by name.

**Rejected — a rolling cooldown** (`--exclude-newer "7 days"`). uv supports it and frames it as a
security posture. It is wrong here, and the reasoning is worth keeping: a cooldown would have
*delayed* fastmcp 4 by a week and then broken us anyway. It ages packages; it does not pin them.

**Rejected — merging into `requirements.lock`** and launching by path. Maximum discipline, one lock.
But `fastmcp-slim` requires `mcp<2.0,>=1.24.0` and `claude-agent-sdk` requires `mcp<2.0,>=1.23.0`.
They co-resolve today (`mcp==1.29.0`), so this is not a conflict — it is a *coupling*: ~70 packages
of the broker server's tree would gain the power to move `mcp` or `pydantic` underneath the SDK that
places orders. The `uvx` isolation boundary is load-bearing and is kept deliberately.

### The two constants are self-enforcing, verified

Demonstrated on the droplet, not reasoned:

- `--exclude-newer 2026-09-03T00:00:00Z` + `alpaca-mcp-server@2.3.1` → server starts, resolves
  `fastmcp 3.4.7`.
- `--exclude-newer 2026-06-01T00:00:00Z` → uv **refuses**: *"`alpaca-mcp-server` was filtered by
  `exclude-newer`… The requested version, v2.3.1, was published at 2026-09-01."*

So a bump to `ALPACA_MCP_SPEC` without a matching bump to `MCP_RESOLUTION_DATE` **fails loudly at
launch** rather than silently resolving something stale. That is the coupling working for us, and
it is why the date lives beside the spec in one file rather than in a config.

### Honest costs, stated rather than glossed

1. **The resolved tree is invisible in a diff.** A constraints file would show `fastmcp==3.4.7`;
   a date shows a date. Mitigation is `make preflight`, which exercises the real launch path and is
   already mandatory after any change.
2. **It freezes security updates until the date is bumped.** This is the cost that makes Part 3
   mandatory rather than optional: a fixed date with no bot behind it is a slow rot traded for a
   fast one. Part 3b gives it a bot; the staleness check in the resolved Open Question 1 detects
   the bot dying.

### Blast radius

`ALPACA_MCP_SPEC` has five derived sites, all of which must move to `ALPACA_MCP_ARGS` or they pin a
launch nobody makes:

- `agents/seats.py` — the launch site itself
- `tests/test_trader_wiring.py:37`
- `tests/test_live_smoke.py:195`
- `ops/README.md` ×2 (cache pre-warm, cutover validation)
- `specs/design.md:270`

---

## Part 2 — the `services` line names its alert codes

### The failure

`scripts/dev_status.py::_service()` reads systemd only:

```
🔴 services | fund-daily: exit-code at Wed 2026-09-02 09:36:51 EDT
```

`services` had already been red for 9 days for a *known* reason (#141's accounting shortfall). The
new failure produced an identical-looking line, so it read as the old one — by a human on 09-01, and
by this session's own digest on 09-02, which named #141 as the headline red and was wrong.

The distinguishing evidence was one query away: #141 raises `accounting_shortfall`; the outage
raised `seat_turn_failed` and `pm_timeout`. Different codes entirely.

### The change

Print the latest day's distinct alert codes beside the exit status.

**Reuse the file's existing idiom rather than inventing a second notion of "today".**
`dev_status.py:372` already asks the question this needs:

```sql
select kind from events where date(created_at) = (select max(date(created_at)) from events)
```

That is *the latest date present in `events`*, not wall-clock today — which is both the established
pattern in this file and consistent with the repo's injected-clock discipline (`CLAUDE.md`: never
call `datetime.now()` in business logic). A second, clock-derived definition of "today" sitting
beside this one would eventually disagree with it on exactly the day someone is debugging.

The `events` table is already read here at lines 304 and 372, so this adds a query to an existing
source, not a new dependency:

```
🔴 services | fund-daily: exit-code at Wed 09:36:51 EDT
             alerts 2026-09-02: seat_turn_failed x3, pm_timeout x3, audit_failed
```

Nothing about novelty detection, lookback windows, or first-seen state. A reader who expects
`accounting_shortfall` and sees `seat_turn_failed` has the finding. This alone would have surfaced
the outage on day one.

### Error handling

Follows the file's existing doctrine, which is already written into `_ssh`'s docstring: *"None means
'could not read', never 'read an empty result' — the callers depend on that difference, because
rendering absence as health is the exact failure this package exists to prevent."*

A failed events read renders as **unknown**, never as "no alerts". An empty-but-successful read
renders as "no alerts today". These must be distinguishable in the output.

---

## Part 3 — make the green trustworthy, then let a bot do the bumping

The goal is a codebase that maintains its own dependencies with nobody touching anything by hand.
Parts 1 and 2 are the precondition for that, not a detour from it.

### Why automation cannot come first

**`make test` was green for all three days the fund was down.** The suite is offline by design
("no network, no API keys"), so it cannot start the MCP server and could not have noticed. Adding
auto-merge today would not have prevented this outage — it would have *caused* it faster and with
more confidence: bot bumps, CI green, merge, fund silently stops trading.

**A bot may only merge on a signal that means something.** Today's green does not.

### 3a. The CI probe that makes green mean something

The failure was `ModuleNotFoundError` at **import**. It needed the network to fetch the package but
**no credentials at all** — which is what makes it catchable in CI:

```
uvx <ALPACA_MCP_ARGS> --transport stdio < /dev/null
```

**It must be a separate CI job, never part of `make test`.** The offline guarantee is a stated
property of that suite and several other guarantees rest on it; a network-dependent step inside it
would silently convert `make test` into something that fails on a plane. New job, network allowed,
no secrets required. CI also installs from `requirements.lock` via pip and has no `uv` today, so the
job needs `uv` installed (e.g. `astral-sh/setup-uv`).

This is the same probe #222 put in `ops/README.md`, moved to where a bot can read it.

### 3b. Renovate proposes the bump

A regex custom manager tracks the constants, which are ordinary Python assignments rather than a
manifest entry. Verified as supported: `customType: "regex"` with `managerFilePatterns` and
`matchStrings` over arbitrary source files.

It bumps `ALPACA_MCP_SPEC` and `MCP_RESOLUTION_DATE` **together**. If it ever bumps only the spec,
Part 1's self-enforcing coupling catches it: uv refuses to resolve when the date excludes the pinned
version, so the CI probe goes red and the PR cannot merge. The failure mode is a stuck PR, never a
bad merge.

### 3c. It proposes; it does not auto-merge — deliberately

`tests/test_broker_surface_pin.py` carries a hardcoded version literal **specifically so every bump
costs a human a look**, because the broker's mutating-verb surface is a thing a person must decide
about rather than discover. Auto-merge would defeat a friction point the repo built on purpose.

So an `alpaca-mcp-server` bump lands as: **bot opens the PR, CI proves the server still starts, the
surface-pin test stays red until a human re-enumerates and confirms.** That is precisely this
repo's existing doctrine — *the firm proposes, a human merges* (`specs/improvement.md`) — applied to
dependencies instead of to trading behaviour.

Ordinary `requirements.lock` dependencies carry no such surface, and auto-merge on green is
appropriate for those.

### Where the line sits

Self-healing stops at the invariants, and that boundary is load-bearing rather than bureaucratic:

| Self-heals | Never self-heals |
|---|---|
| dependency versions, resolution dates, lockfiles | gate thresholds (invariant 3) |
| CI and probe wiring | charters, desk config (`specs/design.md` non-goals) |
| | anything reaching `orders` or the broker |

---

## Testing

Offline, no network, no keys — consistent with `make test`.

**Part 1**
- `ALPACA_MCP_ARGS` carries `--exclude-newer` with a value parsing as an ISO-8601 UTC timestamp.
- The spec is the **last** argv entry, so uv reads preceding flags as its own.
- The seat's built options actually carry those args (checked through `build_trader_options`, not
  by reading the constant back).
- `MCP_RESOLUTION_DATE` is not in the future — a future date silently excludes nothing and would
  make the pin decorative.

**Part 2**
- Given a fixture `events` table, the services detail lists each distinct code with its count, for
  the latest date present in `events` — asserted against a fixture holding TWO days, so a test that
  accidentally reports all-time counts fails.
- Given a *failed* read, the detail says unknown — asserted distinctly from the empty case, because
  conflating them is the specific bug this repo keeps rediscovering.

**Part 3** — the probe is itself a test and needs its own proof, or it joins the list of guards in
the table above:

- The CI probe must be shown to **fail on a broken server**, not merely pass on a working one. Run
  it against `alpaca-mcp-server@2.2.1` with no date pin (the exact broken combination) and confirm
  it goes red. Without that demonstration there is no evidence the probe can detect anything, and
  it becomes a fourth green that means nothing.
- The probe must derive its argv from `ALPACA_MCP_ARGS`, never restate it. A probe testing a launch
  nobody makes is the `--help` failure again in a new place.
- Renovate's regex manager must be shown to actually match — a `customManagers` block that silently
  matches nothing produces no PRs and looks identical to "no updates available". This is the dead
  healer that Open Question 1's staleness check exists to catch, and it should be verified once at
  setup rather than discovered in 90 days.

**Every test is manufactured red first.** Drop the `--exclude-newer` flag, reorder the argv so the
spec is not last, set a future date, blank the events table, fail the read, point the probe at the
broken version — and the failure output is read before the green is trusted. A test that passes on
first write pins nothing.

## What this does not fix — after Part 3

- **#108.** One MCP server failing still stops every seat, including seats that need no Alpaca
  tools. That is what made this total rather than partial, and it is the largest remaining risk.
- **`fastmcp` 3.x still floats within the date bound.** Bounded, not eliminated.
- **Detection is still pull-based.** The codes appear when a human runs `make dev-status`. Nothing
  here pushes. The fund's own `OnFailure=` alerting is the push channel and is a separate system.
- **The CI probe proves the server starts, not that its tools still work.** `make schema-pin` and
  `make surface-pin` remain live-only and human-run; that is why 3c keeps a human on the merge.

---

## Open questions — resolved

**1. Who bumps `MCP_RESOLUTION_DATE`?** ~~Open.~~ **Nobody, by hand.** Part 3b's bot proposes it.
The backstop is a staleness check in `dev_status.py`: if the date is older than ~90 days the check
goes amber. Note what that check is actually for — **it detects a dead healer**, which is the
failure mode automation introduces (Renovate disabled, token expired, config silently not matching).
A self-healing system needs a monitor on the healer, or it fails closed and silent.

The freeze-vs-stale trade is not close on the evidence: unpinned resolution has cost three lost
trading days here (plus the 08-17 and 08-18 lost days in the same class), while staleness on a
single-tenant droplet running a **paper** account with no real money and no third-party data costs
close to nothing. Freezing would be the wrong call if this handled real money or multi-tenant data.
It does not.

**2. Is `issue_coverage` broken?** ~~Open.~~ **No — it is correct and its message overstates.**
`devcheck/checks.py:253-259` keys on **check names** (`f.check not in s.tracked_checks`), so it
asks *"does every red check have a `check:<id>` issue?"* `services` had one (#141), so green was
accurate. But it reports **"every alert is tracked by an issue"**, which is not what it verified:
one issue on `services` appears to cover unlimited distinct underlying failures.

This is the same defect as everything else here — a guard proving something adjacent to its claim —
and it needs no separate work beyond narrowing that message string to what it checks. Part 2 fixes
the visible symptom.
