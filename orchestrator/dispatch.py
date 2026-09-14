"""Lane B dispatcher + sweep (docs/superpowers/specs/2026-08-28-resident-seats.md,
R1). No LLM here: the wake is an injected callable, and in R1 the composition
root (scripts/run_dispatcher.py) wires one that has no consumer. Purity-linted
with the rest of orchestrator/: time is the injected Clock, sleeping is an
injected callable, and nothing imports agents/.

Alerts are events(kind='alert') via slackkit.outbox.append_alert — the design
doc's "alerts table" does not exist. Every silent state the design forbids
gets exactly one alert here: an over-lease claim, a past-due open row, a wake
that raised, a row whose (producer, kind) the allow-list rejects."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from orchestrator.clock import Clock, iso
from slackkit.outbox import append_alert, drain
from state import worklist
from state.transition import try_transition

DEFAULT_LEASE_S = 300      # a wake that outlives this is reaped by the sweep
DEFAULT_POLL_S = 5.0       # idle nap between cycles

# Called with the claimed row as a dict. Precondition: the wake must NOT
# transition its own row — dispatch_once owns the claimed -> done | failed
# edge, and a wake that flips it first makes that finalization raise
# StaleTransition and kill the loop (fail fast, by design).
RunWake = Callable[[dict], None]


@dataclass(frozen=True)
class SweepResult:
    reaped: tuple[str, ...]    # claimed -> failed (lease expired)
    expired: tuple[str, ...]   # open -> expired (past expires_at)


@dataclass(frozen=True)
class DispatchResult:
    handled: str | None        # work_id claimed this cycle, None when idle
    swept: SweepResult


def sweep(conn: sqlite3.Connection, now_iso: str) -> SweepResult:
    """No silent states. Over-lease claimed -> failed, past-due open -> expired,
    each with a #risk alert. Never requeues (re-enqueue is a human action).
    Idempotent: a row already moved is not in from_status, so the CAS no-ops
    and no second alert is written. Timestamps compare as strings (iso() is
    fixed-width UTC)."""
    reaped: list[str] = []
    for r in conn.execute(
            "SELECT work_id, kind, seat FROM worklist WHERE status = 'claimed'"
            " AND (claim_expires_at IS NULL OR claim_expires_at <= ?)"
            " ORDER BY created_at, work_id", (now_iso,)).fetchall():
        if try_transition(conn, "worklist", {"work_id": r["work_id"]},
                          "claimed", "failed", now_iso,
                          extra={"finished_at": now_iso}):
            reaped.append(r["work_id"])
            append_alert(conn, "work_lease_expired",
                         f"work_lease_expired — {r['kind']} {r['work_id']} for"
                         f" seat {r['seat']} was claimed and never finished;"
                         f" marked failed, not requeued (re-enqueue is a human"
                         f" action)", now_iso=now_iso)
    expired: list[str] = []
    for r in conn.execute(
            "SELECT work_id, kind, seat FROM worklist WHERE status = 'open'"
            " AND expires_at <= ? ORDER BY created_at, work_id",
            (now_iso,)).fetchall():
        if try_transition(conn, "worklist", {"work_id": r["work_id"]},
                          "open", "expired", now_iso,
                          extra={"finished_at": now_iso}):
            expired.append(r["work_id"])
            append_alert(conn, "work_expired",
                         f"work_expired — {r['kind']} {r['work_id']} for seat"
                         f" {r['seat']} was never claimed before its expiry;"
                         f" marked expired, not requeued", now_iso=now_iso)
    return SweepResult(tuple(reaped), tuple(expired))


def dispatch_once(conn: sqlite3.Connection, clock: Clock, run_wake: RunWake, *,
                  lease_s: int = DEFAULT_LEASE_S) -> DispatchResult:
    """Sweep, then claim at most one row and run it. A raise from run_wake
    fails the row with an alert and is NOT re-raised — the loop must outlive
    one bad wake, and the default is HOLD (invariant 4). The allow-list is
    re-checked at claim so a hand-written row never reaches a wake."""
    now = clock.now()
    swept = sweep(conn, iso(now))
    row = worklist.claim_next(
        conn, now_iso=iso(now),
        lease_until_iso=iso(now + timedelta(seconds=lease_s)))
    if row is None:
        return DispatchResult(None, swept)
    wid, kind, seat = row["work_id"], row["kind"], row["seat"]
    if not worklist.allowed(row["producer"], kind):
        done_at = iso(clock.now())
        worklist.fail(conn, wid, done_at)
        append_alert(conn, "work_disallowed",
                     f"work_disallowed — {kind} {wid} for seat {seat} carries"
                     f" producer {row['producer']!r}, which may not enqueue"
                     f" that kind; marked failed without running a wake",
                     now_iso=done_at)
        return DispatchResult(wid, swept)
    try:
        run_wake(dict(row))
    except Exception as exc:
        done_at = iso(clock.now())
        worklist.fail(conn, wid, done_at)
        append_alert(conn, "work_failed",
                     f"work_failed — {kind} {wid} for seat {seat}:"
                     f" {type(exc).__name__}: {exc}; marked failed, not"
                     f" requeued (re-enqueue is a human action)",
                     now_iso=done_at)
        return DispatchResult(wid, swept)
    worklist.finish(conn, wid, iso(clock.now()))
    return DispatchResult(wid, swept)


def _unposted(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]


def run_dispatcher(conn: sqlite3.Connection, slack, clock: Clock,
                   run_wake: RunWake, *, sleep: Callable[[float], None],
                   poll_s: float = DEFAULT_POLL_S,
                   lease_s: int = DEFAULT_LEASE_S,
                   max_cycles: int | None = None) -> int:
    """The resident loop. Each cycle: dispatch_once; then drain the outbox if
    this cycle wrote something OR an earlier drain left rows unposted (a
    transient Slack failure must retry on the next cycle, idle or not —
    drain()'s contract is "left unposted, retried on the next drain"); nap
    poll_s when idle. drain() is global, so any drain here also posts rows
    run_day queued — the outbox tolerates that. max_cycles=None runs until the
    process is killed. Returns cycles run."""
    cycles = 0
    pending = False
    while max_cycles is None or cycles < max_cycles:
        res = dispatch_once(conn, clock, run_wake, lease_s=lease_s)
        cycles += 1
        if res.handled is not None or res.swept.reaped or res.swept.expired:
            pending = True
        if pending:
            drain(conn, slack, iso(clock.now()))
            pending = _unposted(conn) > 0
        if res.handled is None:
            sleep(poll_s)
    return cycles
