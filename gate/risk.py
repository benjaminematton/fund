"""Deterministic tiered risk sizing (design §5, phase-2 design §2.4).
Pure function over validated inputs. Fail-closed: ANY invalid input ->
Rejected("gate_error"). Thresholds change only by human commit."""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator

Mode = Literal["advisory", "enforce"]
SECTOR_CAP = 0.60
MAX_POSITIONS = 8
CIRCUIT_BREAKER = -0.03

class GateInputs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                               validate_assignment=True)
    ticker: str
    side: Literal["buy", "sell"]
    equity: float
    cash: float
    price: float
    vol_60d: float
    avg_corr: float
    held_qty: int
    position_count: int
    sector: str
    sector_value: float          # book value of this sector at current prices
    daily_pnl_pct: float

    @field_validator("equity", "cash", "price", "vol_60d", "avg_corr",
                     "sector_value", "daily_pnl_pct")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):          # NaN < 0.15 is False — never compare NaN
            raise ValueError("non-finite")
        return v

@dataclass(frozen=True)
class Approved:
    max_qty: int
    pre_sector_qty: int
    side: str

@dataclass(frozen=True)
class Rejected:
    reason: str

def _vol_tier(vol: float) -> float:
    if vol < 0.15: return 0.25
    if vol <= 0.50: return 0.20
    return 0.10

def _corr_mult(corr: float) -> float:
    if corr >= 0.8: return 0.70
    if corr >= 0.6: return 0.85
    if corr >= 0.4: return 0.95
    if corr >= 0.2: return 1.00
    return 1.10

def size(inputs, mode: Mode):
    """inputs: GateInputs OR anything else (dict, garbage). A raw GateInputs
    instance was already validated at construction (model is frozen with
    validate_assignment=True, so it cannot be mutated back into an invalid
    state); anything else is validated here via model_validate. NOTE: an
    instance built via GateInputs.model_construct(...) skips all validation
    and is NOT re-validated by this function — that is a documented caller
    contract, not a gap this function closes.
    Advisory and enforce run the IDENTICAL computation (invariant §3.9)."""
    try:
        i = inputs if isinstance(inputs, GateInputs) else GateInputs.model_validate(inputs)
        if (i.price <= 0 or i.equity <= 0 or i.cash < 0 or i.held_qty < 0
                or i.vol_60d < 0 or i.sector_value < 0 or i.position_count < 0):
            return Rejected("gate_error")
        if i.side == "sell":
            return (Approved(max_qty=i.held_qty, pre_sector_qty=i.held_qty,
                             side="sell") if i.held_qty > 0
                    else Rejected("nothing_held"))
        if i.daily_pnl_pct <= CIRCUIT_BREAKER:
            return Rejected("circuit_breaker")
        if i.held_qty == 0 and i.position_count >= MAX_POSITIONS:
            return Rejected("position_count")
        dollar = i.equity * _vol_tier(i.vol_60d) * _corr_mult(i.avg_corr)
        pre_sector = math.floor(min(dollar, i.cash) / i.price)
        headroom = SECTOR_CAP * i.equity - i.sector_value   # POST-trade cap
        qty = min(pre_sector, math.floor(max(headroom, 0.0) / i.price))
        if qty < 1:
            return Rejected("no_headroom")
        return Approved(max_qty=qty, pre_sector_qty=pre_sector, side="buy")
    except Exception:
        return Rejected("gate_error")
