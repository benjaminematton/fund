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


@pytest.mark.parametrize("field", ["avg_corr", "daily_pnl_pct", "cash"])
def test_model_copy_nan_bypass_is_rejected(field):
    """model_copy(update=...) skips field validators in pydantic v2, so it
    can still produce a frozen, isinstance-valid GateInputs carrying NaN
    even though direct mutation and __init__ are guarded. size() must
    re-check finiteness itself on every call rather than trusting
    isinstance(inputs, GateInputs) to mean "already validated"."""
    g = golden_inputs()
    bad = g.model_copy(update={field: float("nan")})
    assert size(bad, "enforce") == Rejected("gate_error")
