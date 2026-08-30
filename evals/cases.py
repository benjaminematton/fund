"""A case is a FIXTURE, not a test: a brief, a snapshot, a clock, and the one
or two things expected of that situation specifically. The invariant grid
applies to every case implicitly and is never restated in a case file
(docs/evals/PLAN.md §2).

Two SHAPES, one dataclass. A ticker-shaped case (pm, analyst) carries
`tickers` + `snapshot` and its subjects are the tickers. A spec-shaped case
(critic at G1) carries one `spec` and its subject is that spec's id. Both
shapes are validated at load time and a case may declare exactly one: the
alternative was a second Case class, which would fork every grader.

`expect` stays an opaque dict here — grade.py owns its interpretation, and
keeping the runner ignorant of expectations is what keeps run and grade
separable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from fundbt.hashing import spec_id as compute_spec_id
from state.models import StrategySpec


@dataclass(frozen=True)
class Case:
    id: str
    seat: str
    clock: datetime
    tickers: list[str] = field(default_factory=list)   # ticker-shaped
    snapshot: dict = field(default_factory=dict)       # {cash, positions, allowed_actions}
    signals: list[dict] = field(default_factory=list)
    spec: dict | None = None                           # spec-shaped: one strategy_specs row
    journal: str = ""
    expect: dict = field(default_factory=dict)
    # "dev" | "holdout" | "" (unsplit). A set whose acceptance threshold is
    # also the thing a prompt gets tuned against measures the tuning, not the
    # prompt. Declaring the split in the case file rather than a directory
    # keeps the whole set reviewable in one place and keeps load_cases' flat
    # glob working.
    split: str = ""
    notes: str = ""

    @property
    def subjects(self) -> list[str]:
        """The things the turn must produce exactly one row each for. Seat
        graders (I4, EXPECT) key off THIS, never off `tickers` — that is what
        makes them seat-agnostic.

        A spec-shaped case hashes the COERCED fields, never the raw YAML
        mapping. state/specs.py:insert_strategy_spec — the one write path, and
        so the thing that decides what id the case actually registers — hashes
        `StrategySpec.model_dump()`, where `capacity_usd: 4000000` has already
        become `4000000.0`. Hashing the mapping instead made those two spellings
        two different ids: the runner binds `subjects[0]`, so the turn would be
        bound to a spec nothing registered and every verdict in the case would
        be refused. Every case on disk happens to round-trip, which is what kept
        this latent; the pin is
        tests/test_evals_rig.py:test_subjects_is_the_id_the_fixture_registers.
        """
        if self.spec is not None:
            return [compute_spec_id(StrategySpec(**self.spec).model_dump())]
        return list(self.tickers)


def load_case(path: Path | str) -> Case:
    raw = yaml.safe_load(Path(path).read_text())
    clock = raw["clock"]
    if isinstance(clock, str):
        clock = datetime.fromisoformat(clock)
    if clock.tzinfo is None:
        raise ValueError(
            f"case {raw['id']!r}: naive clock {clock!r} — all fund datetimes"
            " are tz-aware (orchestrator/clock.py)")
    ticker_shaped = raw.get("tickers") is not None or raw.get("snapshot") is not None
    spec_shaped = raw.get("spec") is not None
    if ticker_shaped == spec_shaped:
        raise ValueError(
            f"case {raw['id']!r}: a case declares exactly one of"
            " (tickers + snapshot) or (spec). Declaring both makes `subjects`"
            " ambiguous; declaring neither makes the case ungradeable.")
    return Case(id=raw["id"], seat=raw["seat"], clock=clock,
                tickers=list(raw.get("tickers") or []),
                snapshot=raw.get("snapshot") or {},
                signals=list(raw.get("signals") or []),
                spec=raw.get("spec"),
                journal=raw.get("journal") or "",
                expect=raw.get("expect") or {},
                split=raw.get("split") or "",
                notes=raw.get("notes") or "")


def load_cases(directory: Path | str) -> list[Case]:
    """Most consumers collapse this list to `{c.id: c}`, where a second file
    reusing an id silently replaces the first case's expectation while
    editing nothing; the rest keep the list and run that case twice. Refuse
    rather than pick a winner."""
    cases, seen = [], {}
    for path in sorted(Path(directory).glob("*.yaml")):
        case = load_case(path)
        if case.id in seen:
            raise ValueError(
                f"duplicate case id {case.id!r} in {directory}:"
                f" {seen[case.id]} and {path} — consumers that key on c.id"
                " would take the second file's expectation for both;"
                " consumers that keep the list would run this case twice."
                " Give one of them a new id.")
        seen[case.id] = path
        cases.append(case)
    return cases
