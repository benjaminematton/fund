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
from state.specs import insert_strategy_spec, specs_awaiting_critique

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


def test_awaiting_critique_decodes_json_and_drops_reviewed_specs(conn):
    sid = insert_strategy_spec(conn, StrategySpec(**SPEC), NOW)
    pending = specs_awaiting_critique(conn)
    assert [p["spec_id"] for p in pending] == [sid]
    assert pending[0]["universe"]["index"] == "Russell 1000"
    assert pending[0]["param_ranges"]["sigma"] == [1.0, 2.5, 0.25]
    conn.execute(CRITIQUE_SQL, (sid, NOW))
    conn.commit()
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
