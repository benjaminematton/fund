"""`make sim-report` (docs/superpowers/specs/
2026-10-13-agent-self-verification-design.md, piece 3): the agent's
'browser' for a system whose UI is Slack and a database."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.simday import TID

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sim_report.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_golden_report_shows_the_day_from_its_three_sources():
    out = _run("golden")
    assert out.returncode == 0, out.stderr
    text = out.stdout
    assert text.startswith("SIM-REPORT scenario=golden run_date=2026-07-06\n")
    assert "== slack ==" in text
    assert "-- #trade-log --" in text and "-- #pnl --" in text
    assert "bought *66 NVDA* at *$180.14*" in text
    for table in ("signals", "decisions", "tickets", "orders", "events"):
        assert f"== {table} (" in text, table
    assert TID in text
    assert "== scorecard ==" in text
    assert "SCORECARD 2026-07-06" in text


def test_default_scenario_is_golden():
    assert _run().stdout.startswith("SIM-REPORT scenario=golden ")


def test_unknown_scenario_exits_2_and_lists_the_valid_names():
    out = _run("nope")
    assert out.returncode == 2
    assert "unknown scenario 'nope'" in out.stderr
    for name in ("golden", "all_hold", "stop_expires"):
        assert name in out.stderr


def test_report_is_deterministic():
    assert _run("two_orders").stdout == _run("two_orders").stdout
