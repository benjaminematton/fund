"""state/worklist.py — Lane B row ops (resident-seats R1, #228)."""
import re

import pytest

from fundbt.hashing import work_id
from state import worklist
from state.db import connect
from state.transition import StaleTransition

NOW = "2026-07-06T15:30:00+00:00"
LATER = "2026-07-06T20:00:00+00:00"


def test_work_id_is_deterministic_prefixed_and_keyed_on_all_three_parts():
    a = work_id("spec_review", "spec_abc", "0")
    assert re.fullmatch(r"wk_[0-9a-f]{16}", a)
    assert a == work_id("spec_review", "spec_abc", "0")
    assert a != work_id("spec_review", "spec_abc", "1")      # attempts differ
    assert a != work_id("alert_triage", "spec_abc", "0")     # kind differs
    assert a != work_id("spec_review", "spec_xyz", "0")      # subject differs


def _enqueue(conn, **kw):
    args = dict(kind="spec_review", producer="orchestrator", seat="critic",
                subject="spec_abc", expires_at=LATER, now_iso=NOW)
    args.update(kw)
    return worklist.enqueue(conn, **args)


def _row(conn, wid):
    return conn.execute("SELECT * FROM worklist WHERE work_id=?", (wid,)).fetchone()


def test_enqueue_writes_an_open_row_with_sorted_json_payload(fund_db):
    wid = _enqueue(fund_db, payload={"b": 1, "a": 2})
    row = _row(fund_db, wid)
    assert row["status"] == "open"
    assert row["payload"] == '{"a": 2, "b": 1}'
    assert row["attempts"] == 0
    assert row["not_before"] is None
    assert (row["claimed_at"], row["claim_expires_at"], row["finished_at"]) == (None, None, None)
    assert row["created_at"] == NOW


def test_enqueue_is_idempotent_on_kind_subject_attempts(fund_db):
    a = _enqueue(fund_db)
    b = _enqueue(fund_db, payload={"ignored": True})
    assert a == b
    assert fund_db.execute("SELECT COUNT(*) c FROM worklist").fetchone()["c"] == 1
    assert _row(fund_db, a)["payload"] == "{}"          # first write wins


def test_reenqueue_with_attempts_incremented_is_a_new_row(fund_db):
    a = _enqueue(fund_db)
    b = _enqueue(fund_db, attempts=1)
    assert a != b and _row(fund_db, b)["attempts"] == 1


@pytest.mark.parametrize("producer,kind", [
    ("slack_listener", "mention"),      # R3's pair, not registered in R1
    ("orchestrator", "alert_triage"),   # wrong producer for the kind
    ("critic", "spec_review"),          # a seat name is never a producer
])
def test_enqueue_refuses_a_pair_outside_the_allow_list(fund_db, producer, kind):
    with pytest.raises(worklist.DisallowedWork):
        _enqueue(fund_db, producer=producer, kind=kind)
    assert fund_db.execute("SELECT COUNT(*) c FROM worklist").fetchone()["c"] == 0


def test_claim_next_takes_the_oldest_claimable_row_and_sets_the_lease(fund_db):
    # Newer row inserted FIRST: rowid order would return it, only ORDER BY
    # created_at returns the older one.
    _enqueue(fund_db, subject="s2", now_iso="2026-07-06T15:10:00+00:00")
    old = _enqueue(fund_db, subject="s1", now_iso="2026-07-06T15:00:00+00:00")
    row = worklist.claim_next(fund_db, now_iso=NOW,
                              lease_until_iso="2026-07-06T15:35:00+00:00")
    assert row["work_id"] == old
    assert (row["status"], row["claimed_at"], row["claim_expires_at"]) == (
        "claimed", NOW, "2026-07-06T15:35:00+00:00")


def test_claim_next_skips_not_before_in_the_future_and_expired_rows(fund_db):
    _enqueue(fund_db, subject="debounced", not_before="2026-07-06T16:00:00+00:00")
    _enqueue(fund_db, subject="stale", expires_at="2026-07-06T15:00:00+00:00")
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER) is None
    ready = _enqueue(fund_db, subject="ready", not_before=NOW)   # not_before == now: claimable
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)["work_id"] == ready


def test_claim_next_filters_by_seat(fund_db):
    _enqueue(fund_db, seat="critic", subject="c")
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER, seat="pm") is None
    assert worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER, seat="critic") is not None


def test_concurrent_claimers_on_one_row_yield_exactly_one_claim(tmp_path, monkeypatch):
    """CAS under concurrent claim (design R1 acceptance). Two connections to one
    file. Claimer `a` SELECTs the row as open; before its CAS runs, claimer `b`
    claims the same row to completion. `a`'s UPDATE ... WHERE status='open' then
    matches nothing, so `a` gets no row — exactly one claim. Without that WHERE
    guard `a` would overwrite `b`'s claim and both would return the row."""
    path = tmp_path / "fund.sqlite"
    a, b = connect(path), connect(path)
    wid = _enqueue(a)
    real = worklist.try_transition
    raced: list = []

    def interleave(conn, *args, **kw):
        if conn is a and not raced:          # a has SELECTed; b races ahead
            raced.append(worklist.claim_next(b, now_iso=NOW, lease_until_iso=LATER))
        return real(conn, *args, **kw)

    monkeypatch.setattr(worklist, "try_transition", interleave)
    ra = worklist.claim_next(a, now_iso=NOW, lease_until_iso=LATER)
    assert raced[0] is not None and raced[0]["work_id"] == wid   # b won
    assert ra is None                                            # a's CAS lost
    assert b.execute("SELECT COUNT(*) c FROM worklist WHERE status='claimed'").fetchone()["c"] == 1
    assert _row(b, wid)["claim_expires_at"] == LATER
    a.close(); b.close()


def test_finish_and_fail_are_claimed_only_and_stamp_finished_at(fund_db):
    wid = _enqueue(fund_db)
    with pytest.raises(StaleTransition):
        worklist.finish(fund_db, wid, NOW)            # open -> done is not an edge from open
    worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)
    worklist.finish(fund_db, wid, "2026-07-06T15:31:00+00:00")
    row = _row(fund_db, wid)
    assert (row["status"], row["finished_at"]) == ("done", "2026-07-06T15:31:00+00:00")
    with pytest.raises(StaleTransition):
        worklist.fail(fund_db, wid, NOW)              # done is terminal


def test_fail_moves_claimed_to_failed(fund_db):
    wid = _enqueue(fund_db)
    worklist.claim_next(fund_db, now_iso=NOW, lease_until_iso=LATER)
    worklist.fail(fund_db, wid, NOW)
    assert _row(fund_db, wid)["status"] == "failed"
