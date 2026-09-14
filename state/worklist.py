"""worklist rows — Lane B scheduling intent (contracts.md §1, §2; resident-
seats design R1). Row operations only: no alerts, no loop, no clock — those
live in orchestrator/dispatch.py. A row is never truth; the wake re-reads its
subject from the table that owns it.

Purity-linted: pure Python + sqlite3 + fundbt.hashing (the ONLY permitted
hasher)."""

from __future__ import annotations

import json
import sqlite3

from fundbt.hashing import work_id
from state.transition import transition, try_transition

# Kind-by-producer allow-list: which deterministic code path may enqueue which
# kind. An agent never enqueues work, and a producer never gains a kind by
# accident (invariant 6). R2 (spec_review consumer) and R3 (slack_listener ->
# mention) extend this table in their own lanes.
PRODUCER_KINDS: dict[str, frozenset[str]] = {
    "orchestrator": frozenset({"spec_review"}),
    "alert_filer": frozenset({"alert_triage"}),
}


class DisallowedWork(ValueError):
    """(producer, kind) is not in PRODUCER_KINDS."""


def allowed(producer: str, kind: str) -> bool:
    return kind in PRODUCER_KINDS.get(producer, frozenset())


def enqueue(conn: sqlite3.Connection, *, kind: str, producer: str, seat: str,
            subject: str, expires_at: str, now_iso: str,
            payload: dict | None = None, not_before: str | None = None,
            attempts: int = 0) -> str:
    """Insert an open row and return its work_id. Idempotent: the id is
    work_id(kind, subject, attempts), so a repeat is a no-op and the first
    payload wins. Re-enqueue after a failure is a human action that passes
    attempts+1 — a new row, never a flip of the failed one."""
    if not allowed(producer, kind):
        raise DisallowedWork(f"{producer!r} may not enqueue {kind!r}")
    wid = work_id(kind, subject, str(attempts))
    conn.execute(
        "INSERT OR IGNORE INTO worklist (work_id, kind, producer, seat, subject,"
        " payload, attempts, not_before, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, kind, producer, seat, subject,
         json.dumps(payload or {}, sort_keys=True), attempts, not_before,
         expires_at, now_iso))
    conn.commit()
    return wid


def claim_next(conn: sqlite3.Connection, *, now_iso: str, lease_until_iso: str,
               seat: str | None = None) -> sqlite3.Row | None:
    """Oldest claimable open row, CAS'd to claimed with its lease. None when
    nothing is claimable. Two claimers racing on one row: the UPDATE's
    `status = 'open'` predicate lets exactly one win; the loser moves on to
    the next candidate.

    Timestamps compare as strings: every value is orchestrator.clock.iso()
    (UTC, seconds precision, fixed width), so lexical order is time order."""
    where = ("status = 'open' AND (not_before IS NULL OR not_before <= ?)"
             " AND expires_at > ?")
    params: list = [now_iso, now_iso]
    if seat is not None:
        where += " AND seat = ?"
        params.append(seat)
    candidates = conn.execute(
        f"SELECT work_id FROM worklist WHERE {where}"
        " ORDER BY created_at, work_id", params).fetchall()
    for c in candidates:
        if try_transition(conn, "worklist", {"work_id": c["work_id"]},
                          "open", "claimed", now_iso,
                          extra={"claimed_at": now_iso,
                                 "claim_expires_at": lease_until_iso}):
            return conn.execute("SELECT * FROM worklist WHERE work_id = ?",
                                (c["work_id"],)).fetchone()
    return None


def finish(conn: sqlite3.Connection, wid: str, now_iso: str) -> None:
    """claimed -> done. Raises StaleTransition if the row is not claimed."""
    transition(conn, "worklist", {"work_id": wid}, "claimed", "done", now_iso,
               extra={"finished_at": now_iso})


def fail(conn: sqlite3.Connection, wid: str, now_iso: str) -> None:
    """claimed -> failed. Raises StaleTransition if the row is not claimed.
    Never requeues: re-enqueue is a human action (design, 'No silent states')."""
    transition(conn, "worklist", {"work_id": wid}, "claimed", "failed", now_iso,
               extra={"finished_at": now_iso})
