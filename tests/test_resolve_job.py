"""Offline tests for the nightly resolutions job's decision seams.

scripts/resolve_day.py is a composition root like scripts/close_pnl.py, so
main() is never called here — it builds real clients. The arithmetic is
covered in test_resolve.py; what is pinned here is what the job DEPENDS on,
because every dependency it declares is a way for the job to go silent.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from orchestrator.clock import SimClock
from state.db import connect

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resolve_day.py"

NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("resolve_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_day = _load()


class _Source:
    def close_frame(self, tickers, end):
        idx = pd.date_range(end="2026-07-13", periods=6, freq="B",
                            tz="America/New_York")
        return pd.DataFrame({t: [180.0] * 5 + [191.20] if t != "SPY"
                             else [640.0] * 5 + [647.04] for t in tickers},
                            index=idx)


def test_the_job_needs_only_the_broker_and_the_database():
    """No Slack token and no Anthropic key: this job posts nothing and runs no
    seat. Requiring either would let an unrelated missing var stop the
    calibration record from ever being written."""
    assert set(resolve_day.REQUIRED_ENV) == {
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB"}


def test_a_run_with_nothing_due_writes_nothing_and_says_so(tmp_path, capsys):
    """An empty desk is a normal night, not a failure."""
    conn = connect(tmp_path / "fund.sqlite")

    counts = resolve_day.resolve_and_log(conn, _Source(), SimClock(NIGHTLY))

    assert counts == {"resolved": 0, "skipped": 0, "pending": 0}
    assert "resolve_day:" in capsys.readouterr().out


def test_the_run_reports_what_it_wrote(tmp_path, capsys):
    """The count is the operator's only window on this job — it posts nothing
    to Slack. A skipped row means the data pipeline needs looking at."""
    conn = connect(tmp_path / "fund.sqlite")
    conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES"
        " ('2026-07-06','NVDA','buy',96,'t','i','held','x')")
    conn.commit()

    counts = resolve_day.resolve_and_log(conn, _Source(), SimClock(NIGHTLY))

    assert counts["resolved"] == 1
    assert "resolved 1" in capsys.readouterr().out
