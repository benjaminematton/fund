# Lock the launch path, and stop a standing red absorbing new failures

**Status:** design, awaiting implementation plan
**Date:** 2026-09-02
**Origin:** the 2026-08-31 → 09-02 outage. Fix for the trigger shipped as PR #222 (`f902c3a`); this
addresses the two conditions that let it happen and let it stand for three days.
**Not in scope:** #108 (blast radius — one MCP server down stops every seat). Own issue, own design.

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
2. **It freezes security updates until someone bumps the date.** This is a real, permanent cost and
   it needs an owner, or it rots silently. See Open Questions.

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

**Every test is manufactured red first.** Drop the `--exclude-newer` flag, reorder the argv so the
spec is not last, set a future date, blank the events table, fail the read — and the failure output
is read before the green is trusted. A test that passes on first write pins nothing.

## What this does not fix

- **#108.** One MCP server failing still stops every seat, including seats that need no Alpaca
  tools. That is what made this total rather than partial, and it is the largest remaining risk.
- **`fastmcp` 3.x still floats within the date bound.** Bounded, not eliminated.
- **Detection is still pull-based.** The codes appear when a human runs `make dev-status`. Nothing
  here pushes.

## Open questions

1. **Who owns bumping `MCP_RESOLUTION_DATE`, and on what trigger?** Without an answer this freezes
   the tree indefinitely and trades a fast-moving risk for a slow-moving one. Candidates: a checklist
   item in the existing weekly cycle, or a check that flags the date as stale past N days.
2. **Should `issue_coverage` be re-examined?** It reported green throughout the outage. Either it
   does not key on alert codes, or an issue already covers `seat_turn_failed`. Not investigated —
   named here because a check that stayed green through a three-day outage deserves a look.
