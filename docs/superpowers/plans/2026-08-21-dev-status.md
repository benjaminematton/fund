# dev_status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only production health check, `make dev-status`, that answers "is every stated invariant and Phase 2 acceptance criterion still true on the box that trades?" and renders the answer as markdown.

**Architecture:** Pure evaluation over an injected snapshot. `devcheck/` holds a frozen `Snapshot` dataclass, a `Finding` dataclass, and `evaluate(snapshot) -> list[Finding]` — all pure, no I/O, no clock. `scripts/dev_status.py` is a composition root that builds a real `Snapshot` from ssh/broker/SQLite and prints the render. This mirrors `scripts/resolve_day.py` ↔ `orchestrator/resolve.py`: the script is never imported by a test, the seam always is.

**Tech Stack:** Python 3.12, `pytest`, stdlib `sqlite3`, `subprocess` for ssh/git. No new dependencies.

## Global Constraints

- **Read-only.** No check may write, place, cancel, amend, or deploy anything. Copied from spec §1.
- **No wall-clock call in `devcheck/`.** Time arrives as a parameter. `CLAUDE.md`: no `datetime.now()` in business logic.
- **No LLM imports in `devcheck/`.** Pure Python + stdlib.
- **Exit code 0 always.** A failed check renders as a finding, never as a crash that hides the other checks. Spec §2.4.
- **Every check derives from a stated invariant or a phase acceptance criterion.** Incidents validate the list, never source it. Spec §2.4 derivation rule. A check justified only by "this bit us once" does not go in.
- **Offline tests.** `make test` runs with no network and no keys. Every reader is injected.
- Conventional commits. No AI attribution trailer.

---

## File Structure

| File | Responsibility |
|---|---|
| `devcheck/__init__.py` | package marker |
| `devcheck/model.py` | `Finding`, `Position`, `OpenOrder`, `ServiceResult`, `Snapshot` — data only, no logic |
| `devcheck/checks.py` | one pure function per check; each returns `Finding \| None` |
| `devcheck/evaluate.py` | `evaluate(snapshot) -> list[Finding]` — the ordered check registry |
| `devcheck/render.py` | `render(findings) -> str` — markdown |
| `scripts/dev_status.py` | composition root: build a real `Snapshot`, print `render(evaluate(s))` |
| `.claude/health.md` | the fund's descriptor: `health_command` + interpretation prose |
| `tests/test_devcheck_checks.py` | per-check unit tests, including negative controls |
| `tests/test_devcheck_render.py` | render + suppression |
| `Makefile` | `dev-status` target |

---

### Task 1: The model, the registry, and invariant 1

**Files:**
- Create: `devcheck/__init__.py`, `devcheck/model.py`, `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Finding(check: str, severity: str, detail: str)` with severity in `{"ok","warn","alert"}`; `Snapshot` frozen dataclass; `evaluate(snapshot) -> list[Finding]`; `check_paper_trading(snapshot) -> Finding`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_devcheck_checks.py
"""Unit tests for devcheck's pure checks.

Every check is a pure function of a Snapshot, so each test states a whole
world and asserts one verdict. Each check gets a negative control: a world
where it MUST fire. A check whose negative control also passes is not a
check — three tests in this repo passed on 2026-08-21 because they always
passed, and two were caught by luck.
"""

from __future__ import annotations

from devcheck.evaluate import evaluate
from devcheck.model import Snapshot


def _snap(**over) -> Snapshot:
    """A wholly healthy world. Each test darkens exactly one field."""
    base = dict(
        droplet_env={"ALPACA_PAPER_TRADE": "true"},
        seat_trading_toolsets={"exec": True, "pm": False, "analyst": False, "news": False},
        orders=[],
        tickets={},
        events_unposted=0,
        broker_fill_count=0,
        checkpoints=[],
        journals_written=set(),
        seats_participating=set(),
        scorecard_codes=[],
        positions=[],
        open_orders=[],
        due_unresolved=[],
        droplet_head="abc1234",
        origin_master="abc1234",
        commits_behind=0,
        services={},
        suppressed=frozenset(),
    )
    base.update(over)
    return Snapshot(**base)


def test_paper_trading_true_is_ok():
    findings = evaluate(_snap())
    paper = [f for f in findings if f.check == "paper_trading"]
    assert len(paper) == 1
    assert paper[0].severity == "ok"


def test_paper_trading_false_alerts():
    """Negative control for invariant 1 — the most important line in CLAUDE.md."""
    findings = evaluate(_snap(droplet_env={"ALPACA_PAPER_TRADE": "false"}))
    paper = [f for f in findings if f.check == "paper_trading"]
    assert paper[0].severity == "alert"
    assert "invariant 1" in paper[0].detail


def test_paper_trading_missing_alerts():
    """Absent is not the same as false, and both must alert."""
    findings = evaluate(_snap(droplet_env={}))
    paper = [f for f in findings if f.check == "paper_trading"]
    assert paper[0].severity == "alert"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devcheck'`

- [ ] **Step 3: Write minimal implementation**

```python
# devcheck/__init__.py
"""Read-only production health checks for developers.

Every check answers: is a stated invariant or a phase acceptance criterion
still true on the box that trades? Incidents validate this list; they never
source it. See docs/superpowers/specs/2026-08-21-day-bookends-design.md §2.4.

Pure: no I/O, no clock, no LLM imports. scripts/dev_status.py is the only
place that talks to a droplet, a broker, or a database.
"""
```

```python
# devcheck/model.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Finding:
    check: str        # stable id, greppable, never localised
    severity: str     # "ok" | "warn" | "alert"
    detail: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    covering_qty: float      # shares covered by live stops, per design.md §5


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    side: str
    qty: float
    type: str
    status: str


@dataclass(frozen=True)
class OrderRow:
    client_order_id: str
    symbol: str


@dataclass(frozen=True)
class ServiceResult:
    unit: str
    result: str          # "success" | "exit-code" | "unreachable" | ...
    last_run: str        # ISO or "" when never


@dataclass(frozen=True)
class Snapshot:
    """One complete read of production. Every field is data; nothing here
    computes. Built by scripts/dev_status.py, consumed by evaluate()."""

    droplet_env: Mapping[str, str]
    seat_trading_toolsets: Mapping[str, bool]
    orders: Sequence[OrderRow]
    tickets: Mapping[str, str]          # ticket id -> symbol
    events_unposted: int
    broker_fill_count: int
    checkpoints: Sequence[tuple[str, str, str]]   # (run_date, stage, status)
    journals_written: frozenset[str] | set[str]
    seats_participating: frozenset[str] | set[str]
    scorecard_codes: Sequence[str]
    positions: Sequence[Position]
    open_orders: Sequence[OpenOrder]
    due_unresolved: Sequence[int]       # decision ids past horizon with no resolution
    droplet_head: str
    origin_master: str
    commits_behind: int
    services: Mapping[str, ServiceResult]
    suppressed: frozenset[str] = field(default_factory=frozenset)
```

```python
# devcheck/checks.py
from __future__ import annotations

from devcheck.model import Finding, Snapshot


def check_paper_trading(s: Snapshot) -> Finding:
    """Invariant 1 — paper only, everywhere.

    Nothing else in the fund asserts this against the running host.
    scripts/resolve_day.py guards its own startup, which protects that job
    and says nothing about the box.
    """
    value = s.droplet_env.get("ALPACA_PAPER_TRADE")
    if value == "true":
        return Finding("paper_trading", "ok", "ALPACA_PAPER_TRADE=true on the droplet")
    return Finding(
        "paper_trading",
        "alert",
        f"ALPACA_PAPER_TRADE={value!r} on the droplet — invariant 1 requires 'true'",
    )
```

```python
# devcheck/evaluate.py
from __future__ import annotations

from devcheck import checks
from devcheck.model import Finding, Snapshot

# Ordered registry. Adding a check means adding it here and nowhere else.
# Order is display order: invariants first, acceptance criteria second,
# deployment state last.
CHECKS = (
    checks.check_paper_trading,
)


def evaluate(snapshot: Snapshot) -> list[Finding]:
    """Run every check against one snapshot. Pure.

    A check returning None means "nothing to say"; it is omitted rather than
    rendered as an empty row.
    """
    out: list[Finding] = []
    for check in CHECKS:
        result = check(snapshot)
        if result is None:
            continue
        if isinstance(result, Finding):
            out.append(result)
        else:
            out.extend(result)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/ tests/test_devcheck_checks.py
git commit -m "feat: nothing checked that the box that trades is still on paper"
```

---

### Task 2: Invariants 2, 5 and 6 — the broker and DB contracts

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `Finding` from Task 1.
- Produces: `check_trading_toolset(s)`, `check_order_idempotency(s)`, `check_outbox(s)`, `check_db_broker_agreement(s)` — each `Finding | None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py
from devcheck.model import OrderRow


def _only(findings, check):
    matches = [f for f in findings if f.check == check]
    assert len(matches) == 1, f"expected exactly one {check}, got {matches}"
    return matches[0]


def test_trading_toolset_exec_only_is_ok():
    assert _only(evaluate(_snap()), "trading_toolset").severity == "ok"


def test_trading_toolset_second_seat_alerts():
    """Negative control for invariant 2."""
    f = _only(evaluate(_snap(seat_trading_toolsets={"exec": True, "pm": True})), "trading_toolset")
    assert f.severity == "alert"
    assert "pm" in f.detail


def test_trading_toolset_exec_missing_alerts():
    """Exec losing `trading` is silent otherwise: the day just never fills."""
    f = _only(evaluate(_snap(seat_trading_toolsets={"exec": False})), "trading_toolset")
    assert f.severity == "alert"


def test_order_idempotency_ok_when_every_coid_is_a_ticket():
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"})
    assert _only(evaluate(s), "order_idempotency").severity == "ok"


def test_order_idempotency_alerts_on_unknown_coid():
    """Negative control for invariant 5 — a coid that is not a ticket id
    means an order the gate did not authorise, or a minted retry id."""
    s = _snap(orders=[OrderRow("free-form", "NVDA")], tickets={"t-1": "NVDA"})
    f = _only(evaluate(s), "order_idempotency")
    assert f.severity == "alert"
    assert "free-form" in f.detail


def test_outbox_ok_when_drained():
    assert _only(evaluate(_snap()), "outbox").severity == "ok"


def test_outbox_alerts_on_backlog():
    """Negative control for invariant 6 — Slack is the projection, and an
    undrained outbox means it is silently stale."""
    f = _only(evaluate(_snap(events_unposted=3)), "outbox")
    assert f.severity == "alert"
    assert "3" in f.detail


def test_db_broker_agreement_ok_when_counts_match():
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"}, broker_fill_count=1)
    assert _only(evaluate(s), "db_broker_agreement").severity == "ok"


def test_db_broker_agreement_alerts_when_broker_saw_more():
    """Negative control for invariant 6 — SQLite is the source of truth, so
    the broker having seen fills the DB has no row for is a divergence."""
    s = _snap(orders=[OrderRow("t-1", "NVDA")], tickets={"t-1": "NVDA"}, broker_fill_count=3)
    f = _only(evaluate(s), "db_broker_agreement")
    assert f.severity == "alert"
    assert "1" in f.detail and "3" in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: FAIL — `AssertionError: expected exactly one trading_toolset, got []`

- [ ] **Step 3: Write minimal implementation**

```python
# append to devcheck/checks.py

def check_trading_toolset(s: Snapshot) -> Finding:
    """Invariant 2 — only the Execution Trader holds the `trading` toolset."""
    holders = sorted(seat for seat, has in s.seat_trading_toolsets.items() if has)
    if holders == ["exec"]:
        return Finding("trading_toolset", "ok", "only exec holds `trading`")
    extra = [h for h in holders if h != "exec"]
    if extra:
        return Finding(
            "trading_toolset",
            "alert",
            f"seats other than exec hold `trading`: {', '.join(extra)} — invariant 2",
        )
    return Finding(
        "trading_toolset",
        "alert",
        "exec does not hold `trading` — no order can ever be placed",
    )


def check_order_idempotency(s: Snapshot) -> Finding:
    """Invariant 5 — client_order_id is always a gate ticket id."""
    orphans = [o.client_order_id for o in s.orders if o.client_order_id not in s.tickets]
    if not orphans:
        return Finding(
            "order_idempotency",
            "ok",
            f"{len(s.orders)} order(s), every client_order_id is a ticket id",
        )
    return Finding(
        "order_idempotency",
        "alert",
        f"client_order_id not matching any ticket: {', '.join(sorted(orphans))} — invariant 5",
    )


def check_outbox(s: Snapshot) -> Finding:
    """Invariant 6 — Slack is a projection of SQLite. An undrained outbox
    means the projection is silently behind the truth."""
    if s.events_unposted == 0:
        return Finding("outbox", "ok", "events outbox fully drained")
    return Finding(
        "outbox",
        "alert",
        f"{s.events_unposted} event(s) with posted_at IS NULL — Slack is stale",
    )


def check_db_broker_agreement(s: Snapshot) -> Finding:
    """Invariant 6 — the DB is the source of truth, so the broker having
    seen more fills than the DB has rows is a divergence, not a rounding
    difference. Manual out-of-gate orders produce exactly this."""
    rows = len(s.orders)
    if rows == s.broker_fill_count:
        return Finding("db_broker_agreement", "ok", f"orders rows == broker fills ({rows})")
    return Finding(
        "db_broker_agreement",
        "alert",
        f"orders has {rows} row(s); broker has seen {s.broker_fill_count} fill(s) — "
        "the fund's record disagrees with the broker",
    )
```

```python
# devcheck/evaluate.py — replace the CHECKS tuple
CHECKS = (
    checks.check_paper_trading,
    checks.check_trading_toolset,
    checks.check_order_idempotency,
    checks.check_outbox,
    checks.check_db_broker_agreement,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/checks.py devcheck/evaluate.py tests/test_devcheck_checks.py
git commit -m "feat: invariants 2, 5 and 6 asserted against production, not just in tests"
```

---

### Task 3: Invariant 4 and Phase 2 acceptance — the run's own health

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `Finding`.
- Produces: `check_degradations(s)`, `check_checkpoints(s)`, `check_journals(s)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py

def test_degradations_ok_when_none():
    assert _only(evaluate(_snap()), "degradations").severity == "ok"


def test_degradations_warn_on_gate_error():
    """Invariant 4 says a gate_error resolves to HOLD, which is correct
    behaviour — so this warns, it does not alert. The day was not wrong;
    it was degraded, and a degraded day that nobody sees becomes normal."""
    f = _only(evaluate(_snap(scorecard_codes=["gate_error"])), "degradations")
    assert f.severity == "warn"
    assert "gate_error" in f.detail


def test_degradations_warn_on_pm_timeout():
    f = _only(evaluate(_snap(scorecard_codes=["pm_timeout"])), "degradations")
    assert f.severity == "warn"


def test_checkpoints_ok_when_all_done():
    s = _snap(checkpoints=[("2026-08-21", "research", "done"), ("2026-08-21", "gate", "done")])
    assert _only(evaluate(s), "checkpoints").severity == "ok"


def test_checkpoints_alert_on_unfinished_stage():
    """Negative control — Phase 2 acceptance requires every checkpoint done."""
    s = _snap(checkpoints=[("2026-08-21", "research", "done"), ("2026-08-21", "gate", "running")])
    f = _only(evaluate(s), "checkpoints")
    assert f.severity == "alert"
    assert "gate" in f.detail


def test_journals_ok_when_every_participant_wrote():
    s = _snap(seats_participating={"pm", "analyst"}, journals_written={"pm", "analyst"})
    assert _only(evaluate(s), "journals").severity == "ok"


def test_journals_warn_when_a_participant_did_not_write():
    """Phase 2 acceptance: after a day each participating seat has a journal
    entry. Memory is load-bearing in this phase, so a silent seat matters."""
    s = _snap(seats_participating={"pm", "analyst"}, journals_written={"pm"})
    f = _only(evaluate(s), "journals")
    assert f.severity == "warn"
    assert "analyst" in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: FAIL — `expected exactly one degradations, got []`

- [ ] **Step 3: Write minimal implementation**

```python
# append to devcheck/checks.py

# Codes that mean "the pipeline degraded to its default and said so".
# Invariant 4 makes these correct behaviour, not bugs — they warn so a
# permanently degraded day cannot quietly become the normal one.
_DEGRADATION_CODES = ("gate_error", "pm_timeout", "critic_timeout", "missing_signal")


def check_degradations(s: Snapshot) -> Finding:
    """Invariant 4 — every error resolves to HOLD. Correct, and worth seeing."""
    seen = [c for c in s.scorecard_codes if c in _DEGRADATION_CODES]
    if not seen:
        return Finding("degradations", "ok", "no stage degraded to its default")
    return Finding(
        "degradations",
        "warn",
        f"degraded to default: {', '.join(sorted(set(seen)))} — correct per invariant 4, "
        "but the day did not run clean",
    )


def check_checkpoints(s: Snapshot) -> Finding:
    """Phase 2 acceptance — every checkpoint reaches `done`."""
    unfinished = sorted({stage for _, stage, status in s.checkpoints if status != "done"})
    if not unfinished:
        return Finding("checkpoints", "ok", f"{len(s.checkpoints)} checkpoint(s), all done")
    return Finding(
        "checkpoints",
        "alert",
        f"stage(s) not done: {', '.join(unfinished)}",
    )


def check_journals(s: Snapshot) -> Finding:
    """Phase 2 acceptance — each participating seat writes a journal entry.
    design.md §7 makes memory load-bearing in this phase."""
    missing = sorted(set(s.seats_participating) - set(s.journals_written))
    if not missing:
        return Finding("journals", "ok", "every participating seat wrote a journal entry")
    return Finding(
        "journals",
        "warn",
        f"participated but wrote no journal entry: {', '.join(missing)}",
    )
```

```python
# devcheck/evaluate.py — extend CHECKS
CHECKS = (
    checks.check_paper_trading,
    checks.check_trading_toolset,
    checks.check_order_idempotency,
    checks.check_outbox,
    checks.check_db_broker_agreement,
    checks.check_degradations,
    checks.check_checkpoints,
    checks.check_journals,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 19 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/checks.py devcheck/evaluate.py tests/test_devcheck_checks.py
git commit -m "feat: a degraded day and a clean day looked identical from outside"
```

---

### Task 4: Reflection at horizon — the same state, two opposite verdicts

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `Finding`.
- Produces: `check_reflection(s)`.

This is the check the spec calls out by name. On 2026-08-21 `resolutions` was
empty and that was **correct** — no decision had reached its horizon. The same
empty table on 2026-08-24 is a dead job. The snapshot therefore carries
`due_unresolved` (decision ids already past horizon with no row) rather than a
row count, so the check cannot confuse the two.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py

def test_reflection_ok_when_nothing_is_due():
    """2026-08-21: resolutions empty, nothing past horizon. Correct, silent."""
    assert _only(evaluate(_snap(due_unresolved=[])), "reflection").severity == "ok"


def test_reflection_alerts_when_something_is_due_and_unresolved():
    """2026-08-24: same empty table, decisions now past horizon. Dead job.

    This pair is the point of the check — identical `resolutions` content,
    opposite verdicts, distinguished only by whether anything is due."""
    f = _only(evaluate(_snap(due_unresolved=[1, 2])), "reflection")
    assert f.severity == "alert"
    assert "2" in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -k reflection -v`
Expected: FAIL — `expected exactly one reflection, got []`

- [ ] **Step 3: Write minimal implementation**

```python
# append to devcheck/checks.py

def check_reflection(s: Snapshot) -> Finding:
    """Phase 2 acceptance — the nightly job writes `resolutions` at horizon.

    An empty resolutions table is correct until a decision passes its horizon
    and a dead job afterwards. The snapshot carries the decisions that are
    already due, so the two cases cannot be confused: this is the shape that
    fooled a session on 2026-08-21, which read the empty table as a failure.
    """
    if not s.due_unresolved:
        return Finding("reflection", "ok", "no decision is past its horizon and unresolved")
    ids = ", ".join(str(i) for i in s.due_unresolved)
    return Finding(
        "reflection",
        "alert",
        f"{len(s.due_unresolved)} decision(s) past horizon with no resolutions row "
        f"(ids: {ids}) — the nightly reflection job is not landing",
    )
```

```python
# devcheck/evaluate.py — add to CHECKS, after check_journals
    checks.check_reflection,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 21 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/checks.py devcheck/evaluate.py tests/test_devcheck_checks.py
git commit -m "feat: an empty resolutions table means two opposite things, so say which"
```

---

### Task 5: Position coverage — the gate's stop contract

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `Position`.
- Produces: `check_position_coverage(s)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py
from devcheck.model import Position


def test_coverage_ok_when_fully_covered():
    s = _snap(positions=[Position("NVDA", qty=40, covering_qty=40)])
    assert _only(evaluate(s), "position_coverage").severity == "ok"


def test_coverage_alerts_on_a_naked_position():
    """2026-08-21's actual state: 40 shares, zero open orders, no stop."""
    s = _snap(positions=[Position("NVDA", qty=40, covering_qty=0)])
    f = _only(evaluate(s), "position_coverage")
    assert f.severity == "alert"
    assert "NVDA" in f.detail and "0" in f.detail and "40" in f.detail


def test_coverage_alerts_on_partial_cover():
    """Aggregate protection: N shares covered by one or more stops. Partial
    cover is exposure, not protection."""
    s = _snap(positions=[Position("NVDA", qty=80, covering_qty=40)])
    f = _only(evaluate(s), "position_coverage")
    assert f.severity == "alert"


def test_coverage_ok_with_no_positions():
    """Flat is not exposed. The check must not fire on an empty book."""
    assert _only(evaluate(_snap(positions=[])), "position_coverage").severity == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -k coverage -v`
Expected: FAIL — `expected exactly one position_coverage, got []`

- [ ] **Step 3: Write minimal implementation**

```python
# append to devcheck/checks.py

def check_position_coverage(s: Snapshot) -> Finding:
    """design.md §5 — a ticket carrying a stop_price becomes a broker-side
    stop leg, so a held position is expected to be covered.

    Coverage is AGGREGATE: N shares covered by one or more live stops.
    Partial cover is exposure; the uncovered remainder has no code path that
    will protect it.
    """
    naked = [p for p in s.positions if p.covering_qty < p.qty]
    if not naked:
        return Finding(
            "position_coverage",
            "ok",
            f"{len(s.positions)} position(s), every share covered",
        )
    parts = [
        f"{p.symbol} {p.covering_qty:g} of {p.qty:g} covered" for p in naked
    ]
    return Finding(
        "position_coverage",
        "alert",
        "; ".join(parts) + " — the uncovered shares have no code path that will protect them",
    )
```

```python
# devcheck/evaluate.py — add to CHECKS, after check_reflection
    checks.check_position_coverage,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 25 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/checks.py devcheck/evaluate.py tests/test_devcheck_checks.py
git commit -m "feat: a position the broker does not cover is not visible from the database"
```

---

### Task 6: Deployment state — is the code under test the code running

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `ServiceResult`.
- Produces: `check_deploy_state(s)`, `check_services(s)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py
from devcheck.model import ServiceResult


def test_deploy_state_ok_when_level():
    assert _only(evaluate(_snap()), "deploy_state").severity == "ok"


def test_deploy_state_warns_when_behind():
    """Behind is normal and worth seeing — the box is not running the code
    the suite just went green against."""
    s = _snap(droplet_head="aaa1111", origin_master="bbb2222", commits_behind=22)
    f = _only(evaluate(s), "deploy_state")
    assert f.severity == "warn"
    assert "22" in f.detail


def test_services_ok_when_all_succeeded():
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "success", "2026-08-21T09:35")})
    assert _only(evaluate(s), "services").severity == "ok"


def test_services_alert_on_failure():
    """2026-08-21: fund-daily.service exited 1 at 09:38 and sat 8h."""
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "exit-code", "2026-08-21T09:38")})
    f = _only(evaluate(s), "services")
    assert f.severity == "alert"
    assert "fund-daily" in f.detail


def test_services_alert_when_droplet_unreachable():
    """Spec §4: droplet unreachable renders as a finding; other checks still
    run. Absence of data is never rendered as health."""
    s = _snap(services={"fund-daily": ServiceResult("fund-daily", "unreachable", "")})
    f = _only(evaluate(s), "services")
    assert f.severity == "alert"
    assert "unreachable" in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -k "deploy or services" -v`
Expected: FAIL — `expected exactly one deploy_state, got []`

- [ ] **Step 3: Write minimal implementation**

```python
# append to devcheck/checks.py

def check_deploy_state(s: Snapshot) -> Finding:
    """Deployment state — is the code under test the code that is running.

    Warn, not alert: being behind is the normal state between a merge and a
    deploy. It is worth seeing because a green suite says nothing about the
    box, and on 2026-08-21 four sessions each held a different answer.
    """
    if s.commits_behind == 0:
        return Finding("deploy_state", "ok", f"droplet level with origin/master ({s.origin_master})")
    return Finding(
        "deploy_state",
        "warn",
        f"droplet at {s.droplet_head}, origin/master at {s.origin_master} — "
        f"{s.commits_behind} commit(s) behind",
    )


def check_services(s: Snapshot) -> Finding:
    """The scheduled units that constitute the fund actually running."""
    bad = [r for r in s.services.values() if r.result != "success"]
    if not bad:
        names = ", ".join(sorted(s.services))
        return Finding("services", "ok", f"last run succeeded: {names}" if names else "no units read")
    parts = [f"{r.unit}: {r.result}" + (f" at {r.last_run}" if r.last_run else "") for r in bad]
    return Finding("services", "alert", "; ".join(sorted(parts)))
```

```python
# devcheck/evaluate.py — add to CHECKS, last
    checks.check_deploy_state,
    checks.check_services,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 30 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/checks.py devcheck/evaluate.py tests/test_devcheck_checks.py
git commit -m "feat: a green suite says nothing about the box that trades"
```

---

### Task 7: Suppression, and the render

**Files:**
- Create: `devcheck/render.py`
- Modify: `devcheck/evaluate.py`
- Test: `tests/test_devcheck_render.py`

**Interfaces:**
- Consumes: `Finding`, `evaluate`.
- Produces: `render(findings) -> str`; `evaluate` honours `Snapshot.suppressed`.

`model_fallback_used` fires at severity 3 every day and is an SDK auxiliary
Haiku call, not a fallback. Without suppression a reader learns to skip the
whole report inside a week. Suppression downgrades to `ok` and says so — it
never deletes the row, because a silently dropped check is indistinguishable
from a check that never ran.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_devcheck_render.py
"""Render and suppression.

Suppression must DOWNGRADE and annotate, never delete: a row that vanishes is
indistinguishable from a check that never ran, which is the failure this whole
package exists to prevent.
"""

from __future__ import annotations

from devcheck.model import Finding
from devcheck.render import render


def test_render_groups_by_severity_worst_first():
    out = render([
        Finding("a", "ok", "fine"),
        Finding("b", "alert", "broken"),
        Finding("c", "warn", "degraded"),
    ])
    assert out.index("broken") < out.index("degraded") < out.index("fine")


def test_render_names_every_check_id():
    out = render([Finding("position_coverage", "alert", "NVDA 0 of 40 covered")])
    assert "position_coverage" in out


def test_render_handles_no_findings():
    assert render([]).strip() != ""


def test_suppressed_finding_is_downgraded_not_dropped():
    from devcheck.evaluate import apply_suppression
    findings = [Finding("degradations", "warn", "degraded to default: model_fallback_used")]
    out = apply_suppression(findings, frozenset({"degradations"}))
    assert len(out) == 1
    assert out[0].severity == "ok"
    assert "suppressed" in out[0].detail


def test_unsuppressed_finding_is_untouched():
    """Negative control: same finding, empty suppression set, still warns."""
    from devcheck.evaluate import apply_suppression
    findings = [Finding("degradations", "warn", "degraded to default: model_fallback_used")]
    out = apply_suppression(findings, frozenset())
    assert out[0].severity == "warn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devcheck.render'`

- [ ] **Step 3: Write minimal implementation**

```python
# devcheck/render.py
from __future__ import annotations

from typing import Sequence

from devcheck.model import Finding

_ORDER = {"alert": 0, "warn": 1, "ok": 2}
_MARK = {"alert": "🔴", "warn": "🟡", "ok": "🟢"}


def render(findings: Sequence[Finding]) -> str:
    """Markdown, worst first. Every check appears, including the healthy ones:
    a reader must be able to tell "checked and fine" from "not checked"."""
    if not findings:
        return "_no checks ran — this is itself a finding_\n"
    lines = ["| | check | detail |", "|---|---|---|"]
    for f in sorted(findings, key=lambda f: (_ORDER.get(f.severity, 3), f.check)):
        lines.append(f"| {_MARK.get(f.severity, '⚪')} | `{f.check}` | {f.detail} |")
    return "\n".join(lines) + "\n"
```

```python
# append to devcheck/evaluate.py
from dataclasses import replace


def apply_suppression(findings: list[Finding], suppressed: frozenset[str]) -> list[Finding]:
    """Downgrade suppressed checks to ok and say so.

    Never drops the row. A vanished row is indistinguishable from a check
    that never ran, and the whole package exists to remove that ambiguity.
    """
    out = []
    for f in findings:
        if f.check in suppressed and f.severity != "ok":
            out.append(replace(f, severity="ok", detail=f"{f.detail} [suppressed by .claude/health.md]"))
        else:
            out.append(f)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_render.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add devcheck/render.py devcheck/evaluate.py tests/test_devcheck_render.py
git commit -m "feat: a check nobody can silence is a check everybody learns to ignore"
```

---

### Task 8: The composition root and `make dev-status`

**Files:**
- Create: `scripts/dev_status.py`
- Modify: `Makefile`
- Test: `tests/test_dev_status_job.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `make dev-status` printing markdown; `build_snapshot(...)` and `main()` in the script.

Following `scripts/resolve_day.py`: `main()` is a composition root that builds
real clients and is **never called from a test**. The test pins what the job
depends on, because every dependency is a way for the job to go silent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dev_status_job.py
"""Offline tests for the dev-status job's seams.

scripts/dev_status.py is a composition root like scripts/resolve_day.py, so
main() is never called here — it opens ssh connections and a broker client.
What is pinned is what the job DEPENDS on: every dependency it declares is a
way for the job to go silent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("dev_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists():
    assert SCRIPT.exists()


def test_exposes_build_snapshot_and_main():
    m = _load()
    assert callable(m.build_snapshot)
    assert callable(m.main)


def test_reads_suppression_from_health_descriptor(tmp_path):
    """The descriptor's front matter is the only source of suppression."""
    m = _load()
    health = tmp_path / "health.md"
    health.write_text(
        "---\n"
        "health_command: make dev-status\n"
        "suppress:\n"
        "  - degradations\n"
        "---\n\n"
        "# prose\n"
    )
    assert m.read_suppressed(health) == frozenset({"degradations"})


def test_missing_descriptor_suppresses_nothing(tmp_path):
    """Negative control: no file means no suppression, never a crash."""
    m = _load()
    assert m.read_suppressed(tmp_path / "absent.md") == frozenset()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dev_status_job.py -v`
Expected: FAIL — `FileNotFoundError` / `assert SCRIPT.exists()`

- [ ] **Step 3: Write minimal implementation**

```python
#!/usr/bin/env python3
"""Read-only production health check for developers.

    make dev-status

Answers one question: is every stated invariant and Phase 2 acceptance
criterion still true on the box that trades? The checks live in devcheck/ and
are pure; this file is the only place that opens an ssh connection, a broker
client, or the database.

READ-ONLY, ALWAYS. Nothing here writes, places, cancels, amends or deploys.
Every finding is for a human to act on.

EXIT 0 ALWAYS. A check that cannot run renders as a finding. A non-zero exit
would hide every other check behind the first failure, which is the opposite
of the job.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devcheck.evaluate import apply_suppression, evaluate      # noqa: E402
from devcheck.model import Snapshot                            # noqa: E402
from devcheck.render import render                             # noqa: E402

HEALTH = ROOT / ".claude" / "health.md"


def read_suppressed(path: Path) -> frozenset[str]:
    """Parse `suppress:` from the descriptor's YAML front matter.

    Absent file, absent key, or malformed front matter all mean "suppress
    nothing" — never a crash. A descriptor problem must not be able to stop
    the checks from running.
    """
    try:
        text = path.read_text()
    except OSError:
        return frozenset()
    if not text.startswith("---"):
        return frozenset()
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("---")
    out: set[str] = set()
    in_block = False
    for line in front.splitlines():
        if line.strip().startswith("suppress:"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.add(stripped[2:].strip())
            elif stripped:
                break
    return frozenset(out)


def build_snapshot() -> Snapshot:
    """Build one Snapshot from production. The only I/O in the package.

    Each reader is wrapped so a failure becomes data — an unreachable droplet
    yields ServiceResult(result="unreachable"), never an exception that hides
    the broker and database checks behind it.
    """
    raise NotImplementedError("wired in Task 8 step 3b")


def main() -> int:
    snapshot = build_snapshot()
    findings = apply_suppression(evaluate(snapshot), read_suppressed(HEALTH))
    print(render(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3b: Wire `build_snapshot` against real readers**

Replace the `NotImplementedError` body. Each reader is individually wrapped so
one failure never hides the rest.

```python
import subprocess

DROPLET = "root@138.197.47.97"
REMOTE_ROOT = "/opt/fund"


def _ssh(cmd: str, timeout: int = 15) -> str | None:
    """Run one read-only command on the droplet. None on any failure."""
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", DROPLET, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _droplet_env() -> dict[str, str]:
    raw = _ssh(f"grep -E '^ALPACA_PAPER_TRADE=' {REMOTE_ROOT}/.env /etc/fund/env 2>/dev/null | head -1")
    if not raw or "=" not in raw:
        return {}
    _, _, value = raw.strip().partition("ALPACA_PAPER_TRADE=")
    return {"ALPACA_PAPER_TRADE": value.strip().strip("'\"")}


def _service(unit: str) -> "ServiceResult":
    from devcheck.model import ServiceResult
    raw = _ssh(f"systemctl show {unit}.service -p Result -p ExecMainExitTimestamp --value")
    if raw is None:
        return ServiceResult(unit, "unreachable", "")
    parts = [p.strip() for p in raw.splitlines() if p.strip()]
    result = parts[0] if parts else "unknown"
    last = parts[1] if len(parts) > 1 else ""
    return ServiceResult(unit, result, last)
```

Then assemble `Snapshot(...)` from `_droplet_env()`, `_service("fund-daily")`,
`_service("fund-pnl")`, a `git rev-list --count` for `commits_behind`, and
read-only SQLite queries over an `ssh … sqlite3 "file:…?mode=ro"` for
`events_unposted`, `checkpoints`, `orders`, `tickets` and `due_unresolved`.
Positions and `covering_qty` come from `AlpacaSource`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_dev_status_job.py tests/test_devcheck_checks.py tests/test_devcheck_render.py -v`
Expected: PASS — all green

- [ ] **Step 5: Add the Makefile target**

```makefile
dev-status: deps
	$(PYTHON) scripts/dev_status.py
```

- [ ] **Step 6: Verify end to end and commit**

Run: `make dev-status`
Expected: a markdown table, worst-severity first, exit 0.

```bash
git add scripts/dev_status.py Makefile tests/test_dev_status_job.py
git commit -m "feat: one command answers what thirteen sessions each answered differently"
```

---

### Task 9: Findings must become issues

**Files:**
- Modify: `devcheck/checks.py`, `devcheck/model.py`, `devcheck/evaluate.py`
- Test: `tests/test_devcheck_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `Finding`.
- Produces: `Snapshot.tracked_checks: frozenset[str]`; `check_issue_coverage(s, findings)`.

Derived from `docs/agents/issue-tracker.md`: work in this repo lives as GitHub
issues. An `alert` that never becomes an issue dies with the window — on
2026-08-21 four sessions independently re-reported #17, #18, #26 and #32 as
unowned. An issue labelled `check:<check_id>` marks a finding tracked.

This check runs **after** the others because it reads their output. It is the
one check that takes `findings` as well as the snapshot.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devcheck_checks.py

def test_issue_coverage_ok_when_alert_is_tracked():
    s = _snap(
        positions=[Position("NVDA", qty=40, covering_qty=0)],   # raises an alert
        tracked_checks=frozenset({"position_coverage"}),
    )
    assert _only(evaluate(s), "issue_coverage").severity == "ok"


def test_issue_coverage_alerts_when_an_alert_is_untracked():
    """Negative control — the finding exists and nothing will remember it."""
    s = _snap(
        positions=[Position("NVDA", qty=40, covering_qty=0)],
        tracked_checks=frozenset(),
    )
    f = _only(evaluate(s), "issue_coverage")
    assert f.severity == "alert"
    assert "position_coverage" in f.detail
    assert "gh issue create" in f.detail


def test_issue_coverage_ignores_warn_and_ok():
    """Only alerts nag. A warn that nagged daily would train the reader to
    skip the report, which is the failure suppression exists to prevent."""
    s = _snap(commits_behind=22, tracked_checks=frozenset())   # deploy_state warns
    assert _only(evaluate(s), "issue_coverage").severity == "ok"


def test_issue_coverage_does_not_report_itself():
    """Without this it alerts about its own alert, forever."""
    s = _snap(positions=[Position("NVDA", qty=1, covering_qty=0)], tracked_checks=frozenset())
    f = _only(evaluate(s), "issue_coverage")
    assert "issue_coverage" not in f.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_devcheck_checks.py -k issue_coverage -v`
Expected: FAIL — `TypeError: Snapshot.__init__() got an unexpected keyword argument 'tracked_checks'`

- [ ] **Step 3: Write minimal implementation**

Add the field to `Snapshot` (beside `suppressed`, both defaulted):

```python
    tracked_checks: frozenset[str] = field(default_factory=frozenset)
```

Add `tracked_checks=frozenset()` to the `_snap()` helper's `base` dict in the test file.

```python
# append to devcheck/checks.py

def check_issue_coverage(s: Snapshot, findings: list[Finding]) -> Finding:
    """docs/agents/issue-tracker.md — work in this repo lives as GitHub issues.

    An alert nobody files disappears when the window closes. Only `alert`
    participates: a `warn` that nagged every day would train the reader to
    skip the report, which is the failure suppression exists to prevent.
    """
    untracked = sorted(
        f.check
        for f in findings
        if f.severity == "alert"
        and f.check != "issue_coverage"
        and f.check not in s.tracked_checks
    )
    if not untracked:
        return Finding("issue_coverage", "ok", "every alert is tracked by an issue")
    hint = " ".join(f'gh issue create --label "check:{c}"' for c in untracked[:2])
    return Finding(
        "issue_coverage",
        "alert",
        f"alert(s) with no open issue: {', '.join(untracked)} — these die with this "
        f"window. File them: {hint}",
    )
```

```python
# devcheck/evaluate.py — evaluate() gains a second pass
def evaluate(snapshot: Snapshot) -> list[Finding]:
    out: list[Finding] = []
    for check in CHECKS:
        result = check(snapshot)
        if result is None:
            continue
        if isinstance(result, Finding):
            out.append(result)
        else:
            out.extend(result)
    # Runs last: it reads the other checks' output, not just the snapshot.
    out.append(checks.check_issue_coverage(snapshot, out))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_devcheck_checks.py -v`
Expected: PASS — 34 passed

- [ ] **Step 5: Populate `tracked_checks` in the composition root**

In `scripts/dev_status.py`'s `build_snapshot`:

```python
def _tracked_checks() -> frozenset[str]:
    """Open issues labelled check:<id>. Any gh failure means "nothing is
    tracked" — the check then over-reports, which is the safe direction."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--limit", "100",
             "--json", "labels", "-q", '.[].labels[].name'],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if out.returncode != 0:
        return frozenset()
    return frozenset(
        line.strip()[len("check:"):]
        for line in out.stdout.splitlines()
        if line.strip().startswith("check:")
    )
```

- [ ] **Step 6: Commit**

```bash
git add devcheck/ tests/test_devcheck_checks.py scripts/dev_status.py
git commit -m "feat: four sessions each re-reported the same unowned issues, so alerts now name themselves"
```

---

### Task 10: The fund's health descriptor

**Files:**
- Create: `.claude/health.md`
- Test: manual — `make dev-status` honours it.

- [ ] **Step 1: Write the descriptor**

```markdown
---
health_command: make dev-status
suppress:
  - degradations
---

# What healthy means in this repo

`make dev-status` runs `scripts/dev_status.py` — read-only over the droplet,
the broker and the database. It never writes, places, cancels, amends or
deploys.

## Interpreting the output

- **`degradations` is suppressed.** `model_fallback_used` fires at severity 3
  every day: it is an SDK auxiliary Haiku call on Sonnet-configured seats, not
  a real fallback. Suppressed findings still appear, marked `[suppressed]` —
  read them if something else looks wrong.
- **`reflection` ok with an empty `resolutions` table is correct** until a
  decision passes its 5-day horizon. The check knows the difference; a human
  reading the table directly does not.
- **`deploy_state` behind is normal** between a merge and a deploy. It matters
  when the gap contains a gate or seat-surface change.
- **`db_broker_agreement` diverges after any manual out-of-gate order.** That
  is the expected consequence, not a bug in the check.
- **`issue_coverage` is the loop from finding to tracked work.** An alert with
  no open issue labelled `check:<id>` will be re-derived by the next session
  and lost again. All 8 open issues currently carry zero labels, so this check
  fires on everything until they are labelled — that is the intended first run,
  not a bug.

## Filing what this finds

`docs/agents/issue-tracker.md` is the convention. Label a new issue
`check:<check_id>` so the finding stops being re-derived:

    gh issue create --label "check:position_coverage" --title "..." --body "..."

An issue that describes production behaviour should say which check would go
green when it is fixed.

## Escalate, never act

Broker mutations, droplet deploys, and gate thresholds are Benjamin's, in his
own window. `~/.claude/align/fund/decisions.md` is the record; read it there
rather than taking a peer's account of it.
```

- [ ] **Step 2: Verify suppression is live**

Run: `make dev-status`
Expected: any `degradations` row renders 🟢 with `[suppressed by .claude/health.md]` appended.

- [ ] **Step 3: Run the whole suite**

Run: `make test`
Expected: PASS — the 1149 baseline plus this plan's new tests.

- [ ] **Step 4: Commit**

```bash
git add .claude/health.md
git commit -m "docs: the repo says what healthy means, so the check does not have to guess"
```

---

## Self-Review

**Spec coverage.** §2.4's ten checks map to Tasks 1–6; the moved
loss-on-close check is deliberately absent (spec §2.4 assigns it to
`/eod-digest` step 2, a different plan). §2.3's descriptor is Task 9.
§4's error handling is Task 8's `_ssh` wrapper plus the `unreachable`
`ServiceResult` and Task 6's test. §5's required cases are all present:
negative controls throughout, paper-trading absent/false, the reflection pair,
suppression both ways, droplet-unreachable.

**Gap found and accepted.** §2.4's "exec seat is the only one with `trading`"
is checked in Task 2 from `seat_trading_toolsets`, which Task 8 must populate
by reading `agents/config/*.yaml` **on the droplet**, not locally — the local
checkout is not what runs. Called out here because it is the one place a
reader could wire the easy thing and check the wrong host.

**Placeholder scan.** One deliberate two-part step (Task 8, 3 and 3b): the
module skeleton is complete and testable on its own, and the reader wiring is
separated because it is the only untestable-offline code in the plan. Every
other step carries complete code.

**Type consistency.** `Finding(check, severity, detail)`, `Position(symbol,
qty, covering_qty)`, `ServiceResult(unit, result, last_run)`, and
`OrderRow(client_order_id, symbol)` are used with those exact names in every
task. `evaluate()` returns `list[Finding]`; `apply_suppression()` takes and
returns `list[Finding]`; `render()` takes `Sequence[Finding]`.

**Task numbering:** the descriptor is Task 10; Task 9 is the issue-coverage check added after review.

**Not in this plan:** `/eod-digest` (prose, `~/.claude/skills/`, separate plan
via `writing-skills`) and the `morning-standup` intents write (spec §6 puts it
last, after EOD exists to consume the file).
