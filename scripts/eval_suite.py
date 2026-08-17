"""Run the full live eval suite and print the pass^3 report.

Loads .env itself so `make eval` cannot silently half-run on a missing key —
a suite that fails on credentials burns real trials into INCONCLUSIVE traces
before anyone notices.

Usage:  .venv/bin/python3 scripts/eval_suite.py [case-id ...]
        (no args = every case; named cases = a targeted probe run)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_one import ENV, load_env  # noqa: E402

TRIALS = 3


def main(only: list[str]) -> int:
    if not ENV.exists():
        print(f"no .env at {ENV}", file=sys.stderr)
        return 2
    load_env(ENV)

    import os
    if os.environ.get("ALPACA_PAPER_TRADE") != "true":
        print("ALPACA_PAPER_TRADE is not 'true' — refusing", file=sys.stderr)
        return 2

    from evals.cases import load_cases
    from evals.grade import full_registry, grade_trace
    from evals.report import build_report, render
    from evals.runner import run_trial

    cases = load_cases(ROOT / "evals/cases/pm")
    if only:
        cases = [c for c in cases if c.id in only]
        if not cases:
            print(f"no cases matching {only}", file=sys.stderr)
            return 2

    traces, results = [], []
    for case in cases:
        for trial in range(1, TRIALS + 1):
            print(f"  {case.id} trial {trial} ...", flush=True)
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
        print(f"turns: mean {sum(turns) / len(turns):.2f}, max {max(turns)}")

    for r in results:
        for v in r.verdicts:
            if v.outcome != "PASS":
                print(f"  {r.case}/{r.trial} {v.invariant} {v.outcome}"
                      f" [{v.tag}] {v.detail[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
