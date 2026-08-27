"""Characterization tests for account_state()'s positions/prices comprehension.

These pin what market/source_alpaca.py:210 does TODAY, not what it should do.
The truncation of a fractional broker quantity is a KNOWN DEFECT tracked in
#32, still unruled. EVERY truncation assertion in this file is EXPECTED TO
CHANGE when it is ruled -- four of the five tests below assert the defect, not
just the one named for it. Nothing here endorses truncation.

The four account_state tests in tests/test_source_alpaca_helpers.py all use a
fake returning no positions, so the comprehension has never run with a position
in it. Same fake style as that file, reusing its helpers."""
import pytest
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
    """The isinstance is an assertion ABOUT the defect: int() is what makes it
    an int. A #32 ruling that carries the true value reddens this line too.
    Expected to change with the rest."""
    state = _source_holding(_position("NVDA", "80", "214.70")).account_state()
    assert state["positions"] == {"NVDA": 80}
    assert isinstance(state["positions"]["NVDA"], int)


@pytest.mark.parametrize("qty, truncated", [
    ("10.5", 10),
    ("10.7", 10),
    ("-10.5", -10),
])
def test_characterization_fractional_qty_is_truncated_toward_zero(qty,
                                                                 truncated):
    """DEFECT, pinned as current behaviour: int(float("10.5")) is 10, so a
    fractional broker position silently loses its fraction. Known defect,
    tracked in #32, unruled — expected to change when #32 is ruled, and
    rewriting it is that fix's job.

    Three cases, because "toward zero" is a directional claim and 10.5 alone
    cannot back it — int(), floor() and round() all give 10 there. -10.5
    separates int() from floor() (floor gives -11); 10.7 separates it from
    round() (round gives 11). The fund is long-only today (specs/design.md),
    so a negative qty is latent rather than live — and state/protection.py:50
    qty_of REFUSES negatives outright. Two readers of one broker field
    disagreeing is #32's opening thesis.

    Parametrized, not three asserts in one body, because a #32 ruling may
    treat positives and negatives DIFFERENTLY (carry the true value for
    positives, refuse negatives as qty_of does). Sequential asserts would
    report only the first, hiding the split behind a rerun."""
    state = _source_holding(_position("NVDA", qty, "214.70")).account_state()
    assert state["positions"] == {"NVDA": truncated}


def test_characterization_sub_one_share_qty_becomes_zero_but_keeps_its_key():
    """The same defect at its sharpest. 0.4 shares does not lose a fraction,
    it becomes 0 while NVDA remains a KEY — so the line passes every
    truthiness check on the dict, contributes nothing to sector_book_value,
    and offers no sellable shares. A 100% understatement of that holding.
    Tracked in #32; expected to change when it is ruled."""
    state = _source_holding(_position("NVDA", "0.4", "214.70")).account_state()
    assert state["positions"] == {"NVDA": 0}
    assert "NVDA" in state["positions"]


def test_characterization_price_is_carried_as_float():
    state = _source_holding(_position("NVDA", "80", "214.70")).account_state()
    assert state["prices"] == {"NVDA": 214.70}
    assert isinstance(state["prices"]["NVDA"], float)


def test_characterization_every_position_in_the_payload_is_carried():
    """Both lines are carried. NVDA's 10.5 truncates to 10 HERE TOO, so this
    test asserts the #32 defect as well and is expected to change with it —
    the expected value below is not independent of that ruling."""
    state = _source_holding(
        _position("NVDA", "10.5", "214.70"),
        _position("MSFT", "36", "400.00"),
    ).account_state()
    assert state["positions"] == {"NVDA": 10, "MSFT": 36}
    assert state["prices"] == {"NVDA": 214.70, "MSFT": 400.00}
