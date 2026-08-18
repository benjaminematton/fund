"""A case is a FIXTURE, not a test: a brief, a snapshot, a clock, and the one
or two things expected of that situation specifically. The invariant grid
applies to every case implicitly and is never restated in a case file
(docs/evals/PLAN.md §2).

`expect` stays an opaque dict here — grade.py owns its interpretation, and
keeping the runner ignorant of expectations is what keeps run and grade
separable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Case:
    id: str
    seat: str
    clock: datetime
    tickers: list[str]                 # today's ACTIVE tickers (stage prompt)
    snapshot: dict                     # {cash, positions, allowed_actions}
    signals: list[dict] = field(default_factory=list)
    journal: str = ""
    expect: dict = field(default_factory=dict)
    notes: str = ""


def load_case(path: Path | str) -> Case:
    raw = yaml.safe_load(Path(path).read_text())
    clock = raw["clock"]
    if isinstance(clock, str):
        clock = datetime.fromisoformat(clock)
    if clock.tzinfo is None:
        raise ValueError(
            f"case {raw['id']!r}: naive clock {clock!r} — all fund datetimes"
            " are tz-aware (orchestrator/clock.py)")
    return Case(id=raw["id"], seat=raw["seat"], clock=clock,
                tickers=list(raw["tickers"]), snapshot=raw["snapshot"],
                signals=list(raw.get("signals") or []),
                journal=raw.get("journal") or "",
                expect=raw.get("expect") or {},
                notes=raw.get("notes") or "")


def load_cases(directory: Path | str) -> list[Case]:
    return [load_case(p) for p in sorted(Path(directory).glob("*.yaml"))]
