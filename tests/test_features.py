import math
import numpy as np
import pandas as pd
import pytest
from market.features import (annualized_vol, avg_corr_vs_book, build_gate_inputs,
                             build_market_inputs, sector_book_value)
from gate.risk import size, Rejected

def _frame(n=90, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-03-01", periods=n)
    px = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(n, 3)), axis=0)),
        index=idx, columns=["NVDA", "AAPL", "MSFT"])
    return px

def test_annualized_vol_matches_hand_calc():
    px = _frame()
    rets = px["NVDA"].pct_change().dropna().tail(60)
    assert annualized_vol(px["NVDA"]) == pytest.approx(
        rets.std(ddof=1) * np.sqrt(252))

def test_avg_corr_vs_book():
    px = _frame()
    manual = np.mean([px["NVDA"].pct_change().corr(px[t].pct_change())
                      for t in ("AAPL", "MSFT")])
    assert avg_corr_vs_book(px, "NVDA", ["AAPL", "MSFT"]) == pytest.approx(manual)
    # no book -> corr 0.0 (=> 1.10x multiplier tier, most permissive)
    assert avg_corr_vs_book(px, "NVDA", []) == 0.0

def test_sector_book_value_marks_at_current_prices():
    v = sector_book_value(
        positions={"AAPL": 120, "MSFT": 40}, prices={"AAPL": 232.0, "MSFT": 505.0},
        sectors={"AAPL": "tech", "MSFT": "tech"}, sector="tech")
    assert v == 120 * 232.0 + 40 * 505.0

def test_build_gate_inputs_passes_garbage_through():
    """C3: features NEVER rejects — the gate does. NaN vol flows through."""
    gi = build_gate_inputs(
        ticker="NVDA", side="buy", equity=100000.0, cash=30000.0, price=180.0,
        vol_60d=float("nan"), avg_corr=0.55, held_qty=0, position_count=2,
        sectors={"NVDA": "tech"}, sector_value=48040.0, daily_pnl_pct=-0.004)
    assert gi["vol_60d"] != gi["vol_60d"]        # still NaN; dict not model

def test_missing_sector_is_visible_not_guessed():
    gi = build_gate_inputs(ticker="ZZZZ", side="buy", equity=1.0, cash=1.0,
        price=1.0, vol_60d=0.2, avg_corr=0.0, held_qty=0, position_count=0,
        sectors={}, sector_value=0.0, daily_pnl_pct=0.0)
    assert gi["sector"] is None                  # gate's strict model rejects None


def test_avg_corr_vs_book_missing_book_ticker_is_nan():
    """A held book ticker with no price history -> NaN, not a crash."""
    px = _frame()
    result = avg_corr_vs_book(px, "NVDA", ["AAPL", "ZZZZ"])
    assert math.isnan(result)


def test_sector_book_value_missing_price_is_nan():
    """A position with no entry in prices -> NaN, not a crash (never
    silently drop it -- dropping would understate sector book value and
    let the gate approve an oversized position)."""
    v = sector_book_value(
        positions={"AAPL": 120, "MSFT": 40}, prices={"AAPL": 232.0},
        sectors={"AAPL": "tech", "MSFT": "tech"}, sector="tech")
    assert math.isnan(v)


def test_annualized_vol_empty_series_is_nan():
    """Too-short/empty series -> NaN already (no book to drop, no KeyError
    possible), confirming there is no equivalent hole here."""
    assert math.isnan(annualized_vol(pd.Series(dtype=float)))
    assert math.isnan(annualized_vol(pd.Series([100.0])))


def test_missing_data_nan_lands_on_gate_error_not_a_crash():
    """End-to-end: NaN from avg_corr_vs_book / sector_book_value flows
    through build_gate_inputs into gate.risk.size() and is rejected --
    the missing-data path resolves to HOLD, never a crash and never a
    silently-approved oversized position."""
    px = _frame()
    bad_corr = avg_corr_vs_book(px, "NVDA", ["AAPL", "ZZZZ"])
    gi = build_gate_inputs(
        ticker="NVDA", side="buy", equity=100000.0, cash=30000.0, price=180.0,
        vol_60d=0.2, avg_corr=bad_corr, held_qty=0, position_count=2,
        sectors={"NVDA": "tech"}, sector_value=48040.0, daily_pnl_pct=-0.004)
    assert size(gi, "enforce") == Rejected("gate_error")

    bad_sector_value = sector_book_value(
        positions={"AAPL": 120, "MSFT": 40}, prices={"AAPL": 232.0},
        sectors={"AAPL": "tech", "MSFT": "tech"}, sector="tech")
    gi2 = build_gate_inputs(
        ticker="NVDA", side="buy", equity=100000.0, cash=30000.0, price=180.0,
        vol_60d=0.2, avg_corr=0.1, held_qty=0, position_count=2,
        sectors={"NVDA": "tech"}, sector_value=bad_sector_value, daily_pnl_pct=-0.004)
    assert size(gi2, "enforce") == Rejected("gate_error")


# --- build_market_inputs: the live composition root's market snapshot -------

def _account(**over):
    a = dict(equity=100000.0, cash=30000.0, daily_pnl_pct=-0.004,
             positions={"AAPL": 40}, prices={"AAPL": 200.0})
    a.update(over)
    return a


def test_build_market_inputs_covers_watchlist_and_held_positions():
    """The pre-gate runs over watchlist AND held tickers (design §3, 08:45) —
    a position outside today's watchlist must still be sell-able."""
    px = _frame()
    out = build_market_inputs(["NVDA"], _account(), px,
                              {"NVDA": "tech", "AAPL": "tech"})
    assert sorted(out) == ["AAPL", "NVDA"]
    assert out["NVDA"]["held_qty"] == 0
    assert out["AAPL"]["held_qty"] == 40
    assert out["NVDA"]["position_count"] == 1


def test_build_market_inputs_prices_unheld_from_the_last_close():
    """Alpaca's account_state only carries prices for HELD positions; an
    unheld watchlist ticker takes its last close from the frame."""
    px = _frame()
    out = build_market_inputs(["NVDA"], _account(), px, {"NVDA": "tech"})
    assert out["NVDA"]["price"] == pytest.approx(px["NVDA"].iloc[-1])
    # a held ticker keeps the broker's live mark, not the stale close
    assert out["AAPL"]["price"] == 200.0


def test_build_market_inputs_carries_the_gate_fields_through():
    px = _frame()
    out = build_market_inputs(["NVDA"], _account(), px,
                              {"NVDA": "tech", "AAPL": "tech"})["NVDA"]
    assert out["equity"] == 100000.0 and out["cash"] == 30000.0
    assert out["daily_pnl_pct"] == -0.004
    assert out["sector"] == "tech"
    assert out["vol_60d"] == pytest.approx(annualized_vol(px["NVDA"]))
    # correlation is measured against the BOOK, excluding the ticker itself
    assert out["avg_corr"] == pytest.approx(avg_corr_vs_book(px, "NVDA", ["AAPL"]))
    assert out["sector_value"] == pytest.approx(40 * 200.0)


def test_build_market_inputs_unknown_ticker_reaches_the_gate_as_gate_error():
    """No bars and no sector: the assembler never rejects (C3) — it passes the
    NaNs/None through and the GATE fails closed."""
    px = _frame()
    out = build_market_inputs(["ZZZZ"], _account(), px, {"AAPL": "tech"})["ZZZZ"]
    assert math.isnan(out["price"]) and out["sector"] is None
    assert isinstance(size({**out, "side": "buy"}, "advisory"), Rejected)
