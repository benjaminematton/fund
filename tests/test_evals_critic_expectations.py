"""EXPECT, graded against a Critic trace.

Real cases off disk, so the spec id under grade is the real content-addressed
one: m01 expects `objections` naming turnover/momentum, a01 expects `clear`.
Faking the id would let these pass while the fixture seeds a different one.

`objections` is a LIST in these fixtures, not a JSON string: evals/runner.py
decodes JSON columns once, so that is what a grader actually receives.
"""

from __future__ import annotations

from pathlib import Path

from evals.cases import load_case
from evals.config import load_eval_seat
from evals.expectations import case_expectations
from evals.trace import Trace

CASES = Path(__file__).resolve().parents[1] / "evals/cases/critic"


def _case(case_id):
    return load_case(CASES / f"{case_id}.yaml")


def _trace(case, verdict, objections=()):
    spec = case.subjects[0]
    return Trace(
        case=case.id, trial=1, seat="critic", git_sha="d", charter_sha="c",
        charter_text="# Critic", model="m", snapshot={}, brief_tickers=[],
        brief_subjects=[spec],
        tool_names=["mcp__fund__submit_spec_critique"],
        rows_written={"strategy_critiques": [
            {"spec_id": spec, "verdict": verdict,
             "objections": list(objections), "seat": "critic"}]},
        turns=3, cost_usd=0.05)


def test_a_matching_verdict_passes():
    case = _case("a01")
    v = case_expectations(_trace(case, "clear"), load_eval_seat("critic"),
                          case)
    assert v.outcome == "PASS", v.detail


def test_a_wrong_verdict_fails():
    case = _case("m01")
    v = case_expectations(_trace(case, "clear"), load_eval_seat("critic"),
                          case)
    assert v.outcome == "FAIL"
    assert v.tag == "wrong-verdict"


def test_the_right_verdict_for_the_wrong_reason_fails():
    """The failure mode the whole set exists to detect: m01's misalignment is
    the turnover conditioning, and an objection about the predicted Sharpe is
    the Critic guessing its way to the right label."""
    case = _case("m01")
    v = case_expectations(
        _trace(case, "objections", ["the predicted Sharpe looks optimistic"]),
        load_eval_seat("critic"), case)
    assert v.outcome == "FAIL"
    assert v.tag == "wrong-reason"


def test_one_matching_mention_is_enough_and_matching_is_case_insensitive():
    case = _case("m01")
    v = case_expectations(
        _trace(case, "objections",
               ["the rule filters the top TURNOVER decile, inverting it"]),
        load_eval_seat("critic"), case)
    assert v.outcome == "PASS", v.detail


def test_a_missing_row_fails():
    case = _case("a01")
    trace = _trace(case, "clear")
    trace.rows_written = {}
    v = case_expectations(trace, load_eval_seat("critic"), case)
    assert v.outcome == "FAIL"
    assert v.tag == "missing-row"
