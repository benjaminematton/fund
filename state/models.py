"""Pydantic models — contracts.md §3, verbatim. Phase 1 needs Ticket and
GateResult; Signal and Decision arrive with the MVF analyst/PM seats
(Critique is not needed — the trade pipeline has no Critic seat; SpecCritique
is the G1 one)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

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

REGISTERED_FAMILIES = frozenset({"F1", "F2", "F3", "F4", "F5"})
# arbitrary but generous: far below the 500-char prose caps (family is not prose),
# comfortably above petition:<name> (a 9-char prefix + a short snake_case name)
_FAMILY_MAX = 72


def _check_family(v: str) -> str:
    """`family` is a KEY, and a wrong one is permanent and silent.

    strategy_specs is immutable with no delete path, and state/schema.sql:236
    denormalizes family onto trial_registry as the family-N denominator behind
    the deflated-Sharpe correction — so 'F1' and 'mean_reversion' are two
    families to that counter and the correction under-deflates every trial in
    the real one, forever.

    THIS IS THE ONLY ENFORCEMENT. schema.sql:142 carries the vocabulary as a
    COMMENT and no CHECK, and a CHECK could not be added: CREATE TABLE IF NOT
    EXISTS is a no-op against the droplet's existing table (state/db.py:43-44)
    and state/migrations.py expresses only ADD COLUMN (:45-51).

    A validator rather than Field(pattern=...) because pydantic's regex engine
    has NO LOOK-AHEAD (verified: SchemaError at class definition), so the
    petition-shadowing rule is inexpressible as a pattern — and because a
    refusal here says WHY, where a pattern mismatch does not.
    """
    if v in REGISTERED_FAMILIES:
        return v
    if not v.startswith("petition:"):
        raise ValueError(
            f"family must be one of {sorted(REGISTERED_FAMILIES)} (specs/"
            f"strategy.md §3) or 'petition:<name>'; got {v!r}")
    name = v[len("petition:"):]
    if not name or name != name.strip():
        raise ValueError(
            "a petition needs a non-empty name with no surrounding whitespace")
    # strategy.md:51 defines a petition as one for a NEW family, so a petition
    # naming a registered code contradicts the spec. Derived from canon, not
    # invented: nothing else about <name>'s characters is asserted, because
    # nothing else is specified.
    if re.fullmatch(r"F\d.*", name):
        raise ValueError(
            f"a petition is for a NEW family (specs/strategy.md:51) and may"
            f" not shadow a family code; got {v!r}")
    if len(v) > _FAMILY_MAX:
        raise ValueError(
            f"family is a key, not prose: at most {_FAMILY_MAX} characters")
    return v


Family = Annotated[str, AfterValidator(_check_family)]


class StrategySpec(BaseModel):
    """strategy-contracts.md §2 `strategy_specs`, minus the DB-owned
    `spec_id`/`created_at`/`lineage_parent`. These fields ARE the hash input:
    fundbt.hashing.spec_id(model_dump()) is the spec's identity, so adding a
    field here changes every spec id. Canonical DDL wins; do not invent.

    `extra="forbid"` (strategy-contracts.md §3.1) is not tidiness. Under
    pydantic's default the extra field is IGNORED, so it never reaches
    model_dump() and never reaches the hash — two semantically different specs
    then collide on one spec_id and the second is discarded by
    state/specs.py's INSERT OR IGNORE with no error at all. Forbidding cannot
    move an existing id (an ignored field was never in the hash); it only
    turns those silent acceptances into refusals."""
    model_config = ConfigDict(extra="forbid")

    family: Family
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
