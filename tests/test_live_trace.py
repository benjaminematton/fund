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


# --- rows_written ------------------------------------------------------------
#
# The gap that made every live trace ungradeable. `evals/runner.py` populates
# `rows_written` for eval-suite trials; `build_trace` did not, so four graders
# (EXPECT, I1, I3, I4) read an empty dict on every production turn.
#
# I4's failure was the loud one and it was a LIE: with no rows and the submit
# tool present in tool_names it returns FAIL/schema-reject — "the handler
# refused the submission" — on a turn whose rows landed in production.
#
# The rig gives every trial a fresh DB, so a bare table scan there returns
# exactly one seat's rows. A LIVE database accumulates every seat's rows for
# the day, so scoping by run_date alone would hand the news seat's trace the
# analyst's signals. That is why this is not a copy of the rig's _rows.

from datetime import datetime, timezone

from orchestrator.clock import SimClock
from state.db import connect

RUN = "2026-08-20"
NOW = f"{RUN}T13:00:00+00:00"


def _signal(conn, agent, ticker, confidence=60):
    conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, charter_version, model_id)"
        " VALUES (?, ?, ?, 'bullish', ?, 's', ?, 'v1', 'm')",
        (RUN, agent, ticker, confidence, NOW))
    conn.commit()


def _decision(conn, ticker):
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at, charter_version, model_id)"
        " VALUES (?, ?, 'buy', 10, 't', 'n/a', 'submitted', ?, 'v6', 'm')",
        (RUN, ticker, NOW))
    conn.commit()


def test_a_live_trace_carries_the_rows_the_seat_actually_wrote(tmp_path):
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    _signal(conn, "news", "NVDA")
    rows = rows_written(conn, "news", RUN)
    assert [r["ticker"] for r in rows["signals"]] == ["NVDA"]


def test_a_seats_trace_never_carries_another_seats_rows(tmp_path):
    """THE reason this is not the rig's _rows. Two analysts write `signals` on
    the same live day; a scan keyed on run_date alone would put the analyst's
    row in the news seat's trace, and I1/I3 would then grade one seat on
    another's output."""
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    _signal(conn, "analyst", "AMD")
    _signal(conn, "news", "NVDA")

    assert [r["ticker"] for r in rows_written(conn, "news", RUN)["signals"]] \
        == ["NVDA"]
    assert [r["ticker"] for r in rows_written(conn, "analyst", RUN)["signals"]] \
        == ["AMD"]


def test_yesterdays_rows_are_not_todays(tmp_path):
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, charter_version, model_id)"
        " VALUES ('2026-08-19', 'news', 'OLD', 'bullish', 60, 's', ?, 'v1',"
        " 'm')", (NOW,))
    conn.commit()
    assert rows_written(conn, "news", RUN) == {}


def test_the_pm_trace_carries_decisions(tmp_path):
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    _decision(conn, "NVDA")
    rows = rows_written(conn, "pm", RUN)
    assert rows["decisions"][0]["action"] == "buy"


def test_a_seat_that_writes_no_table_is_empty_not_a_crash(tmp_path):
    """`exec` submits nothing through the fund server — it places orders. An
    unmapped seat must produce an empty dict, never a KeyError that costs the
    trace (invariant 4: a trace is evidence, never control flow)."""
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    assert rows_written(conn, "exec", RUN) == {}
    assert rows_written(conn, "some_future_seat", RUN) == {}


def test_a_silent_seat_writes_no_rows_rather_than_a_missing_key(tmp_path):
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    assert rows_written(conn, "news", RUN) == {}


def test_i4_no_longer_reports_a_refusal_that_did_not_happen(tmp_path):
    """The regression, tied to the symptom that found it. fund-07 graded a real
    news trace off the droplet and got I4 FAIL/schema-reject — a defect that
    did not occur, on a turn whose three rows are in production right now."""
    from evals.invariants.i4_schema import i4_schema
    from evals.live import rows_written

    conn = connect(tmp_path / "fund.sqlite")
    for ticker in ("AAPL", "MSFT", "NVDA"):
        _signal(conn, "analyst", ticker)

    trace = build_trace(
        seat="analyst", run_date=RUN, turn_seq=0, git_sha="abc1234",
        charter_text="# Analyst — v2\n", model="claude-haiku-4-5-20251001",
        snapshot={}, brief_tickers=["AAPL", "MSFT", "NVDA"],
        tool_names=["mcp__fund__submit_signal"], result=_Result(),
        rows=rows_written(conn, "analyst", RUN))

    class _Seat:
        name = "analyst"

    class _Case:
        tickers = ["AAPL", "MSFT", "NVDA"]
        clock = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        # I4 keys off `subjects`, not `tickers`, so it can grade a spec-shaped
        # Critic case the same way it grades a ticker-shaped one. For a
        # ticker-shaped case the real Case property returns exactly this.
        subjects = tickers

    verdict = i4_schema(trace, _Seat(), _Case())
    assert verdict.outcome == "PASS", verdict.detail
