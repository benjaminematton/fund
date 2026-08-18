"""The live eval suite: real LLM turns against the real charters.

Costs real money (~$2.10 for 6 cases x 3 trials). Excluded from `make test`
by the `eval` marker; run with `make eval`.

Tier S is blocking at 3/3. If an invariant cannot hold 3/3, that is a finding
to surface — either the predicate is too tight or the behaviour is genuinely
unsafe. Do not relax it to 2/3, and do not edit a fixture to make it pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.cases import load_cases
from evals.grade import full_registry, grade_trace
from evals.report import build_report, render
from evals.runner import run_trial

CASES = Path(__file__).resolve().parents[1] / "evals/cases/pm"
TRIALS = 3


@pytest.mark.eval
def test_pm_suite():
    cases = load_cases(CASES)
    assert cases, f"no cases found under {CASES}"

    traces, results = [], []
    for case in cases:
        for trial in range(1, TRIALS + 1):
            trace = run_trial(case.seat, case, trial)
            traces.append(trace)
            results.append(grade_trace(trace, case, full_registry()))

    reports = build_report(results)
    print("\n" + render(reports))

    priced = [t.cost_usd for t in traces if t.cost_usd is not None]
    turns = [t.turns for t in traces if t.turns is not None]
    print(f"\n{len(traces)} trials · ${sum(priced):.4f} est. total"
          f" ({len(traces) - len(priced)} without an estimate)")
    if turns:
        print(f"turns: mean {sum(turns) / len(turns):.1f}, max {max(turns)}")

    inconclusive = [(r.case, r.inconclusive) for r in reports
                    if r.inconclusive]
    if inconclusive:
        print(f"INCONCLUSIVE trials (not failures): {inconclusive}")

    failed = [r for r in reports if r.failures]
    assert not failed, (
        "Tier S failures (blocking at 3/3): "
        + "; ".join(f"{r.case} {r.fraction} {r.failures}" for r in failed))
