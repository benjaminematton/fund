import json
from pathlib import Path

from tests.fake_alpaca import FakeAlpaca

MARKET = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" /
     "golden-day-market.json").read_text())


def _broker():
    return FakeAlpaca(MARKET["prices"], MARKET["fill_prices"])


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
    b = _broker()
    resp = b.place_order(order())
    assert resp["status"] == "filled"
    assert resp["filled_qty"] == 67 and resp["filled_avg_price"] == 180.14
    assert resp["client_order_id"] == order()["client_order_id"]


def test_duplicate_client_order_id_422_and_original_untouched():
    b = _broker()
    first = b.place_order(order())
    dup = b.place_order(order(qty=1))
    assert dup == {"error": "client_order_id must be unique", "status_code": 422}
    assert len(b.place_attempts) == 2
    got = b.get_order_by_client_order_id(order()["client_order_id"])
    assert got["filled_qty"] == first["filled_qty"] == 67  # reconcile path, §5.1


def test_bracket_order_shape_recorded():
    b = _broker()
    resp = b.place_order(order(order_class="bracket",
                               stop_loss={"stop_price": 168.0}))
    assert resp["order_class"] == "bracket"
    assert resp["stop_loss"] == {"stop_price": 168.0}
    assert b.place_attempts[0]["stop_loss"] == {"stop_price": 168.0}


def test_get_unknown_coid_is_none():
    assert _broker().get_order_by_client_order_id("nope") is None
