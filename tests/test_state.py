import pytest

from orchestrator.clock import iso
from state.db import connect
from state.transition import (EDGES, IllegalTransition, StaleTransition,
                              transition, try_transition)

NOW = "2026-07-06T15:30:00+00:00"

TABLES = {"signals", "critiques", "decisions", "tickets", "orders",
          "resolutions", "checkpoints", "events", "costs"}

STATUSES = {
    "decisions": ["submitted", "approved", "rejected", "held", "executed", "failed", "expired"],
    "tickets": ["open", "consumed", "expired"],
    "orders": ["submitted", "filled", "partially_filled", "canceled", "rejected"],
    "checkpoints": ["pending", "running", "done", "failed"],
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
