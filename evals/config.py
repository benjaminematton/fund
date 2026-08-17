"""Per-seat eval config.

The load-bearing rule: anything that also exists in production is DERIVED from
production, never restated here. The tool glob, the deny list and the model
come from `agents/config/<seat>.yaml`; the charter comes from the path
`agents/seats.py` itself derives (CHARTERS_DIR/<seat>.md, not a cfg key). A
restated copy would drift, and the drift would present as an agent regression
— which is precisely the failure this rig is supposed to detect.

`evals/seats/<seat>.yaml` therefore holds only what is genuinely eval-owned:
the turn/cost ceilings I5 grades against, and which invariants apply.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_CONFIG = ROOT / "agents" / "config"
CHARTERS_DIR = ROOT / "charters"          # mirrors agents/seats.py:17
EVAL_SEATS = Path(__file__).resolve().parent / "seats"


@dataclass(frozen=True)
class EvalSeat:
    name: str
    model: str
    tools: list[str]                      # from production cfg
    disallowed_tools: list[str]           # from production cfg
    charter_path: Path
    charter_text: str
    charter_sha: str
    max_turns: int                        # eval ceiling (I5), not the backstop
    max_cost_usd: float                   # eval ceiling (I5), not the backstop
    invariants: list[str]
    production_max_turns: int             # the SDK backstop, for context
    production_max_budget_usd: float


def load_eval_seat(name: str) -> EvalSeat:
    """Assemble one seat's eval config. Raises on an unknown seat rather than
    returning an empty config — a silently tool-less seat is the failure mode
    build_fund_server already refuses (fund_server.py:289)."""
    eval_path = EVAL_SEATS / f"{name}.yaml"
    prod_path = PRODUCTION_CONFIG / f"{name}.yaml"
    if not eval_path.exists() or not prod_path.exists():
        known = sorted(p.stem for p in EVAL_SEATS.glob("*.yaml"))
        raise ValueError(
            f"unrecognized seat {name!r} — expected one of {known} with a"
            f" matching {prod_path.parent}/{name}.yaml")
    prod = yaml.safe_load(prod_path.read_text())
    ev = yaml.safe_load(eval_path.read_text())
    charter_path = CHARTERS_DIR / f"{name}.md"
    charter_text = charter_path.read_text()
    return EvalSeat(
        name=name,
        model=prod["model"],
        tools=list(prod["tools"]),
        disallowed_tools=list(prod.get("disallowed_tools") or []),
        charter_path=charter_path,
        charter_text=charter_text,
        charter_sha=hashlib.sha256(charter_text.encode()).hexdigest(),
        max_turns=int(ev["max_turns"]),
        max_cost_usd=float(ev["max_cost_usd"]),
        invariants=list(ev["invariants"]),
        production_max_turns=int(prod["max_turns"]),
        production_max_budget_usd=float(prod["max_budget_usd"]),
    )
