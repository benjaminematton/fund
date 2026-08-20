"""`make eval-report [BASELINE=<sha>]` — grade the newest trace set and,
optionally, diff it against a baseline sha. Free and offline: it re-scores
recorded traces, it never runs a turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

from evals.cases import load_cases
from evals.grade import full_registry, grade_traces
from evals.metrics import stop_discipline_for
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


def _report(name: str, cases: dict):
    return build_report(grade_traces(TRACES / name, cases))


def main(run: str | None, baseline: str | None) -> int:
    """No args: every run. One: that run. Two: that run diffed vs a baseline.

    There is deliberately no "latest". Runs are labelled by hand (control,
    primary, secondary) as well as by git sha, and a merge rewrites every
    directory's mtime — so neither name order nor mtime identifies the run you
    meant. Guessing wrong here reports a one-trial smoke run as the result.
    """
    cases = {c.id: c for c in load_cases(CASES)}
    runs = _runs()
    if not runs:
        print("no suite runs recorded yet — run `make eval` first")
        return 1
    for name in (run, baseline):
        if name and name not in runs:
            print(f"no traces for {name!r}; have {runs}")
            return 1

    if run is None:
        for name in runs:
            print(f"=== {name} ===")
            print(render(_report(name, cases)))
            print(f"stop discipline: {stop_discipline_for(TRACES / name)}")
            print()
        return 0

    current = _report(run, cases)
    print(f"=== {run} ===")
    print(render(current))
    print(f"stop discipline: {stop_discipline_for(TRACES / run)}")
    if baseline:
        print(f"\n=== {run} vs {baseline} ===")
        print(diff(current, _report(baseline, cases)))
        # Tier M last, and always — never gated on the pass table moving.
        # Measured 2026-08-17: a charter edit dropped this rate 6/6 -> 2/6
        # while every case still passed 3/3, so a diff that prints only when
        # pass^3 moves is a diff that cannot see the regression it exists for.
        print("\nstop discipline (Tier M — a DROP is the signal):")
        for name in (baseline, run):
            print(f"  {name:<10} {stop_discipline_for(TRACES / name)}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a]
    sys.exit(main(args[0] if args else None,
                  args[1] if len(args) > 1 else None))
