import pytest

from gate.tickets import (create_ticket, expire_open_tickets, get_ticket,
                          open_tickets, open_tickets_without_orders,
                          validate_order)

NOW = "2026-07-06T15:30:00+00:00"        # 11:30 ET on the golden day
EXPIRY = "2026-07-06T16:00:00+00:00"     # ticket expiry
LATER = "2026-07-06T16:00:01+00:00"      # 1s past expiry
TID = "a3f90000-0000-4000-8000-000000000001"
TID2 = "a3f90000-0000-4000-8000-000000000002"


def _seed(conn, *, stop_price=None, expires=EXPIRY, max_qty=67, tid=TID,
          ticker="NVDA", run_date="2026-07-06"):
    # decisions is UNIQUE(run_date, ticker), so a second ticket on the same
    # ticker is by definition a different day's decision.
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " (?, ?, 'buy', 80, 't', 'i', ?, 'approved', ?)",
        (run_date, ticker, stop_price, NOW))
    conn.commit()
    create_ticket(conn, id=tid, decision_id=cur.lastrowid, ticker=ticker,
                  side="buy", max_qty=max_qty, stop_price=stop_price,
                  expires_at_iso=expires, now_iso=NOW)
    return cur.lastrowid


def order(**over):
    # gtc, not day: a stop-carrying order must outlive the session that placed
    # it (2026-08-19). The stopless path stays time-in-force-agnostic, and both
    # branches are asserted explicitly in the time_in_force section below.
    base = {"client_order_id": TID, "symbol": "NVDA", "side": "buy",
            "qty": 67, "type": "market", "time_in_force": "gtc"}
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
    assert "not a parameter place_stock_order accepts" in reason


def test_a_flat_stop_leg_is_read_from_a_string_like_the_real_tool_sends(fund_db):
    """Every numeric the Alpaca MCP place tool sends is a STRING, stop prices
    included — the float form is the courtesy case, not the real one. Both
    must compare equal to the ticket's REAL-typed stop_price."""
    _seed(fund_db, stop_price=168.0)
    for value in ("168.0", "168", 168.0, 168):
        ok, reason = validate_order(
            fund_db, order(order_class="oto", stop_loss_stop_price=value), NOW)
        assert ok, f"{value!r} rejected: {reason}"
    for value in ("168.01", "", "abc", None):
        ok, _ = validate_order(
            fund_db, order(order_class="oto", stop_loss_stop_price=value), NOW)
        assert not ok, f"{value!r} was accepted as the ticket's stop"
    ok, _ = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price=True), NOW)
    assert not ok, "True was accepted as the ticket's stop"


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


def test_a_stopped_ticket_authorizes_a_stop_market_and_nothing_else(fund_db):
    """Review finding, 2026-08-17 fix wave. The first pass at the flat-leg
    rewrite validated stop_loss_stop_price and computed the other legs
    without ever checking them, so extra exits rode along on a valid stop.

    stop_loss_limit_price is the dangerous one: it converts the ticket's
    authorized stop-MARKET into a stop-LIMIT, which can go unfilled straight
    through a gap-down — precisely the move the stop exists to survive. The
    gate approved a market exit; anything else is a different order."""
    _seed(fund_db, stop_price=168.0)
    for extra in ("stop_loss_limit_price", "take_profit_limit_price"):
        ok, reason = validate_order(fund_db, order(
            order_class="oto", stop_loss_stop_price="168.0",
            **{extra: "100.0"}), NOW)
        assert not ok, f"{extra} rode along on a valid stop"
        assert extra in reason
    # the stop alone still passes — the check must not deny the legal order
    ok, reason = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price="168.0"), NOW)
    assert ok, reason


def test_the_nested_shape_is_denied_on_a_stopless_ticket_too(fund_db):
    """Before the flat rewrite, a nested stop_loss on a stopless ticket was
    denied. It must stay denied: the broker has no such parameter, so
    approving it would let an order through carrying an exit instruction
    nobody validated and Alpaca would silently drop."""
    _seed(fund_db)  # stop_price NULL
    ok, reason = validate_order(
        fund_db, order(stop_loss={"stop_price": 168.0}), NOW)
    assert not ok
    assert "not a parameter place_stock_order accepts" in reason


def test_bool_is_rejected_as_a_type_not_by_numeric_coincidence(fund_db):
    """float(True) == 1.0, so a ticket whose stop happens to BE 1.0 is the
    only place that distinguishes "bool is not a price" from "the number did
    not match". Without this, the bool branch of _as_price could be deleted
    and every other stop test would stay green."""
    _seed(fund_db, stop_price=1.0)
    ok, reason = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price=True), NOW)
    assert not ok, "True was accepted as a stop of 1.0"
    ok, _ = validate_order(
        fund_db, order(order_class="oto", stop_loss_stop_price="1.0"), NOW)
    assert ok, "the real stop of 1.0 must still pass"


# ---- time_in_force: the 2026-08-19 missing-stop class ----

def test_deny_day_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """The 2026-08-19 incident, in one assertion. The 08-17 NVDA entry placed
    as an OTO whose stop leg inherited the parent's time_in_force: DAY; the
    leg expired at 16:00 ET the same day and the position sat unprotected for
    two sessions while the DB asserted a live stop at 215. A stop must outlive
    the session that created it."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(
        order_class="oto", stop_loss_stop_price="168.0",
        time_in_force="day"), NOW)
    assert not ok, "a DAY stop leg was accepted"
    assert "gtc" in reason


def test_deny_missing_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """The real tool OMITS time_in_force unless the seat passes it, and its
    default is 'day' (schema-pinned in tests/test_live_smoke.py). Absent must
    therefore deny, not fall through."""
    _seed(fund_db, stop_price=168.0)
    tool_input = order(order_class="oto", stop_loss_stop_price="168.0")
    del tool_input["time_in_force"]
    ok, reason = validate_order(fund_db, tool_input, NOW)
    assert not ok, "a missing time_in_force was accepted"
    assert "gtc" in reason


def test_deny_unreadable_time_in_force_when_the_ticket_has_a_stop(fund_db):
    """Deny-by-default on anything that is not a string: a time_in_force the
    gate cannot read is a stop lifetime the gate cannot verify (invariant 4)."""
    _seed(fund_db, stop_price=168.0)
    for value in (None, 0, 1, True, ["gtc"], {"tif": "gtc"}):
        ok, reason = validate_order(fund_db, order(
            order_class="oto", stop_loss_stop_price="168.0",
            time_in_force=value), NOW)
        assert not ok, f"{value!r} was accepted as a time_in_force"
        assert "gtc" in reason


def test_time_in_force_is_read_case_insensitively(fund_db):
    """Alpaca's enum is lowercase but a seat may well send 'GTC'. Denying that
    would be a false deny on an order that is exactly right."""
    _seed(fund_db, stop_price=168.0)
    for value in ("GTC", "Gtc", " gtc "):
        ok, reason = validate_order(fund_db, order(
            order_class="oto", stop_loss_stop_price="168.0",
            time_in_force=value), NOW)
        assert ok, f"{value!r} was denied: {reason}"


def test_a_stopless_ticket_stays_time_in_force_agnostic(fund_db):
    """The plain path is deliberately untouched: a stopless order has no leg
    to outlive the session, so requiring gtc there would be a false deny on a
    legitimate DAY order."""
    _seed(fund_db)  # stop_price NULL
    ok, reason = validate_order(fund_db, order(time_in_force="day"), NOW)
    assert ok, reason


def test_order_class_is_denied_before_time_in_force(fund_db):
    """A bracket order with a DAY tif violates both rules. order_class is the
    more specific defect and must be the reason reported, so
    test_deny_bracket_order_class_when_stop keeps testing what it names."""
    _seed(fund_db, stop_price=168.0)
    ok, reason = validate_order(fund_db, order(
        order_class="bracket", stop_loss_stop_price="168.0",
        time_in_force="day"), NOW)
    assert not ok
    assert "oto" in reason


# ---- open_tickets_without_orders: "did this ticket produce an order?" ----

def _place(conn, tid=TID, symbol="NVDA"):
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " submitted_at) VALUES (?, ?, 'buy', 67, 'submitted', ?)",
        (tid, symbol, NOW))
    conn.commit()


def test_an_open_ticket_with_no_order_is_returned(fund_db):
    _seed(fund_db)
    assert [t["id"] for t in open_tickets_without_orders(fund_db)] == [TID]


def test_a_ticket_with_an_order_row_is_excluded(fund_db):
    """invariant 5: orders.client_order_id IS the ticket id, so the existence
    of that row is the whole answer to "did the turn place it?"."""
    _seed(fund_db)
    _place(fund_db)
    assert open_tickets_without_orders(fund_db) == []


@pytest.mark.parametrize("status", ["consumed", "expired"])
def test_a_ticket_that_is_no_longer_open_is_excluded(fund_db, status):
    """Only status='open' is a ticket the turn still owed an order for; a
    consumed or already-swept one is settled business."""
    _seed(fund_db)
    fund_db.execute("UPDATE tickets SET status=? WHERE id=?", (status, TID))
    fund_db.commit()
    assert open_tickets_without_orders(fund_db) == []


def test_a_past_ttl_ticket_is_still_returned_where_open_tickets_is_empty(fund_db):
    """The point of the function (issue #40). open_tickets filters on the
    clock — correct for "what may the trader still act on?", pinned above at
    test_open_tickets_excludes_expired_even_before_sweep. But a turn that
    overruns the TTL leaves a ticket still status='open' with no order, and
    that filter deletes exactly that row from the answer. This question has no
    clock in it, so the signature has no clock in it either."""
    _seed(fund_db)
    assert open_tickets(fund_db, LATER) == []
    assert [t["id"] for t in open_tickets_without_orders(fund_db)] == [TID]


def test_only_the_ticket_with_no_order_of_its_own_is_returned(fund_db):
    """Two live tickets, one order. Every other test here holds a single
    ticker, which cannot tell a per-ticket answer from a per-symbol one."""
    _seed(fund_db)                              # NVDA, order placed
    _place(fund_db)
    _seed(fund_db, ticker="AMD", tid=TID2)      # AMD, nothing placed
    assert [t["id"] for t in open_tickets_without_orders(fund_db)] == [TID2]


def test_a_ticket_is_not_suppressed_by_another_tickets_same_symbol_order(fund_db):
    """The join key is the ticket ID, not the symbol (invariant 5:
    orders.client_order_id IS the ticket id). Yesterday's NVDA order is keyed
    by yesterday's ticket and says nothing about whether today's NVDA ticket
    was placed. Correlating on o.symbol = t.ticker instead still passes every
    single-ticker test in this file while silently deleting today's ticket
    from the answer — the exact suppression issue #40 exists to surface."""
    _seed(fund_db, run_date="2026-07-03")       # yesterday's ticket...
    _place(fund_db)                             # ...and its NVDA order row
    fund_db.execute("UPDATE tickets SET status='consumed' WHERE id=?", (TID,))
    fund_db.commit()
    _seed(fund_db, tid=TID2)                    # today's NVDA ticket, no order
    assert [t["id"] for t in open_tickets_without_orders(fund_db)] == [TID2]


def test_the_predicate_returns_only_the_repair_fields(fund_db):
    """Narrow on purpose: the ticket a repair pass would re-place, and what
    that ticket authorizes. No field here is without a reader."""
    _seed(fund_db)
    assert sorted(open_tickets_without_orders(fund_db)[0]) == [
        "id", "max_qty", "side", "ticker"]
