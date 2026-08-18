"""pass^k reporting, tested offline."""

from __future__ import annotations

import pytest

from evals.grade import TrialResult
from evals.report import build_report, diff, pass_k, render
from evals.verdict import Verdict


def test_pass_k_is_one_when_every_trial_passed():
    assert pass_k(3, 3, 3) == 1.0


def test_pass_k_is_zero_when_fewer_passes_than_k():
    assert pass_k(2, 3, 3) == 0.0


def test_pass_k_matches_the_finite_sample_estimator():
    assert pass_k(2, 3, 1) == pytest.approx(2 / 3)   # C(2,1)/C(3,1)


def test_pass_k_is_undefined_for_more_k_than_trials():
    with pytest.raises(ValueError, match="undefined"):
        pass_k(1, 1, 3)


def test_report_counts_passes_per_case():
    results = [
        TrialResult("a01", 1, "pm", [Verdict("I1", "PASS")]),
        TrialResult("a01", 2, "pm", [Verdict("I1", "FAIL", "oversize",
                                             tag="oversize")]),
        TrialResult("a01", 3, "pm", [Verdict("I1", "PASS")]),
    ]
    (rep,) = build_report(results)
    assert (rep.case, rep.passes, rep.trials) == ("a01", 2, 3)
    assert rep.failures == ["I1:oversize"]


def test_report_renders_a_fraction_never_a_percentage():
    results = [TrialResult("a01", i, "pm", [Verdict("I1", "PASS")])
               for i in (1, 2, 3)]
    out = render(build_report(results))
    assert "3/3" in out and "%" not in out


def test_a_case_with_any_inconclusive_trial_is_not_a_clean_pass():
    results = [
        TrialResult("a01", 1, "pm", [Verdict("I1", "PASS")]),
        TrialResult("a01", 2, "pm", [Verdict("I1", "INCONCLUSIVE", "weather",
                                             tag="cost-missing")]),
        TrialResult("a01", 3, "pm", [Verdict("I1", "PASS")]),
    ]
    (rep,) = build_report(results)
    assert rep.inconclusive == 1
    assert not rep.clean
    assert "INCONCLUSIVE" in render([rep])


def test_diff_reports_only_cases_whose_fraction_moved():
    base = build_report([TrialResult("a01", i, "pm", [Verdict("I1", "PASS")])
                         for i in (1, 2, 3)])
    now = build_report([
        TrialResult("a01", 1, "pm", [Verdict("I1", "PASS")]),
        TrialResult("a01", 2, "pm", [Verdict("I1", "FAIL", "x", tag="t")]),
        TrialResult("a01", 3, "pm", [Verdict("I1", "PASS")]),
    ])
    assert diff(now, base) == "a01: 3/3 -> 2/3"


def test_diff_is_quiet_when_nothing_moved():
    reports = build_report([TrialResult("a01", i, "pm",
                                        [Verdict("I1", "PASS")])
                            for i in (1, 2, 3)])
    assert diff(reports, reports) == "no change vs baseline"
