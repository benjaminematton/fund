from datetime import datetime, timezone

import pytest

from orchestrator.clock import SimClock, et_run_date, iso


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


def test_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        iso(datetime(2026, 7, 6, 15, 30))


def test_wallclock_is_aware_utc():
    from agents.wallclock import WallClock

    now = WallClock().now()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 0


def test_et_run_date_same_calendar_date():
    # 23:30 UTC -> 19:30 ET, same date (schema.sql's run_date is ET, not UTC)
    assert et_run_date(datetime(2026, 7, 6, 23, 30, tzinfo=UTC)) == "2026-07-06"


def test_et_run_date_previous_calendar_date():
    # 00:30 UTC -> 20:30 ET the prior day
    assert et_run_date(datetime(2026, 7, 7, 0, 30, tzinfo=UTC)) == "2026-07-06"


def test_et_run_date_across_dst_transition():
    # 04:30 UTC is the run_date rollover instant year-round, but which side of
    # midnight it lands on depends on the ET offset: EST (winter, UTC-5) puts
    # it just before midnight the previous day; EDT (summer, UTC-4) puts it
    # just after midnight the same day.
    assert et_run_date(datetime(2026, 1, 15, 4, 30, tzinfo=UTC)) == "2026-01-14"
    assert et_run_date(datetime(2026, 7, 15, 4, 30, tzinfo=UTC)) == "2026-07-15"


def test_et_run_date_rejects_naive_datetime():
    with pytest.raises(ValueError):
        et_run_date(datetime(2026, 7, 6, 23, 30))
