"""A live seat turn becomes a Trace the existing grader can read.

The whole point of recording production turns is that `evals/grade.py` is a
pure function of a trace and a case — so a NEW invariant written next year
re-scores every trace ever recorded, for $0. That property is worth nothing
without a corpus, and a corpus cannot be reconstructed after the fact.

Everything here is offline: no SDK import, no filesystem except the one sink
test, no network. `build_trace` is pure by design so the trace path is testable
without running a day.
"""

from __future__ import annotations

import json

from evals.live import build_trace, file_sink
from evals.trace import Trace


class _Result:
    """Stands in for the SDK's ResultMessage.

    Production reads that message by attribute and never by isinstance (the SDK
    type is not importable from the purity-lint-adjacent test path), so a plain
    object carrying the same attributes exercises the same code the live day
    runs. Same discipline as agents/runtime.py's cost seam."""

    num_turns = 3
    total_cost_usd = 0.0141
    duration_ms = 8120
    is_error = False


def test_build_trace_maps_a_live_turn():
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=2, git_sha="abc1234",
        charter_text="# Portfolio Manager — v6\n", model="claude-sonnet-5",
        snapshot={"cash": 1000.0, "positions": [], "allowed_actions": ["hold"]},
        brief_tickers=["NVDA"], tool_names=["mcp__fund__submit_decision"],
        result=_Result())

    assert t.case == "live-2026-08-18"
    assert t.trial == 2
    assert t.seat == "pm"
    assert t.tool_names == ["mcp__fund__submit_decision"]
    assert t.cost_usd == 0.0141
    assert t.turns == 3
    assert t.duration_ms == 8120
    assert t.is_error is False
    assert t.error is None


def test_the_case_name_marks_the_corpus_as_live():
    """`case` and `trial` are the eval rig's provenance — a named scenario run
    N times — and a live turn has neither. The `live-` prefix keeps the overload
    self-documenting and makes the corpus separable by a prefix test if Trace is
    later split into a turn payload plus a provenance discriminator."""
    t = build_trace(
        seat="news", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Result())
    assert t.case.startswith("live-")


def test_charter_sha_is_derived_from_the_carried_text():
    """Computed here, not passed in, and by the same rule evals/config.py uses.
    Two fields that must agree cannot disagree if only one is an input — and a
    trace carries the charter TEXT precisely so it stays gradeable after the
    charter on disk is edited."""
    import hashlib

    charter = "# Portfolio Manager — v6\n"
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text=charter, model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Result())

    assert t.charter_text == charter
    assert t.charter_sha == hashlib.sha256(charter.encode()).hexdigest()


def test_a_charter_edit_changes_the_sha():
    shas = {build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text=text, model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Result()).charter_sha
        for text in ("# PM — v6\n", "# PM — v7\n")}
    assert len(shas) == 2


def test_build_trace_keeps_a_missing_cost_none_not_zero():
    """A fabricated 0.0 makes real spend look free — the lie agents/runtime.py
    refuses to tell, and what invariant I5 pairs with the cost_unavailable
    alert production is required to raise."""

    class _NoCost:
        num_turns = 1
        total_cost_usd = None
        duration_ms = 10
        is_error = False

    t = build_trace(
        seat="analyst", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_NoCost())
    assert t.cost_usd is None


def test_a_turn_that_produced_no_result_is_an_errored_trace():
    """A seat that timed out hands back None. That is a trace, not an exception:
    the day continues on its defaults (invariant 4), and an errored trace is
    INCONCLUSIVE to every grader rather than a manufactured failure."""
    t = build_trace(
        seat="critic", run_date="2026-08-18", turn_seq=1, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=None)

    assert t.is_error is True
    assert t.error == "no result message"
    assert t.cost_usd is None
    assert t.turns is None


def test_an_sdk_error_result_marks_the_trace_errored():
    class _Errored:
        num_turns = 2
        total_cost_usd = 0.002
        duration_ms = 40
        is_error = True

    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Errored())
    assert t.is_error is True


def test_build_trace_copies_its_sequence_inputs():
    """The caller's lists must not alias the trace — a later mutation of the
    turn's tool_names would otherwise silently rewrite recorded history."""
    tools = ["mcp__fund__get_stage_brief"]
    tickers = ["NVDA"]
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=tickers,
        tool_names=tools, result=_Result())

    tools.append("mcp__alpaca__place_order")
    tickers.append("AMD")
    assert t.tool_names == ["mcp__fund__get_stage_brief"]
    assert t.brief_tickers == ["NVDA"]


def test_file_sink_writes_where_the_grader_reads(tmp_path):
    """<root>/<git_sha>/<case>/<trial>.json — the layout grade_traces globs."""
    sink = file_sink(str(tmp_path))
    t = build_trace(
        seat="pm", run_date="2026-08-18", turn_seq=0, git_sha="abc1234",
        charter_text="x", model="m", snapshot={}, brief_tickers=[],
        tool_names=[], result=_Result())
    sink(t)

    written = tmp_path / "abc1234" / "live-2026-08-18" / "0.json"
    assert written.exists()
    assert Trace.read(written).seat == "pm"
    assert json.loads(written.read_text())["case"] == "live-2026-08-18"


def test_file_sink_keeps_one_file_per_turn(tmp_path):
    """Two seats in the same day must not collide: the turn sequence is the
    filename, so the caller owns a single per-day counter."""
    sink = file_sink(str(tmp_path))
    for seq, seat in enumerate(("news", "pm", "exec")):
        sink(build_trace(
            seat=seat, run_date="2026-08-18", turn_seq=seq, git_sha="abc1234",
            charter_text="x", model="m", snapshot={}, brief_tickers=[],
            tool_names=[], result=_Result()))

    day = tmp_path / "abc1234" / "live-2026-08-18"
    assert sorted(p.name for p in day.glob("*.json")) == ["0.json", "1.json",
                                                          "2.json"]
