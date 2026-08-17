"""Offline coverage for orchestrator/pnl.py — the EOD P&L vs SPY line.

contracts.md §8 has always specified the digest as "P&L $ and % vs SPY,
positions table, decisions + outcomes, est. inference cost". run_close emits
only the last three: the fund could report what it DID and what it cost, never
what it was WORTH. Both missing numbers are derivable from reads the day
already makes — account_state carries equity and last_equity, close_frame
carries SPY — so this is arithmetic over existing sources, not new storage.

The one thing it cannot be is early. At 09:40 ET, when run_close fires,
daily_pnl_pct is ten minutes of session and close_frame (end - SIP_DELAY =
09:24, pre-open) returns YESTERDAY's SPY bar. Every test below pins the
post-close posture instead.

No network, no keys — the source is faked.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from orchestrator.clock import SimClock
from orchestrator.pnl import PnlUnavailable, eod_pnl, format_line

# 2026-08-18 16:35 ET == 20:35 UTC (EDT). Late enough that close_frame's
# SIP_DELAY shift (-16 min -> 16:19 ET) still lands past the 16:00 close, so
# today's daily bar is complete rather than partial. 16:15 would ask for 15:59
# and get a bar the closing auction had not yet finished writing.
CLOSE_TIME = datetime(2026, 8, 18, 20, 35, tzinfo=timezone.utc)


def _spy_frame(last_session: str = "2026-08-18", closes=(640.0, 646.4)):
    """The single-column close frame close_frame returns for SPY.

    Daily bars are stamped 00:00 ET and tz-aware, like the real feed — the
    same-session guard reads that index, so a naive one here would test a
    shape production never sees.
    """
    idx = pd.date_range(end=last_session, periods=len(closes), freq="B",
                        tz="America/New_York")
    return pd.DataFrame({"SPY": list(closes)}, index=idx)


class _Source:
    """account_state + close_frame, the only two reads an EOD P&L needs."""

    def __init__(self, equity=101_500.0, last_equity=101_000.0, frame=None):
        self._equity, self._last_equity = equity, last_equity
        self._frame = _spy_frame() if frame is None else frame
        self.close_frame_calls: list = []

    def account_state(self) -> dict:
        return {"equity": self._equity, "last_equity": self._last_equity,
                "cash": 30_000.0, "positions": {}}

    def close_frame(self, tickers, end):
        self.close_frame_calls.append((tickers, end))
        return self._frame


@pytest.fixture
def clock():
    return SimClock(CLOSE_TIME)


# --- the numbers ------------------------------------------------------------

def test_eod_pnl_reports_dollars_percent_and_alpha_vs_spy(clock):
    """+$500 on 101,000 against SPY's +1.0% is a day the fund LOST to the
    benchmark despite making money — the exact case a P&L line without a
    benchmark reports as a win."""
    out = eod_pnl(_Source(), clock)

    assert out["run_date"] == "2026-08-18"
    assert out["equity"] == 101_500.0
    assert out["pnl_usd"] == 500.0
    assert out["pnl_pct"] == pytest.approx(0.004950495)
    assert out["spy_pct"] == pytest.approx(0.01)
    assert out["alpha"] == pytest.approx(-0.005049505)


def test_the_spy_close_is_fetched_through_close_frame(clock):
    """Never hand-roll a bars request. close_frame owns the SIP_DELAY shift a
    naive "fetch SPY at 16:00" 403s without — and it applies that shift to the
    CALLER's end, so the injected clock stays the only source of time."""
    source = _Source()
    eod_pnl(source, clock)

    assert len(source.close_frame_calls) == 1
    tickers, end = source.close_frame_calls[0]
    assert tickers == ["SPY"]
    assert end == clock.now()          # unshifted; close_frame does the shifting


# --- fail closed: no line beats a wrong line (invariant 4) ------------------

def test_a_stale_spy_bar_yields_nothing(clock):
    """The holiday case, and the ran-too-early case, in one guard.

    The broker's own clock says "closed" at 16:35 on EVERY day, so it cannot
    tell a holiday from a normal evening; the feed can. If SPY's last daily bar
    is not today's, this session did not trade — and pairing a previous
    session's SPY with today's equity would invent a day that never happened.
    """
    source = _Source(frame=_spy_frame(last_session="2026-08-17"))

    with pytest.raises(PnlUnavailable, match="2026-08-17"):
        eod_pnl(source, clock)


def test_a_single_spy_bar_yields_nothing(clock):
    """A return needs two closes. One bar is a feed window too short to
    measure, not a flat benchmark."""
    with pytest.raises(PnlUnavailable, match="SPY"):
        eod_pnl(_Source(frame=_spy_frame(closes=(646.4,))), clock)


def test_an_empty_spy_frame_yields_nothing(clock):
    """_reshape_close_frame returns an empty frame when the response carries
    no bars at all — a feed outage, not a zero price."""
    with pytest.raises(PnlUnavailable, match="SPY"):
        eod_pnl(_Source(frame=pd.DataFrame(columns=["SPY"], dtype=float)), clock)


def test_a_nan_spy_close_yields_nothing(clock):
    """A ticker with no bars in the window becomes a NaN column rather than
    raising (source_alpaca's documented behaviour), so NaN arrives here as an
    ordinary value and must be rejected explicitly."""
    frame = _spy_frame(closes=(640.0, float("nan")))

    with pytest.raises(PnlUnavailable, match="SPY"):
        eod_pnl(_Source(frame=frame), clock)


@pytest.mark.parametrize("field", ["equity", "last_equity"])
def test_unparseable_account_numbers_yield_nothing(clock, field):
    """account_state's _safe_float yields NaN for a missing/unparseable field.
    A NaN P&L is not a data point — and a fresh paper account reports
    last_equity "0", which must not read as an infinite return."""
    source = _Source(**{field: float("nan")})

    with pytest.raises(PnlUnavailable, match="equity"):
        eod_pnl(source, clock)


def test_a_zero_last_equity_yields_nothing(clock):
    """Division guard, same posture as source_alpaca's _pnl_pct: a fresh
    account's last_equity == 0 fails closed rather than reading as a flat
    day."""
    with pytest.raises(PnlUnavailable, match="equity"):
        eod_pnl(_Source(last_equity=0.0), clock)


# --- the Slack line ---------------------------------------------------------

def test_format_line_signs_every_figure():
    """A losing day and a winning one must not differ by a character someone
    can miss while skimming #pnl — and the sign belongs outside the dollar
    sign, not inside it."""
    line = format_line({"run_date": "2026-08-18", "equity": 101_500.0,
                        "pnl_usd": 500.0, "pnl_pct": 0.004950495,
                        "spy_pct": 0.01, "alpha": -0.005049505})

    assert line == ("P&L +$500.00 (+0.50%) · SPY +1.00% · alpha -0.50% ·"
                    " equity $101,500.00")


def test_format_line_on_a_losing_day():
    line = format_line({"run_date": "2026-08-18", "equity": 100_500.0,
                        "pnl_usd": -500.0, "pnl_pct": -0.004950495,
                        "spy_pct": -0.01, "alpha": 0.005049505})

    assert line.startswith("P&L -$500.00 (-0.50%) · SPY -1.00% · alpha +0.50%")
