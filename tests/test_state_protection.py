"""The protection log: an append-only record of what the fund SAW protecting
a position. Never a source of what protection exists — that stays a broker
read in orchestrator/protection.py:_covering_qty (ADR-0004's standing rule)."""

from orchestrator.clock import iso
from state.protection import log_observed


def _leg(**kw):
    base = {"symbol": "NVDA", "side": "sell", "qty": "80", "type": "stop",
            "status": "new", "id": "alp-0002", "client_order_id": "t1-stop",
            "stop_price": "215.0", "expires_at": "2026-11-17T21:00:00+00:00"}
    return {**base, **kw}


def test_a_protective_order_is_logged(fund_db, sim_clock):
    now = iso(sim_clock.now())
    assert log_observed(fund_db, [_leg()], now_iso=now) == [f"alp-0002@{now}"]
    row = fund_db.execute("SELECT * FROM protection").fetchone()
    assert (row["symbol"], row["qty"], row["stop_price"]) == ("NVDA", 80, 215.0)
    assert row["alpaca_order_id"] == "alp-0002"
    assert row["client_order_id"] == "t1-stop"
    assert row["provenance_kind"] == "observed"
    assert row["broker_expires_at"] == "2026-11-17T21:00:00+00:00"
    assert row["observed_at"] == now


def test_the_id_is_derived_from_the_order_and_the_observation(fund_db):
    """The id is a function of (alpaca_order_id, observed_at), not minted.
    That is what keeps assert_positions_protected's signature untouched
    (review 3, C1) and sim-day deterministic without sharing ctx.id_factory
    with tickets (review 3, L2). Pinned so a later tidy-up cannot quietly
    reintroduce a factory."""
    now = "2026-08-20T20:05:00+00:00"
    log_observed(fund_db, [_leg()], now_iso=now)
    assert fund_db.execute(
        "SELECT id FROM protection").fetchone()["id"] == f"alp-0002@{now}"


def test_the_same_run_logs_one_row_per_order(fund_db, sim_clock):
    """assert_positions_protected re-reads after its nap; the second call must
    not double-log the same observation. The empty return is the signal that
    nothing was written — which a caller cannot derive from the ids alone."""
    now = iso(sim_clock.now())
    log_observed(fund_db, [_leg()], now_iso=now)
    assert log_observed(fund_db, [_leg()], now_iso=now) == []
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_later_run_logs_a_second_observation(fund_db):
    """This is a LOG. The same order seen on two days is two rows — that is
    what makes observed_at meaningful rather than decorative."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    log_observed(fund_db, [_leg()], now_iso="2026-08-21T20:05:00+00:00")
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM protection").fetchone()["c"] == 2


def test_an_order_that_vanished_leaves_the_log_alone(fund_db):
    """Nothing is ever closed or rewritten. A stop that dies simply stops
    appearing in later runs; the earlier row stays exactly as written, because
    a log records what was seen and never revises it."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    assert log_observed(fund_db, [], now_iso="2026-08-21T20:05:00+00:00") == []
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_non_protective_order_is_not_logged(fund_db, sim_clock):
    """A sell LIMIT is a take-profit: it caps the upside and leaves the
    downside exposed. Same predicate as STOP_TYPES."""
    assert log_observed(fund_db, [_leg(type="limit", stop_price=None)],
                        now_iso=iso(sim_clock.now())) == []


def test_a_buy_stop_is_not_logged(fund_db, sim_clock):
    """CLOSING_SIDE, not just STOP_TYPES. A buy stop on a long position is an
    entry, not protection; counting it would inflate cover against a position
    it does nothing to protect."""
    assert log_observed(fund_db, [_leg(side="buy")],
                        now_iso=iso(sim_clock.now())) == []


def test_an_unreadable_order_is_skipped_and_nothing_else_changes(fund_db):
    """Invariant 4. Skipping costs one observation; under revision 2's sweep
    it permanently closed a live row with no way back (review 2, N3). Seed a
    prior row so that regression could be seen if it ever returned.

    stop_price is NOT in this list — it is nullable, because trailing_stop
    carries no stop price (review 3, M1)."""
    log_observed(fund_db, [_leg()], now_iso="2026-08-20T20:05:00+00:00")
    later = "2026-08-21T20:05:00+00:00"
    for bad in ({"qty": "eighty"}, {"qty": None}, {"id": None}):
        assert log_observed(fund_db, [_leg(**bad)], now_iso=later) == []
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM protection").fetchone()["c"] == 1


def test_a_trailing_stop_is_logged_with_no_stop_price(fund_db, sim_clock):
    """_STOP_TYPES counts trailing_stop and _covering_qty counts it toward
    cover, so the log must too. A NOT NULL stop_price would silently skip an
    order the alert's own number includes (review 3, M1)."""
    now = iso(sim_clock.now())
    assert log_observed(fund_db, [_leg(type="trailing_stop", stop_price=None)],
                        now_iso=now) != []
    assert fund_db.execute(
        "SELECT stop_price FROM protection").fetchone()["stop_price"] is None
