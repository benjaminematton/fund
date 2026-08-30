"""Read/write path for `strategy_specs` and its `strategies` lifecycle row
(strategy-contracts.md §2). Registration writes both (§3.1).

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
    """INSERT one immutable spec plus its lifecycle row; return the spec id.

    strategy-contracts.md §3.1: registration "INSERTs spec + `strategies` row
    in state SPEC". Both writes are here rather than in the
    `submit_strategy_spec` handler because this is the one write path (module
    docstring) — so the eval fixture and production register identically, and
    no caller can produce a spec the §4 lifecycle cannot see.

    Idempotent by construction: the id IS the hash of the fields, so a
    re-insert of identical content collides on the primary key and is ignored.
    The lifecycle row uses INSERT OR IGNORE for the same reason and NOT an
    UPSERT — it is the mutable half, and a re-registration must not rewind a
    spec that has already advanced to BACKTEST or reset the state_version a
    CAS transition reads.

    ORDER IS LOAD-BEARING: strategies.strategy_id REFERENCES
    strategy_specs(spec_id) and state/db.py:22 sets PRAGMA foreign_keys = ON,
    so the spec row must exist before the lifecycle row that names it.

    THE LIFECYCLE INSERT SELECTS FROM strategy_specs rather than taking `sid`
    as a literal, so the foreign key is satisfied by construction and the two
    rows cannot disagree about which specs exist. It has to: `INSERT OR
    IGNORE` swallows a CHECK violation on the spec row but NOT a foreign-key
    violation (SQLite's ON CONFLICT algorithms do not apply to foreign keys —
    tests/synthetic.py:69 measured this). A payload pydantic accepts and the
    DDL rejects would therefore raise IntegrityError from here instead of
    reaching the handler's confirming SELECT, turning a legible "was not
    written" refusal into a stack trace. Selecting from the spec table writes
    zero rows in exactly that case and leaves the refusal where it belongs.

    ONE COMMIT, so both rows land together or neither does — and so the
    handler's write stays the two transactions
    agents/tools/fund_server.py:263 documents (this function, then
    append_event), not three.
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
    conn.execute(
        "INSERT OR IGNORE INTO strategies (strategy_id, state, updated_at)"
        " SELECT spec_id, 'SPEC', ? FROM strategy_specs WHERE spec_id = ?",
        (now_iso, sid))
    conn.commit()
    return sid


def specs_awaiting_critique(conn: sqlite3.Connection, *,
                            limit: int = 1) -> list[dict]:
    """Specs whose lifecycle row is in SPEC and that carry no G1 verdict yet,
    oldest first.

    BOTH PREDICATES, and they are not redundant. strategy-contracts.md §4
    makes `strategies.state == 'SPEC'` the canonical condition for a spec
    awaiting review, and §2 used to call that equivalent to "has no critique
    row". It is not: §4's transition table has NO G1 edge, so writing a
    verdict moves nothing out of SPEC. On `state = 'SPEC'` alone an
    already-critiqued spec is re-selected every night; submit_spec_critique is
    write-once (§3.4), so the second verdict is refused, the turn fails, and
    the queue head blocks every spec behind it forever. Measured against the
    full suite, not reasoned: dropping `c.spec_id IS NULL` reddens twelve
    tests across three files, including critic_g1's "never bought again".

    WHICH ONE IS LOAD-BEARING WILL SWAP. Today the critique predicate does the
    work and the state predicate is a structural bound — it is what keeps a
    spec with no lifecycle row out of the queue. When §4 grows a G1 edge
    (#181) so that a verdict advances or rejects the spec, the state predicate
    becomes the one that retires reviewed specs, and the critique predicate
    degrades to a guard against the window between the two writes. Neither is
    safe to drop before that lands.

    The INNER JOIN is deliberate: a spec with no `strategies` row is not
    "assumed pending". Defaulting a missing lifecycle row to SPEC would be a
    guess, and invariant 4 resolves ambiguity to no action. Registration
    writes both rows in one transaction (insert_strategy_spec above), so the
    orphan is not a state any write path produces.

    DEFAULT LIMIT 1, deliberately. The design has the orchestrator assign the
    Critic a turn when a spec enters SPEC — one turn per spec — so a brief
    carrying the whole backlog would put N reviews in a turn budgeted for one.
    It would also make max_turns a function of research throughput rather than
    of the seat, so the ceiling measured against a one-spec eval case would
    redden on the first busy day. A future batched turn is a `limit=` argument,
    not a refactor.

    ORDER is oldest-first by created_at, then spec_id. Two specs registered
    in the same second tie on the timestamp and are ordered by hash — stable
    across calls, but not registration order. Nothing depends on which of two
    same-second specs is reviewed first; both get a turn.

    NO DATE BOUND, which scripts/critic_g1.py:26 relies on to put this leg
    last: a spec skipped tonight is re-selected every night after.

    JSON columns are decoded here so the tool layer hands the seat structured
    data, never a string it might try to parse.
    """
    rows = conn.execute(
        "SELECT s.* FROM strategy_specs s"
        " JOIN strategies st ON st.strategy_id = s.spec_id"
        " LEFT JOIN strategy_critiques c ON c.spec_id = s.spec_id"
        " WHERE st.state = 'SPEC' AND c.spec_id IS NULL"
        " ORDER BY s.created_at, s.spec_id"
        " LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        spec = dict(row)
        for col in JSON_COLUMNS:
            spec[col] = json.loads(spec[col])
        out.append(spec)
    return out
