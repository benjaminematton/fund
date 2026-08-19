"""The trace: the one artifact `grade.py` reads, and the only thing joining
the run process to the grade process.

Self-contained on purpose. It carries not just `charter_sha` (which answers
"which charter produced this baseline") but the charter TEXT, because I3's
leak scan needs the charter *as it was at run time*. A sha alone would make
every historical trace ungradeable the moment a charter is edited — which
would silently cost the rig the property that justifies the run/grade split:
a new invariant re-scores every trace ever recorded, for $0.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The columns `rows_written` carries per table, and the tables each seat writes.
#
# They live HERE, beside the field they populate, because two writers fill it
# from opposite directions — `evals/runner.py` for eval-suite trials and
# `evals/live.py` for production turns — while four graders read it (EXPECT,
# I1, I3, I4). Two copies would drift, and a grader would then see different
# columns depending on which writer produced the trace.
#
# Deliberately NOT left in runner.py where they started: `evals/live.py` runs
# on the live trading path, and production importing the eval rig is backwards
# — runner pulls in agents.seats, and the SDK with it.
ROW_COLUMNS = {
    "decisions": ["ticker", "action", "qty", "thesis", "invalidation",
                  "stop_price", "status"],
    "signals": ["agent", "ticker", "direction", "confidence", "summary"],
    "strategy_critiques": ["spec_id", "verdict", "objections", "seat"],
}

# Seat -> the tables it writes. `news` is the second analyst seat and writes
# `signals` exactly as `analyst` does. `exec` places orders rather than
# submitting rows, so it maps to nothing and yields an empty dict.
WRITE_TABLES = {"pm": ["decisions"], "analyst": ["signals"],
                "news": ["signals"], "critic": ["strategy_critiques"]}

# Tables keyed on the trading day. `strategy_critiques` is NOT: a spec is
# reviewed once, not once per day, so it carries no `run_date` and no `ticker`.
# A live scan that assumed otherwise would emit invalid SQL rather than an
# empty result — see evals/live.py:rows_written, which skips what it cannot
# scope. The eval rig has no such problem: its trial DB is fresh, so an
# unscoped select already means "this trial" (evals/runner.py:ROW_SCOPE).
DAILY_TABLES = frozenset({"decisions", "signals"})


@dataclass
class Trace:
    # identity
    case: str
    trial: int
    seat: str
    git_sha: str
    charter_sha: str
    charter_text: str
    model: str

    # what the seat was SHOWN — I1 grades sizes against allowed_actions and
    # I4 grades tickers against the brief, so both must travel in the trace
    # rather than being re-derived from the case file at grade time.
    snapshot: dict
    brief_tickers: list[str]
    # The seat-agnostic version of brief_tickers: what the seat was shown and
    # is therefore allowed to write a row about. Defaulted so every trace
    # recorded before the Critic existed still loads — I4 falls back to
    # brief_tickers when this is empty.
    brief_subjects: list[str] = field(default_factory=list)

    # what the seat DID
    tool_names: list[str] = field(default_factory=list)
    rows_written: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    alerts: list = field(default_factory=list)
    permission_denials: list = field(default_factory=list)

    # what it cost. cost_usd is None (never 0.0) when the SDK did not populate
    # total_cost_usd — a fabricated zero makes real spend look free, the same
    # lie agents/runtime.py:243 refuses to tell. I5 pairs a None here with the
    # cost_unavailable alert that production is required to raise.
    turns: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None

    # how it ended
    is_error: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Trace":
        return cls(**d)

    def write(self, root: Path | str) -> Path:
        """<root>/<git_sha>/<case>/<trial>.json"""
        path = Path(root) / self.git_sha / self.case / f"{self.trial}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        return path

    @classmethod
    def read(cls, path: Path | str) -> "Trace":
        return cls.from_dict(json.loads(Path(path).read_text()))
