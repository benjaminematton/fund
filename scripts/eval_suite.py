"""Run the full live eval suite and print the pass^3 report.

Loads .env itself so `make eval` cannot silently half-run on a missing key —
a suite that fails on credentials burns real trials into INCONCLUSIVE traces
before anyone notices.

Usage:  .venv/bin/python3 scripts/eval_suite.py [--label NAME] [case-id ...]
        (no cases = every case; named cases = a targeted probe run)

--label scopes the run's traces to evals/traces/<NAME>/. Use it for anything
that changes a charter WITHOUT committing: traces are keyed by git sha, and an
uncommitted charter edit leaves the sha identical, so a probe run would
silently overwrite the control baseline it is being compared against.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_one import ENV, load_env  # noqa: E402

TRIALS = 3


def main(argv: list[str]) -> int:
    label = None
    if argv and argv[0] == "--label":
        label = argv[1]
        argv = argv[2:]
    only = argv
    traces_root = (ROOT / "evals/traces" / label) if label else None
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
            trace = run_trial(case.seat, case, trial,
                              traces_root=traces_root)
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

    # Tier M: measured, never blocking. Compare against the baseline run —
    # a DROP is the signal, not any single vague invalidation.
    from evals.metrics import stop_discipline
    print(f"stop discipline: {stop_discipline(traces)}")

    for r in results:
        for v in r.verdicts:
            if v.outcome != "PASS":
                print(f"  {r.case}/{r.trial} {v.invariant} {v.outcome}"
                      f" [{v.tag}] {v.detail[:120]}")
    if label:
        print(f"traces: evals/traces/{label}/")

    # A trial that never completed a turn is a RIG failure, not seat data: uvx
    # missing from systemd's PATH, the MCP server refusing to connect, no API
    # key. `make preflight` gates a deploy on this exit code, so returning 0
    # here hands back a green checkmark for the exact 2026-08-18 class it
    # exists to catch — the same shape as a market-closed rehearsal that exits
    # early and proves nothing. Consistent with the `return 2` guards above:
    # non-zero means the rig could not run, never that a seat judged badly.
    # Verdict FAILs stay exit 0 on purpose — `make eval` measures judgment, and
    # a case the seat fails is a result, not an error.
    errored = [t for t in traces if t.is_error]
    if errored:
        print(f"{len(errored)}/{len(traces)} trials never completed a turn —"
              " rig or environment failure, not a seat result", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
