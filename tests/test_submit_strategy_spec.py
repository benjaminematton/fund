"""submit_strategy_spec — strategy-contracts.md §3.1.

extra="forbid" and no partial specs: a malformed payload writes nothing.

THE HANDLER IS CALLED DIRECTLY, never through a built server, because this
tool is deliberately unregistered: the CEO's G-2(iii) ruling grants the cap to
no seat and adds no `@tool`, so there is no MCP surface to reach. The §4 row
carries status `not served` and `test_no_shipped_seat_can_call_this_tool`
below pins that as a fact of the shipped table rather than an omission.

The cap is monkeypatched onto `analyst` for the write-path tests. That is not
"adding a cap for testing" in the sense Step 4 forbids — SEAT_CAPS on disk is
untouched, and the guard under test is the handler's own `_can`, which is
reached in exactly this way by
test_tool_surface_canon.py::test_the_handler_refuses_even_when_registration_is_wrong.
Without it every handler call refuses and the write path would go untested.
"""
import json

import pytest

from agents.tools import fund_server
from agents.tools.fund_server import SEAT_CAPS, handle_submit_strategy_spec
from tests.synthetic import spec_payload

NOW = "2026-08-29T14:00:00Z"


@pytest.fixture
def granted(monkeypatch):
    """`analyst` holds the cap for the duration of one test, and only there."""
    monkeypatch.setitem(fund_server.SEAT_CAPS, "analyst",
                        SEAT_CAPS["analyst"] | {"submit_strategy_spec"})


def _submit(fund_db, payload, seat="analyst"):
    return handle_submit_strategy_spec(
        fund_db, seat=seat, args=payload, now_iso=NOW)


def _count(fund_db, table="strategy_specs"):
    return fund_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_no_shipped_seat_can_call_this_tool():
    """G-2(iii), asserted rather than intended. The tool ships with a handler,
    a §4 row and no caller; a cap granted here without the charter and the
    schedule that justify it would widen a trading seat's write surface with a
    tool nothing drives."""
    holders = [s for s, caps in SEAT_CAPS.items()
               if "submit_strategy_spec" in caps]
    assert holders == []


def test_a_registered_spec_gets_a_content_addressed_id(granted, fund_db):
    r = _submit(fund_db, spec_payload())
    assert r["ok"] is True and r["spec_id"].startswith("spec_")
    assert _count(fund_db) == 1


def test_the_seat_is_bound_by_the_handler_not_the_payload(granted, fund_db):
    """§3.1's input is "all strategy_specs fields except ids/timestamps", and
    `seat` is the one of them the caller does not supply — attribution comes
    from who is calling, never from what they typed."""
    r = _submit(fund_db, spec_payload())
    row = fund_db.execute("SELECT seat FROM strategy_specs WHERE spec_id = ?",
                          (r["spec_id"],)).fetchone()
    assert row["seat"] == "analyst"


def test_registering_the_same_content_twice_returns_the_same_id(granted,
                                                                fund_db):
    first = _submit(fund_db, spec_payload())
    second = _submit(fund_db, spec_payload())
    assert second["spec_id"] == first["spec_id"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert _count(fund_db) == 1


def test_an_unknown_field_is_refused_and_writes_nothing(granted, fund_db):
    """F-1. The fields ARE the hash input, so a field the model merely ignored
    would let two different specs collide on one id and the second vanish into
    INSERT OR IGNORE — a spec the researcher believes was registered, was
    not."""
    r = _submit(fund_db, spec_payload() | {"sharpe_target": 2.0})
    assert r["ok"] is False
    assert "sharpe_target" in r["error"]
    assert _count(fund_db) == 0


def test_a_missing_field_is_refused_and_writes_nothing(granted, fund_db):
    """The other half of "no partial specs": absent is refused like unknown."""
    payload = spec_payload()
    del payload["invalidation"]
    assert _submit(fund_db, payload)["ok"] is False
    assert _count(fund_db) == 0


def test_a_seat_without_the_cap_is_refused(granted, fund_db):
    r = _submit(fund_db, spec_payload(), seat="exec")
    assert r["ok"] is False and "not granted" in r["error"]
    assert _count(fund_db) == 0


def test_changing_a_field_produces_a_different_spec_id(granted, fund_db):
    """Spec immutability (acceptance Phase 5): a change is a new spec."""
    a = _submit(fund_db, spec_payload())
    b = _submit(fund_db, spec_payload(hypothesis="a different mechanism"))
    assert a["spec_id"] != b["spec_id"]
    assert _count(fund_db) == 2


def test_a_registration_projects_a_summary_to_research(granted, fund_db):
    """§3.1: "projects a summary to #research". Through the events outbox, so
    a crash between the row and the post can neither lose nor duplicate it
    (invariant 6)."""
    from slackkit.render import render

    r = _submit(fund_db, spec_payload())
    rows = fund_db.execute(
        "SELECT payload FROM events WHERE kind = 'strategy_spec'").fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["spec_id"] == r["spec_id"] and payload["seat"] == "analyst"
    assert render("strategy_spec", payload).channel == "#research"


def test_a_duplicate_registration_projects_nothing(granted, fund_db):
    """The outbox posts every unposted row, so projecting a re-registration
    would announce a spec that already exists as though it were new. Slack is
    a projection of what changed, and nothing did."""
    _submit(fund_db, spec_payload())
    _submit(fund_db, spec_payload())
    assert fund_db.execute(
        "SELECT count(*) FROM events WHERE kind = 'strategy_spec'"
    ).fetchone()[0] == 1


def test_a_refused_payload_projects_nothing(granted, fund_db):
    """Default HOLD: no row, no event."""
    _submit(fund_db, spec_payload() | {"sharpe_target": 2.0})
    _submit(fund_db, spec_payload(), seat="exec")
    assert _count(fund_db, "events") == 0
