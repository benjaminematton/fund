import json
from pathlib import Path

from tests.fake_alpaca import FakeAlpaca

MARKET = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" /
     "golden-day-market.json").read_text())


def _broker(mode="fill"):
    return FakeAlpaca(MARKET["prices"], MARKET["fill_prices"], mode=mode)


def order(**over):
    base = {"client_order_id": "a3f90000-0000-4000-8000-000000000001",
            "symbol": "NVDA", "side": "buy", "qty": 67, "type": "market",
            "time_in_force": "day"}
    base.update(over)
    return base


def test_market_fixture_matches_golden_day():
    assert MARKET["prices"]["NVDA"] == 180.00
    assert MARKET["fill_prices"]["NVDA"] == 180.14
    assert MARKET["equity"] == 100000.0 and MARKET["cash"] == 30000.0


def test_instant_fill_at_fixture_price():
    b = _broker(mode="instant")
    resp = b.place_order(order())
    assert resp["status"] == "filled"
    assert resp["filled_qty"] == 67 and resp["filled_avg_price"] == 180.14
    assert resp["client_order_id"] == order()["client_order_id"]


def test_duplicate_client_order_id_422_and_original_untouched():
    b = _broker(mode="instant")
    first = b.place_order(order())
    dup = b.place_order(order(qty=1))
    assert dup == {"error": "client_order_id must be unique", "status_code": 422}
    assert len(b.place_attempts) == 2
    got = b.get_order_by_client_order_id(order()["client_order_id"])
    # place_order's direct return stays native (67); get_order_by_client_order_id
    # returns the live string shape ("67") — authorized change, see ruling.
    assert first["filled_qty"] == 67
    assert got["filled_qty"] == "67"  # reconcile path, §5.1


def test_oto_stop_leg_shape_recorded():
    b = _broker()
    resp = b.place_order(order(order_class="oto",
                               stop_loss={"stop_price": 168.0}))
    assert resp["order_class"] == "oto"
    assert resp["stop_loss"] == {"stop_price": 168.0}
    assert b.place_attempts[0]["stop_loss"] == {"stop_price": 168.0}


def test_bracket_without_take_profit_is_422():
    """Real Alpaca rejects a bracket order lacking a take_profit leg (BUG D). A
    stop exit is an 'oto', never a bracket — the fake mirrors the 422 so a
    hand-written recording can't resurrect the fiction. The attempt is recorded
    but no order is stored."""
    b = _broker()
    resp = b.place_order(order(order_class="bracket",
                               stop_loss={"stop_price": 168.0}))
    assert resp["status_code"] == 422 and "take_profit" in resp["error"]
    assert b.get_order_by_client_order_id(order()["client_order_id"]) is None


def test_get_unknown_coid_is_none():
    assert _broker().get_order_by_client_order_id("nope") is None


def test_market_order_acks_accepted_then_fills_on_tick():
    b = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14})
    ack = b.place_order({"client_order_id": "c1", "symbol": "NVDA",
                         "side": "buy", "qty": 67})
    assert ack["status"] == "accepted"
    assert ack["filled_qty"] == 0 and ack["filled_avg_price"] is None
    b.tick()
    o = b.get_order_by_client_order_id("c1")
    # authorized change: get_order_by_client_order_id reproduces the LIVE
    # STRING shape (alpaca-py 0.44 Order.filled_qty/filled_avg_price are
    # Optional[str]) — was `o["filled_qty"] == 67` / `180.14`.
    assert o["status"] == "filled" and o["filled_qty"] == "67"
    assert o["filled_avg_price"] == "180.14"


def test_cancel_marks_order_canceled_and_records_the_attempt():
    b = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    b.place_order({"client_order_id": "c1", "symbol": "NVDA", "side": "buy", "qty": 5})
    b.cancel_order("c1")
    assert b.cancel_attempts == ["c1"]
    o = b.get_order_by_client_order_id("c1")
    assert o["status"] == "canceled" and o["filled_avg_price"] is None


def test_cancel_unknown_or_terminal_order_raises():
    """The live broker 404s an unknown client id and 422s a terminal order —
    the fake raises so the caller's fail-closed path is exercised offline."""
    b = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14}, mode="instant")
    try:
        b.cancel_order("nope")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    b.place_order({"client_order_id": "c1", "symbol": "NVDA", "side": "buy", "qty": 5})
    try:
        b.cancel_order("c1")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_fill_during_cancel_mode_fills_in_the_race():
    """The order never fills on tick(), then fills as the cancel arrives."""
    b = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14}, mode="fill_during_cancel")
    b.place_order({"client_order_id": "c1", "symbol": "NVDA", "side": "buy", "qty": 67})
    for _ in range(10): b.tick()
    assert b.get_order_by_client_order_id("c1")["status"] == "accepted"
    b.cancel_order("c1")
    o = b.get_order_by_client_order_id("c1")
    # live string shape, as everywhere else on this method
    assert o["status"] == "filled" and o["filled_qty"] == "67"
    assert o["filled_avg_price"] == "180.14"


def test_never_fill_and_partial_modes():
    b = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    b.place_order({"client_order_id": "c1", "symbol": "NVDA", "side": "buy", "qty": 5})
    for _ in range(10): b.tick()
    assert b.get_order_by_client_order_id("c1")["status"] == "accepted"
    p = FakeAlpaca({"NVDA": 180.0}, mode="partial")
    p.place_order({"client_order_id": "c2", "symbol": "NVDA", "side": "buy", "qty": 10})
    p.tick()
    o = p.get_order_by_client_order_id("c2")
    # authorized change: filled_qty is the live numeric string shape.
    assert o["status"] == "partially_filled" and 0 < int(o["filled_qty"]) < 10
