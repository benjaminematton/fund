import pytest

from gate.tickets import (create_ticket, expire_open_tickets, get_ticket,
                          open_tickets, validate_order)

NOW = "2026-07-06T15:30:00+00:00"        # 11:30 ET on the golden day
EXPIRY = "2026-07-06T16:00:00+00:00"     # ticket expiry
LATER = "2026-07-06T16:00:01+00:00"      # 1s past expiry
TID = "a3f90000-0000-4000-8000-000000000001"


def _seed(conn, *, stop_price=None, expires=EXPIRY, max_qty=67, tid=TID):
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " ('2026-07-06', 'NVDA', 'buy', 80, 't', 'i', ?, 'approved', ?)",
        (stop_price, NOW))
    conn.commit()
    create_ticket(conn, id=tid, decision_id=cur.lastrowid, ticker="NVDA",
                  side="buy", max_qty=max_qty, stop_price=stop_price,
                  expires_at_iso=expires, now_iso=NOW)
    return cur.lastrowid


def order(**over):
    base = {"client_order_id": TID, "symbol": "NVDA", "side": "buy",
            "qty": 67, "type": "market", "time_in_force": "day"}
    base.update(over)
    return base


def test_create_and_get_ticket(fund_db):
    _seed(fund_db)
    t = get_ticket(fund_db, TID)
    assert t["status"] == "open" and t["max_qty"] == 67 and t["stop_price"] is None


def test_create_ticket_validates_via_model(fund_db):
    with pytest.raises(Exception):
        _seed(fund_db, max_qty=0)


def test_open_tickets_excludes_expired_even_before_sweep(fund_db):
    _seed(fund_db)
    assert [t["id"] for t in open_tickets(fund_db, NOW)] == [TID]
    assert open_tickets(fund_db, LATER) == []


def test_expiry_sweep_expires_ticket_and_decision(fund_db):
    did = _seed(fund_db)
    assert expire_open_tickets(fund_db, NOW) == []          # not yet expired
    assert expire_open_tickets(fund_db, LATER) == [TID]
    assert get_ticket(fund_db, TID)["status"] == "expired"
    d = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert d["status"] == "expired"
    assert expire_open_tickets(fund_db, LATER) == []        # idempotent


def test_validate_happy_path_plain(fund_db):
    _seed(fund_db)
    ok, reason = validate_order(fund_db, order(), NOW)
    assert ok, reason


def test_validate_happy_path_bracket(fund_db):
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(
        fund_db, order(order_class="bracket", stop_loss={"stop_price": 168.0}), NOW)
    assert ok, reason


# the five acceptance deny cases (acceptance.md Phase 1, "Hook")
def test_deny_no_ticket(fund_db):
    ok, reason = validate_order(fund_db, order(client_order_id="tkt-none"), NOW)
    assert not ok and "no gate ticket" in reason


def test_deny_expired_ticket(fund_db):
    _seed(fund_db)
    ok, reason = validate_order(fund_db, order(), LATER)
    assert not ok and "expired" in reason


def test_deny_qty_over_max(fund_db):
    _seed(fund_db)
    ok, reason = validate_order(fund_db, order(qty=105), NOW)
    assert not ok and "max_qty" in reason


def test_deny_wrong_symbol(fund_db):
    _seed(fund_db)
    ok, reason = validate_order(fund_db, order(symbol="AAPL"), NOW)
    assert not ok and "symbol" in reason


def test_deny_stop_leg_mismatch(fund_db):
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(
        fund_db, order(order_class="bracket", stop_loss={"stop_price": 150.0}), NOW)
    assert not ok and "stop" in reason


def test_deny_stop_leg_on_stopless_ticket(fund_db):
    _seed(fund_db)  # stop_price NULL
    ok, reason = validate_order(
        fund_db, order(order_class="bracket", stop_loss={"stop_price": 168.0}), NOW)
    assert not ok and "stop" in reason


def test_deny_missing_stop_leg_when_ticket_has_stop(fund_db):
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(), NOW)
    assert not ok and "stop" in reason


@pytest.mark.parametrize("bad", [
    {"client_order_id": None}, {"qty": "67"}, {"qty": 67.5}, {"qty": 0},
    {"qty": -3}, {"side": "sell"}, {"side": None},
])
def test_deny_malformed_or_mismatched_input(fund_db, bad):
    _seed(fund_db)
    ok, _ = validate_order(fund_db, order(**bad), NOW)
    assert not ok  # invariant 4: malformed input never resolves to a guess


def test_deny_non_dict_input(fund_db):
    ok, _ = validate_order(fund_db, "buy NVDA lol", NOW)
    assert not ok
