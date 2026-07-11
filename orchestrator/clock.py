"""Injected time (design.md §4 Testability). Business logic never reads the
wall clock; it receives a Clock. The only real-clock implementation lives in
agents/wallclock.py — this package is purity-linted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


def iso(dt: datetime) -> str:
    """Canonical timestamp format for the DB: ISO8601 UTC, seconds precision."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime — all fund datetimes are tz-aware")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


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
