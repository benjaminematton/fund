import json
from pathlib import Path
import pytest
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
