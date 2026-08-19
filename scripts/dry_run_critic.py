"""Grade the Critic case set against a scripted ORACLE, offline.

The oracle submits each case's own expected verdict, so every case must grade
PASS. A red case here is a RIG defect — a fixture that cannot seed, a grader
that cannot read the row, a case whose expectation nothing can satisfy — and
finding one costs nothing. Finding it inside a live suite costs 36 trials of
real money and reads as a seat failure.

Runs BOTH splits, holdout included, and that does not burn the holdout: the
oracle is a scripted function, no model is ever shown a case, and the run
produces no information about how the seat behaves. What it proves is that the
rig can seed, submit and grade — a property of the plumbing, not of the Critic.

No network, no API key, no SDK. Usage: .venv/bin/python3 scripts/dry_run_critic.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.seats import charter_version_for                      # noqa: E402
from agents.tools.fund_server import handle_submit_spec_critique  # noqa: E402
from evals.cases import load_cases                                # noqa: E402
from evals.grade import grade_trace, seat_registry                # noqa: E402
from evals.runner import run_trial                                # noqa: E402
from orchestrator.clock import iso                                # noqa: E402

CASES = ROOT / "evals/cases/critic"


def oracle(case, charter_version: str, model_id: str):
    """Submit exactly what the case expects, with an objection that names the
    first mention it demands — the minimum a passing seat would produce."""
    def session(options, prompt, state):
        args = {"spec_id": case.subjects[0],
                "verdict": case.expect["verdict"]}
        mentions = case.expect.get("objection_mentions") or []
        if case.expect["verdict"] == "objections":
            args["objections"] = [
                f"the coded rule contradicts the hypothesis on {mentions[0]}"]
        # Attribution is bound here the way build_fund_server binds it for a
        # real turn: required, never defaulted, because strategy_critiques
        # forbids 'unknown'.
        result = handle_submit_spec_critique(
            state.conn, seat="critic", args=args, now_iso=iso(case.clock),
            charter_version=charter_version, model_id=model_id)
        if not result["ok"]:
            raise RuntimeError(
                f"{case.id}: the oracle's own submission was refused —"
                f" {result['error']}")
        return (["mcp__fund__get_spec_brief",
                 "mcp__fund__submit_spec_critique"], _Result())
    return session


class _Result:
    """Minimal stand-in for the SDK's ResultMessage, so I5 has turns and cost
    to grade instead of scoring the run INCONCLUSIVE on missing evidence."""
    num_turns = 3
    total_cost_usd = 0.05
    duration_ms = 1000
    is_error = False
    permission_denials: list = []


def main() -> int:
    cases = load_cases(CASES)
    registry = seat_registry("critic")
    charter_version = charter_version_for({"seat": "critic"})
    model_id = "claude-sonnet-5"
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for case in cases:
            trace = run_trial(
                "critic", case, 1,
                session=oracle(case, charter_version, model_id),
                workdir=work, traces_root=work / "traces")
            result = grade_trace(trace, case, registry)
            if not result.passed:
                failures.append((case.id, [
                    f"{v.invariant}:{v.outcome}[{v.tag}] {v.detail[:120]}"
                    for v in result.verdicts if v.outcome != "PASS"]))
    for case_id, details in failures:
        print(f"  {case_id}")
        for d in details:
            print(f"    {d}")
    passed = len(cases) - len(failures)
    print(f"DRY RUN {'CLEAN' if not failures else 'RED'}"
          f" {passed}/{len(cases)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
