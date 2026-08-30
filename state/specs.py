"""Read/write path for `strategy_specs` (strategy-contracts.md §2).

Lives in state/ for the same reason state/critiques.py does: orchestrator/
must not import from agents/ (CLAUDE.md), and the G1 stage that assigns the
Critic its turn is orchestrator code. The `submit_strategy_spec` MCP handler
calls insert_strategy_spec rather than writing its own INSERT — one write
path, so a spec the fixture can build is a spec production can build.

Purity-linted package: pure Python + sqlite3 + pydantic + fundbt.hashing
(the ONLY permitted hasher, strategy-contracts.md §1). No wall clock.
"""

from __future__ import annotations

import json
import sqlite3

from fundbt.hashing import spec_id as compute_spec_id
from state.models import StrategySpec

JSON_COLUMNS = ("universe", "signal_rule", "param_ranges", "predicted")
COLUMNS = ("family", "seat", "hypothesis", "mechanism_class", "universe",
           "liquidity_bucket", "signal_rule", "param_ranges", "search_budget",
           "holding_period_d", "rebalance", "expected_turnover", "exit_rule",
           "invalidation", "capacity_usd", "predicted", "llm_in_loop")


def insert_strategy_spec(conn: sqlite3.Connection, spec: StrategySpec,
                         now_iso: str) -> str:
    """INSERT one immutable spec; return its content-addressed id.

    Idempotent by construction: the id IS the hash of the fields, so a
    re-insert of identical content collides on the primary key and is ignored.
    """
    fields = spec.model_dump()
    sid = compute_spec_id(fields)
    values = [json.dumps(fields[c], sort_keys=True) if c in JSON_COLUMNS
              else fields[c] for c in COLUMNS]
    conn.execute(
        f"INSERT OR IGNORE INTO strategy_specs"
        f" (spec_id, {', '.join(COLUMNS)}, created_at)"
        f" VALUES ({', '.join(['?'] * (len(COLUMNS) + 2))})",
        [sid, *values, now_iso])
    conn.commit()
    return sid


def specs_awaiting_critique(conn: sqlite3.Connection, *,
                            limit: int = 1) -> list[dict]:
    """Registered specs with no G1 verdict yet, oldest first.

    The absence of a `strategy_critiques` row is the whole selector: at G1 a
    spec with no verdict has not been reviewed, and nothing anywhere writes a
    default row (the design's inverted default).

    DEFAULT LIMIT 1, deliberately. The design has the orchestrator assign the
    Critic a turn when a spec enters SPEC — one turn per spec — so a brief
    carrying the whole backlog would put N reviews in a turn budgeted for one.
    It would also make max_turns a function of research throughput rather than
    of the seat, so the ceiling measured against a one-spec eval case would
    redden on the first busy day. A future batched turn is a `limit=` argument,
    not a refactor.

    KNOWN DIVERGENCE from strategy-contracts.md §4: the canonical selector for
    a reviewable spec is `strategies.state == 'SPEC'`, but the `strategies`
    lifecycle table is Phase-5 work and is deliberately not created here.
    "Has no critique row" is equivalent while nothing else writes either table,
    and is the condition to replace when `strategies` lands.

    ORDER is oldest-first by created_at, then spec_id. Two specs registered
    in the same second tie on the timestamp and are ordered by hash — stable
    across calls, but not registration order. Nothing depends on which of two
    same-second specs is reviewed first; both get a turn.

    JSON columns are decoded here so the tool layer hands the seat structured
    data, never a string it might try to parse.
    """
    rows = conn.execute(
        "SELECT s.* FROM strategy_specs s"
        " LEFT JOIN strategy_critiques c ON c.spec_id = s.spec_id"
        " WHERE c.spec_id IS NULL"
        " ORDER BY s.created_at, s.spec_id"
        " LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        spec = dict(row)
        for col in JSON_COLUMNS:
            spec[col] = json.loads(spec[col])
        out.append(spec)
    return out
