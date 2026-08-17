"""Generate the offline grader-regression fixtures in evals/traces/recorded/.

Deliberately synthetic and offline. These fixtures exist to catch GRADER bugs
on every commit, which needs structurally real traces spanning all three
verdict values — not real LLM output. Generating them through the `session=`
seam means they cost nothing to regenerate, so they actually get regenerated;
a fixture set that costs $2.10 to rebuild is one that silently rots.

Run:  .venv/bin/python3 scripts/record_eval_fixtures.py
Then: git add evals/traces/recorded evals/traces/recorded-expected.json

NEVER run this to make tests/test_evals_recorded.py pass. A moved verdict is
the signal that test exists to give.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.tools.fund_server import handle_submit_decision  # noqa: E402
from evals.cases import load_cases  # noqa: E402
from evals.grade import full_registry, grade_traces  # noqa: E402
from evals.runner import run_trial  # noqa: E402

CASES = ROOT / "evals/cases/pm"
RECORDED = ROOT / "evals/traces/recorded"
EXPECTED = ROOT / "evals/traces/recorded-expected.json"


class FakeResult:
    """Read by attribute, never isinstance — same discipline as
    agents/runtime.py:record_turn_result."""

    def __init__(self, *, num_turns=5, total_cost_usd=0.1161,
                 duration_ms=14200):
        self.num_turns = num_turns
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.is_error = False
        self.session_id = "recorded"
        self.permission_denials = []


def _session(decisions, result):
    """A scripted PM turn: reads the brief, submits the given decisions."""
    def session(options, prompt, state):
        for args in decisions:
            handle_submit_decision(state.conn, seat="pm", args=args,
                                   run_date=state.run_date,
                                   now_iso="2026-07-06T13:45:00+00:00")
        return (["mcp__fund__get_stage_brief", "mcp__fund__submit_decision"],
                result)
    return session


def _dec(ticker, action, qty, thesis="Capex thesis intact; adding on strength.",
         invalidation="close below 170 on volume"):
    return {"ticker": ticker, "action": action, "qty": qty, "thesis": thesis,
            "invalidation": invalidation}


# (case id, trial, decisions, ResultMessage) — one clean PASS, one FAIL, one
# INCONCLUSIVE, so the fixture set exercises all three verdict values.
SCRIPTS = [
    ("a01", 1, [_dec("NVDA", "buy", 24)], FakeResult()),
    ("a01", 2, [_dec("NVDA", "buy", 400)], FakeResult()),          # I1 oversize
    ("a01", 3, [_dec("NVDA", "buy", 24)],
     FakeResult(total_cost_usd=None)),                             # I5 weather
]


def main() -> int:
    cases = {c.id: c for c in load_cases(CASES)}
    if RECORDED.exists():
        shutil.rmtree(RECORDED)
    work = Path(tempfile.mkdtemp())

    for case_id, trial, decisions, result in SCRIPTS:
        case = cases[case_id]
        trace = run_trial(case.seat, case, trial, workdir=work,
                          traces_root=RECORDED,
                          session=_session(decisions, result))
        print(f"recorded {case_id}/{trial}: qty="
              f"{[d['qty'] for d in decisions]} cost={trace.cost_usd}")

    results = grade_traces(RECORDED, cases=cases, invariants=full_registry())
    expected = {f"{r.case}/{r.trial}":
                {v.invariant: v.outcome for v in r.verdicts}
                for r in results}
    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")

    print(f"\nwrote {EXPECTED.relative_to(ROOT)}")
    for key, verdicts in sorted(expected.items()):
        print(f"  {key}: {verdicts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
