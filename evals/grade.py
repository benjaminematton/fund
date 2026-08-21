"""grade.py: reads traces, runs nothing.

The whole value of the run/grade split lives in this file's inputs: a Trace
from disk and a Case. No SDK, no network, no DB. Consequences — grader
iteration is free, historical baselines re-score for free, and a NEW invariant
retroactively covers every trace ever recorded.

Two rules that keep a grader honest:
  - An errored trace is INCONCLUSIVE across the board. Grading a turn that
    never completed manufactures failures out of API weather.
  - A grader that RAISES is INCONCLUSIVE, not a dead suite. Grader bugs are
    expected (that is why the code invariants also run offline in `make test`);
    one must not cost the other 17 trials their verdicts.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from evals.cases import Case
from evals.config import load_eval_seat
from evals.expectations import case_expectations
from evals.invariants import REGISTRY
from evals.trace import Trace
from evals.verdict import INCONCLUSIVE, Verdict

Invariant = Callable[[Trace, object, Case], Verdict]


def full_registry() -> dict[str, Invariant]:
    """The Tier S invariants plus the case's own expectation. Callers wanting
    a subset pass their own dict — grade_trace takes any mapping."""
    return {**REGISTRY, "EXPECT": case_expectations}


def seat_registry(seat_name: str) -> dict[str, Invariant]:
    """The invariants THIS seat declares in evals/seats/<seat>.yaml, plus the
    case's own expectation.

    `full_registry()` stays every invariant and stays the default for a caller
    that knows what it is grading. A seat that declares a subset gets exactly
    its own set: the Critic writes no sized orders and gets no allowed_actions,
    so I1 has nothing to grade and would score every Critic trial
    INCONCLUSIVE — and an INCONCLUSIVE trial is not a pass, which would put
    the seat's acceptance threshold permanently out of reach for a reason that
    has nothing to do with its judgment."""
    seat = load_eval_seat(seat_name)
    return {name: REGISTRY[name] for name in seat.invariants} | {
        "EXPECT": case_expectations}


@dataclass
class TrialResult:
    case: str
    trial: int
    seat: str
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.verdicts) and all(v.outcome == "PASS"
                                           for v in self.verdicts)

    @property
    def inconclusive(self) -> bool:
        return any(v.outcome == INCONCLUSIVE for v in self.verdicts)


def grade_trace(trace: Trace, case: Case,
                invariants: dict[str, Invariant]) -> TrialResult:
    seat = load_eval_seat(trace.seat)
    result = TrialResult(case=trace.case, trial=trace.trial, seat=trace.seat)
    for name, fn in invariants.items():
        if trace.is_error:
            result.verdicts.append(Verdict(
                name, INCONCLUSIVE,
                f"turn did not complete: {trace.error or 'is_error'}",
                tag="turn-error"))
            continue
        try:
            result.verdicts.append(fn(trace, seat, case))
        except Exception as exc:
            result.verdicts.append(Verdict(
                name, INCONCLUSIVE,
                f"grader raised {type(exc).__name__}: {exc}\n"
                f"{traceback.format_exc()}", tag="grader-error"))
    return result


def grade_traces(traces_root: Path | str, cases: dict[str, Case],
                 invariants: dict[str, Invariant] | None = None
                 ) -> list[TrialResult]:
    """Grade every trace under <traces_root>/<git_sha>/<case>/<trial>.json.
    A trace whose case file is gone is skipped loudly rather than guessed at.

    `invariants=None` grades each trace against ITS seat's registry — the
    right default for a traces root holding more than one seat. An explicit
    dict is honored verbatim, which is what lets a caller grade a subset.

    A case id identifies a case only WITHIN a seat — evals/cases/<seat>/ is
    per-seat, so an `a02` exists once per seat and they are different cases.
    A mapping that spans seats therefore collides on the id. That matters
    most for exactly the multi-seat root the line above is written for: the
    invariants would be selected from the TRACE's seat while the
    expectations came from another seat's case, which reads as a plausible
    verdict rather than an error. Both sides carry `seat`, so the pair is
    checked rather than assumed."""
    results = []
    for path in sorted(Path(traces_root).rglob("*.json")):
        trace = Trace.read(path)
        case = cases.get(trace.case)
        if case is None:
            raise ValueError(
                f"{path}: no case file for {trace.case!r} — a trace cannot be"
                " graded against expectations that no longer exist")
        if trace.seat != case.seat:
            raise ValueError(
                f"{path}: trace is seat {trace.seat!r} but case"
                f" {trace.case!r} in this mapping is seat {case.seat!r} —"
                " case ids repeat across seats, so this pair would score one"
                " seat against another seat's expectations")
        results.append(grade_trace(
            trace, case,
            invariants if invariants is not None else seat_registry(trace.seat)))
    return sorted(results, key=lambda r: (r.case, r.trial))
