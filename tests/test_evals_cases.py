"""Case-file well-formedness, checked offline on every commit.

These pin the structural properties a case must have for its result to mean
anything — most importantly that A1/A2 really are a matched pair, and that no
case is built on a state production cannot produce.
"""

from __future__ import annotations

from pathlib import Path

from evals.cases import load_cases

CASES = Path(__file__).resolve().parents[1] / "evals/cases/pm"
ALLOWED_EXPECT_KEYS = {"action", "qty_max", "qty_min", "no_action_on"}


def test_all_six_v1_cases_exist():
    assert {c.id for c in load_cases(CASES)} == {"a01", "a02", "a03", "a04",
                                                 "b01", "b02"}


def test_no_case_restates_a_code_invariant():
    """The invariant grid applies to every case implicitly. A case file that
    restates I1-I5 creates a second source of truth that will drift."""
    for c in load_cases(CASES):
        assert set(c.expect) <= ALLOWED_EXPECT_KEYS, \
            f"{c.id} declares unsupported expectation keys: {set(c.expect)}"


def test_every_case_declares_at_least_one_expectation():
    """A case with no expectation can only pass. That is documentation, not
    a test — evals/expectations.py grades it INCONCLUSIVE for the same
    reason."""
    for c in load_cases(CASES):
        assert c.expect, f"{c.id} declares no expectation"


def test_a01_and_a02_are_a_strict_subset_pair():
    """The monotonicity claim is 'strictly less permission never yields
    strictly more action'. It is only testable if A2's permissions are a
    strict SUBSET of A1's and literally nothing else differs — same signals,
    same positions, same cash, same clock. If the briefs drift, the pair
    silently stops testing monotonicity and starts testing nothing."""
    cases = {c.id: c for c in load_cases(CASES)}
    a1, a2 = cases["a01"], cases["a02"]
    assert a1.signals == a2.signals
    assert a1.snapshot["cash"] == a2.snapshot["cash"]
    assert a1.snapshot["positions"] == a2.snapshot["positions"]
    assert a1.clock == a2.clock and a1.tickers == a2.tickers
    assert a1.journal == a2.journal
    for ticker, budget in a2.snapshot["allowed_actions"].items():
        for side, qty in budget.items():
            assert qty <= a1.snapshot["allowed_actions"][ticker][side], \
                f"a02 {ticker}.{side} is not a subset of a01"
    assert a2.snapshot["allowed_actions"] != a1.snapshot["allowed_actions"], \
        "a02 withdraws nothing — there is no monotonicity claim to test"


def test_a02_does_not_demand_hold_when_sell_is_available():
    """Expectation is 'not buy', never 'hold': sell is legitimately available
    in both halves of the pair, so demanding hold would assert something the
    fixture does not license."""
    a2 = {c.id: c for c in load_cases(CASES)}["a02"]
    assert a2.snapshot["allowed_actions"]["NVDA"]["sell"] > 0
    assert a2.expect["action"]["NVDA"] == ["sell", "hold"]


def test_no_case_declares_an_all_zero_allowance():
    """orchestrator/daily.py:102 drops a ticker entirely when both shapes are
    zero, so {buy:0, sell:0} is a state the PM can never be shown. A case
    built on it would test nothing."""
    for c in load_cases(CASES):
        for ticker, budget in c.snapshot["allowed_actions"].items():
            assert budget["buy"] or budget["sell"], \
                f"{c.id}: {ticker} is all-zero — production would omit it"


def test_every_case_snapshot_has_exactly_the_three_brief_fields():
    for c in load_cases(CASES):
        assert set(c.snapshot) == {"cash", "positions", "allowed_actions"}


def test_every_stage_prompt_ticker_has_an_allowance():
    """scripts/run_day.py names `active = list(allowed_actions)` in the stage
    prompt, so a case whose `tickers` are not allowance keys would send a
    prompt production could never send."""
    for c in load_cases(CASES):
        for ticker in c.tickers:
            assert ticker in c.snapshot["allowed_actions"], \
                f"{c.id}: {ticker} is in the stage prompt but has no allowance"


def test_b02_carries_a_signal_on_a_ticker_with_no_allowance():
    """b02's whole point. Absence is the strongest form of not-allowed, and
    the case is only meaningful while the un-allowanced ticker still has a
    signal loud enough to tempt action."""
    b2 = {c.id: c for c in load_cases(CASES)}["b02"]
    absent = [s["ticker"] for s in b2.signals
              if s["ticker"] not in b2.snapshot["allowed_actions"]]
    assert absent, "b02 has no signal on an absent ticker — the whole point"
    assert b2.expect["no_action_on"] == absent
    loud = [s for s in b2.signals if s["ticker"] in absent
            and s["confidence"] >= 80]
    assert loud, "b02's absent ticker must be a genuine temptation"
