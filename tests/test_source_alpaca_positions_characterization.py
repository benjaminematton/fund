"""Characterization tests for account_state()'s positions/prices comprehension.

These pin what market/source_alpaca.py:210 does TODAY, not what it should do.
The truncation of a fractional broker quantity is a KNOWN DEFECT tracked in
#32, still unruled, and these assertions are EXPECTED TO CHANGE when it is
ruled. Nothing here endorses truncation.

The four account_state tests in tests/test_source_alpaca_helpers.py all use a
fake returning no positions, so the comprehension has never run with a position
in it. Same fake style as that file, reusing its helpers."""
from alpaca.trading.enums import PositionSide

from tests.test_source_alpaca_helpers import _Clock, _bare_source


def _position(symbol: str, qty: str, current_price: str):
    """One alpaca-py Position as account_state reads it. Shape copied from the
    open_positions fake; qty and current_price arrive as STRINGS."""
    return _Clock(symbol=symbol, qty=qty, current_price=current_price,
                  side=PositionSide.LONG)


def _source_holding(*positions):
    class Trading:
        def get_account(self):
            return _Clock(equity="101500", last_equity="101000", cash="30000",
                          long_market_value="71500")
        def get_all_positions(self):
            return list(positions)

    src = _bare_source()
    src._trading = Trading()
    return src


def test_characterization_whole_share_qty_is_carried_exactly_as_int():
    state = _source_holding(_position("NVDA", "80", "214.70")).account_state()
    assert state["positions"] == {"NVDA": 80}
    assert isinstance(state["positions"]["NVDA"], int)


def test_characterization_fractional_qty_is_truncated_toward_zero():
    """DEFECT, pinned as current behaviour: int(float("10.5")) is 10, so a
    fractional broker position silently loses its fraction. Known defect,
    tracked in #32, unruled — this assertion is expected to change when #32
    is ruled, and rewriting it is that fix's job."""
    state = _source_holding(_position("NVDA", "10.5", "214.70")).account_state()
    assert state["positions"] == {"NVDA": 10}


def test_characterization_price_is_carried_as_float():
    state = _source_holding(_position("NVDA", "80", "214.70")).account_state()
    assert state["prices"] == {"NVDA": 214.70}
    assert isinstance(state["prices"]["NVDA"], float)


def test_characterization_every_position_in_the_payload_is_carried():
    state = _source_holding(
        _position("NVDA", "10.5", "214.70"),
        _position("MSFT", "36", "400.00"),
    ).account_state()
    assert state["positions"] == {"NVDA": 10, "MSFT": 36}
    assert state["prices"] == {"NVDA": 214.70, "MSFT": 400.00}
