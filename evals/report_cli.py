"""`make eval-report [BASELINE=<sha>]` — grade the newest trace set and,
optionally, diff it against a baseline sha. Free and offline: it re-scores
recorded traces, it never runs a turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

from evals.cases import load_cases
from evals.grade import full_registry, grade_traces
from evals.report import build_report, diff, render

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "evals/traces"
CASES = ROOT / "evals/cases/pm"
NOT_A_RUN = {"recorded"}          # fixtures, not a suite run


def _runs() -> list[str]:
    if not TRACES.is_dir():
        return []
    return sorted(p.name for p in TRACES.iterdir()
                  if p.is_dir() and p.name not in NOT_A_RUN)


def main(baseline: str | None) -> int:
    cases = {c.id: c for c in load_cases(CASES)}
    runs = _runs()
    if not runs:
        print("no suite runs recorded yet — run `make eval` first")
        return 1
    latest = runs[-1]
    current = build_report(grade_traces(TRACES / latest, cases,
                                        full_registry()))
    print(f"=== {latest} ===")
    print(render(current))
    if baseline:
        if baseline not in runs:
            print(f"\nno traces for baseline {baseline!r}; have {runs}")
            return 1
        base = build_report(grade_traces(TRACES / baseline, cases,
                                         full_registry()))
        print(f"\n=== vs baseline {baseline} ===")
        print(diff(current, base))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
