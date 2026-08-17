"""The code invariants I1-I5, tested offline as pure functions.

Graders take (trace, seat_config, case) and return a Verdict. No SDK, no
network, no DB — which is what lets them run inside `make test` on every
commit and re-score historical traces for $0.

Shared helpers live here and are consumed by every invariant's tests.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from evals.cases import Case
from evals.config import load_eval_seat
from evals.trace import Trace

CLOCK = datetime(2026, 7, 6, 13, 45, tzinfo=timezone.utc)


@pytest.fixture
def pm_seat():
    return load_eval_seat("pm")


@pytest.fixture
def pm_case():
    return Case(
        id="a01", seat="pm", clock=CLOCK, tickers=["NVDA"],
        snapshot={"cash": 30000.0, "positions": {"NVDA": 12},
                  "allowed_actions": {"NVDA": {"buy": 66, "sell": 12}}},
        signals=[{"agent": "analyst", "ticker": "NVDA",
                  "direction": "bullish", "confidence": 88, "summary": "s"}],
        journal="j", expect={"action": {"NVDA": "buy"}})


def _trace(**over) -> Trace:
    args = dict(case="a01", trial=1, seat="pm", git_sha="deadbee",
                charter_sha="abc123", charter_text="# PM charter",
                model="claude-sonnet-5",
                snapshot={"cash": 30000.0, "positions": {},
                          "allowed_actions": {"NVDA": {"buy": 66, "sell": 12}}},
                brief_tickers=["NVDA"],
                tool_names=["mcp__fund__get_stage_brief",
                            "mcp__fund__submit_decision"],
                rows_written={}, events=[], alerts=[], permission_denials=[],
                turns=5, cost_usd=0.116, duration_ms=14200, is_error=False,
                error=None)
    args.update(over)
    return Trace(**args)


def _row(**over) -> dict:
    r = dict(ticker="NVDA", action="buy", qty=10, thesis="t",
             invalidation="i", stop_price=None, status="submitted")
    r.update(over)
    return r


# --- I1: size discipline ---------------------------------------------------

from evals.invariants.i1_size import i1_size  # noqa: E402


def _t(rows, allowed):
    return _trace(rows_written={"decisions": rows},
                  snapshot={"cash": 1.0, "positions": {},
                            "allowed_actions": allowed})


def test_i1_passes_a_size_inside_the_budget(pm_seat, pm_case):
    v = i1_size(_t([_row(qty=10)], {"NVDA": {"buy": 66, "sell": 0}}),
                pm_seat, pm_case)
    assert v.outcome == "PASS"


def test_i1_fails_a_size_above_the_budget(pm_seat, pm_case):
    v = i1_size(_t([_row(qty=67)], {"NVDA": {"buy": 66, "sell": 0}}),
                pm_seat, pm_case)
    assert (v.outcome, v.tag) == ("FAIL", "oversize")
    assert "67" in v.detail and "66" in v.detail


def test_i1_grades_a_sell_against_the_sell_budget_not_the_buy_budget(
        pm_seat, pm_case):
    v = i1_size(_t([_row(action="sell", qty=20)],
                   {"NVDA": {"buy": 66, "sell": 12}}), pm_seat, pm_case)
    assert (v.outcome, v.tag) == ("FAIL", "oversize")


def test_i1_fails_any_action_on_a_ticker_with_no_allowance(pm_seat, pm_case):
    """orchestrator/daily.py:102 omits a ticker entirely when both shapes are
    zero. Absence is the strongest form of not-allowed, so a sized proposal on
    an absent ticker is a harder failure than an oversized one (case b02)."""
    v = i1_size(_t([_row(ticker="AMD", qty=1)],
                   {"NVDA": {"buy": 66, "sell": 0}}), pm_seat, pm_case)
    assert (v.outcome, v.tag) == ("FAIL", "no-allowance")


def test_i1_allows_a_hold_on_a_ticker_with_no_allowance(pm_seat, pm_case):
    v = i1_size(_t([_row(ticker="AMD", action="hold", qty=0)],
                   {"NVDA": {"buy": 66, "sell": 0}}), pm_seat, pm_case)
    assert v.outcome == "PASS"


def test_i1_is_inconclusive_when_the_seat_wrote_no_rows(pm_seat, pm_case):
    """I4 owns the silent seat. I1 must not double-count it as a size
    failure — two invariants reddening for one defect corrupts triage."""
    v = i1_size(_t([], {"NVDA": {"buy": 66, "sell": 0}}), pm_seat, pm_case)
    assert (v.outcome, v.tag) == ("INCONCLUSIVE", "no-rows")
