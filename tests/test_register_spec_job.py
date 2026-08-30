"""Offline tests for the hand-run spec-registration job's decision seams (#198).

main() IS CALLED HERE, with everything it builds faked but the decision under
test. An earlier draft of this file said main() "is never called — it builds
real clients", copied from tests/test_critic_g1_job.py. That sentence is stale
in the source it was copied from: that file's ":621 main()'s own exit codes"
section drives main() through three test_main_exits_* cases, added precisely
because an identical assumption in an earlier critic_g1 draft went unpinned and
the claim it protected turned out to be false. The exit code is this job's ONLY
report — there is no OnFailure= unit behind it — so it is the last thing that
may go untested.

THE JOB IS A PRODUCER, which is why it looks different from its siblings. Every
other nightly job drains a queue and can compute how much of its OWN work is
outstanding; this one has none to read — there is no ideas table and no
strategies table in state/schema.sql — so "did the turn work?" is a
strategy_specs row COUNT either side, not a selector re-read. It does report
the DOWNSTREAM G1 queue either side, through the canonical
state.specs.specs_awaiting_critique selector, because that is the thing the
operator wants to know changed.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.tools.fund_server import handle_submit_strategy_spec
from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state.db import connect
from tests.synthetic import spec_payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "register_spec.py"

# An arbitrary attended moment — this job is hand-run, not scheduled.
RUN_AT = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("register_spec", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


register_spec = _load()


def _register(conn, **overrides) -> str:
    """Register a spec exactly the way a real turn does — through the handler,
    from the seat that actually holds the cap. Never a raw INSERT: a fixture
    that can write a row the handler would refuse is a fixture that tests
    nothing."""
    result = handle_submit_strategy_spec(
        conn, seat="quant", args=spec_payload(**overrides), now_iso=iso(RUN_AT))
    assert result["ok"], result
    return result["spec_id"]


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


def test_a_turn_that_registers_a_spec_is_counted_and_drained(db):
    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: _register(db))

    assert counts == {"registered": 1, "failed": 0}
    assert _alert_texts(db) == []
    assert _undrained(db) == 0


def test_the_queue_depth_comes_from_the_canonical_selector(db):
    """The number the operator is told is the number the 16:35 critic_g1 leg
    will act on, or it is worse than saying nothing. Derived from
    state.specs.specs_awaiting_critique — a second copy of the predicate here
    is how the job and the tool come to disagree about what "pending" means,
    which is the reason scripts/critic_g1.py:233-238 gives for its own
    PENDING_REPORT_LIMIT.

    The selector's default is limit=1 (state/specs.py:48-49), so a DEPTH needs
    an explicit limit argument. Asserted against three, which the default
    would have reported as one."""
    from state.specs import specs_awaiting_critique

    assert register_spec.queue_depth(db) == 0
    for budget in (24, 25, 26):
        _register(db, search_budget=budget)

    assert register_spec.queue_depth(db) == 3
    assert len(specs_awaiting_critique(db)) == 1        # the default, for contrast


def test_a_turn_that_writes_nothing_alerts_and_registers_nothing(db):
    """The likeliest real failure, and nothing else catches it:
    run_day.make_turn's own run() catches every exception and returns
    normally, so a seat that never calls submit_strategy_spec — or calls it
    and gives up on {"ok": false} — raises nothing here either."""
    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    assert counts == {"registered": 0, "failed": 1}
    assert db.execute("SELECT count(*) c FROM strategy_specs"
                      ).fetchone()["c"] == 0
    assert any("register_spec_wrote_nothing" in t for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_the_wrote_nothing_alert_names_all_four_causes_and_the_queue(db):
    """FOUR causes, not three. charters/quant.md's Mission sanctions "this
    family is tapped out, I am not proposing" as a legitimate output, and this
    job counts a no-write turn as FAILED — so the seat doing the right thing
    and the seat going dark produce the identical alert. The operator can only
    tell them apart if the alert says so; an alert that lists three causes
    when there are four teaches the reader to distrust it.

    The queue depth is in the text for the same reason: "nothing registered"
    and "nothing registered, and there are already 2 specs waiting for G1" are
    different operator problems."""
    _register(db, search_budget=24)          # something already pending

    register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    text = next(t for t in _alert_texts(db)
                if "register_spec_wrote_nothing" in t)
    assert "declined" in text                 # the sanctioned fourth cause
    assert "never called" in text
    assert "refused" in text
    assert "duplicate" in text or "already on the books" in text
    assert "G1 queue" in text and "1" in text


def test_a_re_registration_counts_as_wrote_nothing(db):
    """A duplicate is honest and it is also NOT a new spec: the content hash
    collides, INSERT OR IGNORE writes no row, and the outbox gets no event
    (agents/tools/fund_server.py:290-309). The count either side does not
    move, so the operator is told the run produced nothing — which is true,
    and is the only answer that does not overstate what happened."""
    _register(db)

    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: _register(db))

    assert counts == {"registered": 0, "failed": 1}
    assert any("register_spec_wrote_nothing" in t for t in _alert_texts(db))


def test_a_turn_that_raises_alerts_and_writes_nothing(db):
    """Defence in depth — not reachable through run_day.make_turn today, which
    swallows everything. Costs nothing to keep, and the alternative is a
    traceback out of a hand-run command with no Slack record."""
    def _boom():
        raise RuntimeError("sdk exploded")

    counts = register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), _boom)

    assert counts == {"registered": 0, "failed": 1}
    assert any("register_spec_turn_failed" in t and "sdk exploded" in t
               and "G1 queue" in t
               for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_the_job_never_registers_a_spec_of_its_own(db):
    """Invariant 7: structured data reaches state only through the seat's tool
    call. A job that could seed a spec on a failed turn would be manufacturing
    the fund's research record."""
    register_spec.register_and_log(
        db, FakeSlack(), SimClock(RUN_AT), lambda: None)

    assert db.execute("SELECT count(*) c FROM strategy_specs"
                      ).fetchone()["c"] == 0


def test_a_failure_inside_the_body_is_alerted_and_exits_nonzero(db):
    """No systemd unit stands behind this job (CEO ruling B1: no fifth leg),
    so unlike critic_g1 there is no OnFailure= to carry a failure out of the
    box. The drained alert and the nonzero exit are the whole report."""
    def _body():
        raise RuntimeError("db went away")

    rc = register_spec._guarded(db, FakeSlack(), SimClock(RUN_AT), _body)

    assert rc == 1
    assert any("register_spec_failed" in t and "db went away" in t
               for t in _alert_texts(db))
    assert _undrained(db) == 0


def test_a_clean_run_returns_the_bodys_own_code(db):
    """A NONZERO sentinel, deliberately — tests/test_critic_g1_job.py:612-618's
    shape. `lambda: 0` asserted against 0 cannot tell pass-through from a
    swallow: it is the assertion that goes green under either implementation.
    It matters more here than there, because this job's _body returns 1 for a
    turn that wrote nothing (ruling 16) and a _guarded that quietly returned 0
    would report a failed registration as a success at the shell."""
    assert register_spec._guarded(db, FakeSlack(), SimClock(RUN_AT),
                                  lambda: 7) == 7
    assert _alert_texts(db) == []


def test_the_turn_surface_is_exactly_the_one_cap_the_seat_holds(db, tmp_path):
    """DRIVEN THROUGH build_seat_options, not compared against a constant.

    The earlier version of this test asserted
    `REGISTER_TOOLS == [f"mcp__fund__{c}" for c in sorted(SEAT_CAPS["quant"])]`
    — two constants, derived from each other, green on first run and green
    under any build_seat_options. What actually has to hold is that the
    narrowing SURVIVES the builder: build_seat_options refuses a per-turn list
    naming anything the seat's yaml does not already grant, and it refuses a
    glob, so a REGISTER_TOOLS the seat cannot carry fails at turn time — on a
    host, in front of a human waiting on a $0.75 turn.

    The standing guards are re-asserted on the narrowed options for the reason
    tests/test_exec_seat_tool_surface.py:343-353 gives: a narrowing must not
    become the place a second guard is quietly dropped."""
    from agents.seats import build_seat_options, load_seat_config
    from agents.tools.fund_server import SEAT_CAPS

    cfg = load_seat_config("agents/config/quant.yaml")
    opts = build_seat_options(cfg, tmp_path / "fund.sqlite",
                              SimClock(RUN_AT),
                              tools=register_spec.REGISTER_TOOLS)

    assert opts.tools == register_spec.REGISTER_TOOLS
    # ...and the surface really is the seat's whole cap set, so this narrowing
    # cannot silently drop a capability the seat needs.
    assert opts.tools == [f"mcp__fund__{cap}"
                          for cap in sorted(SEAT_CAPS["quant"])]
    assert "mcp__alpaca__*" not in opts.tools
    assert "mcp__fund__*" not in opts.tools

    # Every other guard, unchanged by the narrowing.
    assert "mcp__alpaca__place_*" in (opts.disallowed_tools or [])
    assert opts.hooks in (None, {})
    assert opts.setting_sources == []
    assert opts.permission_mode == "dontAsk"
    assert opts.max_budget_usd == cfg["max_budget_usd"]
