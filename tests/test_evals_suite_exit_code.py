"""scripts/eval_suite.py's exit code, pinned in BOTH directions.

`make preflight` gates a droplet deploy on this exit code, so the two halves
are not symmetric in consequence:

  * a trial that never completed a turn (uvx off systemd's PATH, the MCP
    server refusing to connect, no API key) is a RIG failure -> exit 1. Commit
    8903d3a fixed main() returning 0 unconditionally, which handed `make
    preflight` a green checkmark while every trial blew up — the 2026-08-18
    class the gate exists to catch.
  * a verdict FAIL with no rig error is a SEAT RESULT -> exit 0. `make eval`
    measures judgment; a case the seat fails is data, not a broken rig.
    Inverting this is the plausible regression: it would make every charter
    experiment look like a broken environment and block deploys on judgment.

The LLM never runs here — evals.runner.run_trial is replaced, the same seam
tests/test_evals_runner.py uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import evals.runner
import scripts.eval_suite as suite
from evals.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
CHARTER = ROOT / "charters" / "pm.md"


def a01_trace(trial: int, *, action: str = "buy", is_error: bool = False):
    """One a01 trial that clears every Tier S invariant, so the only verdict
    that can move is EXPECT. a01 expects `buy` with qty >= 1; passing
    action="hold" fails EXPECT alone (tag wrong-action)."""
    qty = 0 if action == "hold" else 10
    return Trace(
        case="a01", trial=trial, seat="pm", git_sha="deadbee",
        charter_sha="x" * 64, charter_text=CHARTER.read_text(),
        model="claude-sonnet-5",
        snapshot={"cash": 30000.0, "positions": {"NVDA": 12},
                  "allowed_actions": {"NVDA": {"buy": 66, "sell": 12}}},
        brief_tickers=["NVDA"],
        tool_names=["mcp__fund__get_stage_brief",
                    "mcp__fund__submit_decision"],
        rows_written={"decisions": [
            {"ticker": "NVDA", "action": action, "qty": qty,
             "thesis": "capex re-accelerating", "invalidation": "closes below $150",
             "stop_price": 150.0 if action == "buy" else None,
             "status": "submitted"}]},
        # I5 ceilings from evals/seats/pm.yaml; comfortably inside both.
        turns=4, cost_usd=0.05, duration_ms=14200,
        is_error=is_error,
        error="RuntimeError: mcp server never connected" if is_error else None)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A suite run whose only live parts are the grader and main() itself."""
    env = tmp_path / ".env"
    env.write_text("ALPACA_PAPER_TRADE=true\n")
    monkeypatch.setattr(suite, "ENV", env)
    # load_env() uses os.environ.setdefault, so a value already in the ambient
    # environment would win over the file and silently skip the guard.
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
    return monkeypatch


def run_suite(monkeypatch, **trace_kw) -> int:
    counter = {"n": 0}

    def fake_run_trial(seat, case, trial, **kw):
        counter["n"] += 1
        return a01_trace(trial, **trace_kw)

    monkeypatch.setattr(evals.runner, "run_trial", fake_run_trial)
    code = suite.main(["a01"])
    assert counter["n"] == suite.TRIALS, "the suite did not run a01's 3 trials"
    return code


def test_a_trial_that_never_completed_a_turn_exits_1(rig):
    """The rig could not run. `make preflight` must go red."""
    assert run_suite(rig, is_error=True) == 1


def test_a_verdict_fail_with_no_rig_error_exits_0(rig, capsys):
    """The seat judged badly and the rig worked. That is a result, not an
    error — exit 0, and the FAIL still shows up in the report."""
    code = run_suite(rig, action="hold")
    out = capsys.readouterr().out
    assert code == 0
    assert "EXPECT" in out and "wrong-action" in out, out


def test_a_clean_run_exits_0(rig):
    assert run_suite(rig) == 0
