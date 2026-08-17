"""run_trial and the run/grade split, tested offline.

The LLM session is injected (`session=`), the same seam tests/conftest.py's
make_executor uses for recorded days, so every branch of the runner — cost
missing, session crash, rows captured, alerts captured — is exercised for $0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.tools.fund_server import handle_submit_decision
from evals.cases import load_case
from evals.grade import grade_trace, grade_traces
from evals.prompts import PROMPT_TEMPLATES, stage_prompt
from evals.runner import run_trial
from evals.trace import Trace
from orchestrator.clock import iso
from slackkit.outbox import append_event

ROOT = Path(__file__).resolve().parents[1]

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


@pytest.fixture
def case(tmp_path):
    p = tmp_path / "a01.yaml"
    p.write_text(CASE_YAML)
    return load_case(p)


class FakeResult:
    """Stands in for the SDK's ResultMessage. The runner reads it by
    ATTRIBUTE, never isinstance — same discipline as
    agents/runtime.py:record_turn_result, so an offline stub exercises the
    same code a live turn does."""

    def __init__(self, *, num_turns=5, total_cost_usd=0.1161,
                 duration_ms=14200, is_error=False):
        self.num_turns = num_turns
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.is_error = is_error
        self.session_id = "s1"
        self.permission_denials = []


def decide(**over):
    """A session that behaves like a PM turn: reads the brief, submits."""
    args = dict(ticker="NVDA", action="buy", qty=10, thesis="t",
                invalidation="i")
    args.update(over.pop("args", {}))
    result = over.pop("result", FakeResult())

    def session(options, prompt, state):
        handle_submit_decision(state.conn, seat="pm", args=args,
                               run_date=state.run_date,
                               now_iso="2026-07-06T15:30:00+00:00")
        return (["mcp__fund__get_stage_brief", "mcp__fund__submit_decision"],
                result)
    return session


# --- the mcp_servers seam --------------------------------------------------

def test_run_trial_refuses_a_non_none_mcp_servers_rather_than_dropping_it(case):
    """The parameter is in the signature from day one (PLAN §3) but
    build_seat_options has nowhere to put it until Step 6 — agents/seats.py:49
    is still hardcoded. A parameter that silently does nothing is worse than
    one that isn't there, so this fails loudly."""
    with pytest.raises(NotImplementedError, match="seats.py:49"):
        run_trial("pm", case, 1, mcp_servers={"alpaca": {}},
                  session=decide())


def test_run_trial_accepts_mcp_servers_none(case, tmp_path):
    trace = run_trial("pm", case, 1, mcp_servers=None, session=decide(),
                      workdir=tmp_path)
    assert trace.seat == "pm"


# --- what lands in the trace ----------------------------------------------

def test_trace_identifies_the_charter_that_produced_it(case, tmp_path):
    import hashlib
    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    text = (ROOT / "charters" / "pm.md").read_text()
    assert trace.charter_text == text
    assert trace.charter_sha == hashlib.sha256(text.encode()).hexdigest()
    assert len(trace.git_sha) >= 7


def test_trace_captures_the_tool_names_the_turn_called(case, tmp_path):
    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    assert trace.tool_names == ["mcp__fund__get_stage_brief",
                                "mcp__fund__submit_decision"]


def test_trace_captures_the_decision_rows_the_seat_wrote(case, tmp_path):
    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    assert trace.rows_written["decisions"] == [
        {"ticker": "NVDA", "action": "buy", "qty": 10, "thesis": "t",
         "invalidation": "i", "stop_price": None, "status": "submitted"}]


def test_trace_does_not_report_fixture_seeded_signals_as_pm_output(case,
                                                                   tmp_path):
    """The PM's write table is `decisions`. The case's seeded signal rows are
    input, not output — counting them as rows_written would make every PM
    trace look like it invented a signal."""
    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    assert "signals" not in trace.rows_written


def test_trace_captures_what_the_seat_was_shown(case, tmp_path):
    """I1 grades sizes against allowed_actions and I4 grades tickers against
    the brief; grade.py only ever reads the trace, so both travel in it."""
    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    assert trace.snapshot["allowed_actions"] == {"NVDA": {"buy": 66,
                                                          "sell": 12}}
    assert trace.brief_tickers == ["NVDA"]


def test_trace_captures_only_alerts_raised_during_the_turn(case, tmp_path):
    """Fixture setup appends `signal` events of its own. Only events above the
    build watermark are the seat's."""
    def session(options, prompt, state):
        append_event(state.conn, "alert", {"text": "boom"},
                     "2026-07-06T15:30:00+00:00")
        return [], FakeResult()

    trace = run_trial("pm", case, 1, session=session, workdir=tmp_path)
    assert [a["payload"]["text"] for a in trace.alerts] == ["boom"]
    assert all(e["kind"] != "signal" for e in trace.events)


def test_trace_records_a_missing_cost_as_none(case, tmp_path):
    trace = run_trial("pm", case, 1, workdir=tmp_path,
                      session=decide(result=FakeResult(total_cost_usd=None)))
    assert trace.cost_usd is None and trace.turns == 5


def test_a_turn_without_a_cost_estimate_leaves_the_production_alert(case,
                                                                    tmp_path):
    """record_turn_result is what raises `cost_unavailable`
    (agents/runtime.py:247), and scripts/run_day.py calls it after EVERY seat
    turn. The rig calls it too — otherwise I5's "cost missing AND no alert is
    a FAIL" would fire on the rig's own omission rather than a real defect."""
    trace = run_trial("pm", case, 1, workdir=tmp_path,
                      session=decide(result=FakeResult(total_cost_usd=None)))
    assert trace.cost_usd is None
    assert any("cost_unavailable" in a["payload"]["text"]
               for a in trace.alerts)


def test_a_turn_with_a_cost_estimate_raises_no_alert(case, tmp_path):
    trace = run_trial("pm", case, 1, workdir=tmp_path, session=decide())
    assert trace.alerts == []


def test_trace_records_a_result_that_never_arrived(case, tmp_path):
    """run_seat_turn returns (names, None) when the stream carried no
    ResultMessage (agents/exec_turn.py:98). That is a real production posture,
    not a crash."""
    trace = run_trial("pm", case, 1, workdir=tmp_path,
                      session=lambda o, p, s: ([], None))
    assert trace.cost_usd is None and trace.turns is None


def test_a_session_that_raises_becomes_a_recorded_error_not_a_lost_trial(
        case, tmp_path):
    """Invariant 4 in eval clothing: a blown turn resolves to a trace saying so
    (graded INCONCLUSIVE), never to a crashed suite that loses the other 17
    trials' spend."""
    def boom(options, prompt, state):
        raise RuntimeError("overloaded_error")

    trace = run_trial("pm", case, 1, session=boom, workdir=tmp_path)
    assert trace.is_error and "overloaded_error" in trace.error


def test_each_trial_gets_a_fresh_db_and_journal(case, tmp_path):
    """Per TRIAL, not per case — shared state produces correlated failures."""
    a = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    b = run_trial("pm", case, 2, session=decide(), workdir=tmp_path)
    assert a.rows_written == b.rows_written
    dbs = list(tmp_path.rglob("fund.sqlite"))
    assert len({p.parent for p in dbs}) == 2


def test_run_trial_writes_the_trace_to_disk(case, tmp_path):
    trace = run_trial("pm", case, 3, session=decide(), workdir=tmp_path,
                      traces_root=tmp_path / "traces")
    path = tmp_path / "traces" / trace.git_sha / "a01" / "3.json"
    assert Trace.read(path) == trace


# --- the stage prompt must be the production one ---------------------------

def test_stage_prompt_is_verbatim_the_one_production_sends():
    """evals/ cannot import scripts/run_day.py (it opens Slack and Alpaca), so
    the prompt is duplicated — and pinned here by grepping the source. If
    run_day.py's wording changes and this is not updated, the rig is silently
    evaluating a seat production no longer runs."""
    def norm(s):        # drop the quotes that join adjacent string literals
        return " ".join(s.replace('"', "").split())

    src = norm((ROOT / "scripts" / "run_day.py").read_text())
    for seat, template in PROMPT_TEMPLATES.items():
        assert norm(template) in src, \
            f"{seat} stage prompt drifted from scripts/run_day.py"
    assert "Today's active tickers: NVDA, MSFT." in stage_prompt(
        "pm", ["NVDA", "MSFT"])


# --- run / grade separation -----------------------------------------------

def test_grade_reads_traces_from_disk_without_rerunning_anything(case,
                                                                 tmp_path):
    run_trial("pm", case, 1, session=decide(), workdir=tmp_path,
              traces_root=tmp_path / "traces")
    results = grade_traces(tmp_path / "traces", cases={"a01": case},
                           invariants={})
    assert [r.case for r in results] == ["a01"]
    assert results[0].trial == 1


def test_grade_applies_the_seats_invariant_registry(case, tmp_path):
    from evals.verdict import Verdict

    calls = []

    def i_always_fails(trace, seat, case):
        calls.append(trace.case)
        return Verdict("I9", "FAIL", "nope")

    run_trial("pm", case, 1, session=decide(), workdir=tmp_path,
              traces_root=tmp_path / "traces")
    results = grade_traces(tmp_path / "traces", cases={"a01": case},
                           invariants={"I9": i_always_fails})
    assert calls == ["a01"]
    assert results[0].verdicts[0].outcome == "FAIL"


def test_grade_of_an_errored_trace_is_inconclusive_not_failed(case, tmp_path):
    from evals.verdict import Verdict

    def boom(options, prompt, state):
        raise RuntimeError("overloaded_error")

    def i_would_fail(trace, seat, case):
        return Verdict("I9", "FAIL", "nope")

    trace = run_trial("pm", case, 1, session=boom, workdir=tmp_path)
    result = grade_trace(trace, case, {"I9": i_would_fail})
    assert [v.outcome for v in result.verdicts] == ["INCONCLUSIVE"]


def test_a_grader_that_raises_is_inconclusive_not_a_dead_suite(case, tmp_path):
    from evals.verdict import Verdict          # noqa: F401

    def buggy(trace, seat, case):
        raise KeyError("size")

    trace = run_trial("pm", case, 1, session=decide(), workdir=tmp_path)
    result = grade_trace(trace, case, {"I9": buggy})
    assert result.verdicts[0].outcome == "INCONCLUSIVE"
    assert "KeyError" in result.verdicts[0].detail
