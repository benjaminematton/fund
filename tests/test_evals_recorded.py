"""The code invariants, re-scored against committed traces on every commit.

Zero cost, no network. This is what catches a grader bug without spending
inference, and it is the mechanism that makes "a new invariant re-scores every
trace ever recorded" true rather than aspirational.

The fixtures are generated offline via scripts/record_eval_fixtures.py, not
lifted from a live run: a fixture that costs $2.10 to regenerate is a fixture
nobody regenerates.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.cases import load_cases
from evals.grade import full_registry, grade_traces

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "evals/traces/recorded"
EXPECTED = ROOT / "evals/traces/recorded-expected.json"
CASES = ROOT / "evals/cases/pm"


def test_recorded_traces_exist_to_grade():
    assert list(RECORDED.rglob("*.json")), \
        "no recorded traces — the offline grader regression has nothing to run"


def test_every_recorded_trace_grades_without_a_grader_error():
    cases = {c.id: c for c in load_cases(CASES)}
    results = grade_traces(RECORDED, cases=cases, invariants=full_registry())
    bad = [(r.case, r.trial, v.invariant, v.detail)
           for r in results for v in r.verdicts if v.tag == "grader-error"]
    assert not bad, f"grader raised on recorded traces: {bad}"


def test_recorded_traces_reproduce_their_expected_verdicts():
    """Each recorded trace ships with the verdict set it produced when it was
    committed. Any grader change that moves a historical verdict shows up
    here, on every commit, for $0.

    NEVER regenerate recorded-expected.json to make this pass. A moved
    verdict is the signal this test exists to give; regenerate only by
    deliberate reviewed intent, and never in the same commit as a grader
    change."""
    cases = {c.id: c for c in load_cases(CASES)}
    expected = json.loads(EXPECTED.read_text())
    results = grade_traces(RECORDED, cases=cases, invariants=full_registry())
    got = {f"{r.case}/{r.trial}": {v.invariant: v.outcome for v in r.verdicts}
           for r in results}
    assert got == expected


def test_the_fixture_set_covers_all_three_verdict_values():
    """A fixture set of only-passes cannot catch a grader that never fails."""
    cases = {c.id: c for c in load_cases(CASES)}
    results = grade_traces(RECORDED, cases=cases, invariants=full_registry())
    seen = {v.outcome for r in results for v in r.verdicts}
    assert seen == {"PASS", "FAIL", "INCONCLUSIVE"}, \
        f"recorded fixtures only produce {seen}"
