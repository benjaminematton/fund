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
