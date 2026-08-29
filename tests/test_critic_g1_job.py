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


# --- #169 bullet 2: a crashed turn writes nothing and the night completes ---

def test_a_turn_that_writes_no_verdict_stops_the_night_and_alerts(db):
    """The likeliest real failure: run_day.make_turn's run() catches every
    exception and returns normally, so a seat that never calls
    submit_spec_critique never raises here. Counting on the absence of an
    exception would report that turn as critiqued.

    STOPPING, not continuing: get_spec_brief takes no arguments and always
    returns the OLDEST unreviewed spec, so the next turn would be shown the
    SAME spec and fail the same way. Breaking bounds the spend at one turn
    instead of MAX_G1_TURNS_PER_NIGHT.

    THE ALERT MUST CARRY THE PENDING COUNT. Because the loop breaks, the
    for...else never runs and `capped` stays False — so without the count this
    is the ONLY message the operator gets, and a head blocking four specs looks
    exactly like a head that is the whole queue."""
    blocking = _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    behind = _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    ran = []

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: ran.append(job["spec_id"]))

    assert ran == [blocking]                       # `behind` was never bought
    assert counts == {"critiqued": 0, "failed": 1}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 0       # NO DEFAULT ROW, EVER
    texts = _alert_texts(db)
    assert len(texts) == 1                         # no cap alert on this path
    assert "critic_g1_turn_wrote_nothing" in texts[0]
    assert blocking in texts[0]
    assert behind not in texts[0]                  # the id, not the count
    assert "2 spec(s) are now pending" in texts[0]
    assert _undrained(db) == 0


def test_the_blocking_alert_counts_everything_still_queued(db):
    """Five pending, the head blocking: one alert, and it must say five. The
    count is what tells an operator whether this is one stuck spec or a stalled
    pipeline."""
    for i in range(5):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        lambda job: None)

    assert counts == {"critiqued": 0, "failed": 1}
    texts = _alert_texts(db)
    assert len(texts) == 1
    assert "5 spec(s) are now pending" in texts[0]


def test_a_turn_that_raises_leaves_no_row_and_the_spec_still_pending(db):
    """Defence in depth for a run_turn that DOES raise — not reachable through
    make_turn today, and costs nothing to keep. It is also the exact shape a
    turn abandoned at SEAT_MAX_WALL_S leaves behind."""
    sid = _spec(db)

    def _boom(job):
        raise TimeoutError("no result after 240s (SEAT_MAX_WALL_S ceiling)")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _boom)

    assert counts == {"critiqued": 0, "failed": 1}
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 0
    # the whole recovery story, asserted rather than asserted-in-prose:
    assert [s["spec_id"] for s in specs_awaiting_critique(db)] == [sid]
    texts = _alert_texts(db)
    assert len(texts) == 1 and "critic_g1_turn_failed" in texts[0]
    assert _undrained(db) == 0


def test_an_interrupted_night_is_retried_not_lost(db):
    """The systemd-SIGTERM story, modelled: night one buys a turn that never
    writes; night two re-selects the same spec and completes it. There are no
    checkpoints on this path — the `c.spec_id IS NULL` predicate is the whole
    recovery mechanism."""
    sid = _spec(db)

    critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                               lambda job: None)
    assert [s["spec_id"] for s in specs_awaiting_critique(db)] == [sid]

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": 1, "failed": 0}
    assert specs_awaiting_critique(db) == []


def test_a_verdict_is_written_once_so_a_re_fire_cannot_double_it(db):
    """The window a SIGTERM between the tool's commit and this job's re-read
    leaves open. submit_spec_critique is PK-write-once and refuses a second
    verdict with the first intact, so re-running the night is safe."""
    sid = _spec(db)
    _verdict(db, sid, "objections", ["the entry clause ignores funding cost"])

    second = handle_submit_spec_critique(
        db, seat="critic",
        args={"spec_id": sid, "verdict": "clear", "objections": []},
        now_iso=iso(NIGHTLY), charter_version="v3", model_id="claude-sonnet-5")

    assert second["ok"] is False and "written once" in second["error"]
    assert db.execute("SELECT verdict FROM strategy_critiques WHERE spec_id = ?",
                      (sid,)).fetchone()["verdict"] == "objections"


# --- the cap: what bounds the number of turns per night ---------------------

def test_the_night_is_capped_and_a_silent_cap_is_alerted(db):
    n = critic_g1.MAX_G1_TURNS_PER_NIGHT
    for i in range(n + 2):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")
    ran = []

    def _turn(job):
        ran.append(job["spec_id"])
        _verdict(db, job["spec_id"], "clear")

    counts = critic_g1.critique_and_log(db, FakeSlack(), SimClock(NIGHTLY),
                                        _turn)

    assert len(ran) == n
    assert counts == {"critiqued": n, "failed": 0}
    texts = _alert_texts(db)
    assert len(texts) == 1
    assert "critic_g1_backlog_capped" in texts[0]
    assert "2 pending spec(s)" in texts[0]     # n+2 registered, n critiqued
    assert "stay pending for the next night" in texts[0]
    assert _undrained(db) == 0


def test_a_cap_that_exactly_drains_the_queue_raises_no_alert(db):
    """The cap alert must mean "there is a backlog", not "we hit the number"."""
    n = critic_g1.MAX_G1_TURNS_PER_NIGHT
    for i in range(n):
        _spec(db, family=f"F{i}", created_at=f"2026-08-20T18:00:{i:02d}+00:00")

    counts = critic_g1.critique_and_log(
        db, FakeSlack(), SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "clear"))

    assert counts == {"critiqued": n, "failed": 0}
    assert _alert_texts(db) == []


def test_the_nightly_cap_is_derived_not_inherited_from_reflect(db):
    """reflect's MAX_TURNS_PER_NIGHT=25 is sized for one turn per resolved
    decision. Inheriting it here would ask for 100 minutes and \$18.75 of worst
    case from the LAST leg of a unit whose whole budget is 30 minutes — i.e.
    would guarantee this leg is cut by the guillotine.

    A guard-rail on the constants, not on behaviour — the behaviour is pinned
    by the cap test above. This exists so a later re-tune cannot silently
    cross the unit's budget or the fund's daily spend."""
    import yaml

    cap = critic_g1.MAX_G1_TURNS_PER_NIGHT
    critic = yaml.safe_load((ROOT / "agents/config/critic.yaml").read_text())

    assert cap >= 1
    # at most 40% of the nightly unit's TimeoutStartSec=30min, leaving the rest
    # for two arithmetic legs and reflect
    assert cap * critic_g1.run_day.SEAT_MAX_WALL_S <= 0.4 * 30 * 60
    # and a hard cost backstop an operator would not be shocked by
    assert cap * critic["max_budget_usd"] <= 2.5


# --- the drain is in `finally` ---------------------------------------------

def test_a_db_error_mid_queue_still_drains_what_the_night_produced(
        db, monkeypatch):
    """reflect_day's N1, borrowed: audit_day's undrained-events check has NO
    date bound, so an appended-but-undrained event reddens every audit until
    the next drain. The drain lives in `finally` for exactly that reason."""
    _spec(db, family="F1", created_at="2026-08-20T18:00:00+00:00")
    _spec(db, family="F2", created_at="2026-08-24T18:00:00+00:00")
    calls = {"n": 0}
    real = critic_g1.next_pending_spec

    def _flaky(conn):
        calls["n"] += 1
        if calls["n"] > 1:
            raise sqlite3.OperationalError("database is locked")
        return real(conn)

    monkeypatch.setattr(critic_g1, "next_pending_spec", _flaky)

    with pytest.raises(sqlite3.OperationalError):
        critic_g1.critique_and_log(
            db, FakeSlack(), SimClock(NIGHTLY),
            lambda job: _verdict(db, job["spec_id"], "clear"))

    assert _undrained(db) == 0
    assert db.execute("SELECT COUNT(*) c FROM strategy_critiques"
                      ).fetchone()["c"] == 1


def test_the_verdict_reaches_research_through_the_outbox(db):
    """Invariant 6: outbound delivery goes through the events outbox, so a
    crash or retry can neither lose nor duplicate a post. slackkit/render.py
    projects a spec_critique event to #research."""
    sid = _spec(db)
    slack = FakeSlack()

    critic_g1.critique_and_log(
        db, slack, SimClock(NIGHTLY),
        lambda job: _verdict(db, job["spec_id"], "objections",
                             ["the entry clause ignores the funding-cost"
                              " condition the hypothesis calls essential"]))

    kinds = [r["kind"] for r in db.execute(
        "SELECT kind FROM events WHERE posted_at IS NOT NULL")]
    assert "spec_critique" in kinds
    # slackkit/fake.py:13 — `posts` is dict[str, list[dict]] KEYED BY CHANNEL,
    # so iterating it yields channel-name strings, not posts. Index the channel
    # instead, which also asserts the thing the docstring claims: the verdict
    # reached #research, not merely somewhere.
    research = slack.posts.get("#research", [])
    assert any(sid in str(post) for post in research), slack.posts
