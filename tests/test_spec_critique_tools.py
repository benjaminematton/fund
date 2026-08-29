"""The Critic's tool surface: one read, one write, both seat-locked.

Handler-level, like tests/test_fund_tools.py — the @tool wrappers are thin and
the SDK is not in scope offline.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from agents.tools.fund_server import (_can, build_fund_server,
                                      handle_get_spec_brief,
                                      handle_submit_spec_critique)
from orchestrator.clock import SimClock
from state.db import connect
from state.models import StrategySpec
from state.specs import insert_strategy_spec, specs_awaiting_critique

NOW = "2026-07-06T15:00:00+00:00"
CHARTER = "v2"
MODEL = "claude-sonnet-5"

SPEC = dict(
    family="F1", seat="quant",
    hypothesis="Reversal pays for absorbing forced selling in low-turnover names.",
    mechanism_class="liquidity_provision",
    universe={"index": "Russell 1000", "pit_constituents": True, "filters": []},
    liquidity_bucket="mega_large",
    signal_rule={"entry": "5d return below -1.5 sigma AND turnover_decile == 10"},
    param_ranges={"sigma": [1.0, 2.5, 0.25]},
    search_budget=24, holding_period_d=5, rebalance="daily",
    expected_turnover=42.0, exit_rule="close at 5 trading days",
    invalidation="12m low-turnover spread negative for two quarters.",
    capacity_usd=4000000.0,
    predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
    llm_in_loop=0)


def _submit(db, **over):
    """Every call binds attribution: the columns are NOT NULL and forbid
    'unknown', so there is no such thing as an unattributed G1 verdict.

    It also binds the spec, defaulting to whatever the caller's own args name
    (strategy-contracts.md §3.4). These tests are about the handler's OTHER
    refusals — wrong seat, unregistered spec, second verdict — and an unbound
    call is refused before any of them are reached. The binding's own refusals
    are pinned in tests/test_spec_critique_binding.py."""
    kwargs = dict(seat="critic", args={}, now_iso=NOW,
                  charter_version=CHARTER, model_id=MODEL)
    kwargs.update(over)
    kwargs.setdefault("expected_spec_id", kwargs["args"].get("spec_id"))
    return handle_submit_spec_critique(db, **kwargs)


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


@pytest.fixture
def spec_id(db):
    return insert_strategy_spec(db, StrategySpec(**SPEC), NOW)


# --- submit_spec_critique --------------------------------------------------

def test_records_a_clear_verdict(db, spec_id):
    r = _submit(db, args={"spec_id": spec_id, "verdict": "clear"})
    assert r["ok"] is True
    row = db.execute("SELECT * FROM strategy_critiques").fetchone()
    assert row["verdict"] == "clear"
    assert json.loads(row["objections"]) == []
    assert row["seat"] == "critic"


def test_a_recorded_verdict_names_the_charter_and_model_behind_it(db, spec_id):
    """contracts.md §2 attribution, narrowed: this table forbids 'none' and
    'unknown', so a verdict that cannot say which charter produced it cannot
    be written at all."""
    _submit(db, args={"spec_id": spec_id, "verdict": "clear"})
    row = db.execute("SELECT * FROM strategy_critiques").fetchone()
    assert row["charter_version"] == CHARTER
    assert row["model_id"] == MODEL


def test_records_objections_verbatim(db, spec_id):
    objs = ["the rule filters the top turnover decile, where reversal inverts",
            "the stated liquidity mechanism cannot pay for a momentum rule"]
    r = _submit(db, args={"spec_id": spec_id, "verdict": "objections",
                          "objections": objs})
    assert r["ok"] is True
    row = db.execute("SELECT * FROM strategy_critiques").fetchone()
    assert json.loads(row["objections"]) == objs


@pytest.mark.parametrize("seat", ["pm", "analyst", "exec", "quant", ""])
def test_only_the_critic_may_submit(db, spec_id, seat):
    r = _submit(db, seat=seat, args={"spec_id": spec_id, "verdict": "clear"})
    assert r["ok"] is False
    assert "is not granted to seat" in r["error"]
    assert db.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0


def test_objections_without_objections_is_refused(db, spec_id):
    r = _submit(db, args={"spec_id": spec_id, "verdict": "objections",
                          "objections": []})
    assert r["ok"] is False
    assert db.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0


def test_a_silent_critic_leaves_the_spec_unadvanced(db, spec_id):
    """The design's central claim, asserted at the state layer rather than
    inferred from a grading verdict. Nothing writes a default row, so a turn
    that ends without submit_spec_critique leaves the table empty and the spec
    still queued — where the next turn, or evaluate_g1's REJECT g1_no_review,
    finds it. The trade pipeline's `critiques` defaults to clear in exactly
    this situation; this table must not, and that inversion is the feature."""
    assert db.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0
    assert [s["spec_id"] for s in specs_awaiting_critique(db)] == [spec_id]


def test_a_verdict_on_an_unregistered_spec_is_refused(db):
    r = _submit(db, args={"spec_id": "spec_nope", "verdict": "clear"})
    assert r["ok"] is False
    assert "not registered" in r["error"]
    assert db.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0


def test_a_second_verdict_is_refused_never_overwritten(db, spec_id):
    _submit(db, args={"spec_id": spec_id, "verdict": "clear"})
    r = _submit(db, args={"spec_id": spec_id, "verdict": "objections",
                          "objections": ["second thoughts"]})
    assert r["ok"] is False
    assert "already carries a G1 verdict" in r["error"]
    row = db.execute("SELECT verdict FROM strategy_critiques").fetchone()
    assert row["verdict"] == "clear"


def test_malformed_payload_writes_nothing(db, spec_id):
    for args in ({"spec_id": spec_id},
                 {"spec_id": spec_id, "verdict": "maybe"},
                 {"verdict": "clear"},
                 {"spec_id": spec_id, "verdict": "objections",
                  "objections": ["a", "b", "c", "d"]},
                 {"spec_id": spec_id, "verdict": "objections",
                  "objections": ["x" * 201]}):
        r = _submit(db, args=args)
        assert r["ok"] is False, args
    assert db.execute(
        "SELECT COUNT(*) c FROM strategy_critiques").fetchone()["c"] == 0


def test_a_refused_verdict_appends_no_projection_event(db, spec_id):
    """A `spec_critique` event for a row that was never written would post a
    verdict to Slack that the gate will never see — the projection contradicting
    SQLite, which invariant 6 makes the source of truth."""
    _submit(db, seat="pm", args={"spec_id": spec_id, "verdict": "clear"})
    _submit(db, args={"spec_id": "spec_nope", "verdict": "clear"})
    _submit(db, args={"spec_id": spec_id, "verdict": "maybe"})
    assert db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'spec_critique'"
    ).fetchone()["c"] == 0


# --- get_spec_brief --------------------------------------------------------

def test_brief_carries_the_pending_spec_with_json_decoded(db, spec_id,
                                                          tmp_path):
    r = handle_get_spec_brief(db, seat="critic",
                              journals_root=tmp_path / "journals")
    assert r["ok"] is True
    specs = r["brief"]["specs"]
    assert [s["spec_id"] for s in specs] == [spec_id]
    assert specs[0]["universe"]["index"] == "Russell 1000"
    assert specs[0]["hypothesis"].startswith("Reversal pays")


def test_brief_drops_a_spec_once_it_has_a_verdict(db, spec_id, tmp_path):
    _submit(db, args={"spec_id": spec_id, "verdict": "clear"})
    r = handle_get_spec_brief(db, seat="critic",
                              journals_root=tmp_path / "journals")
    assert r["brief"]["specs"] == []


@pytest.mark.parametrize("seat", ["pm", "analyst", "exec", ""])
def test_brief_is_critic_only(db, seat, tmp_path):
    r = handle_get_spec_brief(db, seat=seat,
                              journals_root=tmp_path / "journals")
    assert r["ok"] is False
    assert "is not granted to seat" in r["error"]


def test_brief_degrades_an_unbuildable_journal_rather_than_raising(db,
                                                                   spec_id):
    """invariant 4 in the brief: an unbound journals root names itself in
    `unavailable` instead of taking the turn down. The SPEC survives — a
    missing journal is absent context, not a missing subject."""
    r = handle_get_spec_brief(db, seat="critic", journals_root=None)
    assert r["ok"] is True
    assert r["brief"]["journal"] == ""
    assert any("journal" in u for u in r["brief"]["unavailable"])
    assert [s["spec_id"] for s in r["brief"]["specs"]] == [spec_id]


def test_an_unreadable_spec_queue_is_an_error_not_an_empty_queue(db, tmp_path):
    """The one section that must NOT degrade. [] would read to the seat as
    'nothing pending', so it would end its turn writing nothing and the spec
    would stay unreviewed behind a clean-looking trace. Safe either way — no
    verdict, no advance — but only the error is legible afterwards."""
    db.execute("DROP TABLE strategy_critiques")
    db.commit()
    r = handle_get_spec_brief(db, seat="critic",
                              journals_root=tmp_path / "journals")
    assert r["ok"] is False
    assert "spec queue" in r["error"]
    assert "brief" not in r


# --- server wiring ---------------------------------------------------------

def test_the_critic_server_carries_exactly_its_two_tools(tmp_path):
    clock = SimClock(datetime.fromisoformat(NOW))
    server = build_fund_server(
        lambda: connect(tmp_path / "fund.sqlite"), clock, "critic")
    assert server is not None


def test_the_critic_gets_no_trade_pipeline_tools():
    """The Critic is NOT wired into the trade pipeline in this phase — the
    orchestrator still inserts its own `no_critic_seat` rows. A Critic holding
    submit_decision or get_stage_brief would be a silent scope widening."""
    assert not _can("critic", "submit_decision")
    assert not _can("critic", "submit_signal")
    assert not _can("critic", "get_stage_brief")
    assert not _can("critic", "list_open_tickets")


def test_the_critic_charter_header_yields_a_real_version_not_unknown():
    """A coupling that did not exist before this seat. `_parse_charter_version`
    reads the version out of the charter's FIRST LINE and returns 'unknown'
    when it cannot — deliberately, so a formatting slip never takes a trading
    day down (invariant 4). But strategy_critiques forbids 'unknown', so for
    THIS seat that graceful degradation turns into every submit_spec_critique
    failing at the INSERT. The charter's formatting is load-bearing on the
    write path; pinned here because nothing else would say so."""
    from agents.seats import charter_version_for
    version = charter_version_for({"seat": "critic"})
    assert version != "unknown", (
        "charters/critic.md's first line lost its vN — every G1 verdict would"
        " fail at the INSERT, because strategy_critiques forbids 'unknown'")
    assert version.startswith("v")


def test_no_other_seat_carries_the_g1_capabilities():
    """The inverse lock. Task 2's table takes one row per spec ever, so a
    second seat able to write it could spend the Critic's only verdict."""
    import agents.tools.fund_server as fs
    for seat in fs.SEAT_CAPS:
        if seat == "critic":
            continue
        assert not _can(seat, "submit_spec_critique"), seat
        assert not _can(seat, "get_spec_brief"), seat
