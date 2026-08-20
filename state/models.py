"""Pydantic models — contracts.md §3, verbatim. Phase 1 needs Ticket and
GateResult; Signal and Decision arrive with the MVF analyst/PM seats
(Critique is not needed — the trade pipeline has no Critic seat; SpecCritique
is the G1 one)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Side = Literal["buy", "sell"]
Direction = Literal["bullish", "bearish", "neutral"]
Action = Literal["buy", "sell", "hold"]


class Signal(BaseModel):
    run_date: date
    agent: str
    ticker: str
    direction: Direction
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(max_length=500)


class Decision(BaseModel):
    run_date: date
    ticker: str
    action: Action
    qty: int = Field(ge=0)
    thesis: str
    invalidation: str
    stop_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def hold_means_zero(self):
        assert (self.action == "hold") == (self.qty == 0)
        assert self.stop_price is None or self.action == "buy"   # stops guard new or added longs only
        return self


class Ticket(BaseModel):
    id: str
    decision_id: int
    ticker: str
    side: Side
    max_qty: int = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    expires_at: datetime


class GateResult(BaseModel):
    approved: bool
    ticket: Ticket | None = None
    reason: str | None = None                # required when approved=False


MechanismClass = Literal["behavioral", "institutional", "risk_premium",
                         "liquidity_provision"]
LiquidityBucket = Literal["mega_large", "mid", "small", "micro"]
SpecVerdict = Literal["clear", "objections"]


class StrategySpec(BaseModel):
    """strategy-contracts.md §2 `strategy_specs`, minus the DB-owned
    `spec_id`/`created_at`/`lineage_parent`. These fields ARE the hash input:
    fundbt.hashing.spec_id(model_dump()) is the spec's identity, so adding a
    field here changes every spec id. Canonical DDL wins; do not invent."""
    family: str
    seat: str
    hypothesis: str = Field(max_length=500)
    mechanism_class: MechanismClass
    universe: dict
    liquidity_bucket: LiquidityBucket
    signal_rule: dict
    param_ranges: dict
    search_budget: int = Field(gt=0)
    holding_period_d: int = Field(gt=0)
    rebalance: str
    expected_turnover: float = Field(ge=0)
    exit_rule: str
    invalidation: str = Field(max_length=500)
    capacity_usd: float = Field(gt=0)
    predicted: dict
    llm_in_loop: int = Field(ge=0, le=1)


class SpecCritique(BaseModel):
    """The Critic's G1 verdict. `objections` is non-empty exactly when the
    verdict is `objections` — a cleared spec with objections attached, or a
    rejection with no stated defect, is a record nobody can act on.

    Attribution (`charter_version`, `model_id`) is deliberately NOT here: it
    comes from the runtime, not from the seat's tool call, and this model is
    the agent-facing payload. The handler binds it (contracts.md §2)."""
    spec_id: str
    verdict: SpecVerdict
    objections: list[str] = Field(default_factory=list, max_length=3)
    seat: str

    @model_validator(mode="after")
    def objections_match_verdict(self):
        assert (self.verdict == "objections") == bool(self.objections)
        assert all(len(o) <= 200 for o in self.objections)
        return self
