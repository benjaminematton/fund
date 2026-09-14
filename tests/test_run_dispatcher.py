"""scripts/run_dispatcher.py — the Lane B composition root (resident-seats R1,
#228). R1 registers no consumer: every claimed row fails loudly."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.clock import SimClock, iso
from slackkit.fake import FakeSlack
from state import worklist
from state.db import connect

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_dispatcher", ROOT / "scripts" / "run_dispatcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_dispatcher = _load()
START = datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc)


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "true")
    monkeypatch.setenv("FUND_DB", str(tmp_path / "fund.sqlite"))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_CHANNEL_OVERRIDES", raising=False)


def test_no_consumer_raises_naming_the_kind():
    with pytest.raises(run_dispatcher.NoConsumer, match="spec_review"):
        run_dispatcher.no_consumer({"kind": "spec_review", "work_id": "wk_x"})


def test_main_fails_every_row_loudly_and_exits_zero(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    slack = FakeSlack()
    monkeypatch.setattr(run_dispatcher, "_build_slack", lambda env, environ: slack)
    monkeypatch.setattr(run_dispatcher.time, "sleep", lambda s: None)
    conn = connect(tmp_path / "fund.sqlite")
    wid = worklist.enqueue(conn, kind="spec_review", producer="orchestrator",
                           seat="critic", subject="spec_abc",
                           expires_at="2999-01-01T00:00:00+00:00",
                           now_iso=iso(START))
    conn.close()

    assert run_dispatcher.main(["--cycles", "2"]) == 0

    conn = connect(tmp_path / "fund.sqlite")
    assert conn.execute("SELECT status FROM worklist WHERE work_id=?",
                        (wid,)).fetchone()["status"] == "failed"
    alerts = [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert [a["code"] for a in alerts] == ["work_failed"]
    assert "NoConsumer" in alerts[0]["text"]
    assert len(slack.posts["#risk"]) == 1


def test_main_exits_one_and_alerts_when_the_body_raises(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    slack = FakeSlack()
    monkeypatch.setattr(run_dispatcher, "_build_slack", lambda env, environ: slack)

    def boom(*a, **k):
        raise RuntimeError("db on fire")
    monkeypatch.setattr(run_dispatcher, "run_dispatcher_loop", boom)

    assert run_dispatcher.main(["--cycles", "1"]) == 1
    conn = connect(tmp_path / "fund.sqlite")
    alerts = [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert [a["code"] for a in alerts] == ["dispatcher_failed"]
    assert "db on fire" in alerts[0]["text"]
    assert len(slack.posts["#risk"]) == 1


def test_main_refuses_to_run_without_the_paper_flag(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("ALPACA_PAPER_TRADE", "false")
    with pytest.raises(SystemExit):
        run_dispatcher.main(["--cycles", "1"])


def test_main_exits_zero_when_another_dispatcher_holds_the_lock(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    lock = run_dispatcher.run_day.acquire_lock(tmp_path / run_dispatcher.LOCK_NAME)
    assert lock is not None
    monkeypatch.setattr(run_dispatcher, "_build_slack",
                        lambda env, environ: pytest.fail("must not build slack"))
    assert run_dispatcher.main(["--cycles", "1"]) == 0
