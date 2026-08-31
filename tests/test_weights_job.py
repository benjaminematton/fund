"""scripts/weights_day.py — the seams of the nightly scoring job.

A composition root like scripts/resolve_day.py: main() builds real clients
and is never called here. The arithmetic is tests/test_improve.py; what is
pinned here is what the job depends on and how it fails, because each is a
way for the scoreboard to go quietly stale.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator import improve
from orchestrator.clock import SimClock
from orchestrator.improve import WeightsConfig
from state.db import connect
from tests.test_improve import _two_seat_history

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weights_day.py"
NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("weights_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


weights_day = _load()


def _alerts(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def test_the_job_needs_only_the_database():
    """No broker, no Slack token, no Anthropic key: it reads rows and writes
    rows. Requiring anything else would let an unrelated missing var stop the
    scoreboard from ever being written (same posture as resolve_day)."""
    assert set(weights_day.REQUIRED_ENV) == {"FUND_DB"}


def test_config_comes_from_the_committed_yaml():
    assert weights_day.load_config(weights_day.CONFIG_YAML) == WeightsConfig(
        window_days=20, horizon_days=5)


def test_a_config_missing_a_key_fails_loud(tmp_path):
    bad = tmp_path / "improvement.yaml"
    bad.write_text("window_days: 20\n")
    with pytest.raises(KeyError, match="horizon_days"):
        weights_day.load_config(bad)


def test_a_normal_night_writes_and_logs(tmp_path, capsys):
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY),
                                    WeightsConfig(20, 5))

    assert out["written"] == ["a", "b"] and not out.get("failed")
    assert "weights_day: 2026-07-13 · written a, b" in capsys.readouterr().out
    assert _alerts(conn) == []


def test_a_scoring_failure_alerts_once_and_leaves_the_table_alone(
        tmp_path, monkeypatch, capsys):
    """improvement.md §6: no row, last good rows stand, ONE alert. The job
    returns rather than raising so fund-pnl.service's next leg (reflect_day,
    perishable) still runs — see the module docstring."""
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)
    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))
    before = [dict(r) for r in conn.execute("SELECT * FROM weights ORDER BY id")]

    def boom(conn, clock, cfg):
        raise RuntimeError("numpy went away")
    monkeypatch.setattr(weights_day, "write_weights", boom)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    assert out["failed"] is True
    assert [dict(r) for r in conn.execute("SELECT * FROM weights ORDER BY id")] == before
    alerts = _alerts(conn)
    assert [a["code"] for a in alerts] == ["weights_job_failed"]
    assert "RuntimeError: numpy went away" in alerts[0]["text"]
    assert "ALERT" in capsys.readouterr().out


def test_skipped_seats_are_named_in_one_alert(tmp_path, monkeypatch):
    conn = connect(tmp_path / "fund.sqlite")
    _two_seat_history(conn)

    def partial(conn, clock, cfg):
        return {"as_of_date": "2026-07-13", "written": ["b"],
                "unchanged": [], "skipped": ["quant", "macro"]}
    monkeypatch.setattr(weights_day, "write_weights", partial)

    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    alerts = _alerts(conn)
    assert [a["code"] for a in alerts] == ["weights_seat_skipped"]
    assert alerts[0]["text"].endswith(": quant, macro")
    assert "2 seat(s)" in alerts[0]["text"]


def test_a_failed_alert_write_is_logged_and_still_exits_clean(
        tmp_path, monkeypatch, capsys):
    """The likeliest cause of a scoring crash is the database, and the alert
    goes through the same connection. A raise out of the except would exit
    1 and hold back reflect_day; instead it is logged to stdout (journald)
    and the job returns — reflect_day hits the same database and fails loud
    on its own, which is OnFailure='s job."""
    conn = connect(tmp_path / "fund.sqlite")

    def boom(conn, clock, cfg):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(weights_day, "write_weights", boom)

    def alert_boom(conn, clock, code, text, **payload):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(weights_day.run_day, "_alert", alert_boom)

    out = weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))

    assert out["failed"] is True
    assert _alerts(conn) == []
    assert "ALERT NOT WRITTEN" in capsys.readouterr().out


def test_the_job_never_drains_the_outbox(tmp_path, monkeypatch):
    """It holds no Slack token. The alert sits in `events` for the next leg's
    drain (reflect_day runs right after it on fund-pnl.service)."""
    conn = connect(tmp_path / "fund.sqlite")

    def boom(conn, clock, cfg):
        raise RuntimeError("x")
    monkeypatch.setattr(weights_day, "write_weights", boom)
    weights_day.write_and_log(conn, SimClock(NIGHTLY), WeightsConfig(20, 5))
    assert conn.execute("SELECT posted_at FROM events").fetchone()["posted_at"] is None
