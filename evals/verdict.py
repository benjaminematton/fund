"""Three-valued verdicts (docs/evals/PLAN.md §5).

PASS / FAIL / INCONCLUSIVE, never a forced binary: a trial whose turn never
completed, or whose evidence the SDK did not supply, has not passed and has
not failed. Collapsing that third state into FAIL manufactures failures out of
API weather; collapsing it into PASS hides them. Both corrupt a baseline.

`tag` carries the SUB-KIND of a failure so triage does not start by re-reading
transcripts — e.g. I4 distinguishes `schema-reject` (the seat submitted and
was refused) from `silent-seat` (the seat never submitted at all). Both end as
default hold/0 + alert in production, and they are different defects.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"
OUTCOMES = (PASS, FAIL, INCONCLUSIVE)


@dataclass(frozen=True)
class Verdict:
    invariant: str
    outcome: str
    detail: str = ""
    tag: str = ""

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"{self.invariant}: outcome {self.outcome!r} is not one of"
                f" {OUTCOMES} — a two-valued verdict on a stochastic system is"
                " the bug this type exists to prevent")

    def to_dict(self) -> dict:
        return {"invariant": self.invariant, "outcome": self.outcome,
                "detail": self.detail, "tag": self.tag}
