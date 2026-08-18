"""Pydantic models — contracts.md §3, verbatim. Phase 1 needs Ticket and
GateResult; Signal and Decision arrive with the MVF analyst/PM seats
(Critique is not needed — MVF has no Critic seat)."""

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
