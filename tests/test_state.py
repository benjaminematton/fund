import pytest

from orchestrator.clock import iso
from state.db import connect
from state.transition import (EDGES, STATE_COLUMN, IllegalTransition,
                              StaleTransition, transition, try_transition)

NOW = "2026-07-06T15:30:00+00:00"

TABLES = {"signals", "critiques", "decisions", "tickets", "orders",
          "resolutions", "checkpoints", "events", "costs", "offered", "weights",
          "worklist"}

STATUSES = {
    "decisions": ["submitted", "approved", "rejected", "held", "executed", "failed", "expired"],
    "tickets": ["open", "consumed", "expired"],
    "orders": ["submitted", "filled", "partially_filled", "canceled", "rejected"],
    "checkpoints": ["pending", "running", "done", "failed"],
    "worklist": ["open", "claimed", "done", "failed", "expired"],
    "strategies": ["SPEC", "BACKTEST", "VALIDATED", "INCUBATING", "ALLOCATED",
                   "SCALED", "PROBATION", "RETIRED", "REJECTED"],
}

NON_EDGES = [(t, a, b) for t, ss in STATUSES.items()
             for a in ss for b in ss if (a, b) not in EDGES[t]]


def test_ddl_applies_cleanly_and_is_idempotent(tmp_path):
    path = tmp_path / "fund.sqlite"
    conn = connect(path)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, created_at) VALUES"
        " ('2026-07-06', 'NVDA', 'buy', 67, 't', 'i', ?)", (NOW,))
    conn.commit()
    conn.close()
    conn2 = connect(path)  # re-open existing DB: must not re-run schema or wipe data
    assert conn2.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 1
    conn2.close()


def test_every_status_table_has_a_state_machine(fund_db):
    """Every table with a state column has a machine, and every machine has
    a table. The column is `status` except where STATE_COLUMN says otherwise
    (`strategies.state`, strategy-contracts.md §2)."""
    tables = {r["name"] for r in fund_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    with_status = {t for t in tables if any(
        c["name"] == STATE_COLUMN.get(t, "status")
        for c in fund_db.execute(f"PRAGMA table_info({t})"))}
    assert with_status == set(STATUSES) == set(EDGES)


def test_foreign_keys_enforced(fund_db):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        fund_db.execute(
            "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
            " expires_at, created_at) VALUES ('t1', 999, 'NVDA', 'buy', 1, ?, ?)",
            (NOW, NOW))


@pytest.mark.parametrize("table,frm,to", NON_EDGES)
def test_every_non_edge_raises(fund_db, table, frm, to):
    key = {"id": 1} if table != "checkpoints" else {
        "run_date": "2026-07-06", "stage": "execution", "ticker": "*"}
    if table == "orders":
        key = {"client_order_id": "x"}
    if table == "worklist":
        key = {"work_id": "wk_x"}
    if table == "strategies":
        key = {"strategy_id": "x"}
    with pytest.raises(IllegalTransition):
        transition(fund_db, table, key, frm, to, NOW)


def _seed_decision(conn, status="submitted"):
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06', 'NVDA', 'buy', 67, 't', 'i', ?, ?)", (status, NOW))
    conn.commit()
    return cur.lastrowid


def test_cas_moves_row(fund_db):
    did = _seed_decision(fund_db)
    transition(fund_db, "decisions", {"id": did}, "submitted", "approved", NOW)
    row = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["status"] == "approved"


def test_cas_stale_raises_and_leaves_row(fund_db):
    did = _seed_decision(fund_db, status="approved")
    with pytest.raises(StaleTransition):
        transition(fund_db, "decisions", {"id": did}, "submitted", "approved", NOW)
    row = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["status"] == "approved"  # never overwritten


def test_try_transition_returns_false_on_stale(fund_db):
    did = _seed_decision(fund_db, status="approved")
    assert try_transition(fund_db, "decisions", {"id": did},
                          "submitted", "approved", NOW) is False
    assert try_transition(fund_db, "decisions", {"id": did},
                          "approved", "executed", NOW) is True


def test_checkpoint_transition_touches_updated_at(fund_db):
    fund_db.execute(
        "INSERT INTO checkpoints (run_date, stage, ticker, status, updated_at)"
        " VALUES ('2026-07-06', 'execution', '*', 'pending', 'old')")
    fund_db.commit()
    key = {"run_date": "2026-07-06", "stage": "execution", "ticker": "*"}
    transition(fund_db, "checkpoints", key, "pending", "running", NOW)
    row = fund_db.execute(
        "SELECT status, updated_at FROM checkpoints WHERE run_date='2026-07-06'"
        " AND stage='execution' AND ticker='*'").fetchone()
    assert row["status"] == "running" and row["updated_at"] == NOW


def _seed_ticket(conn, decision_id, status="open",
                 tid="a3f90000-0000-4000-8000-000000000001"):
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " expires_at, status, created_at) VALUES (?, ?, 'NVDA', 'buy', 67, ?, ?, ?)",
        (tid, decision_id, NOW, status, NOW))
    conn.commit()
    return tid


def _seed_order(conn, client_order_id, status="submitted"):
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " submitted_at) VALUES (?, 'NVDA', 'buy', 67, ?, ?)",
        (client_order_id, status, NOW))
    conn.commit()


def test_cas_moves_ticket(fund_db):
    # TEXT primary key (uuid), unlike the INTEGER-id decisions above — proves the
    # CAS flips a tickets row, the happy path 1b's gate/execution stages ride on.
    tid = _seed_ticket(fund_db, _seed_decision(fund_db))
    transition(fund_db, "tickets", {"id": tid}, "open", "consumed", NOW)
    row = fund_db.execute("SELECT status FROM tickets WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "consumed"


def test_cas_moves_order(fund_db):
    # decision -> ticket -> order FK chain; client_order_id IS the ticket id
    # (invariant 5). Exercises the multi-hop submitted -> partially_filled -> filled
    # path 1b's execution stage depends on.
    tid = _seed_ticket(fund_db, _seed_decision(fund_db))
    _seed_order(fund_db, tid)
    transition(fund_db, "orders", {"client_order_id": tid},
               "submitted", "partially_filled", NOW)
    transition(fund_db, "orders", {"client_order_id": tid},
               "partially_filled", "filled", NOW)
    row = fund_db.execute(
        "SELECT status FROM orders WHERE client_order_id=?", (tid,)).fetchone()
    assert row["status"] == "filled"


def test_unknown_table_or_bad_key_raises(fund_db):
    with pytest.raises(IllegalTransition):
        transition(fund_db, "signals", {"id": 1}, "a", "b", NOW)
    with pytest.raises(ValueError):
        transition(fund_db, "decisions", {"wrong_col": 1}, "submitted", "approved", NOW)


def test_submitted_to_held_is_legal(fund_db, sim_clock):
    now = iso(sim_clock.now())
    did = _seed_decision(fund_db)
    transition(fund_db, "decisions", {"id": did}, "submitted", "held", now)
    assert fund_db.execute("SELECT status FROM decisions WHERE id=?",
                           (did,)).fetchone()["status"] == "held"

def test_held_is_terminal(fund_db, sim_clock):
    # no edge out of held: held -> approved (and every other target) raises
    with pytest.raises(IllegalTransition):
        transition(fund_db, "decisions", {"id": 1}, "held", "approved",
                   iso(sim_clock.now()))


def test_ticket_and_gateresult_models_validate():
    from pydantic import ValidationError

    from state.models import GateResult, Ticket

    t = Ticket(id="a3f90000-0000-4000-8000-000000000001", decision_id=1,
               ticker="NVDA", side="buy", max_qty=67, stop_price=None,
               expires_at="2026-07-06T16:00:00+00:00")
    assert t.max_qty == 67
    with pytest.raises(ValidationError):
        Ticket(id="x", decision_id=1, ticker="NVDA", side="buy", max_qty=0,
               expires_at="2026-07-06T16:00:00+00:00")
    r = GateResult(approved=False, reason="gate_error")
    assert r.ticket is None


def test_connect_sets_wal_and_busy_timeout(tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_a_database_without_the_log_gains_it_on_reconnect(tmp_path):
    """The droplet case. _TABLES is parsed from schema.sql, so a table added
    there is created on an existing database at the next connect() — no
    migration. This pins that the new table is actually picked up by that
    mechanism, which depends on the DDL saying CREATE TABLE IF NOT EXISTS."""
    path = tmp_path / "fund.sqlite"
    conn = connect(path)
    conn.execute("DROP TABLE protection")
    conn.commit()
    conn.close()

    conn = connect(path)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='protection'"
    ).fetchone() is not None


def _seed_work(conn, wid="wk_0000000000000001", status="open"):
    conn.execute(
        "INSERT INTO worklist (work_id, kind, producer, seat, subject, status,"
        " expires_at, created_at) VALUES (?, 'spec_review', 'orchestrator',"
        " 'critic', 'spec_abc', ?, '2026-07-06T20:00:00+00:00', ?)",
        (wid, status, NOW))
    conn.commit()
    return wid


def test_transition_extra_columns_ride_in_the_same_cas_update(fund_db):
    """A claim that wins the CAS must carry its lease atomically — a second
    UPDATE could be lost to a crash and leave a claimed row with no lease."""
    wid = _seed_work(fund_db)
    ok = try_transition(fund_db, "worklist", {"work_id": wid}, "open", "claimed",
                        NOW, extra={"claimed_at": NOW,
                                    "claim_expires_at": "2026-07-06T15:35:00+00:00"})
    assert ok is True
    row = fund_db.execute("SELECT * FROM worklist WHERE work_id=?", (wid,)).fetchone()
    assert (row["status"], row["claimed_at"], row["claim_expires_at"]) == (
        "claimed", NOW, "2026-07-06T15:35:00+00:00")


def test_transition_extra_is_not_applied_when_the_cas_misses(fund_db):
    wid = _seed_work(fund_db, status="claimed")
    ok = try_transition(fund_db, "worklist", {"work_id": wid}, "open", "claimed",
                        NOW, extra={"claimed_at": "should-not-land"})
    assert ok is False
    row = fund_db.execute("SELECT claimed_at FROM worklist WHERE work_id=?", (wid,)).fetchone()
    assert row["claimed_at"] is None


def test_transition_extra_rejects_a_non_identifier_column(fund_db):
    """The f-string interpolation of `extra` keys is safe only because every
    caller passes literal column names; this makes the docstring's claim true
    by construction rather than by discipline."""
    wid = _seed_work(fund_db)
    with pytest.raises(ValueError, match="not an identifier"):
        try_transition(fund_db, "worklist", {"work_id": wid}, "open", "claimed",
                       NOW, extra={"claimed_at = ?, status": NOW})
    assert fund_db.execute("SELECT status FROM worklist WHERE work_id=?",
                           (wid,)).fetchone()["status"] == "open"


# --- strategies: the `state` + `state_version` machine (strategy-contracts §4)

def _seed_strategy(conn, state="SPEC", version=0):
    from tests.synthetic import seed_spec_row
    sid = seed_spec_row(conn)
    conn.execute("UPDATE strategies SET state = ?, state_version = ?"
                 " WHERE strategy_id = ?", (state, version, sid))
    conn.commit()
    return sid


def _strategy(conn, sid):
    return conn.execute("SELECT state, state_version, updated_at, reject_reason"
                        " FROM strategies WHERE strategy_id = ?", (sid,)).fetchone()


def test_strategies_edges_are_exactly_section_4():
    """Literal, not derived from EDGES: strategy-contracts.md §4's table, with
    PROBATION -> "prior state" spelled as the two states that enter
    PROBATION. NON_EDGES above is filtered by EDGES and so cannot see an
    edge go missing; this can."""
    assert EDGES["strategies"] == {
        ("SPEC", "BACKTEST"),
        ("SPEC", "REJECTED"), ("BACKTEST", "REJECTED"),
        ("BACKTEST", "VALIDATED"),
        ("VALIDATED", "INCUBATING"),
        ("INCUBATING", "ALLOCATED"), ("INCUBATING", "REJECTED"),
        ("ALLOCATED", "SCALED"),
        ("ALLOCATED", "PROBATION"), ("SCALED", "PROBATION"),
        ("PROBATION", "ALLOCATED"), ("PROBATION", "SCALED"),
        ("PROBATION", "RETIRED"),
    }


def test_strategies_cas_moves_the_row_bumps_the_token_and_stamps_updated_at(fund_db):
    sid = _seed_strategy(fund_db)
    later = "2026-07-07T15:30:00+00:00"
    assert try_transition(fund_db, "strategies", {"strategy_id": sid},
                          "SPEC", "BACKTEST", later,
                          expected_state_version=0) is True
    row = _strategy(fund_db, sid)
    assert (row["state"], row["state_version"], row["updated_at"]) == (
        "BACKTEST", 1, later)


def test_strategies_stale_token_writes_nothing_and_raises(fund_db):
    """§4: "every transition passes expected_state_version; mismatch ->
    no-op". Right state, wrong token: the WHERE matches nothing."""
    sid = _seed_strategy(fund_db, version=3)
    with pytest.raises(StaleTransition, match="state_version 0"):
        transition(fund_db, "strategies", {"strategy_id": sid},
                   "SPEC", "BACKTEST", NOW, expected_state_version=0)
    assert tuple(_strategy(fund_db, sid))[:2] == ("SPEC", 3)


def test_strategies_wrong_state_with_the_right_token_is_stale_too(fund_db):
    sid = _seed_strategy(fund_db, state="BACKTEST", version=1)
    assert try_transition(fund_db, "strategies", {"strategy_id": sid},
                          "SPEC", "REJECTED", NOW,
                          expected_state_version=1) is False
    assert tuple(_strategy(fund_db, sid))[:2] == ("BACKTEST", 1)


def test_strategies_extra_rides_in_the_cas_update(fund_db):
    """§2: reject_reason is "required when state='REJECTED'"; the caller
    carries it in the same UPDATE as the move, like a worklist lease."""
    sid = _seed_strategy(fund_db)
    transition(fund_db, "strategies", {"strategy_id": sid}, "SPEC", "REJECTED",
               NOW, expected_state_version=0, extra={"reject_reason": "30d idle"})
    row = _strategy(fund_db, sid)
    assert (row["state"], row["state_version"], row["reject_reason"]) == (
        "REJECTED", 1, "30d idle")


def test_strategies_requires_the_cas_token(fund_db):
    """§4's "every transition passes expected_state_version" is a
    requirement, not a default: a token-less call on this table is a bug,
    never a move."""
    sid = _seed_strategy(fund_db)
    with pytest.raises(ValueError, match="expected_state_version"):
        transition(fund_db, "strategies", {"strategy_id": sid},
                   "SPEC", "BACKTEST", NOW)
    assert tuple(_strategy(fund_db, sid))[:2] == ("SPEC", 0)


def test_status_tables_reject_a_cas_token(fund_db):
    """No other table carries state_version; a token there would be
    silently ignored, and a caller who thought it was a CAS was wrong."""
    did = _seed_decision(fund_db)
    with pytest.raises(ValueError, match="state_version"):
        transition(fund_db, "decisions", {"id": did}, "submitted", "approved",
                   NOW, expected_state_version=0)
    row = fund_db.execute("SELECT status FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["status"] == "submitted"
