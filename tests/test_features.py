import math
import numpy as np
import pandas as pd
import pytest
from market.features import (annualized_vol, avg_corr_vs_book, build_gate_inputs,
                             build_market_inputs, sector_book_value,
                             unmapped_holdings, unpriceable_book_tickers)
from gate.risk import size, Approved, Rejected

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


# --- hardcoded goldens: the two numbers that pick the sizing tiers ----------
#
# The two tests above re-derive their expectation with the SAME expression the
# implementation uses, so a wrong annualization factor or a ddof flip passes
# both. vol_60d selects _vol_tier and avg_corr selects _corr_mult, so either
# error mis-sizes EVERY position on EVERY day. The literals below are hand
# derived once, from an exact input series, and never recomputed from the code.

# Closes chosen so every pct_change is exactly +/- r, r = 0.01.
GOLDEN_A = [100.0, 101.0, 99.99, 100.9899]          # returns ( r, -r,  r)
GOLDEN_B = [50.0, 50.5, 49.995, 50.49495]           # returns ( r, -r,  r)
GOLDEN_C = [200.0, 202.0, 204.02, 201.9798]         # returns ( r,  r, -r)


def test_annualized_vol_is_the_hand_derived_literal():
    """Hand derivation, returns r = 0.01 over [r, -r, r]:

        mean            = r/3
        deviations      = (2r/3, -4r/3, 2r/3)
        sum of squares  = (4 + 16 + 4) r^2 / 9 = 24 r^2 / 9
        var  (ddof=1)   = (24 r^2 / 9) / 2     =  4 r^2 / 3
        std             = 2r / sqrt(3)
        annualized      = 2r * sqrt(252/3) = 2r * sqrt(84)
                        = 0.02 * 9.16515139... = 0.18330302779823...

    ddof=0 would divide by 3 instead of 2 and give 0.14966...; dropping the
    sqrt() from the annualization would give 2.909... Both are RED here.
    """
    series = pd.Series(GOLDEN_A, index=pd.bdate_range("2026-06-29", periods=4))
    assert annualized_vol(series) == pytest.approx(0.1833030277982336, abs=1e-12)


def test_avg_corr_vs_book_is_the_hand_derived_literal():
    """Hand derivation, in units of r (the level of r cancels in Pearson's rho):

        candidate A returns  x = ( 1, -1,  1)
        book ticker B        y = ( 1, -1,  1)  -> identical -> rho = +1.0
        book ticker C        z = ( 1,  1, -1)

        For x vs z:  dx = (2/3, -4/3, 2/3), dz = (2/3, 2/3, -4/3)
          sum dx*dz = ( 4 - 8 - 8) / 9 = -12/9
          sum dx^2  = sum dz^2 = (4 + 16 + 4)/9 = 24/9
          rho       = (-12/9) / (24/9) = -0.5

        mean(+1.0, -0.5) = 0.25
    """
    px = pd.DataFrame({"A": GOLDEN_A, "B": GOLDEN_B, "C": GOLDEN_C},
                      index=pd.bdate_range("2026-06-29", periods=4))
    assert avg_corr_vs_book(px, "A", ["B"]) == pytest.approx(1.0, abs=1e-12)
    assert avg_corr_vs_book(px, "A", ["C"]) == pytest.approx(-0.5, abs=1e-12)
    assert avg_corr_vs_book(px, "A", ["B", "C"]) == pytest.approx(0.25, abs=1e-12)

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


def test_sector_book_value_missing_sector_entry_is_nan():
    """A held ticker with no config/sectors.yaml entry used to be silently
    dropped from EVERY sector's book value, understating concentration and
    letting the 60% post-trade sector cap approve more than it should. It
    fails closed now, exactly like its missing-price neighbour above."""
    v = sector_book_value(
        positions={"AAPL": 120, "MSFT": 40},
        prices={"AAPL": 232.0, "MSFT": 505.0},
        sectors={"AAPL": "tech"},                     # MSFT is unmapped
        sector="tech")
    assert math.isnan(v)
    # an explicit null entry in the yaml is as unmapped as a missing key
    assert math.isnan(sector_book_value(
        positions={"AAPL": 120, "MSFT": 40},
        prices={"AAPL": 232.0, "MSFT": 505.0},
        sectors={"AAPL": "tech", "MSFT": None}, sector="tech"))
    # ...and a fully mapped book still marks at the golden day's $48,040
    assert sector_book_value(
        positions={"AAPL": 120, "MSFT": 40},
        prices={"AAPL": 232.0, "MSFT": 505.0},
        sectors={"AAPL": "tech", "MSFT": "tech"}, sector="tech") == 48040.0


def test_unmapped_holdings_names_exactly_the_missing_yaml_entries():
    """The caller alerts from this, so the fix is a one-line yaml commit."""
    assert unmapped_holdings({"AAPL": 120, "MSFT": 40}, {"AAPL": "tech"}) == ["MSFT"]
    assert unmapped_holdings({"AAPL": 120}, {"AAPL": "tech"}) == []
    assert unmapped_holdings({}, {}) == []


def test_an_unmapped_holding_fails_buys_closed_through_the_gate():
    """End-to-end: the NaN reaches gate.risk.size() as gate_error, so an
    unknown sector can never quietly widen the sector cap's headroom."""
    px = _frame()
    account = _account()
    out = build_market_inputs(["NVDA"], account, px, {"NVDA": "tech"})["NVDA"]
    assert math.isnan(out["sector_value"])           # AAPL is held, unmapped
    assert size({**out, "side": "buy"}, "enforce") == Rejected("gate_error")


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


def test_one_unpriceable_holding_does_not_reject_the_whole_universe():
    """Every candidate correlates against the SAME book, so a book ticker
    whose correlation came back NaN made avg_corr NaN for the whole
    universe -> Rejected('gate_error') for every ticker, i.e. a data gap in
    one unrelated position cost the entire trading day. AlpacaSource's
    _reshape_close_frame gives a ticker with zero bars an all-NaN COLUMN,
    which is the shape reproduced here."""
    px = _frame()
    px["AAPL"] = float("nan")                  # held, and the feed gave no bars
    # a book of TWO holdings, only one of which is unpriceable: a partial
    # exclusion is the case this behaviour exists for. (A book whose every
    # member is unpriceable is a data outage and fails closed instead —
    # test_a_book_we_cannot_price_at_all_fails_closed.)
    account = _account(positions={"AAPL": 40, "MSFT": 40},
                       prices={"AAPL": 200.0, "MSFT": 200.0})
    sectors = {"NVDA": "tech", "MSFT": "tech", "AAPL": "tech"}
    out = build_market_inputs(["NVDA", "MSFT", "AAPL"], account, px, sectors)

    assert not math.isnan(out["NVDA"]["avg_corr"])   # measured vs MSFT alone
    assert isinstance(size({**out["NVDA"], "side": "buy"}, "enforce"), Approved)

    # ...and the poisoned ticker still rejects ALONE: its price is the
    # broker's live mark, so NaN vol from its own missing bars is the only
    # thing failing it. A candidate's OWN NaN must never stop being a reject.
    assert math.isnan(out["AAPL"]["vol_60d"])
    assert out["AAPL"]["price"] == 200.0
    assert size({**out["AAPL"], "side": "buy"}, "enforce") == Rejected("gate_error")

    # MSFT is held, so ITS book is the unpriceable AAPL alone -> every member
    # excluded -> NaN -> rejected. Asserted here rather than hidden: the
    # blast radius of a data outage is exactly the tickers whose whole book
    # went dark, and never wider.
    assert math.isnan(out["MSFT"]["avg_corr"])
    assert size({**out["MSFT"], "side": "buy"}, "enforce") == Rejected("gate_error")


def test_avg_corr_vs_book_excludes_a_book_ticker_with_no_bars():
    """The exclusion is on the BOOK basket only, and is measured over what
    is left — not NaN, and not a stale 0.0."""
    px = _frame()
    px["AAPL"] = float("nan")
    assert avg_corr_vs_book(px, "NVDA", ["AAPL", "MSFT"]) == pytest.approx(
        avg_corr_vs_book(px, "NVDA", ["MSFT"]))
    # the candidate's own missing history still rejects the candidate alone
    assert math.isnan(avg_corr_vs_book(px, "AAPL", ["NVDA", "MSFT"]))


def test_an_empty_book_sizes_at_the_permissive_tier():
    """No holdings means no correlation risk to measure, so 0.0 -> the 1.10x
    tier is the CORRECT answer, not a fallback. Pinned so the all-excluded
    fix below cannot collaterally start rejecting the first trade of the
    fund's life, when the book is legitimately empty."""
    px = _frame()
    assert avg_corr_vs_book(px, "NVDA", []) == 0.0
    inputs = build_gate_inputs(
        ticker="NVDA", side="buy", price=100.0, equity=100_000.0, cash=100_000.0,
        sectors={"NVDA": "tech"}, vol_60d=0.30, avg_corr=avg_corr_vs_book(px, "NVDA", []),
        held_qty=0, position_count=0, sector_value=0.0, daily_pnl_pct=0.0)
    assert isinstance(size(inputs, "enforce"), Approved)


def test_a_book_we_cannot_price_at_all_fails_closed():
    """Distinct from the empty book above: we HOLD things, we just can't
    price any of them. That is a data outage, not an absence of correlation
    risk — inferring 'uncorrelated' from it would size UP on missing data,
    which is the one direction gate/ must never fail. NaN -> gate_error."""
    px = _frame()
    px["AAPL"] = float("nan")
    assert math.isnan(avg_corr_vs_book(px, "NVDA", ["AAPL"]))
    # and it is loud: the caller still names exactly what was dropped
    assert unpriceable_book_tickers(px, ["AAPL"]) == ["AAPL"]
    inputs = build_gate_inputs(
        ticker="NVDA", side="buy", price=100.0, equity=100_000.0, cash=100_000.0,
        sectors={"NVDA": "tech"}, vol_60d=0.30, avg_corr=avg_corr_vs_book(px, "NVDA", ["AAPL"]),
        held_qty=0, position_count=1, sector_value=0.0, daily_pnl_pct=0.0)
    assert size(inputs, "enforce") == Rejected("gate_error")


def test_unpriceable_book_tickers_names_exactly_what_was_excluded():
    """The caller alerts from this; it must name the dropped tickers and
    nothing else."""
    px = _frame()
    px["AAPL"] = float("nan")
    assert unpriceable_book_tickers(px, ["NVDA", "AAPL", "MSFT"]) == ["AAPL"]
    assert unpriceable_book_tickers(px, ["NVDA", "MSFT"]) == []
    # a ticker the frame does not carry AT ALL is a caller/wiring bug, not a
    # feed gap: it is NOT excluded (it still fails closed as NaN above), so
    # it must not be reported as excluded either
    assert unpriceable_book_tickers(px, ["ZZZZ"]) == []


def test_a_well_formed_snapshot_is_APPROVED_with_the_hand_derived_qty():
    """The APPROVE side of the features -> gate seam.

    Every other test that feeds build_market_inputs' output into size() asserts
    Rejected, which is indistinguishable from a total schema break: GateInputs
    is extra="forbid", so ONE spurious key in build_gate_inputs' dict turns
    every day into a permanent full HOLD with the whole suite still green.
    This test is the instrument that measures that — it is RED the moment the
    dict and the model stop matching.

    Hand derivation from the fixed frame below (r = 0.01):
      vol_60d    = 2r * sqrt(84)      = 0.18330...  -> 0.15 < v <= 0.50 -> 0.20
      avg_corr   = rho(x, y)          = 0.50        -> 0.4 <= c < 0.6   -> 0.95
      dollar     = 100_000 * 0.20 * 0.95            = 19_000.00
      cash cap   = min(19_000, 30_000)              = 19_000.00
      price      = last close of NVDA               =    100.9899
      pre_sector = floor(19_000 / 100.9899)         = floor(188.1376) = 188
      sector_val = 40 AAPL * broker mark 50.00      =  2_000.00
      headroom   = 0.60 * 100_000 - 2_000           = 58_000.00
      cap        = floor(58_000 / 100.9899)         = floor(574.3148) = 574
      max_qty    = min(188, 574)                    = 188
    """
    px = pd.DataFrame(
        {"NVDA": [100.0, 101.0, 99.99, 100.9899],   # returns x = ( r, -r,  r)
         "AAPL": [50.0, 49.5, 49.005, 49.49505]},   # returns y = (-r, -r,  r)
        index=pd.bdate_range("2026-06-29", periods=4))
    account = dict(equity=100000.0, cash=30000.0, daily_pnl_pct=-0.004,
                   positions={"AAPL": 40}, prices={"AAPL": 50.0})
    inputs = build_market_inputs(["NVDA"], account, px,
                                 {"NVDA": "tech", "AAPL": "tech"})
    out = inputs["NVDA"]

    # the inputs the derivation above rests on, so a drift shows up as itself
    assert out["price"] == 100.9899
    assert out["vol_60d"] == pytest.approx(0.1833030277982336, abs=1e-12)
    assert out["avg_corr"] == pytest.approx(0.5, abs=1e-9)
    assert out["sector_value"] == 2000.0

    assert size({**out, "side": "buy"}, "enforce") == Approved(
        max_qty=188, pre_sector_qty=188, side="buy")
    # ...and the held leg is sell-able for exactly what is held
    assert size({**inputs["AAPL"], "side": "sell"}, "enforce") == Approved(
        max_qty=40, pre_sector_qty=40, side="sell")


def test_build_market_inputs_unknown_ticker_reaches_the_gate_as_gate_error():
    """No bars and no sector: the assembler never rejects (C3) — it passes the
    NaNs/None through and the GATE fails closed."""
    px = _frame()
    out = build_market_inputs(["ZZZZ"], _account(), px, {"AAPL": "tech"})["ZZZZ"]
    assert math.isnan(out["price"]) and out["sector"] is None
    assert isinstance(size({**out, "side": "buy"}, "advisory"), Rejected)
