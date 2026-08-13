import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from gate.risk import GateInputs, size, Approved, Rejected

FIX = json.loads((Path(__file__).resolve().parents[1]
                  / "fixtures" / "golden-day-market.json").read_text())
GOLDEN_MAX_QTY = 66  # ← set to the human-settled Task-4-Step-1 value

def golden_inputs(**over):
    base = dict(
        ticker="NVDA", side="buy", equity=FIX["equity"], cash=FIX["cash"],
        price=FIX["prices"]["NVDA"], vol_60d=FIX["vol_60d"]["NVDA"],
        avg_corr=FIX["avg_corr"]["NVDA"], held_qty=0,
        position_count=FIX["position_count"], sector="tech",
        sector_value=120 * 232.0 + 40 * 505.0,   # book at current prices
        daily_pnl_pct=FIX["daily_pnl_pct"])
    base.update(over)
    return GateInputs(**base)

def golden_dict(**over):
    """Same golden vector as golden_inputs(), but returns a plain dict
    instead of a constructed GateInputs -- the shape build_gate_inputs()
    (plans/mvf.md:473) actually hands to size(), so garbage can flow
    through to the gate's own validator instead of being rejected early
    by test code."""
    base = dict(
        ticker="NVDA", side="buy", equity=FIX["equity"], cash=FIX["cash"],
        price=FIX["prices"]["NVDA"], vol_60d=FIX["vol_60d"]["NVDA"],
        avg_corr=FIX["avg_corr"]["NVDA"], held_qty=0,
        position_count=FIX["position_count"], sector="tech",
        sector_value=120 * 232.0 + 40 * 505.0,
        daily_pnl_pct=FIX["daily_pnl_pct"])
    base.update(over)
    return base

def test_golden_day_vector_both_step_values():
    r = size(golden_inputs(), mode="enforce")
    assert isinstance(r, Approved)
    assert r.pre_sector_qty == 105        # the intermediate is asserted too
    assert r.max_qty == GOLDEN_MAX_QTY

def test_advisory_equals_enforcement_on_identical_inputs():
    a = size(golden_inputs(), mode="advisory")
    e = size(golden_inputs(), mode="enforce")
    assert a.max_qty == e.max_qty == GOLDEN_MAX_QTY

@pytest.mark.parametrize("field", ["daily_pnl_pct", "cash", "avg_corr"])
def test_post_construction_nan_mutation_is_refused(field):
    """A GateInputs is frozen=True, so mutating a live instance to NaN after
    construction must raise a pydantic frozen_instance error — not merely
    "some Exception" (which could pass even for a misspelled field name) —
    and the field's value must be unchanged afterward."""
    g = golden_inputs()
    original = getattr(g, field)
    with pytest.raises(ValidationError) as exc_info:
        setattr(g, field, float("nan"))
    assert exc_info.value.errors()[0]["type"] == "frozen_instance"
    assert getattr(g, field) == original

@pytest.mark.parametrize("field,value", [
    ("vol_60d", -1.0),
    ("sector_value", -1e9),
    ("position_count", -5),
    ("avg_corr", -99.0),
    ("avg_corr", 1.5),
])
def test_negative_nonsensical_inputs_rejected(field, value):
    r = size(golden_inputs(**{field: value}), mode="enforce")
    assert r == Rejected("gate_error")

@pytest.mark.parametrize("value", [-1.0, 1.0])
def test_avg_corr_boundary_still_accepted(value):
    """-1.0 and 1.0 are legitimate perfect correlations, not malformed
    input — the out-of-range guard must not reject them."""
    r = size(golden_inputs(avg_corr=value), mode="enforce")
    assert isinstance(r, Approved)


@pytest.mark.parametrize("field", ["avg_corr", "daily_pnl_pct", "cash",
                                   "vol_60d", "equity", "sector_value"])
def test_model_copy_nan_bypass_is_rejected(field):
    """model_copy(update=...) skips field validators in pydantic v2, so it
    can still produce a frozen, isinstance-valid GateInputs carrying NaN
    even though direct mutation and __init__ are guarded. size() must
    re-check finiteness itself on every call rather than trusting
    isinstance(inputs, GateInputs) to mean "already validated"."""
    g = golden_inputs()
    bad = g.model_copy(update={field: float("nan")})
    assert size(bad, "enforce") == Rejected("gate_error")


def test_model_copy_nan_held_qty_with_position_count_bypass_is_rejected():
    """held_qty=NaN via model_copy makes `i.held_qty == 0` False, bypassing
    the MAX_POSITIONS check even though position_count=8 alone would reject
    with a valid held_qty=0."""
    g = golden_inputs()
    bad = g.model_copy(update={"held_qty": float("nan"), "position_count": 8})
    assert size(bad, "enforce") == Rejected("gate_error")


def test_valid_held_qty_zero_with_position_count_at_limit_still_rejected():
    """Sanity check for the case above: with a legitimate held_qty=0, the
    same position_count=8 must still be rejected on MAX_POSITIONS."""
    r = size(golden_inputs(held_qty=0, position_count=8), mode="enforce")
    assert r == Rejected("position_count")


def test_model_copy_nan_position_count_bypass_is_rejected():
    """position_count=NaN via model_copy makes `NaN >= MAX_POSITIONS` False,
    bypassing the MAX_POSITIONS check."""
    g = golden_inputs()
    bad = g.model_copy(update={"position_count": float("nan")})
    assert size(bad, "enforce") == Rejected("gate_error")


def test_model_construct_nan_held_qty_bypass_is_rejected():
    """model_construct(...) skips field validators entirely (not just
    model_copy's update path), so held_qty=NaN must still be caught."""
    base = dict(
        ticker="NVDA", side="buy", equity=FIX["equity"], cash=FIX["cash"],
        price=FIX["prices"]["NVDA"], vol_60d=FIX["vol_60d"]["NVDA"],
        avg_corr=FIX["avg_corr"]["NVDA"], sector="tech",
        sector_value=120 * 232.0 + 40 * 505.0,
        daily_pnl_pct=FIX["daily_pnl_pct"])
    bad = GateInputs.model_construct(**base, held_qty=float("nan"), position_count=8)
    assert size(bad, "enforce") == Rejected("gate_error")


def test_model_copy_inf_held_qty_sell_bypass_is_rejected():
    """held_qty=+inf via model_copy on a sell must not reach Approved with a
    non-integer, unbounded max_qty — the worst-case bypass."""
    g = golden_inputs()
    bad = g.model_copy(update={"held_qty": float("inf"), "side": "sell"})
    assert size(bad, "enforce") == Rejected("gate_error")


def test_position_count_positive_inf_still_rejected():
    """Confirm the already-correctly-handled neighbour keeps working:
    position_count=+inf must still be Rejected (not Approved) — reached via
    model_copy since strict=True forbids a float in this int field via the
    normal constructor."""
    g = golden_inputs()
    bad = g.model_copy(update={"position_count": float("inf")})
    r = size(bad, "enforce")
    assert isinstance(r, Rejected)


def test_held_qty_negative_inf_still_rejected():
    """Confirm the already-correctly-handled neighbour keeps working:
    held_qty=-inf is caught by the held_qty < 0 comparison."""
    g = golden_inputs()
    bad = g.model_copy(update={"held_qty": float("-inf")})
    assert size(bad, "enforce") == Rejected("gate_error")


def test_legitimate_sell_still_approved():
    r = size(golden_inputs(side="sell", held_qty=40), mode="enforce")
    assert r == Approved(max_qty=40, pre_sector_qty=40, side="sell")


def test_boundary_zero_and_edge_inputs_still_approved():
    r = size(golden_inputs(held_qty=0, position_count=0, sector_value=0.0,
                            vol_60d=0.0, daily_pnl_pct=-0.0299), mode="enforce")
    assert isinstance(r, Approved)


@pytest.mark.parametrize("vol,tier", [(0.149, 0.25), (0.15, 0.20),
                                      (0.499, 0.20), (0.50, 0.20), (0.501, 0.10)])
def test_vol_tier_boundaries(vol, tier):
    r = size(golden_inputs(vol_60d=vol, avg_corr=0.0, sector_value=0.0), "enforce")
    assert r.pre_sector_qty == int((100000 * tier * 1.10) // 180)

@pytest.mark.parametrize("corr,mult", [(0.19, 1.10), (0.2, 1.00), (0.39, 1.00),
    (0.4, 0.95), (0.6, 0.85), (0.79, 0.85), (0.8, 0.70)])
def test_corr_multiplier_boundaries(corr, mult):
    r = size(golden_inputs(avg_corr=corr, sector_value=0.0), "enforce")
    assert r.pre_sector_qty == int((100000 * 0.20 * mult) // 180)

def test_cash_cap_binds():
    r = size(golden_inputs(cash=1800.0, sector_value=0.0), "enforce")
    assert r.max_qty == 10                       # floor(1800/180)

def test_position_count_hard_reject_new_position_only():
    assert size(golden_inputs(position_count=8), "enforce") == Rejected("position_count")
    r = size(golden_inputs(position_count=8, held_qty=5), "enforce")
    # adding to an existing position is not a new slot
    assert r == Approved(max_qty=GOLDEN_MAX_QTY, pre_sector_qty=105, side="buy")

def test_circuit_breaker_rejects_buys():
    assert size(golden_inputs(daily_pnl_pct=-0.03), "enforce") == Rejected("circuit_breaker")

def test_sell_is_capped_at_held():
    r = size(golden_inputs(side="sell", held_qty=40), "enforce")
    assert r.max_qty == 40
    assert size(golden_inputs(side="sell", held_qty=0), "enforce") == Rejected("nothing_held")

MALFORMED_FIELDS = [
    ("vol_60d", float("nan")), ("vol_60d", float("inf")), ("avg_corr", float("nan")),
    ("price", 0.0), ("price", -1.0), ("equity", float("nan")), ("cash", -5.0),
    ("daily_pnl_pct", float("nan")), ("sector_value", float("inf"))]

# Of the 9 cases above, these 6 are non-finite values on fields covered by
# GateInputs' own field_validator, so they raise ValidationError at
# construction rather than reaching size()'s runtime re-check.
MALFORMED_NONFINITE_FIELDS = [
    ("vol_60d", float("nan")), ("vol_60d", float("inf")), ("avg_corr", float("nan")),
    ("equity", float("nan")), ("daily_pnl_pct", float("nan")), ("sector_value", float("inf"))]

@pytest.mark.parametrize("field,val", MALFORMED_FIELDS)
def test_fail_closed_on_malformed(field, val):
    """Fail-closed on malformed input via the PRODUCTION caller shape:
    build_gate_inputs() (plans/mvf.md:473) hands size() a plain dict, NOT a
    GateInputs, so garbage flows to the gate's own validator (review
    decision C3: gate/risk.py owns ALL fail-closed validation). Malformed
    input must never reach Approved, regardless of which layer inside
    size() catches it."""
    base = golden_dict(**{field: val})
    assert size(base, "enforce") == Rejected("gate_error")

@pytest.mark.parametrize("field,val", MALFORMED_NONFINITE_FIELDS)
def test_fail_closed_on_malformed_rejected_at_construction(field, val):
    """Companion to test_fail_closed_on_malformed: for the 6 (of 9) cases
    that are non-finite values on validated fields, pin the stronger
    construction-time guarantee directly -- GateInputs(**bad) itself must
    raise ValidationError, not just size() returning Rejected."""
    with pytest.raises(ValidationError):
        GateInputs(**golden_dict(**{field: val}))

def test_fail_closed_on_garbage_types():
    assert size({"ticker": "NVDA"}, "enforce") == Rejected("gate_error")
    assert size(None, "enforce") == Rejected("gate_error")

def test_hold_skip_shape():
    """{buy:0, sell:0} shape the pre-gate uses: no cash, nothing held.
    Pins the exact reason codes -- a normal no_headroom/nothing_held skip
    is semantically different from a gate_error malfunction and drives a
    different downstream event. The buy branch runs in advisory mode to
    pin the advisory==enforce invariant (spec §3.9) on a reject path too."""
    buy = size(golden_inputs(cash=0.0), "advisory")
    sell = size(golden_inputs(side="sell", held_qty=0), "enforce")
    assert buy == Rejected("no_headroom")
    assert sell == Rejected("nothing_held")
