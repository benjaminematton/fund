from datetime import datetime, timezone

import pytest

from orchestrator.clock import SimClock, iso


UTC = timezone.utc


def test_simclock_returns_start():
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    assert c.now() == datetime(2026, 7, 6, 15, 30, tzinfo=UTC)


def test_simclock_set_and_advance():
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    c.advance(minutes=45)
    assert c.now() == datetime(2026, 7, 6, 16, 15, tzinfo=UTC)
    c.advance(days=5)  # acceleratable: jump days at will
    assert c.now() == datetime(2026, 7, 11, 16, 15, tzinfo=UTC)
    c.set(datetime(2026, 7, 6, 12, 0, tzinfo=UTC))
    assert c.now() == datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def test_simclock_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 7, 6, 15, 30))
    c = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=UTC))
    with pytest.raises(ValueError):
        c.set(datetime(2026, 7, 6))


def test_iso_normalizes_to_utc_seconds():
    assert iso(datetime(2026, 7, 6, 15, 30, tzinfo=UTC)) == "2026-07-06T15:30:00+00:00"


def test_wallclock_is_aware_utc():
    from agents.wallclock import WallClock

    now = WallClock().now()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 0
