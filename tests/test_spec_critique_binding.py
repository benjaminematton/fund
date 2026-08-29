"""The verdict a G1 turn writes is bound to the spec it was SHOWN.

submit_spec_critique is write-once, so a verdict written for the wrong
spec makes that spec permanently unreviewable through any shipped path.
Detection after an irreversible write is not a mitigation, which is why
this is a binding (strategy-contracts.md §3.4) and not a post-hoc check.
"""
from agents.tools.fund_server import handle_submit_spec_critique
from tests.synthetic import make_spec, seed_spec_row


def _submit(fund_db, spec_id, expected_spec_id):
    return handle_submit_spec_critique(
        fund_db, seat="critic",
        args={"spec_id": spec_id, "verdict": "clear", "objections": []},
        now_iso="2026-08-29T16:35:00Z",
        charter_version="v1", model_id="claude-opus-5",
        expected_spec_id=expected_spec_id)


def _critique_count(fund_db) -> int:
    return fund_db.execute(
        "SELECT count(*) FROM strategy_critiques").fetchone()[0]


def test_an_unbound_turn_refuses_to_write(fund_db):
    """None is the default, so an un-threaded caller must fail closed."""
    sid = seed_spec_row(fund_db)
    r = _submit(fund_db, sid, expected_spec_id=None)
    assert r["ok"] is False
    assert "not bound" in r["error"]
    assert _critique_count(fund_db) == 0


def test_a_verdict_for_a_spec_the_turn_was_not_shown_is_refused(fund_db):
    """The defect this binding exists for: A shown, B written."""
    shown = seed_spec_row(fund_db, make_spec("spec_shown00000000a"))
    other = seed_spec_row(fund_db, make_spec("spec_other00000000b"))
    assert shown != other
    r = _submit(fund_db, other, expected_spec_id=shown)
    assert r["ok"] is False
    assert other in r["error"] and shown in r["error"]
    assert _critique_count(fund_db) == 0


def test_the_matching_verdict_is_written(fund_db):
    sid = seed_spec_row(fund_db)
    assert _submit(fund_db, sid, expected_spec_id=sid)["ok"] is True
    assert fund_db.execute(
        "SELECT spec_id FROM strategy_critiques").fetchone()["spec_id"] == sid
