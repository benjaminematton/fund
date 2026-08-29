"""Offline tests for the nightly G1 job's decision seams (issue #169).

scripts/critic_g1.py is a composition root like reflect_day.py, so main() is
never called here — it builds real clients. What is pinned is what it SELECTS,
what it does when a turn misbehaves, and that it writes no verdict of its own,
because every turn it runs costs real money and every row it touches is the
gate a strategy passes through.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.tools.fund_server import handle_submit_spec_critique
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state.db import connect
from state.models import StrategySpec
from state.specs import insert_strategy_spec, specs_awaiting_critique

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "critic_g1.py"

# 2026-08-25 16:35 ET == 20:35 UTC (EDT) — the scheduled fire.
NIGHTLY = datetime(2026, 8, 25, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("critic_g1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


critic_g1 = _load()

# Copied from tests/test_state_specs.py — the same shape state.specs already
# pins, so a spec this fixture can build is a spec insert_strategy_spec can.
SPEC = dict(
    family="F1", seat="quant",
    hypothesis="Reversal pays for absorbing forced selling.",
    mechanism_class="liquidity_provision",
    universe={"index": "Russell 1000", "pit_constituents": True, "filters": []},
    liquidity_bucket="mega_large",
    signal_rule={"entry": "5d return below -1.5 sigma"},
    param_ranges={"sigma": [1.0, 2.5, 0.25]},
    search_budget=24, holding_period_d=5, rebalance="daily",
    expected_turnover=42.0, exit_rule="close at 5 trading days",
    invalidation="12m low-turnover spread negative for two quarters.",
    capacity_usd=4000000.0,
    predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
    llm_in_loop=0)


def _spec(conn, *, family="F1", created_at="2026-08-25T18:00:00+00:00") -> str:
    """One registered spec with NO critique row — the G1 precondition. `family`
    varies the content because spec_id is the hash of the FIELDS: two specs
    that differ only in created_at collide on the primary key and the second
    insert is silently ignored."""
    return insert_strategy_spec(conn, StrategySpec(**dict(SPEC, family=family)),
                                created_at)


def _verdict(conn, spec_id: str, verdict: str = "clear",
             objections=()) -> None:
    """Write a verdict exactly the way a real turn does — through the handler,
    with attribution bound by the caller (strategy_critiques forbids
    'unknown'). Never a raw INSERT: a fixture that can write a row the handler
    would refuse is a fixture that tests nothing."""
    result = handle_submit_spec_critique(
        conn, seat="critic",
        args={"spec_id": spec_id, "verdict": verdict,
              "objections": list(objections)},
        now_iso=iso(NIGHTLY), charter_version="v3",
        model_id="claude-sonnet-5")
    assert result["ok"], result


def _alert_texts(conn) -> list[str]:
    return [r["payload"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def _undrained(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    yield conn
    conn.close()


# --- #169 bullet 1a: a registered spec gets a critique row that night --------

def test_a_pending_spec_gets_a_verdict_row_the_same_night(db):
    sid = _spec(db)

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": 1, "failed": 0}
    rows = [dict(r) for r in db.execute(
        "SELECT spec_id, verdict, seat, charter_version, model_id"
        " FROM strategy_critiques")]
    assert rows == [{"spec_id": sid, "verdict": "clear", "seat": "critic",
                     "charter_version": "v3", "model_id": "claude-sonnet-5"}]
    assert _undrained(db) == 0          # the spec_critique event reached Slack


def test_the_queue_is_taken_oldest_first(db):
    """get_spec_brief's selector is ORDER BY created_at, spec_id — the job must
    not impose its own order, or the seat would be shown a different spec than
    the job re-reads."""
    old = _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    new = _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    seen = []

    def _turn(job):
        seen.append(job["spec_id"])
        _verdict(db, job["spec_id"], "clear")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _turn)

    assert seen == [old, new]
    assert counts == {"critiqued": 2, "failed": 0}


def test_a_night_with_nothing_pending_runs_no_turn_and_says_so(db, capsys):
    """An empty queue is the normal state today — there is no live
    submit_strategy_spec producer yet. Spending nothing is correct, and this
    leg costs $0 on such a night."""
    ran = []

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        lambda job: ran.append(job))

    assert ran == [] and counts == {"critiqued": 0, "failed": 0}
    assert "critic_g1:" in capsys.readouterr().out
    assert _alert_texts(db) == []


def test_a_spec_that_already_carries_a_verdict_is_never_bought_again(db):
    """Row-level idempotency, the only kind on this path: there are no
    checkpoints on the nightly job. A re-fire pays only for what is still
    pending — the same predicate that makes a SIGTERM'd night retryable."""
    _spec(db)
    critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                               lambda job: _verdict(db, job["spec_id"]))

    bought = []
    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY), lambda job: bought.append(job))

    assert bought == []
    assert counts == {"critiqued": 0, "failed": 0}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 1


def test_the_job_never_writes_a_verdict_of_its_own(db):
    """strategy-contracts.md §3.4: no default row, ever. The job SELECTS the
    queue and RE-READS the result; the only INSERT is the seat's own tool call.
    Same instrument tests/test_state_specs.py:203 points at orchestrator/ — a
    lint, not a comment, because prose cannot hold this."""
    source = SCRIPT.read_text()
    for verb in ("INSERT INTO strategy_critiques",
                 "UPDATE strategy_critiques",
                 "DELETE FROM strategy_critiques",
                 "insert_strategy_spec"):
        assert verb not in source, f"{SCRIPT.name} writes G1 state: {verb!r}"
