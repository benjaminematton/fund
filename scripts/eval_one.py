"""Run ONE live eval trial and print what came back.

The cheap plumbing check before spending a full suite: proves the Alpaca MCP
server connects, the model id resolves, the fund tools are reachable, and a
decision row actually lands. ~$0.12.

Usage:  .venv/bin/python3 scripts/eval_one.py <case-id> [trial]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# .env lives in the primary checkout; a worktree has none of its own.
ENV = ROOT / ".env"
if not ENV.exists():
    ENV = Path("/Users/benjaminmatton/Developer/fund/.env")


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    if not ENV.exists():
        print(f"no .env at {ENV}", file=sys.stderr)
        return 2
    load_env(ENV)
    if os.environ.get("ALPACA_PAPER_TRADE") != "true":
        print("ALPACA_PAPER_TRADE is not 'true' — refusing", file=sys.stderr)
        return 2

    from evals.cases import load_cases
    from evals.grade import full_registry, grade_trace
    from evals.runner import run_trial

    case_id = sys.argv[1] if len(sys.argv) > 1 else "a01"
    trial = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    cases = {c.id: c for c in load_cases(ROOT / "evals/cases/pm")}
    case = cases[case_id]

    print(f"running {case_id} trial {trial} live ...", flush=True)
    trace = run_trial(case.seat, case, trial)

    print(f"\nerror        {trace.error}")
    print(f"tools        {trace.tool_names}")
    print(f"turns        {trace.turns}")
    print(f"cost est.    {trace.cost_usd}")
    print(f"duration_ms  {trace.duration_ms}")
    print(f"rows         {trace.rows_written}")
    print(f"alerts       {[a['payload'] for a in trace.alerts]}")

    result = grade_trace(trace, case, full_registry())
    print("\nverdicts:")
    for v in result.verdicts:
        print(f"  {v.invariant:<7} {v.outcome:<13} {v.tag or '':<28}"
              f" {v.detail[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
