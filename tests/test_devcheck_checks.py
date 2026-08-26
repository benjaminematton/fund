"""Unit tests for devcheck's pure checks.

Every check is a pure function of a Snapshot, so each test states a whole
world and asserts one verdict. Each check gets a negative control: a world
where it MUST fire. A check whose negative control also passes is not a
check — three tests in this repo passed on 2026-08-21 because they always
passed, and two were caught by luck.
"""

from __future__ import annotations

from devcheck.evaluate import evaluate
from devcheck.model import OrderRow, Snapshot


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
