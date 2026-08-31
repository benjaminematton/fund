"""submit_strategy_spec — strategy-contracts.md §3.1.

extra="forbid" and no partial specs: a malformed payload writes nothing.

THE HANDLER IS CALLED DIRECTLY, never through a built server. That is now a
choice about what THIS file tests, not a necessity: #198 registered the tool
and granted the cap to `quant`, so an MCP surface does exist and
tests/test_tool_surface_canon.py drives it. What is under test here is the
handler's own contract — the seat binding, the content-addressed id, the
duplicate report, and the fail-closed branch when the DDL rejects a row the
model accepted — none of which the wrapper adds anything to.

The seat used below is `quant`, the real holder. The earlier version of this
file monkeypatched the cap onto `analyst` because NO seat held it; doing that
now would test a non-holder and quietly stop exercising the grant that
actually ships.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest

import state.db
from agents.tools.fund_server import SEAT_CAPS, handle_submit_strategy_spec
from tests.synthetic import spec_payload

NOW = "2026-08-29T14:00:00Z"


@pytest.fixture
def db_with_an_unmirrored_check(tmp_path):
    """The shipped schema, plus one CHECK that `StrategySpec` does not mirror.

    `rebalance` is the column chosen because it is the real gap: the DDL types
    it `TEXT NOT NULL` and the model types it a bare `str`, so a CHECK added
    there tomorrow is a constraint nothing upstream enforces. The DDL is
    DERIVED from state/schema.sql and patched by substitution, never restated —
    a copied table would stop being the shipped one the day the real column
    changes, and the substitution count asserts the patch actually landed.
    """
    ddl = Path(state.db.__file__).with_name("schema.sql").read_text()
    patched, n = re.subn(
        r"^  rebalance +TEXT NOT NULL,$",
        "  rebalance        TEXT NOT NULL CHECK(rebalance IN ('weekly')),",
        ddl, flags=re.M)
    assert n == 1, "schema.sql's `rebalance` column moved — re-derive this"
    conn = sqlite3.connect(tmp_path / "fund.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(patched)
    yield conn
    conn.close()


def _submit(fund_db, payload, seat="quant"):
    return handle_submit_strategy_spec(
        fund_db, seat=seat, args=payload, now_iso=NOW)


def _count(fund_db, table="strategy_specs"):
    return fund_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_exactly_one_seat_holds_this_cap_and_it_is_the_quant_seat():
    """Was `assert holders == []` under G-2(iii) (#171), which held while
    nothing drove the tool. #198 staffs the driving seat, so the assertion
    inverts rather than disappears — and it stays an EQUALITY, which is the
    part that was load-bearing. What it guarded against was a cap appearing
    on a seat without the charter and the schedule that justify it; an
    equality still catches that, because a second holder reddens here.

    THE INVERSION CARRIES ITS OWN PREMISE, executably. `holders == ["quant"]`
    is only safe because `quant` is not a trading-day seat: scripts/run_day.py
    passes no `tools=` to make_turn (:716-719), so agents/seats.py:145-147
    returns the seat's STANDING cfg["tools"] verbatim and a cap on a
    run_day.SEATS member is a cap that seat holds at 09:00. That premise was
    asserted in four places in this lane and all four were PROSE — a comment
    cannot fail. The second assertion below is the same claim as code, and it
    is derived from the holders list rather than from the literal "quant", so
    it keeps biting if the cap ever moves to a different seat.

    specs/design.md:70 is what makes `quant` the right seat to hold it.
    """
    holders = [s for s, caps in SEAT_CAPS.items()
               if "submit_strategy_spec" in caps]
    assert holders == ["quant"]

    # The premise, not a restatement of the line above: NO holder of this cap
    # may be a trading-day seat. run_day.SEATS is {stage: (seat, ...)}.
    import scripts.run_day as run_day

    # The comprehension below assumes that shape. Were SEATS ever flattened to
    # {stage: seat}, it would iterate CHARACTERS and the intersection would be
    # empty for a reason that has nothing to do with this test's premise — a
    # vacuous pass. Assert the shape so that flattening reddens here instead.
    assert all(isinstance(v, tuple) for v in run_day.SEATS.values())

    trading_day_seats = {s for seats in run_day.SEATS.values() for s in seats}
    assert set(holders) & trading_day_seats == set(), (
        "a submit_strategy_spec holder is scheduled on the trading day: its"
        " standing surface carries this write cap at 09:00")


def test_a_registered_spec_gets_a_content_addressed_id(fund_db):
    r = _submit(fund_db, spec_payload())
    assert r["ok"] is True and r["spec_id"].startswith("spec_")
    assert _count(fund_db) == 1


def test_the_seat_is_bound_by_the_handler_not_the_payload(fund_db):
    """§3.1's input is "all strategy_specs fields except ids/timestamps", and
    `seat` is the one of them the caller does not supply — attribution comes
    from who is calling, never from what they typed."""
    r = _submit(fund_db, spec_payload())
    row = fund_db.execute("SELECT seat FROM strategy_specs WHERE spec_id = ?",
                          (r["spec_id"],)).fetchone()
    assert row["seat"] == "quant"


def test_registering_the_same_content_twice_returns_the_same_id(fund_db):
    first = _submit(fund_db, spec_payload())
    second = _submit(fund_db, spec_payload())
    assert second["spec_id"] == first["spec_id"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert _count(fund_db) == 1


def test_a_row_the_ddl_rejected_is_not_reported_as_a_duplicate(
        db_with_an_unmirrored_check):
    """`INSERT OR IGNORE` swallows a CHECK violation as quietly as a duplicate
    id, so a row count either side of the INSERT cannot tell the two apart. If
    the handler answered from the count alone it would tell the seat its spec
    was already registered when nothing was ever written — a fail-open, which
    invariant 4 forbids: an ambiguity resolves to no action, never to a guess.

    Unreachable against today's schema, and that is the point: it is held shut
    only by every DDL constraint happening to be mirrored at least as strictly
    in `StrategySpec`, a property stated nowhere and tested nowhere. This
    fixture is that property failing, which is the only honest way to reach the
    branch.
    """
    conn = db_with_an_unmirrored_check
    payload = spec_payload()
    assert payload["rebalance"] == "daily"      # the CHECK admits 'weekly' only
    r = handle_submit_strategy_spec(conn, seat="quant", args=payload,
                                    now_iso=NOW)
    assert r["ok"] is False
    assert "was not written" in r["error"]
    assert "duplicate" not in r
    assert _count(conn) == 0
    assert _count(conn, "events") == 0


def test_an_unknown_field_is_refused_and_writes_nothing(fund_db):
    """F-1. The fields ARE the hash input, so a field the model merely ignored
    would let two different specs collide on one id and the second vanish into
    INSERT OR IGNORE — a spec the researcher believes was registered, was
    not."""
    r = _submit(fund_db, spec_payload() | {"sharpe_target": 2.0})
    assert r["ok"] is False
    assert "sharpe_target" in r["error"]
    assert _count(fund_db) == 0


def test_a_missing_field_is_refused_and_writes_nothing(fund_db):
    """The other half of "no partial specs": absent is refused like unknown."""
    payload = spec_payload()
    del payload["invalidation"]
    assert _submit(fund_db, payload)["ok"] is False
    assert _count(fund_db) == 0


def test_a_seat_without_the_cap_is_refused(fund_db):
    """`exec` rather than the default seat, deliberately: `exec` holds no
    `submit_strategy_spec` cap in the shipped SEAT_CAPS, so the refusal comes
    from the real table rather than from anything this file arranges."""
    r = _submit(fund_db, spec_payload(), seat="exec")
    assert r["ok"] is False and "not granted" in r["error"]
    assert _count(fund_db) == 0


def test_changing_a_field_produces_a_different_spec_id(fund_db):
    """Spec immutability (acceptance Phase 5): a change is a new spec."""
    a = _submit(fund_db, spec_payload())
    b = _submit(fund_db, spec_payload(hypothesis="a different mechanism"))
    assert a["spec_id"] != b["spec_id"]
    assert _count(fund_db) == 2


def test_a_registration_projects_a_summary_to_research(fund_db):
    """§3.1: "projects a summary to #research". Through the events outbox, so
    a crash between the row and the post can neither lose nor duplicate it
    (invariant 6)."""
    from slackkit.render import render

    r = _submit(fund_db, spec_payload())
    rows = fund_db.execute(
        "SELECT payload FROM events WHERE kind = 'strategy_spec'").fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["spec_id"] == r["spec_id"] and payload["seat"] == "quant"
    assert render("strategy_spec", payload).channel == "#research"


def test_a_duplicate_registration_projects_nothing(fund_db):
    """The outbox posts every unposted row, so projecting a re-registration
    would announce a spec that already exists as though it were new. Slack is
    a projection of what changed, and nothing did."""
    _submit(fund_db, spec_payload())
    _submit(fund_db, spec_payload())
    assert fund_db.execute(
        "SELECT count(*) FROM events WHERE kind = 'strategy_spec'"
    ).fetchone()[0] == 1


def test_a_refused_payload_projects_nothing(fund_db):
    """Default HOLD: no row, no event."""
    _submit(fund_db, spec_payload() | {"sharpe_target": 2.0})
    _submit(fund_db, spec_payload(), seat="exec")
    assert _count(fund_db, "events") == 0
