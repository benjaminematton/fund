"""Compare-and-swap state transitions — contracts.md §1. The ONLY way any
workflow row changes status. Illegal transition = raise, never overwrite."""

from __future__ import annotations

import sqlite3

EDGES: dict[str, set[tuple[str, str]]] = {
    "decisions": {("submitted", "approved"), ("submitted", "rejected"),
                  ("submitted", "held"),
                  ("approved", "executed"), ("approved", "failed"),
                  ("approved", "expired")},
    "tickets": {("open", "consumed"), ("open", "expired")},
    "orders": {("submitted", "filled"), ("submitted", "partially_filled"),
               ("submitted", "canceled"), ("submitted", "rejected"),
               ("partially_filled", "filled"), ("partially_filled", "canceled")},
    "checkpoints": {("pending", "running"), ("running", "done"),
                    ("running", "failed")},
    "worklist": {("open", "claimed"), ("open", "expired"),
                 ("claimed", "done"), ("claimed", "failed")},
    # strategy-contracts.md §4, verbatim. "PROBATION -> prior state" is the
    # two states that can enter PROBATION.
    "strategies": {("SPEC", "BACKTEST"),
                   ("SPEC", "REJECTED"), ("BACKTEST", "REJECTED"),
                   ("BACKTEST", "VALIDATED"),
                   ("VALIDATED", "INCUBATING"),
                   ("INCUBATING", "ALLOCATED"), ("INCUBATING", "REJECTED"),
                   ("ALLOCATED", "SCALED"),
                   ("ALLOCATED", "PROBATION"), ("SCALED", "PROBATION"),
                   ("PROBATION", "ALLOCATED"), ("PROBATION", "SCALED"),
                   ("PROBATION", "RETIRED")},
}

KEYS: dict[str, tuple[str, ...]] = {
    "decisions": ("id",),
    "tickets": ("id",),
    "orders": ("client_order_id",),
    "checkpoints": ("run_date", "stage", "ticker"),
    "worklist": ("work_id",),
    "strategies": ("strategy_id",),
}

# The state column is `status` everywhere except here.
STATE_COLUMN: dict[str, str] = {"strategies": "state"}

# Tables carrying a `state_version` CAS token (strategy-contracts.md §4):
# every transition passes `expected_state_version`, the WHERE matches on it,
# and the same UPDATE bumps it.
VERSIONED: frozenset[str] = frozenset({"strategies"})

# Tables whose UPDATE stamps `updated_at` with the injected clock.
TOUCHES_UPDATED_AT: frozenset[str] = frozenset({"checkpoints", "strategies"})


class IllegalTransition(Exception):
    """The requested edge does not exist in the state machine."""


class StaleTransition(Exception):
    """Legal edge, but the row is not in from_status (CAS failed)."""


def try_transition(conn: sqlite3.Connection, table: str, key: dict,
                   from_status: str, to_status: str, now_iso: str, *,
                   extra: dict[str, object] | None = None,
                   expected_state_version: int | None = None) -> bool:
    """CAS the row from from_status to to_status. True if the row moved; false
    if the row is not in from_status (lets idempotent handlers no-op on
    re-run, contracts §5.2).

    `extra` — column -> value pairs written in the SAME UPDATE as the status
    flip, so a caller that wins the CAS always carries them (a worklist claim's
    lease). Column names are code-supplied, never input.

    `expected_state_version` — required for a VERSIONED table and refused
    for any other: the CAS also matches on the token, and a hit bumps it."""
    if table not in EDGES:
        raise IllegalTransition(f"no state machine for table {table!r}")
    if (from_status, to_status) not in EDGES[table]:
        raise IllegalTransition(
            f"{table}: {from_status!r} -> {to_status!r} is not a legal edge")
    if set(key) != set(KEYS[table]):
        raise ValueError(f"{table} key must be exactly {KEYS[table]}, got {tuple(key)}")
    if table in VERSIONED:
        if expected_state_version is None:
            raise ValueError(f"{table}: expected_state_version is required"
                             " (strategy-contracts.md §4 CAS token)")
    elif expected_state_version is not None:
        raise ValueError(f"{table} has no state_version column;"
                         " expected_state_version must be None")
    state_col = STATE_COLUMN.get(table, "status")
    sets = f"{state_col} = ?"
    params: list = [to_status]
    if table in VERSIONED:
        sets += ", state_version = state_version + 1"
    if table in TOUCHES_UPDATED_AT:
        sets += ", updated_at = ?"
        params.append(now_iso)
    for col, val in (extra or {}).items():
        if not col.isidentifier():
            raise ValueError(f"{table}: extra column {col!r} is not an identifier")
        sets += f", {col} = ?"
        params.append(val)
    where = " AND ".join(f"{col} = ?" for col in KEYS[table]) + f" AND {state_col} = ?"
    params += [key[col] for col in KEYS[table]] + [from_status]
    if table in VERSIONED:
        where += " AND state_version = ?"
        params.append(expected_state_version)
    cur = conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", params)
    conn.commit()
    return cur.rowcount == 1


def transition(conn: sqlite3.Connection, table: str, key: dict,
               from_status: str, to_status: str, now_iso: str, *,
               extra: dict[str, object] | None = None,
               expected_state_version: int | None = None) -> None:
    """CAS that raises StaleTransition when the row is not in from_status
    (or, on a VERSIONED table, not at expected_state_version)."""
    if not try_transition(conn, table, key, from_status, to_status, now_iso,
                          extra=extra, expected_state_version=expected_state_version):
        token = ("" if expected_state_version is None
                 else f" at state_version {expected_state_version}")
        raise StaleTransition(
            f"{table} {key}: not in {from_status!r}{token} (or missing) — refusing to overwrite")
