"""Offline tests for the post-close P&L job's decision seams.

scripts/close_pnl.py is a composition root like scripts/run_day.py, so main()
is never called here — it builds real clients. What IS testable is every place
it decides something: whether to post at all, whether a re-fire double-posts,
and what happens on a day the benchmark cannot be measured.

Why the job exists at all: contracts §8 specifies "P&L $ and % vs SPY" in the
EOD digest, but run_close fires at ~09:40 ET under the compressed MVF
schedule, when daily_pnl_pct is ten minutes of session and close_frame returns
yesterday's SPY bar. The actions half of the digest is correct at 09:40; the
P&L half can only be correct after the close.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from orchestrator.clock import SimClock
from slackkit.fake import FakeSlack

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "close_pnl.py"

# 2026-08-18 16:35 ET == 20:35 UTC (EDT) — the scheduled fire.
CLOSE_TIME = datetime(2026, 8, 18, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("close_pnl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


close_pnl = _load()


class _Source:
    def __init__(self, last_session: str = "2026-08-18"):
        idx = pd.date_range(end=last_session, periods=2, freq="B",
                            tz="America/New_York")
        self._frame = pd.DataFrame({"SPY": [640.0, 646.4]}, index=idx)

    def account_state(self) -> dict:
        return {"equity": 101_500.0, "last_equity": 101_000.0,
                "cash": 30_000.0, "positions": {}}

    def close_frame(self, tickers, end):
        return self._frame


@pytest.fixture
def clock():
    return SimClock(CLOSE_TIME)


def test_a_clean_run_posts_one_pnl_line_to_pnl(fund_db, clock):
    slack = FakeSlack()

    assert close_pnl.post_eod_pnl(fund_db, slack, _Source(), clock) == 1

    text = slack.posts["#pnl"][0]["text"]
    assert "2026-08-18" in text
    assert "SPY" in text and "alpha" in text
    assert "+$500.00" in text        # dollars, off last_equity, not inverted %


def test_a_re_fire_does_not_double_post(fund_db, clock):
    """launchd re-fires and manual reruns both happen. The events table is the
    idempotency record, matched on kind='pnl' + run_date."""
    slack = FakeSlack()
    close_pnl.post_eod_pnl(fund_db, slack, _Source(), clock)

    assert close_pnl.post_eod_pnl(fund_db, slack, _Source(), clock) == 0
    assert len(slack.posts["#pnl"]) == 1
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'pnl'").fetchone()["c"] == 1


def test_a_re_fire_still_drains_an_event_slack_never_took(fund_db, clock):
    """The first run appended the row but Slack was down, so it stayed
    unposted. The idempotency guard must not turn that into a permanently
    lost line — the second run posts the row it already has rather than
    appending a second one."""
    class _DeadSlack:
        def post(self, channel, text, thread_ts=None, blocks=None):
            raise ConnectionError("slack down")

    close_pnl.post_eod_pnl(fund_db, _DeadSlack(), _Source(), clock)
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'pnl'"
        " AND posted_at IS NULL").fetchone()["c"] == 1

    slack = FakeSlack()
    assert close_pnl.post_eod_pnl(fund_db, slack, _Source(), clock) == 1
    assert len(slack.posts["#pnl"]) == 1


def test_a_day_the_benchmark_cannot_be_measured_posts_nothing(fund_db, clock):
    """Holiday, or a run that fired before the close settled: SPY's last bar
    is not today's. Posting a P&L against a stale benchmark would report a day
    that never happened, so the job says nothing at all and leaves no event
    behind to drain (invariant 4)."""
    slack = FakeSlack()

    assert close_pnl.post_eod_pnl(
        fund_db, slack, _Source(last_session="2026-08-17"), clock) == 0
    assert slack.posts == {}
    assert fund_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0


def test_the_job_never_needs_an_anthropic_key(fund_db):
    """No LLM is involved in arithmetic over two broker reads. Requiring the
    key would make a missing one silence the P&L line for no reason."""
    assert "ANTHROPIC_API_KEY" not in close_pnl.REQUIRED_ENV
    assert {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
            "SLACK_BOT_TOKEN"} <= set(close_pnl.REQUIRED_ENV)
