"""The daily scorecard ranks what to read first, and never fails the day.

Two properties carry the whole design and both are tested here by name.

Ranking is a FIXED severity order, not a weighted score. A weight is a number
someone tunes until the day looks good — a scoreboard you can p-hack with no
LLM involved.

The exit code is always 0. Failing the day belongs to scripts/audit_day.py
alone, which is wired into run_day and the systemd failure path; a scorecard
that could fail the day would make every mediocre day an incident, and the
whole point is a ranking a human reads on good days too.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_sim_day import golden_day

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_day.py"


def _load():
    """scripts/ is not a package — same loader as tests/test_audit_day.py."""
    spec = importlib.util.spec_from_file_location("score_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_day = _load()


@pytest.fixture
def day(tmp_path):
    sim = golden_day(tmp_path)
    return sim, str(tmp_path / "fund.sqlite")


def _kinds(path, run_date):
    return [r["kind"] for r in score_day.score(path, run_date)]


def _rows(path, run_date, kind):
    return [r for r in score_day.score(path, run_date) if r["kind"] == kind]


# --- the ranking -------------------------------------------------------------

def test_a_clean_day_ranks_nothing_urgent(day):
    """The golden day: every seat reported, the PM decided, the gate approved,
    the order filled. Severity 0-2 is the 'something went wrong' band and it
    must be empty, or the scorecard cries wolf on the reference day."""
    sim, path = day
    assert [r for r in score_day.score(path, sim.run_date)
            if r["severity"] <= 2] == []


def test_defaults_outrank_gate_rejects_which_outrank_outliers(day):
    """The fixed order, asserted end to end. A silent seat is worse than a
    blocked trade, which is worse than an expensive one: the first means the
    fund did not think, the second means it thought and was overruled, the
    third means it thought expensively."""
    sim, path = day
    conn = sim.conn
    conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    conn.execute("UPDATE decisions SET status = 'rejected' WHERE ticker = 'NVDA'")
    _seed_cost_history(conn, "pm", sim.run_date)
    conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, 'pm', 's', 99.0, ?)",
        (sim.run_date, f"{sim.run_date}T13:00:00+00:00"))
    conn.commit()

    kinds = _kinds(path, sim.run_date)
    assert kinds.index("critic_timeout") < kinds.index("gate_rejected")
    assert kinds.index("gate_rejected") < kinds.index("cost_outlier")


def test_rows_come_back_sorted_by_severity(day):
    """The caller prints them in order and reads top-down. Sorting is the
    script's job, not the reader's."""
    sim, path = day
    sim.conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    sim.conn.execute("UPDATE decisions SET status = 'expired'")
    sim.conn.commit()
    severities = [r["severity"] for r in score_day.score(path, sim.run_date)]
    assert severities == sorted(severities)


# --- severity 0: the fund did not think --------------------------------------

def test_a_silent_seat_is_one_line_with_a_count_not_one_line_per_ticker(day):
    """The load-bearing aggregation. The defaulted-signal guarantee is per
    (seat, ticker), so a 3-ticker day with a silent seat writes three rows —
    and that population grows with seat count, against the eleven seats
    design.md commits to. One line per row would bury every other severity
    under near-identical entries on exactly the days worth reading."""
    sim, path = day
    now = f"{sim.run_date}T13:00:00+00:00"
    for ticker in ("AMD", "TSLA", "INTC"):
        sim.conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at, charter_version, model_id)"
            " VALUES (?, 'news', ?, 'neutral', 0, 'no report', ?, 'none',"
            " 'none')", (sim.run_date, ticker, now))
    sim.conn.commit()

    rows = _rows(path, sim.run_date, "defaulted_signal")
    assert len(rows) == 1, rows
    assert "news" in rows[0]["detail"]
    assert "3/4" in rows[0]["detail"]      # 3 silent of the 4 news covered
    assert "AMD" in rows[0]["detail"]


def test_a_defaulted_decision_is_ranked_even_though_its_alert_expired(day):
    """run_decision appends a pm_timeout `alert` AND writes the row with
    charter_version 'none'. The scorecard reads the row, not the event: the
    column is the durable fact, and events are a projection queue that a
    future prune could empty."""
    sim, path = day
    sim.conn.execute("UPDATE decisions SET charter_version = 'none'")
    sim.conn.execute("DELETE FROM events WHERE kind = 'alert'")
    sim.conn.commit()
    assert _rows(path, sim.run_date, "defaulted_decision")


def test_unknown_attribution_is_not_a_default(day):
    """'unknown' means the row predates attribution; 'none' means a seat was
    silent. Collapsing them would make every historical row read as a failure
    and drown the band that matters."""
    sim, path = day
    sim.conn.execute("UPDATE signals SET charter_version = 'unknown'")
    sim.conn.execute("UPDATE decisions SET charter_version = 'unknown'")
    sim.conn.commit()
    assert _rows(path, sim.run_date, "defaulted_signal") == []
    assert _rows(path, sim.run_date, "defaulted_decision") == []


# --- severity 1 and 2 --------------------------------------------------------

def test_gate_rejection_carries_its_reason(day):
    """A rejection with no reason tells the reader to go open the DB, which is
    what the scorecard exists to save them from."""
    sim, path = day
    decision_id = sim.conn.execute(
        "SELECT id FROM decisions WHERE ticker = 'NVDA'").fetchone()["id"]
    sim.conn.execute(
        "UPDATE decisions SET status = 'rejected' WHERE ticker = 'NVDA'")
    sim.conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES"
        " ('gate_rejected', ?, ?)",
        (f'{{"decision_id": {decision_id}, "reason": "no_headroom"}}',
         f"{sim.run_date}T13:00:00+00:00"))
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "gate_rejected")
    assert "no_headroom" in rows[0]["detail"]
    assert "NVDA" in rows[0]["detail"]


def test_a_rejection_with_no_event_still_ranks(day):
    """The reason is a nice-to-have; the rejection is the fact. Reading the
    reason from a JOIN that may miss must never drop the row itself."""
    sim, path = day
    sim.conn.execute(
        "UPDATE decisions SET status = 'rejected' WHERE ticker = 'NVDA'")
    sim.conn.commit()
    assert len(_rows(path, sim.run_date, "gate_rejected")) == 1


def test_an_order_that_failed_or_expired_ranks_below_a_rejection(day):
    """A rejection is the gate working. A failure is the fund believing it
    traded when it did not — worse to miss, but the gate rejection is the one
    that changes tomorrow's charter, so it reads first."""
    sim, path = day
    sim.conn.execute("UPDATE decisions SET status = 'failed'")
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "execution_failed")
    assert rows and rows[0]["severity"] == 2


# --- severity 3: outliers ----------------------------------------------------

def _seed_cost_history(conn, agent, run_date, days=6, usd=0.05):
    """`days` prior run_dates for one seat, so the baseline exists. Dates are
    fabricated strings, not real trading days — the query groups by run_date
    and never parses one."""
    for n in range(days):
        conn.execute(
            "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
            " recorded_at) VALUES (?, ?, 's', ?, ?)",
            (f"2026-06-{n + 1:02d}", agent, usd, f"2026-06-{n + 1:02d}T13:00:00Z"))
    conn.commit()


def test_a_seat_with_no_history_is_never_called_an_outlier(day):
    """A mean of two points is not a baseline. Flagging against one would make
    every seat's first week an outlier and teach the reader to skip the band."""
    sim, path = day
    sim.conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, 'quant', 's', 99.0, ?)",
        (sim.run_date, f"{sim.run_date}T13:00:00+00:00"))
    sim.conn.commit()
    assert _rows(path, sim.run_date, "cost_outlier") == []


def test_a_seat_that_costs_far_more_than_its_own_history_ranks(day):
    sim, path = day
    _seed_cost_history(sim.conn, "pm", sim.run_date)
    sim.conn.execute(
        "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
        " recorded_at) VALUES (?, 'pm', 's', 9.0, ?)",
        (sim.run_date, f"{sim.run_date}T13:00:00+00:00"))
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "cost_outlier")
    assert len(rows) == 1 and rows[0]["severity"] == 3
    assert "pm" in rows[0]["detail"]


def test_a_seat_within_its_own_history_does_not_rank(day):
    """The other direction, because an always-on outlier row is worse than no
    outlier row at all."""
    sim, path = day
    _seed_cost_history(sim.conn, "pm", sim.run_date)
    assert _rows(path, sim.run_date, "cost_outlier") == []


def test_a_confidence_swing_against_the_seats_own_history_ranks(day):
    """Not against other seats: a cautious analyst and a bold one are both
    doing their job. The comparison that means something is a seat against
    itself."""
    sim, path = day
    for n in range(6):
        sim.conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at, charter_version, model_id)"
            " VALUES (?, 'news', 'AMD', 'bullish', 55, 's', ?, 'v1', 'm')",
            (f"2026-06-{n + 1:02d}", f"2026-06-{n + 1:02d}T13:00:00Z"))
    sim.conn.execute(
        "UPDATE signals SET confidence = 99, charter_version = 'v1'"
        " WHERE run_date = ? AND agent = 'news'", (sim.run_date,))
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "confidence_outlier")
    assert len(rows) == 1 and "news" in rows[0]["detail"]


def test_a_defaulted_signal_is_not_a_confidence_outlier(day):
    """A silent seat writes confidence 0, which is a 55-point swing against any
    history. It is already the severity-0 line; ranking it twice would make the
    outlier band a mirror of the default band."""
    sim, path = day
    for n in range(6):
        sim.conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at, charter_version, model_id)"
            " VALUES (?, 'news', 'AMD', 'bullish', 55, 's', ?, 'v1', 'm')",
            (f"2026-06-{n + 1:02d}", f"2026-06-{n + 1:02d}T13:00:00Z"))
    sim.conn.execute(
        "UPDATE signals SET confidence = 0, charter_version = 'none'"
        " WHERE run_date = ? AND agent = 'news'", (sim.run_date,))
    sim.conn.commit()
    assert _rows(path, sim.run_date, "confidence_outlier") == []


def test_a_model_fallback_ranks_beside_the_outliers(day):
    """agents/runtime.py records it when model_usage names a model the seat was
    not configured to run. Severity 3, not 0: the fund traded correctly and
    only model_id is stale."""
    sim, path = day
    sim.conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES"
        " ('model_fallback_used', ?, ?)",
        ('{"seat": "analyst", "configured": "claude-haiku-4-5-20251001",'
         ' "served": ["claude-sonnet-5"]}',
         f"{sim.run_date}T13:00:00+00:00"))
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "model_fallback_used")
    assert len(rows) == 1 and rows[0]["severity"] == 3
    assert "claude-sonnet-5" in rows[0]["detail"]


def test_yesterdays_fallback_does_not_rank_today(day):
    """events has no run_date, only created_at, so the day window is computed
    in ET exactly as audit_day.py does it. Without that, one bad day would
    ratchet onto every later one."""
    sim, path = day
    sim.conn.execute(
        "INSERT INTO events (kind, payload, created_at) VALUES"
        " ('model_fallback_used', ?, '2026-07-02T13:00:00+00:00')",
        ('{"seat": "analyst", "configured": "a", "served": ["b"]}',))
    sim.conn.commit()
    assert _rows(path, sim.run_date, "model_fallback_used") == []


# --- severity 4 --------------------------------------------------------------

def test_a_researched_ticker_with_no_decision_ranks_last(day):
    """audit_day already FAILS the day on this, so the scorecard's job is only
    to place it — last, because the loud channel already has it."""
    sim, path = day
    sim.conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at, charter_version, model_id) VALUES"
        " (?, 'news', 'AMD', 'bullish', 60, 's', ?, 'v1', 'm')",
        (sim.run_date, f"{sim.run_date}T13:00:00+00:00"))
    sim.conn.commit()
    rows = _rows(path, sim.run_date, "coverage_gap")
    assert len(rows) == 1 and rows[0]["severity"] == 4
    assert "AMD" in rows[0]["detail"]


# --- the contract ------------------------------------------------------------

def test_a_terrible_day_still_exits_zero(day):
    """The non-zero exit belongs to audit_day.py alone."""
    sim, path = day
    sim.conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    sim.conn.execute("UPDATE decisions SET status = 'rejected'")
    sim.conn.commit()
    proc = subprocess.run([sys.executable, str(SCRIPT), path, sim.run_date],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "critic_timeout" in proc.stdout


def test_a_clean_day_says_so_rather_than_printing_nothing(day):
    """Silence is ambiguous between 'nothing to report' and 'the job did not
    run' — the same reason close_pnl's silent paths made the scorecard need
    its own event."""
    sim, path = day
    proc = subprocess.run([sys.executable, str(SCRIPT), path, sim.run_date],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert sim.run_date in proc.stdout


def test_it_runs_on_the_stdlib_alone(day):
    """Zero-dependency, like audit_day.py: it must run on a bare host with
    nothing installed, so -S (no site-packages) has to be enough."""
    sim, path = day
    proc = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), path, sim.run_date],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_a_missing_argument_is_a_usage_error_not_a_traceback(day):
    proc = subprocess.run([sys.executable, str(SCRIPT)],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert "usage" in proc.stderr


# --- the outbox event --------------------------------------------------------

def test_the_scorecard_posts_even_when_the_pnl_job_posts_nothing(day):
    """close_pnl.py has paths that log and exit 0 posting nothing. The
    scorecard must not ride them: its absence would read as a quiet day rather
    than a skipped job."""
    sim, path = day
    score_day.append_scorecard_event(sim.conn, path, sim.run_date,
                                     f"{sim.run_date}T13:40:00+00:00")
    kinds = [r["kind"] for r in sim.conn.execute("SELECT kind FROM events")]
    assert "scorecard" in kinds


def test_the_scorecard_event_carries_the_rows_not_a_rendered_string(day):
    """A renderer never parses its own text (contracts §8), so the payload
    carries the ranked rows as fields."""
    sim, path = day
    sim.conn.execute("UPDATE critiques SET note = 'critic_timeout'")
    sim.conn.commit()
    score_day.append_scorecard_event(sim.conn, path, sim.run_date,
                                     f"{sim.run_date}T13:40:00+00:00")
    import json
    payload = json.loads(sim.conn.execute(
        "SELECT payload FROM events WHERE kind = 'scorecard'").fetchone()[0])
    assert payload["run_date"] == sim.run_date
    assert payload["rows"][0]["kind"] == "critic_timeout"
