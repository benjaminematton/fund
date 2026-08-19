"""scripts/audit_day.py, both directions (MVF T4): clean against a real
golden-day sim DB, and one named violation per doctored DB. An audit that
cannot detect a violation is decoration, so every check gets its own crash
shape — the DB is doctored with raw SQL exactly the way a kill mid-stage
would leave it."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_sim_day import _nvda, golden_day, sim_day

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_day.py"
RUN_DAY_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_day.py"


class FailingSlack:
    """Slack whose post() raises unconditionally — reviewer's repro for Fix
    1: a real outage through drain()'s except path, rather than a doctored
    DB. Records nothing, because it delivers nothing."""

    def post(self, channel: str, text: str, thread_ts: str | None = None,
             blocks: list[dict] | None = None, username: str | None = None,
             icon_emoji: str | None = None) -> str:
        raise RuntimeError("slack outage")


def _load_audit():
    """scripts/ is not a package (the script is argv-driven and stdlib-only)."""
    spec = importlib.util.spec_from_file_location("audit_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_run_day():
    """Same pattern as _load_audit above, and as tests/test_run_day.py's own
    loader — scripts/run_day.py is the ONLY production writer of the
    self-alert marker (via the real slackkit.outbox.append_event), so this is
    what Fix 1's end-to-end test drives instead of hand-building the payload."""
    spec = importlib.util.spec_from_file_location("run_day", RUN_DAY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_day = _load_audit()
run_day_script = _load_run_day()


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


def _event(day, kind: str, payload: dict, created_at: str) -> None:
    """An already-drained event (posted_at set, so it never also trips the
    undrained check) at a chosen instant — the knob the day-scoping tests turn."""
    _doctor(day, "INSERT INTO events (kind, payload, created_at, posted_at)"
                 " VALUES (?, ?, ?, ?)",
            kind, json.dumps(payload), created_at, created_at)


def _complete_day(day, run_date: str, *, costs: bool = True) -> None:
    """Doctor in a SECOND, structurally clean day: every stage checkpointed
    done, optionally one cost row. No signals, no decisions, no orders — the
    zero-active-ticker day shape (HANDOFF-LIVE §2)."""
    stamp = f"{run_date}T15:30:00+00:00"
    for stage in audit_day.STAGES:
        _doctor(day, "INSERT INTO checkpoints (run_date, stage, ticker, status,"
                     " updated_at) VALUES (?, ?, '*', 'done', ?)",
                run_date, stage, stamp)
    if costs:
        _doctor(day, "INSERT INTO costs (run_date, agent, session_id,"
                     " usd_estimate, recorded_at) VALUES (?, 'analyst', 's', 0.01, ?)",
                run_date, stamp)


# --- clean ------------------------------------------------------------------

def test_golden_day_audits_clean(day):
    assert _audit(day) == []


@pytest.fixture
def all_hold_day(tmp_path):
    """Fix 2: the all-hold day shape — no orders, no tickets, so several
    checks are trivially satisfied. Must still audit clean for real, not by
    accident."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  pm_recs=("mvf_pm_hold.jsonl",))
    return sim, str(tmp_path / "fund.sqlite")


@pytest.fixture
def gate_reject_day(tmp_path):
    """Fix 2: the gate-reject day shape — a rejected decision, no ticket, no
    order."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()},
                  feed_break={"NVDA": {"vol_60d": float("nan")}})
    return sim, str(tmp_path / "fund.sqlite")


def test_all_hold_day_audits_clean(all_hold_day):
    assert _audit(all_hold_day) == []


def test_gate_reject_day_audits_clean(gate_reject_day):
    assert _audit(gate_reject_day) == []


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


def test_a_total_slack_outage_never_audits_clean(tmp_path):
    """Fix 1 — reviewer's repro: a golden day run against a Slack whose
    post() raises on every call. The day must not audit clean.

    A post() failure is TRANSIENT, so drain() no longer dead-letters it
    (that discarded the day's whole projection forever): the rows stay
    unposted and the undrained check is what fails the day — and every
    message is still deliverable once Slack is back."""
    sim = sim_day(tmp_path, market={"NVDA": _nvda()}, slack=FailingSlack())
    path = str(tmp_path / "fund.sqlite")

    projection_errors = sim.conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'projection_error'"
        ).fetchone()["c"]
    assert projection_errors == 0                # nothing was discarded...
    undrained = sim.conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]
    assert undrained == 6                        # ...it is all still queued
                                                 # (6 not 5: the news seat's signal)

    assert audit_day.audit(path, sim.run_date) == ["undrained outbox events: 6"]

    # and the queue really does clear the moment Slack works
    from slackkit.fake import FakeSlack
    from slackkit.outbox import drain
    slack = FakeSlack()
    assert drain(sim.conn, slack, "2026-07-06T20:00:00+00:00") == 6
    assert sum(len(v) for v in slack.posts.values()) == 6


def test_alert_event_reported(day):
    """An already-drained alert (posted_at set, so it does not also trip the
    undrained check) still needs to surface: it means something needed
    human attention."""
    _doctor(day, "INSERT INTO events (kind, payload, created_at, posted_at)"
                 " VALUES ('alert', '{\"text\": \"doctored\"}',"
                 " '2026-07-06T15:30:00+00:00', '2026-07-06T15:30:00+00:00')")
    assert _audit(day) == ["alert events raised: 1"]


def test_no_cost_rows(day):
    _doctor(day, "DELETE FROM costs")
    assert _audit(day) == ["no cost rows recorded"]


# --- day scoping (Fix 1: the self-poisoning ratchet) ------------------------

def test_an_earlier_days_alert_does_not_red_a_later_day(day):
    """THE ratchet. The alert count was global, so ONE alert on any past day —
    a pm_timeout (the DESIGNED default), a cost_unavailable, a gate_error
    drop — reddened every future audit forever. Worse, run_day's report_audit
    appends an `alert` BECAUSE the audit failed, so the count grew by one
    every single day, unbounded, and `make live-day` exited 1 with an
    `audit … FAILED` post to #risk from day two onward regardless of what
    actually happened."""
    sim, path = day
    _event(day, "alert", {"text": "pm_timeout NVDA — defaulted to hold"},
           "2026-07-06T15:30:00+00:00")
    assert audit_day.audit(path, sim.run_date) == ["alert events raised: 1"]

    _complete_day(day, "2026-07-07")
    assert audit_day.audit(path, "2026-07-07") == []


def test_an_earlier_days_dead_letter_does_not_red_a_later_day(day):
    """Same ratchet, second counter: a Slack outage on Monday must not read as
    a broken projection on Friday."""
    sim, path = day
    _event(day, "projection_error", {"event_id": 1, "kind": "digest"},
           "2026-07-06T15:30:00+00:00")
    assert audit_day.audit(path, sim.run_date) == ["dead-lettered outbox events: 1"]

    _complete_day(day, "2026-07-07")
    assert audit_day.audit(path, "2026-07-07") == []


def test_the_window_is_the_et_day_not_the_utc_day(day):
    """run_date is an ET calendar date (schema.sql), and created_at is UTC.
    21:00 ET on the 6th is 01:00 UTC on the 7th and is still the 6th's alert;
    23:00 ET on the 5th is 03:00 UTC on the 6th and is NOT."""
    sim, path = day
    _event(day, "alert", {"text": "late"}, "2026-07-07T01:00:00+00:00")
    assert audit_day.audit(path, sim.run_date) == ["alert events raised: 1"]

    _event(day, "alert", {"text": "yesterday"}, "2026-07-06T03:00:00+00:00")
    assert audit_day.audit(path, sim.run_date) == ["alert events raised: 1"]


def test_the_audits_own_alert_does_not_red_a_rerun_of_the_same_day(day):
    """report_audit appends its failure as an `alert` on the SAME ET day it
    audits, so day-scoping alone cannot stop a crash-resume re-fire (or
    HANDOFF-LIVE §3's explicit second audit run) from counting the previous
    attempt's audit alert as a fresh violation. The payload marker can."""
    _event(day, "alert", {"text": "audit 2026-07-06 FAILED: no cost rows recorded",
                          audit_day.SELF_ALERT_KEY: True},
           "2026-07-06T15:30:00+00:00")
    assert _audit(day) == []


def test_the_real_self_alert_survives_the_real_production_writer(day):
    """Fix 1 — the two tests above each hand-build the payload with their OWN
    json.dumps call, so neither exercises the actual write path. The real one
    is scripts/run_day.py's report_audit, which appends the marker through
    the real slackkit.outbox.append_event (default json.dumps separators,
    i.e. `"audit_report": true` WITH the space). A LIKE pattern hardcoding
    that space would pass both hand-built tests and still silently break the
    instant append_event's separators changed — only routing through the
    real writer would catch that, which is the whole point of this test."""
    sim, path = day

    _doctor(day, "DELETE FROM costs")
    assert audit_day.audit(path, sim.run_date) == ["no cost rows recorded"]

    # The real production writer: posts the self-alert via the real
    # append_event and drains it, exactly as a live run_day does after every
    # audited day.
    assert run_day_script.report_audit(
        sim.conn, sim.slack, path, sim.run_date, sim.clock) == 1

    # Fix the real violation so the self-alert is the only anomaly left.
    _doctor(day, "INSERT INTO costs (run_date, agent, session_id,"
                 " usd_estimate, recorded_at) VALUES (?, 'analyst', 's', 0.01, ?)",
            sim.run_date, f"{sim.run_date}T15:30:00+00:00")
    assert audit_day.audit(path, sim.run_date) == []

    # A genuine alert on the same day, sitting right next to the (excluded)
    # self-alert, must still count.
    _event(day, "alert", {"text": "cost_unavailable pm"},
           f"{sim.run_date}T15:31:00+00:00")
    assert audit_day.audit(path, sim.run_date) == ["alert events raised: 1"]


def test_a_real_alert_still_reds_the_day_it_was_raised_on(day):
    """The marker excludes exactly one thing. Everything else still counts —
    including an alert sitting next to a marked one."""
    _event(day, "alert", {"text": "audit 2026-07-06 FAILED: whatever",
                          audit_day.SELF_ALERT_KEY: True},
           "2026-07-06T15:30:00+00:00")
    _event(day, "alert", {"text": "cost_unavailable pm"},
           "2026-07-06T15:31:00+00:00")
    assert _audit(day) == ["alert events raised: 1"]


# --- Fix 4: a zero-active-ticker day is a legitimate outcome ----------------

def test_zero_active_ticker_day_needs_no_cost_rows(day):
    """HANDOFF-LIVE §2 lists `no active tickers` as a legitimate outcome, but
    with no active tickers no seat turn is ever scheduled, so `costs` has zero
    rows and the audit failed the run anyway. Every stage still ran; there was
    simply nothing to research."""
    _, path = day
    _complete_day(day, "2026-07-07", costs=False)
    assert audit_day.audit(path, "2026-07-07") == []


def test_a_day_with_coverage_and_no_cost_rows_still_fails(day):
    """The other half: a day that SHOULD have burned turns (the analyst
    covered a ticker) and recorded no cost is still a violation — the check
    stays honest, it does not just get switched off."""
    _, path = day
    _complete_day(day, "2026-07-07", costs=False)
    _doctor(day, "INSERT INTO signals (run_date, agent, ticker, direction,"
                 " confidence, summary, created_at) VALUES"
                 " (?, 'analyst', 'NVDA', 'neutral', 0, 'no report', ?)",
            "2026-07-07", "2026-07-07T15:30:00+00:00")
    _doctor(day, "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
                 " invalidation, status, created_at) VALUES"
                 " (?, 'NVDA', 'hold', 0, 't', 'i', 'held', ?)",
            "2026-07-07", "2026-07-07T15:30:00+00:00")
    assert audit_day.audit(path, "2026-07-07") == ["no cost rows recorded"]


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
