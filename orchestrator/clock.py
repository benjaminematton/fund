"""Injected time (design.md §4 Testability). Business logic never reads the
wall clock; it receives a Clock. The only real-clock implementation lives in
agents/wallclock.py — this package is purity-linted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class Clock(Protocol):
    def now(self) -> datetime: ...


def iso(dt: datetime) -> str:
    """Canonical timestamp format for the DB: ISO8601 UTC, seconds precision."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime — all fund datetimes are tz-aware")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def et_run_date(now: datetime) -> str:
    """YYYY-MM-DD in America/New_York — schema.sql documents run_date as ET,
    not UTC. Pure: takes a datetime, never reads the clock itself, so callers
    keep the injected-Clock discipline and this stays purity-lint clean.
    Naive datetimes are rejected (all fund datetimes are tz-aware, see
    iso()) rather than silently assumed to be UTC."""
    if now.tzinfo is None:
        raise ValueError("naive datetime — all fund datetimes are tz-aware")
    return now.astimezone(_ET).date().isoformat()


def et_hhmm(now: datetime) -> str:
    """HH:MM in America/New_York — the twin of et_run_date for a gate
    ticket's expiry. Every other time posted to #risk is ET; a bare UTC
    HH:MM there misreads as a much longer TTL than it is. Pure and naive-
    datetime-rejecting for the same reasons as et_run_date."""
    if now.tzinfo is None:
        raise ValueError("naive datetime — all fund datetimes are tz-aware")
    return now.astimezone(_ET).strftime("%H:%M")


class SimClock:
    """Settable, acceleratable clock for tests and sim-day (acceptance §0)."""

    def __init__(self, start: datetime):
        self._now = _aware(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = _aware(dt)

    def advance(self, *, seconds: int = 0, minutes: int = 0, hours: int = 0,
                days: int = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours,
                               days=days)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("SimClock requires tz-aware datetimes")
    return dt
