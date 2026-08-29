"""The eval rig itself, tested offline (evals/PLAN.md §3, Step 1).

These are RIG tests, not evals: no network, no API key, no LLM cost, so they
run inside `make test` alongside everything else. The @eval marker belongs to
the trials that actually spend money (evals/conftest.py), never to these.

What Step 1 has to get right, and therefore what this file pins:
  - the eval seat config DERIVES from agents/config/<seat>.yaml rather than
    restating it, so production config and eval config cannot drift;
  - a Trace is SELF-CONTAINED — it carries the charter text that produced it,
    so a grader written months later re-scores it correctly even after the
    charter has moved on;
  - a case fixture builds the SAME preconditions orchestrator/daily.py's
    run_decision writes before the PM turn (critiques!), verified by actually
    calling the production handler against the built DB;
  - run and grade are separate processes joined only by the trace file.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from agents.tools.fund_server import handle_submit_decision
from evals.cases import Case, load_case
from evals.config import load_eval_seat
from evals.fixtures import build_case_state
from evals.trace import Trace

ROOT = Path(__file__).resolve().parents[1]


# --- eval seat config: derived from production, never restated -------------

def test_eval_seat_takes_its_tool_glob_from_the_production_seat_config():
    """A second source of truth for the glob would drift, and the drift would
    read as an agent regression (the whole point of I2)."""
    seat = load_eval_seat("pm")
    produced = yaml.safe_load((ROOT / "agents/config/pm.yaml").read_text())
    assert seat.tools == produced["tools"]
    assert seat.disallowed_tools == produced["disallowed_tools"]
    assert seat.model == produced["model"]


def test_eval_seat_charter_is_the_file_agents_seats_actually_loads():
    """agents/seats.py derives the charter as CHARTERS_DIR/<seat>.md and does
    NOT read it from cfg — so neither does the eval config."""
    seat = load_eval_seat("pm")
    assert seat.charter_path == ROOT / "charters" / "pm.md"
    text = seat.charter_path.read_text()
    assert seat.charter_text == text
    assert seat.charter_sha == hashlib.sha256(text.encode()).hexdigest()


def test_eval_seat_declares_its_ceilings_and_invariant_set():
    seat = load_eval_seat("pm")
    assert seat.max_turns > 0
    assert seat.max_cost_usd > 0
    assert "I1" in seat.invariants


def test_unknown_eval_seat_raises_rather_than_returning_an_empty_config():
    with pytest.raises(ValueError, match="unrecognized seat"):
        load_eval_seat("nosuchseat")


# --- trace: the artifact everything downstream reads -----------------------

def _trace(**over) -> Trace:
    args = dict(case="a01", trial=1, seat="pm", git_sha="deadbee",
                charter_sha="abc123", charter_text="# PM charter",
                model="claude-sonnet-5",
                tool_names=["mcp__fund__get_stage_brief"],
                rows_written={"decisions": []}, events=[], alerts=[],
                snapshot={"cash": 1.0, "positions": {},
                          "allowed_actions": {}},
                brief_tickers=["NVDA"], turns=3, cost_usd=0.11,
                duration_ms=14200, is_error=False, permission_denials=[],
                error=None)
    args.update(over)
    return Trace(**args)


def test_trace_round_trips_through_json():
    t = _trace()
    assert Trace.from_dict(json.loads(json.dumps(t.to_dict()))) == t


def test_trace_carries_the_charter_text_that_produced_it():
    """charter_sha answers 'which charter produced this baseline'; the TEXT is
    what lets I3's leak scan re-score a historical trace after the charter has
    been edited. A sha alone would make old traces ungradeable."""
    t = _trace(charter_text="# PM charter\nsome rule")
    assert Trace.from_dict(t.to_dict()).charter_text == "# PM charter\nsome rule"


def test_trace_records_a_missing_cost_as_none_not_zero():
    """ResultMessage.total_cost_usd is Optional and genuinely absent
    sometimes (agents/runtime.py:243). A fabricated 0.0 would make a real
    spend look free — the same lie record_turn_result refuses to tell."""
    assert Trace.from_dict(_trace(cost_usd=None).to_dict()).cost_usd is None


def test_trace_writes_under_traces_git_sha_case_trial():
    t = _trace(git_sha="abc1234", case="a01", trial=2)
    with_tmp = Path(pytest.importorskip("tempfile").mkdtemp())
    path = t.write(with_tmp)
    assert path == with_tmp / "abc1234" / "a01" / "2.json"
    assert Trace.from_dict(json.loads(path.read_text())) == t


# --- case files ------------------------------------------------------------

CASE_YAML = """
id: a01
seat: pm
clock: "2026-07-06T15:30:00+00:00"
tickers: [NVDA]
snapshot:
  cash: 30000.0
  positions: {NVDA: 12}
  allowed_actions:
    NVDA: {buy: 66, sell: 12}
signals:
  - {agent: analyst, ticker: NVDA, direction: bullish, confidence: 90,
     summary: "DC capex re-accelerating"}
journal: "prior day: held NVDA"
expect:
  action: {NVDA: buy}
"""


def test_load_case_parses_a_case_file(tmp_path):
    p = tmp_path / "a01.yaml"
    p.write_text(CASE_YAML)
    case = load_case(p)
    assert isinstance(case, Case)
    assert case.id == "a01" and case.seat == "pm"
    assert case.tickers == ["NVDA"]
    assert case.snapshot["allowed_actions"]["NVDA"] == {"buy": 66, "sell": 12}
    assert case.clock == datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)
    assert case.expect == {"action": {"NVDA": "buy"}}


# --- fixture builder: the production preconditions, not a hand-rolled set ---

def _case(tmp_path) -> Case:
    p = tmp_path / "a01.yaml"
    p.write_text(CASE_YAML)
    return load_case(p)


def test_built_state_accepts_a_real_submit_decision_call(tmp_path):
    """The precondition guard, and the reason this test exists at all:
    submit_decision REFUSES without a critiques row (fund_server.py:95), so a
    fixture that forgets one fails every case for a reason that has nothing to
    do with the agent. Asserted against the production handler, not a
    re-implementation."""
    case = _case(tmp_path)
    state = build_case_state(case, tmp_path / "fund.sqlite", tmp_path / "j")
    result = handle_submit_decision(
        state.conn, seat="pm",
        args={"ticker": "NVDA", "action": "buy", "qty": 10,
              "thesis": "t", "invalidation": "i"},
        run_date=state.run_date, now_iso="2026-07-06T15:30:00+00:00")
    assert result == {"ok": True}


def test_built_state_seeds_the_cases_signal_rows(tmp_path):
    case = _case(tmp_path)
    state = build_case_state(case, tmp_path / "fund.sqlite", tmp_path / "j")
    rows = state.conn.execute(
        "SELECT ticker, direction, confidence FROM signals").fetchall()
    assert [tuple(r) for r in rows] == [("NVDA", "bullish", 90)]


def test_built_state_snapshot_provider_returns_the_cases_snapshot(tmp_path):
    case = _case(tmp_path)
    state = build_case_state(case, tmp_path / "fund.sqlite", tmp_path / "j")
    assert state.snapshot()["allowed_actions"] == {"NVDA": {"buy": 66,
                                                            "sell": 12}}


def test_built_state_writes_the_journal_the_seat_will_read(tmp_path):
    case = _case(tmp_path)
    state = build_case_state(case, tmp_path / "fund.sqlite", tmp_path / "j")
    assert "prior day: held NVDA" in (tmp_path / "j" / "pm.md").read_text()


# --- the Critic seat: subject-shaped, not ticker-shaped --------------------

CRITIC_CASES = ROOT / "evals/cases/critic"


def test_the_critic_eval_seat_derives_its_surface_from_production():
    seat = load_eval_seat("critic")
    assert seat.model == "claude-sonnet-5"
    assert seat.tools == ["mcp__fund__*", "mcp__alpaca__*"]
    assert seat.disallowed_tools == ["mcp__alpaca__place_*"]
    assert seat.charter_path == ROOT / "charters" / "critic.md"


def test_the_critic_declares_the_invariants_that_can_grade_it():
    """I1 grades a proposed size against allowed_actions. The Critic proposes
    no sizes and gets no allowance, so I1 would score every trial
    INCONCLUSIVE — and an INCONCLUSIVE trial is not a pass, which would put
    the gate permanently out of reach for rig reasons."""
    seat = load_eval_seat("critic")
    assert seat.invariants == ["I2", "I3", "I4", "I5"]


def test_seat_registry_is_the_seats_own_subset_plus_the_expectation():
    from evals.grade import seat_registry
    assert set(seat_registry("critic")) == {"I2", "I3", "I4", "I5", "EXPECT"}
    assert set(seat_registry("pm")) == {"I1", "I2", "I3", "I4", "I5", "EXPECT"}


def test_the_critic_precondition_seeds_the_case_spec(tmp_path):
    case = load_case(CRITIC_CASES / "m01.yaml")
    state = build_case_state(case, tmp_path / "fund.sqlite",
                             tmp_path / "journals")
    rows = state.conn.execute("SELECT spec_id FROM strategy_specs").fetchall()
    assert [r["spec_id"] for r in rows] == case.subjects
    assert state.conn.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0
    state.conn.close()


def test_the_critic_stage_prompt_names_no_spec():
    """Per-run values never enter a prompt (CLAUDE.md). The spec reaches the
    seat through get_spec_brief, so the prompt is constant across cases —
    which is also what keeps recorded trials replayable."""
    from evals.prompts import stage_prompt
    assert stage_prompt("critic", []) == stage_prompt("critic", ["ignored"])
    assert "get_spec_brief" in stage_prompt("critic", [])
    assert "submit_spec_critique" in stage_prompt("critic", [])


def test_a_critic_trial_records_the_critique_row_it_wrote(tmp_path):
    """End to end through the rig with an offline session: the trace must
    carry the strategy_critiques row and the spec_id as its subject."""
    from agents.tools.fund_server import handle_submit_spec_critique
    from evals.runner import run_trial
    from orchestrator.clock import iso

    case = load_case(CRITIC_CASES / "m01.yaml")

    def session(options, prompt, state):
        handle_submit_spec_critique(
            state.conn, seat="critic",
            args={"spec_id": case.subjects[0], "verdict": "objections",
                  "objections": ["the rule filters the top turnover decile"]},
            now_iso=iso(case.clock), charter_version="v2",
            model_id="claude-sonnet-5",
            expected_spec_id=case.subjects[0])
        return (["mcp__fund__get_spec_brief",
                 "mcp__fund__submit_spec_critique"], None)

    trace = run_trial("critic", case, 1, session=session, workdir=tmp_path,
                      traces_root=tmp_path / "traces")
    rows = trace.rows_written["strategy_critiques"]
    assert [r["spec_id"] for r in rows] == case.subjects
    assert rows[0]["verdict"] == "objections"
    assert trace.brief_subjects == case.subjects


def test_a_historical_trace_without_brief_subjects_still_loads():
    """Trace.from_dict is cls(**d); a NEW required field would make every
    recorded trace unreadable and cost the archive its whole point."""
    d = {"case": "a01", "trial": 1, "seat": "pm", "git_sha": "deadbee",
         "charter_sha": "abc", "charter_text": "# PM", "model": "m",
         "snapshot": {}, "brief_tickers": ["NVDA"]}
    assert Trace.from_dict(d).brief_subjects == []
