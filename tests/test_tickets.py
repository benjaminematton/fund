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


def test_validate_happy_path_oto(fund_db):
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price="168.0"), NOW)
    assert ok, reason


def test_validate_happy_real_tool_shape(fund_db):
    """The real alpaca-mcp-server place tool sends qty as a STRING and omits
    type/time_in_force (captured live 2026-07-12). The gate must accept it —
    BUG C: it used to deny "qty must be a positive integer", blocking every
    real order once the hook actually fired."""
    _seed(fund_db)
    real_input = {"client_order_id": TID, "symbol": "NVDA", "side": "buy",
                  "qty": "67"}
    ok, reason = validate_order(fund_db, real_input, NOW)
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
        fund_db, order(order_class="oto", stop_loss_stop_price="150.0"), NOW)
    assert not ok and "stop" in reason


def test_deny_stop_leg_on_stopless_ticket(fund_db):
    _seed(fund_db)  # stop_price NULL
    ok, reason = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price="168.0"), NOW)
    assert not ok and "stop" in reason


def test_deny_missing_stop_leg_when_ticket_has_stop(fund_db):
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(), NOW)
    assert not ok and "stop" in reason


def test_deny_bracket_order_class_when_stop(fund_db):
    """BUG D: a stop exit places at Alpaca as order_class 'oto', never
    'bracket' — bracket 422s ("bracket orders require take_profit"), and the
    ticket has no take-profit field. The stop leg here MATCHES the ticket, so
    order_class is the only thing under test: the gate must fail-fast on the
    unplaceable class rather than pass it to the broker (invariant 4)."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(
        fund_db, order(order_class="bracket", stop_loss_stop_price="168.0"), NOW)
    assert not ok and "oto" in reason


# A digit-string qty ("67") is VALID (real tool shape). But floats, negatives,
# and non-numeric strings are never whole shares — deny (invariant 4).
@pytest.mark.parametrize("bad", [
    {"client_order_id": None}, {"qty": 67.5}, {"qty": 0}, {"qty": -3},
    {"qty": "67.5"}, {"qty": "-3"}, {"qty": "abc"}, {"qty": ""},
    {"side": "sell"}, {"side": None},
])
def test_deny_malformed_or_mismatched_input(fund_db, bad):
    _seed(fund_db)
    ok, _ = validate_order(fund_db, order(**bad), NOW)
    assert not ok  # invariant 4: malformed input never resolves to a guess


def test_deny_non_dict_input(fund_db):
    ok, _ = validate_order(fund_db, "buy NVDA lol", NOW)
    assert not ok


# --- 2026-08-17: the stop leg is FLAT, and the nested shape is not a stop ---

def test_the_nested_stop_leg_is_not_accepted_as_a_stop(fund_db):
    """THE first-live-day bug, as a test. The gate used to read a nested
    `stop_loss: {stop_price: ...}` that the real place_stock_order has never
    exposed. Both sides of the mismatch were undeliverable: the gate denied
    the broker's real flat shape, and the broker rejected the gate's assumed
    nested one, so a ticket carrying a stop_price could not be filled at all
    while the whole offline suite stayed green.

    The nested object must now read as NO stop leg — the order is denied for
    missing the ticket's stop, never silently approved on a key the broker
    would ignore."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(
        fund_db, order(order_class="oto", stop_loss={"stop_price": 168.0}), NOW)
    assert not ok
    assert "stop_loss_stop_price" in reason


def test_a_flat_stop_leg_is_read_from_a_string_like_the_real_tool_sends(fund_db):
    """Every numeric the Alpaca MCP place tool sends is a STRING, stop prices
    included — the float form is the courtesy case, not the real one. Both
    must compare equal to the ticket's REAL-typed stop_price."""
    _seed(fund_db, stop_price=168.0)
    for value in ("168.0", "168", 168.0, 168):
        ok, reason = validate_order(
            fund_db, order(order_class="oto", stop_loss_stop_price=value), NOW)
        assert ok, f"{value!r} rejected: {reason}"
    for value in ("168.01", "", "abc", None, True):
        ok, _ = validate_order(
            fund_db, order(order_class="oto", stop_loss_stop_price=value), NOW)
        assert not ok, f"{value!r} was accepted as the ticket's stop"


def test_a_stopless_ticket_refuses_every_exit_leg_not_just_the_stop(fund_db):
    """A take-profit smuggled onto an unstopped ticket is as much an
    unauthorised exit as a stop is; the gate names every leg the real tool
    exposes rather than only the one that caused the outage."""
    _seed(fund_db)  # stop_price NULL
    for leg in ("stop_loss_stop_price", "stop_loss_limit_price",
                "take_profit_limit_price"):
        ok, reason = validate_order(fund_db, order(**{leg: "168.0"}), NOW)
        assert not ok, f"{leg} was allowed on a stopless ticket"
        assert leg in reason
