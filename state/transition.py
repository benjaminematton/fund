"""Compare-and-swap state transitions — contracts.md §1. The ONLY way any
workflow row changes status. Illegal transition = raise, never overwrite."""

from __future__ import annotations

import sqlite3

EDGES: dict[str, set[tuple[str, str]]] = {
    "decisions": {("submitted", "approved"), ("submitted", "rejected"),
                  ("approved", "executed"), ("approved", "failed"),
                  ("approved", "expired")},
    "tickets": {("open", "consumed"), ("open", "expired")},
    "orders": {("submitted", "filled"), ("submitted", "partially_filled"),
               ("submitted", "canceled"), ("submitted", "rejected"),
               ("partially_filled", "filled"), ("partially_filled", "canceled")},
    "checkpoints": {("pending", "running"), ("running", "done"),
                    ("running", "failed")},
}

KEYS: dict[str, tuple[str, ...]] = {
    "decisions": ("id",),
    "tickets": ("id",),
    "orders": ("client_order_id",),
    "checkpoints": ("run_date", "stage", "ticker"),
}


class IllegalTransition(Exception):
    """The requested edge does not exist in the state machine."""


class StaleTransition(Exception):
    """Legal edge, but the row is not currently in from_status (CAS failed)."""


def try_transition(conn: sqlite3.Connection, table: str, key: dict,
                   from_status: str, to_status: str, now_iso: str) -> bool:
    """CAS the row from from_status to to_status. False if the row is not in
    from_status (lets idempotent handlers no-op on re-run, contracts §5.2)."""
    if table not in EDGES:
        raise IllegalTransition(f"no state machine for table {table!r}")
    if (from_status, to_status) not in EDGES[table]:
        raise IllegalTransition(
            f"{table}: {from_status!r} -> {to_status!r} is not a legal edge")
    if set(key) != set(KEYS[table]):
        raise ValueError(f"{table} key must be exactly {KEYS[table]}, got {tuple(key)}")
    sets = "status = ?" + (", updated_at = ?" if table == "checkpoints" else "")
    params: list = [to_status] + ([now_iso] if table == "checkpoints" else [])
    where = " AND ".join(f"{col} = ?" for col in KEYS[table]) + " AND status = ?"
    params += [key[col] for col in KEYS[table]] + [from_status]
    cur = conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", params)
    conn.commit()
    return cur.rowcount == 1


def transition(conn: sqlite3.Connection, table: str, key: dict,
               from_status: str, to_status: str, now_iso: str) -> None:
    """CAS that raises StaleTransition when the row is not in from_status."""
    if not try_transition(conn, table, key, from_status, to_status, now_iso):
        raise StaleTransition(
            f"{table} {key}: not in {from_status!r} (or missing) — refusing to overwrite")
