"""strategy_specs / strategy_critiques — the pure state layer under the Critic.

state/ is a purity-linted package: no agents/, no SDK, no wall clock. These
tests run entirely offline against a temp DB.
"""

from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from state.db import connect
from state.models import SpecCritique, StrategySpec
from state.specs import (OrphanedSpecs, insert_strategy_spec,
                         specs_awaiting_critique)

NOW = "2026-07-06T15:00:00+00:00"

SPEC = dict(
    family="F1", seat="quant",
    hypothesis="Reversal pays for absorbing forced selling.",
    mechanism_class="liquidity_provision",
    universe={"index": "Russell 1000", "pit_constituents": True, "filters": []},
    liquidity_bucket="mega_large",
    signal_rule={"entry": "5d return below -1.5 sigma"},
    param_ranges={"sigma": [1.0, 2.5, 0.25]},
    search_budget=24, holding_period_d=5, rebalance="daily",
    expected_turnover=42.0, exit_rule="close at 5 trading days",
    invalidation="12m low-turnover spread negative for two quarters.",
    capacity_usd=4000000.0,
    predicted={"net_sharpe": 0.8, "max_dd": 0.14, "hit_rate": 0.55},
    llm_in_loop=0)

CRITIQUE_SQL = (
    "INSERT INTO strategy_critiques (spec_id, verdict, objections, seat,"
    " charter_version, model_id, created_at)"
    " VALUES (?, 'clear', '[]', 'critic', 'critic-v2', 'claude-sonnet-5', ?)")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fund.sqlite")
    yield c
    c.close()


def test_both_strategy_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"strategy_specs", "strategy_critiques"} <= names


def test_schema_reaches_a_db_that_predates_the_new_tables(tmp_path):
    """state/db.py used to apply schema.sql only when `tickets` was absent, so
    a table added later never reached an existing DB — the droplet's live one
    included. Adding a table must be enough."""
    path = tmp_path / "fund.sqlite"
    c = connect(path)
    c.execute("DROP TABLE strategy_critiques")
    c.execute("DROP TABLE strategy_specs")
    c.commit()
    c.close()
    c2 = connect(path)
    names = {r["name"] for r in c2.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"strategy_specs", "strategy_critiques"} <= names
    c2.close()


def test_a_complete_db_does_not_rerun_the_schema_script(tmp_path, monkeypatch):
    """connect() runs per TOOL CALL (agents/seats.py hands build_fund_server a
    conn_factory), so an unconditional executescript would take a write lock on
    every submit_signal and every gate hook. One sqlite_master query decides;
    the script runs only when a table is missing.

    Watches the schema READ rather than executescript: sqlite3.Connection is a
    C type whose methods cannot be patched, and connect() reads _SCHEMA only
    inside the branch that applies it, so a read is exactly an apply.
    """
    import state.db as db
    path = tmp_path / "fund.sqlite"
    connect(path).close()                       # first open builds everything

    reads = []
    real = db._SCHEMA

    class _Tripwire:
        def read_text(self):
            reads.append(1)
            return real.read_text()

    monkeypatch.setattr(db, "_SCHEMA", _Tripwire())
    connect(path).close()
    assert reads == [], "schema re-applied on a database that was already complete"


def test_reopening_a_db_never_wipes_data(tmp_path):
    path = tmp_path / "fund.sqlite"
    c = connect(path)
    insert_strategy_spec(c, StrategySpec(**SPEC), NOW)
    c.close()
    c2 = connect(path)
    assert c2.execute(
        "SELECT COUNT(*) c FROM strategy_specs").fetchone()["c"] == 1
    c2.close()


def test_insert_returns_the_content_addressed_id(conn):
    from fundbt.hashing import spec_id
    got = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    assert got == spec_id(SPEC)
    assert got.startswith("spec_")


def test_insert_is_idempotent(conn):
    a = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    b = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    assert a == b
    assert conn.execute(
        "SELECT COUNT(*) c FROM strategy_specs").fetchone()["c"] == 1


def test_a_changed_field_is_a_different_spec(conn):
    a = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    other = dict(SPEC, holding_period_d=6)
    b = insert_strategy_spec(conn, StrategySpec(**other), NOW)
    assert a != b


def test_registration_writes_the_lifecycle_row_in_state_spec(conn):
    """strategy-contracts.md §3.1: registration "INSERTs spec + `strategies`
    row in state SPEC". Both INSERTs live here rather than in the MCP handler
    because this is the one write path — so the eval fixture, the tests and
    `submit_strategy_spec` all produce the same pair of rows, and a spec is
    never registered into a state the selector cannot see."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    row = conn.execute("SELECT * FROM strategies WHERE strategy_id = ?",
                       (sid,)).fetchone()
    assert row is not None, \
        "no lifecycle row — the spec is structurally invisible to the selector"
    assert row["state"] == "SPEC"
    assert row["state_version"] == 0
    assert row["updated_at"] == NOW, "updated_at is the injected clock"
    assert row["reject_reason"] is None
    assert row["gate_results"] is None


def test_re_registration_does_not_reset_an_advanced_spec(conn):
    """INSERT OR IGNORE, never an UPSERT. §3.1 makes a duplicate registration
    idempotent — "return existing id" — and the spec table gets that free from
    the content-addressed primary key. The lifecycle row is the mutable half,
    so the same call must not rewind it: an UPSERT would put a spec already in
    BACKTEST back into SPEC and reset the state_version every CAS transition
    reads, re-queueing a strategy that has already been reviewed and run."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    advanced = "2026-07-08T15:00:00+00:00"
    conn.execute("UPDATE strategies SET state = 'BACKTEST', state_version = 3,"
                 " updated_at = ? WHERE strategy_id = ?", (advanced, sid))
    conn.commit()

    assert insert_strategy_spec(conn, StrategySpec(**SPEC),
                                "2026-07-09T15:00:00+00:00") == sid

    row = conn.execute("SELECT * FROM strategies WHERE strategy_id = ?",
                       (sid,)).fetchone()
    assert row["state"] == "BACKTEST", "re-registration rewound the lifecycle"
    assert row["state_version"] == 3, "re-registration reset the CAS token"
    assert row["updated_at"] == advanced
    assert conn.execute(
        "SELECT COUNT(*) c FROM strategies").fetchone()["c"] == 1


def test_awaiting_critique_decodes_json_and_drops_reviewed_specs(conn):
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    pending = specs_awaiting_critique(conn)
    assert [p["spec_id"] for p in pending] == [sid]
    assert pending[0]["universe"]["index"] == "Russell 1000"
    assert pending[0]["param_ranges"]["sigma"] == [1.0, 2.5, 0.25]
    conn.execute(CRITIQUE_SQL, (sid, NOW))
    conn.commit()
    assert specs_awaiting_critique(conn) == []


def test_a_spec_the_ddl_rejected_writes_no_lifecycle_row_and_does_not_raise(
        tmp_path):
    """The lifecycle INSERT SELECTs from strategy_specs instead of taking the
    id as a literal, and that is not a style choice.

    `INSERT OR IGNORE` swallows a CHECK violation on the spec row but NOT a
    foreign-key violation — SQLite's ON CONFLICT algorithms do not apply to
    foreign keys (tests/synthetic.py:69 measured it). So a payload pydantic
    accepts and the DDL rejects would raise IntegrityError out of this
    function, past agents/tools/fund_server.py's confirming SELECT, turning
    that handler's legible "was not written" refusal into a stack trace.
    Selecting from the spec table writes zero rows in exactly that case.

    The DDL is DERIVED from state/schema.sql by substitution, never restated,
    and the substitution count asserts the patch landed — same instrument as
    tests/test_submit_strategy_spec.py's fixture, which is the handler-level
    half of this. `rebalance` is the real gap: TEXT NOT NULL in the DDL, a
    bare `str` in the model.
    """
    import pathlib
    import re

    import state.db
    ddl = pathlib.Path(state.db.__file__).with_name("schema.sql").read_text()
    patched, n = re.subn(r"^  rebalance +TEXT NOT NULL,$",
                         "  rebalance        TEXT NOT NULL"
                         " CHECK(rebalance IN ('weekly')),", ddl, flags=re.M)
    assert n == 1, "schema.sql's `rebalance` column moved — re-derive this"
    c = sqlite3.connect(tmp_path / "fund.sqlite")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(patched)

    assert SPEC["rebalance"] == "daily"          # the CHECK admits 'weekly'
    insert_strategy_spec(c, StrategySpec(**SPEC), NOW)   # must not raise

    assert c.execute("SELECT COUNT(*) c FROM strategy_specs").fetchone()["c"] == 0
    assert c.execute("SELECT COUNT(*) c FROM strategies").fetchone()["c"] == 0
    c.close()


def test_a_spec_with_no_lifecycle_row_stops_the_queue_read(conn):
    """A `strategy_specs` row with no `strategies` row STOPS the G1 read. It is
    not filtered out of it.

    THIS REPLACES THE ASSERTION THAT USED TO STAND HERE — that the INNER JOIN
    quietly excluded such a spec, "the reach of the join, not a live hazard".
    Both halves of that were wrong. Excluding it is the failure
    strategy-contracts.md §3.4 forbids: "an empty list is indistinguishable
    from 'nothing pending' and would end the turn with an unreviewed spec
    behind a clean-looking trace". And the hazard is live: a spec registered
    by a build that predates §3.1's paired write carries no lifecycle row, and
    #198's hand-run driver is what puts real specs on that path.

    All three tolerant remedies fail the same way. A bare inner join DROPS the
    spec; a LEFT JOIN GUESSES that a missing row means SPEC (invariant 4
    resolves ambiguity to no action, not to a guess); a silent backfill
    INVENTS lifecycle state for a row nobody looked at. Each ends the turn
    cleanly with a spec nobody reviewed.

    The message is asserted, not just the type, because the only place it is
    ever read is a Slack alert from scripts/critic_g1.py — the operator acting
    on it does not have the source open."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    assert [p["spec_id"] for p in specs_awaiting_critique(conn)] == [sid]
    conn.execute("DELETE FROM strategies WHERE strategy_id = ?", (sid,))
    conn.commit()

    with pytest.raises(OrphanedSpecs) as exc:
        specs_awaiting_critique(conn)
    assert sid in str(exc.value), \
        "the alert does not name the spec — nobody can repair it"
    assert "INSERT INTO strategies" in str(exc.value), \
        "the alert states no repair — the operator has to read the source"


def test_a_critiqued_spec_with_no_lifecycle_row_raises_too(conn):
    """The check is NOT scoped to the queue's own filter, and that is the whole
    difference between this and a fix that only looks sufficient.

    A critiqued spec is outside the queue under BOTH predicates, so a check
    that ran only over pending rows — or only when the queue came back short —
    would pass the test above and still leave this row stranded. With no
    lifecycle row it has no lifecycle state at all: it cannot advance to
    BACKTEST either, because §3.2 reads and UPSERTs `strategies.state` and
    there is nothing to read. The stranding would then surface one gate later,
    at run_backtest, in a different lane from the one that caused it."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    conn.execute(CRITIQUE_SQL, (sid, NOW))
    conn.execute("DELETE FROM strategies WHERE strategy_id = ?", (sid,))
    conn.commit()
    assert conn.execute("SELECT 1 FROM strategy_critiques WHERE spec_id = ?",
                        (sid,)).fetchone() is not None, "not a critiqued spec"

    with pytest.raises(OrphanedSpecs) as exc:
        specs_awaiting_critique(conn)
    assert sid in str(exc.value)


def test_the_orphan_check_never_fires_on_a_spec_the_write_path_registered(conn):
    """The half that rots quietly if it is left out: a blocking check is only
    correct if it cannot fire on a healthy tree. Every state a spec can reach
    through the shipped write path is present here — pending, critiqued and
    advanced — and none of them is an orphan, so the queue behaves exactly as
    it did before the check existed.

    THE ADVANCED SPEC IS THE LOAD-BEARING ONE. It is kept out of the queue by
    the `state = 'SPEC'` predicate, NOT by the orphan check, and the two must
    not be confused: a check rewritten as "has no row in state SPEC" would
    pass both tests above and redden here, having turned every advanced
    strategy in the fund into a permanent G1 outage."""
    pending = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    critiqued = insert_strategy_spec(
        conn, StrategySpec(**dict(SPEC, search_budget=25)), NOW)
    conn.execute(CRITIQUE_SQL, (critiqued, NOW))
    advanced = insert_strategy_spec(
        conn, StrategySpec(**dict(SPEC, search_budget=26)), NOW)
    conn.execute("UPDATE strategies SET state = 'BACKTEST'"
                 " WHERE strategy_id = ?", (advanced,))
    conn.commit()

    assert [p["spec_id"] for p in specs_awaiting_critique(conn, limit=10)] == \
        [pending]


def test_a_critiqued_spec_still_in_state_spec_is_not_returned(conn):
    """Why the selector carries BOTH predicates. §4's transition table has no
    G1 edge, so a verdict moves nothing: the spec is still in SPEC after being
    critiqued. On `state = 'SPEC'` alone it would be re-selected every night,
    and submit_spec_critique is write-once (§3.4) — the second verdict is
    refused, the turn fails, and the queue head blocks everything behind it.

    The first assertion is the load-bearing one: it states the fact that makes
    the second predicate necessary, so this reddens if a G1 edge ever lands
    (#181) rather than silently becoming a tautology."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    conn.execute(CRITIQUE_SQL, (sid, NOW))
    conn.commit()

    assert conn.execute("SELECT state FROM strategies WHERE strategy_id = ?",
                        (sid,)).fetchone()["state"] == "SPEC", \
        "a G1 edge now exists — the state predicate alone may be enough"
    assert specs_awaiting_critique(conn) == []


def test_a_backlog_yields_one_spec_per_turn_oldest_first(conn):
    """One turn reviews one spec. A brief carrying the whole backlog would put
    N reviews in a turn budgeted for one, and would make the seat's max_turns
    a function of research throughput — so the ceiling measured on a one-spec
    eval case would redden on the first busy day."""
    older = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    newer = insert_strategy_spec(conn, StrategySpec(**dict(SPEC, search_budget=25)),
                                 "2026-07-07T15:00:00+00:00")
    assert [p["spec_id"] for p in specs_awaiting_critique(conn)] == [older]
    assert {p["spec_id"] for p in specs_awaiting_critique(conn, limit=10)} == \
        {older, newer}
    conn.execute(CRITIQUE_SQL, (older, NOW))
    conn.commit()
    assert [p["spec_id"] for p in specs_awaiting_critique(conn)] == [newer]


def test_same_second_registrations_have_a_deterministic_order(conn):
    """"Oldest first" only orders what has distinct timestamps. Two specs
    registered in the same second tie on created_at and fall through to
    spec_id — a content hash, so the winner is stable but arbitrary. Pinned
    because the docstring says "oldest first" and a reader could otherwise
    assume registration order is preserved within a second; it is not."""
    a = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    b = insert_strategy_spec(conn, StrategySpec(**dict(SPEC, search_budget=25)),
                             NOW)
    first = [p["spec_id"] for p in specs_awaiting_critique(conn)]
    assert first == [min(a, b)], "tie is not broken by spec_id"
    assert [p["spec_id"] for p in specs_awaiting_critique(conn)] == first, \
        "same-second order is not stable across calls"


def test_a_critique_row_must_carry_its_attribution(conn):
    """contracts.md §2: every seat-written row names the charter and model that
    produced it. NOT NULL with no default, so an INSERT that forgets fails at
    the DB rather than recording an unattributable G1 verdict."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO strategy_critiques (spec_id, verdict, objections,"
            " seat, created_at) VALUES (?, 'clear', '[]', 'critic', ?)",
            (sid, NOW))


def test_a_critique_row_refuses_the_placeholder_attribution_values(conn):
    """A NARROWING of contracts.md §2's three values, not a fourth rule. §2
    allows 'none' (orchestrator-written) and 'unknown'; neither can legally
    occur here, because nothing but submit_spec_critique writes this table and
    the orchestrator is forbidden from defaulting a G1 verdict at all."""
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    for column, bad in (("charter_version", "none"), ("charter_version", "unknown"),
                        ("model_id", "none"), ("model_id", "unknown")):
        values = {"charter_version": "critic-v2", "model_id": "claude-sonnet-5"}
        values[column] = bad
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO strategy_critiques (spec_id, verdict, objections,"
                " seat, charter_version, model_id, created_at)"
                " VALUES (?, 'clear', '[]', 'critic', ?, ?, ?)",
                (sid, values["charter_version"], values["model_id"], NOW))


def test_the_orchestrator_never_writes_a_g1_verdict():
    """strategy-contracts.md §3.4: "No default row, ever. Neither the
    orchestrator nor any handler may insert a default strategy_critiques row."

    Prose cannot hold that. orchestrator/ may legally import state.specs, so
    nothing structural stops a future stage body from inserting one the way
    run_decision already inserts default `critiques` — and that failure would
    be invisible in the worst way: specs advancing on verdicts nobody produced,
    which is the exact fail-open shape the inverted default exists to prevent.

    Same instrument the repo already uses for "no LLM code in gate/"
    (scripts/check_purity.py): a lint, not a comment."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "orchestrator"
    offenders = sorted(p.name for p in root.glob("*.py")
                       if "strategy_critiques" in p.read_text())
    assert offenders == [], \
        f"orchestrator/ references strategy_critiques: {offenders} —" \
        " at G1 the absence of a row IS the signal; writing one defaults it"


def test_spec_critique_requires_objections_iff_verdict_is_objections():
    with pytest.raises(ValidationError):
        SpecCritique(spec_id="spec_x", verdict="objections", objections=[],
                     seat="critic")
    with pytest.raises(ValidationError):
        SpecCritique(spec_id="spec_x", verdict="clear", objections=["a"],
                     seat="critic")
    ok = SpecCritique(spec_id="spec_x", verdict="objections",
                      objections=["the rule filters the wrong turnover tail"],
                      seat="critic")
    assert len(ok.objections) == 1


def test_spec_critique_caps_objections_at_three_of_two_hundred_chars():
    with pytest.raises(ValidationError):
        SpecCritique(spec_id="spec_x", verdict="objections",
                     objections=["a", "b", "c", "d"], seat="critic")
    with pytest.raises(ValidationError):
        SpecCritique(spec_id="spec_x", verdict="objections",
                     objections=["x" * 201], seat="critic")


def test_hypothesis_and_invalidation_are_capped_at_five_hundred_chars():
    with pytest.raises(ValidationError):
        StrategySpec(**dict(SPEC, hypothesis="x" * 501))
    with pytest.raises(ValidationError):
        StrategySpec(**dict(SPEC, invalidation="x" * 501))
