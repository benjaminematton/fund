"""Pydantic models — contracts.md §3, verbatim. Phase 1 needs Ticket and
GateResult; Signal/Critique/Decision arrive with Phase 2 seats."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]


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
