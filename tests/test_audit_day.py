"""scripts/audit_day.py, both directions (MVF T4): clean against a real
golden-day sim DB, and one named violation per doctored DB. An audit that
cannot detect a violation is decoration, so every check gets its own crash
shape — the DB is doctored with raw SQL exactly the way a kill mid-stage
would leave it."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_sim_day import golden_day

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_day.py"


def _load_audit():
    """scripts/ is not a package (the script is argv-driven and stdlib-only)."""
    spec = importlib.util.spec_from_file_location("audit_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_day = _load_audit()


@pytest.fixture
def day(tmp_path):
    """A real golden-day sim: DB path + the live connection to doctor it."""
    sim = golden_day(tmp_path)
    return sim, str(tmp_path / "fund.sqlite")


def _audit(day) -> list[str]:
    sim, path = day
    return audit_day.audit(path, sim.run_date)


def _doctor(day, sql: str, *args) -> None:
    sim, _ = day
    sim.conn.execute(sql, args)
    sim.conn.commit()


# --- clean ------------------------------------------------------------------

def test_golden_day_audits_clean(day):
    assert _audit(day) == []


def test_stage_list_matches_the_orchestrator(day):
    """The audit's hardcoded STAGES (it must stay stdlib-only) is the same set
    run_day actually checkpoints — otherwise 'missing' never fires, or fires
    always."""
    sim, _ = day
    stages = {r["stage"] for r in sim.conn.execute(
        "SELECT stage FROM checkpoints WHERE run_date = ?", (sim.run_date,))}
    assert stages == set(audit_day.STAGES)


# --- violations -------------------------------------------------------------

def test_checkpoint_left_running(day):
    _doctor(day, "UPDATE checkpoints SET status = 'running' WHERE stage = 'gate'")
    assert _audit(day) == ["checkpoint gate = running"]


def test_checkpoint_stage_never_ran(day):
    _doctor(day, "DELETE FROM checkpoints WHERE stage = 'close'")
    assert _audit(day) == ["checkpoint close missing"]


def test_order_left_submitted(day):
    _doctor(day, "UPDATE orders SET status = 'submitted'")
    assert _audit(day) == ["order a3f90000 stuck submitted"]


def test_decision_left_approved(day):
    _doctor(day, "UPDATE decisions SET status = 'approved' WHERE ticker = 'NVDA'")
    assert _audit(day) == ["decision NVDA stuck at approved"]


def test_covered_ticker_with_no_decision(day):
    sim, _ = day
    _doctor(day, "INSERT INTO signals (run_date, agent, ticker, direction,"
                 " confidence, summary, created_at) VALUES"
                 " (?, 'analyst', 'AAPL', 'neutral', 0, 'no report', ?)",
            sim.run_date, "2026-07-06T15:30:00+00:00")
    assert _audit(day) == ["no decision row for AAPL"]


def test_undrained_outbox_event(day):
    _doctor(day, "UPDATE events SET posted_at = NULL WHERE kind = 'digest'")
    assert _audit(day) == ["undrained outbox events: 1"]


def test_no_cost_rows(day):
    _doctor(day, "DELETE FROM costs")
    assert _audit(day) == ["no cost rows recorded"]


def test_violations_accumulate(day):
    """Findings are a list, not a first-failure — a bad day reports all of it."""
    _doctor(day, "UPDATE checkpoints SET status = 'running' WHERE stage = 'close'")
    _doctor(day, "DELETE FROM costs")
    assert _audit(day) == ["checkpoint close = running", "no cost rows recorded"]


def test_audit_of_a_day_that_never_ran(day):
    """Wrong/empty run_date must never read as clean (the vacuous-pass hole)."""
    _, path = day
    assert audit_day.audit(path, "2026-07-07") == [
        *[f"checkpoint {s} missing" for s in audit_day.STAGES],
        "no cost rows recorded"]


# --- CLI --------------------------------------------------------------------

def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def test_cli_exits_zero_and_says_clean(day):
    sim, path = day
    done = _run(path, sim.run_date)
    assert done.returncode == 0
    assert done.stdout.strip() == f"AUDIT CLEAN {sim.run_date}"


def test_cli_exits_nonzero_on_violation(day):
    sim, path = day
    _doctor(day, "DELETE FROM costs")
    done = _run(path, sim.run_date)
    assert done.returncode == 1
    assert done.stdout.strip() == "no cost rows recorded"


def test_cli_usage_error(tmp_path):
    done = _run(str(tmp_path / "fund.sqlite"))
    assert done.returncode == 2
    assert "usage:" in done.stderr
