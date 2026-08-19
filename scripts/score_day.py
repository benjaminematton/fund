#!/usr/bin/env python3
"""Daily scorecard: rank which of the day's turns are worth a human's time.

Zero dependencies (stdlib sqlite3 only) and argv-driven, so it runs against a
live DB with nothing installed:

    python3 scripts/score_day.py state/fund.sqlite 2026-07-06

**This never exits non-zero.** Failing the day belongs to scripts/audit_day.py
alone, which is wired into run_day's return value and the systemd failure path.
A scorecard that could fail the day would make every mediocre day an incident,
and the point is a ranking a human reads on GOOD days too — the one artifact
that says "read this turn first" rather than "something is broken".

**Ranking is a fixed severity order, not a weighted score.** A weight is a
number somebody tunes until the day looks good — a scoreboard you can p-hack
with no LLM involved. The order is a claim about what a reader should look at
first and it changes only by human commit:

    0  the fund did not think   a silent seat, a silent PM, a timed-out critic
    1  the gate said no         decisions.status='rejected', with the reason
    2  the trade did not land   decisions.status in ('failed','expired')
    3  outliers and divergence  cost, confidence, a fallback model that served
    4  coverage                 a researched ticker with no decision

Two omissions from that table are deliberate.

`invalidated` is absent because orchestrator/resolve.py writes it as a constant
0 — neither invalidation signal the fund has is readable from that job — so
ranking on it would silently rank on nothing.

Stage latency is absent because it is not recorded. `checkpoints` carries one
`updated_at` per stage, overwritten on each transition, so a "latency" computed
from it is the gap between two stages' LAST writes — which includes the
orchestrator's deliberate 09:00→11:00 wait. That is a schedule, not a
slowdown, and a metric that mostly measures a constant is worse than none.

Alerts are absent for a different reason: audit_day already fails the day on
every one of them, so repeating them here would add noise to the quiet channel
without adding a fact. The severity-0 band reads the DURABLE markers instead —
`charter_version = 'none'` and `critiques.note` — which survive an events
prune and mean the same thing a year later.

The thresholds below are detection rules, not ranking weights: they decide
whether a row appears, never where it sorts. They change only by human commit.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# A seat is an outlier against ITS OWN history, never against other seats: a
# cautious analyst and a bold one are both doing their job, and the only
# comparison that carries information is a seat against yesterday's self.
BASELINE_WINDOW = 20            # prior observations that form the baseline
MIN_BASELINE = 5                # below this there is no baseline, so no flag
COST_OUTLIER_MULTIPLE = 3.0     # today's spend vs the seat's mean day
CONFIDENCE_SWING_POINTS = 20    # mean conviction, on the 0-100 scale


def et_day_window(run_date: str) -> tuple[str, str]:
    """[start, end) of the ET calendar day `run_date`, in events.created_at's
    own format. Copied from scripts/audit_day.py rather than imported: both
    scripts must stay runnable with nothing on sys.path but the stdlib.

    DST-safe: aware-datetime + timedelta is wall-clock arithmetic, so the
    second bound is midnight the next ET day whatever the UTC offset did in
    between."""
    day = datetime.strptime(run_date, "%Y-%m-%d").date()
    start = datetime.combine(day, time(0, 0), tzinfo=_ET)
    return (_stamp(start), _stamp(start + timedelta(days=1)))


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row(severity: int, kind: str, detail: str) -> dict:
    return {"severity": severity, "kind": kind, "detail": detail}


def _listed(tickers) -> str:
    return ", ".join(sorted(tickers))


# --- severity 0: the fund did not think --------------------------------------

def _silent_seats(conn, run_date: str) -> list[dict]:
    """One line per seat, never one per row.

    The defaulted-signal guarantee is per (seat, ticker), so a 3-ticker day
    with two silent seats writes six rows — and that population grows with
    seat count, against the eleven seats specs/design.md commits to. One line
    per row would bury every other severity under near-identical entries on
    exactly the days worth reading. The count is the signal; the repetition is
    not.

    'none' only, never 'unknown': 'none' means a seat was silent and the
    orchestrator wrote the row, 'unknown' means the row predates attribution.
    Collapsing them would make every historical row read as a failure."""
    out = []
    for r in conn.execute(
            "SELECT agent,"
            " SUM(charter_version = 'none') silent, COUNT(*) total,"
            " GROUP_CONCAT(CASE WHEN charter_version = 'none' THEN ticker END)"
            "   tickers"
            " FROM signals WHERE run_date = ? GROUP BY agent"
            " HAVING silent > 0 ORDER BY agent", (run_date,)):
        out.append(_row(0, "defaulted_signal",
                        f"{r['agent']}: silent on {r['silent']}/{r['total']}"
                        f" tickers ({_listed(r['tickers'].split(','))})"))
    return out


def _silent_pm(conn, run_date: str) -> list[dict]:
    """The pm_timeout default. Read off the decision ROW, not off the alert
    event run_decision also appends: the column is the durable fact and events
    are a projection queue a future prune could empty."""
    rows = conn.execute(
        "SELECT ticker FROM decisions WHERE run_date = ?"
        " AND charter_version = 'none' ORDER BY ticker", (run_date,)).fetchall()
    if not rows:
        return []
    total = conn.execute("SELECT COUNT(*) c FROM decisions WHERE run_date = ?",
                         (run_date,)).fetchone()["c"]
    tickers = [r["ticker"] for r in rows]
    return [_row(0, "defaulted_decision",
                 f"pm: no decision on {len(tickers)}/{total} tickers"
                 f" ({_listed(tickers)})")]


def _timed_out_critic(conn, run_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT ticker FROM critiques WHERE run_date = ?"
        " AND note = 'critic_timeout' ORDER BY ticker", (run_date,)).fetchall()
    if not rows:
        return []
    tickers = [r["ticker"] for r in rows]
    return [_row(0, "critic_timeout",
                 f"critic: timed out on {len(tickers)} tickers"
                 f" ({_listed(tickers)})")]


# --- severity 1 and 2 --------------------------------------------------------

def _gate_rejections(conn, run_date: str) -> list[dict]:
    """The reason is read from the gate's own event via json_extract, not
    parsed out of any rendered text. A missing event must never drop the row:
    the rejection is the fact, the reason is the convenience."""
    out = []
    for r in conn.execute(
            "SELECT d.ticker, d.action, ("
            "  SELECT json_extract(e.payload, '$.reason') FROM events e"
            "  WHERE e.kind = 'gate_rejected'"
            "  AND json_extract(e.payload, '$.decision_id') = d.id"
            "  ORDER BY e.id DESC LIMIT 1) reason"
            " FROM decisions d WHERE d.run_date = ? AND d.status = 'rejected'"
            " ORDER BY d.ticker", (run_date,)):
        why = r["reason"] or "no reason recorded"
        out.append(_row(1, "gate_rejected",
                        f"{r['ticker']} {r['action']} blocked: {why}"))
    return out


def _failed_executions(conn, run_date: str) -> list[dict]:
    return [_row(2, "execution_failed", f"{r['ticker']} {r['status']}")
            for r in conn.execute(
                "SELECT ticker, status FROM decisions WHERE run_date = ?"
                " AND status IN ('failed', 'expired') ORDER BY ticker",
                (run_date,))]


# --- severity 3: outliers and divergence -------------------------------------

def _model_divergences(conn, run_date: str) -> list[dict]:
    """agents/runtime.py appends these when model_usage names a model the seat
    was not configured to run, which makes that seat's model_id column stale
    for the day. Severity 3, not 0: the fund traded correctly."""
    out = []
    for r in conn.execute(
            "SELECT payload FROM events WHERE kind = 'model_fallback_used'"
            " AND created_at >= ? AND created_at < ? ORDER BY id",
            et_day_window(run_date)):
        p = json.loads(r["payload"])
        out.append(_row(3, "model_fallback_used",
                        f"{p['seat']} ran {', '.join(p['served'])},"
                        f" configured {p['configured']}"))
    return out


def _baseline(history: list[float]) -> float | None:
    """The mean of a seat's own recent observations, or None when there are too
    few. A mean of two points is not a baseline: flagging against one would
    make every seat's first week an outlier and teach the reader to skip the
    whole band."""
    window = history[:BASELINE_WINDOW]
    if len(window) < MIN_BASELINE:
        return None
    return sum(window) / len(window)


def _prior(conn, sql: str, run_date: str) -> dict[str, list[float]]:
    """Per-seat history, most recent first, for a query yielding (agent, value)."""
    out: dict[str, list[float]] = {}
    for r in conn.execute(sql, (run_date,)):
        out.setdefault(r["agent"], []).append(r["value"])
    return out


def _cost_outliers(conn, run_date: str) -> list[dict]:
    history = _prior(
        conn,
        "SELECT agent, SUM(usd_estimate) value FROM costs WHERE run_date < ?"
        " GROUP BY agent, run_date ORDER BY run_date DESC", run_date)
    out = []
    for r in conn.execute(
            "SELECT agent, SUM(usd_estimate) today FROM costs"
            " WHERE run_date = ? GROUP BY agent ORDER BY agent", (run_date,)):
        mean = _baseline(history.get(r["agent"], []))
        if mean is None or r["today"] <= mean * COST_OUTLIER_MULTIPLE:
            continue
        out.append(_row(3, "cost_outlier",
                        f"{r['agent']}: ${r['today']:.2f} est. today vs"
                        f" ${mean:.2f} mean over its last"
                        f" {len(history[r['agent']][:BASELINE_WINDOW])} days"))
    return out


def _confidence_outliers(conn, run_date: str) -> list[dict]:
    """Defaulted signals are excluded from BOTH sides. A silent seat writes
    confidence 0, which is a large swing against any history — but it is
    already the severity-0 line, and ranking it twice would make this band a
    mirror of that one rather than a second kind of information."""
    history = _prior(
        conn,
        "SELECT agent, confidence value FROM signals WHERE run_date < ?"
        " AND charter_version != 'none' ORDER BY id DESC", run_date)
    out = []
    for r in conn.execute(
            "SELECT agent, AVG(confidence) today FROM signals"
            " WHERE run_date = ? AND charter_version != 'none'"
            " GROUP BY agent ORDER BY agent", (run_date,)):
        mean = _baseline(history.get(r["agent"], []))
        if mean is None or abs(r["today"] - mean) <= CONFIDENCE_SWING_POINTS:
            continue
        out.append(_row(3, "confidence_outlier",
                        f"{r['agent']}: mean conviction {r['today']:.0f} today"
                        f" vs {mean:.0f} recently"))
    return out


# --- severity 4 --------------------------------------------------------------

def _coverage_gaps(conn, run_date: str) -> list[dict]:
    """audit_day already FAILS the day on this, so the scorecard's job is only
    to place it — last, because the loud channel already carries it."""
    return [_row(4, "coverage_gap", f"{r['ticker']} researched, no decision")
            for r in conn.execute(
                "SELECT DISTINCT s.ticker FROM signals s WHERE s.run_date = ?"
                " AND NOT EXISTS (SELECT 1 FROM decisions d"
                " WHERE d.run_date = s.run_date AND d.ticker = s.ticker)"
                " ORDER BY s.ticker", (run_date,))]


CHECKS = (_silent_seats, _silent_pm, _timed_out_critic, _gate_rejections,
          _failed_executions, _model_divergences, _cost_outliers,
          _confidence_outliers, _coverage_gaps)


def score(db_path: str, run_date: str) -> list[dict]:
    """The day's findings, most severe first. Ties keep CHECKS order, which is
    itself severity order, so the result is deterministic without sorting on
    the detail text."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [r for check in CHECKS for r in check(conn, run_date)]
    finally:
        conn.close()
    rows.sort(key=lambda r: r["severity"])
    return rows


def append_scorecard_event(conn, db_path: str, run_date: str,
                           now_iso: str) -> None:
    """Append the day's scorecard to the outbox.

    It posts on EVERY day, clean ones included. scripts/close_pnl.py has paths
    that log and exit 0 posting nothing, and a scorecard that rode them would
    make "nothing was posted" ambiguous between a quiet day and a skipped job.

    The payload carries the ranked rows as fields, never a rendered string: a
    renderer never parses its own text (contracts §8).

    slackkit is imported lazily and optionally, so this file stays runnable on
    a bare host — the direct INSERT is the same statement append_event makes."""
    payload = json.dumps({"run_date": run_date, "rows": score(db_path, run_date)})
    try:
        from slackkit.outbox import append_event
    except ImportError:
        conn.execute("INSERT INTO events (kind, payload, created_at)"
                     " VALUES ('scorecard', ?, ?)", (payload, now_iso))
        conn.commit()
        return
    append_event(conn, "scorecard", json.loads(payload), now_iso)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <db_path> <run_date>", file=sys.stderr)
        return 2
    rows = score(argv[1], argv[2])
    print(f"SCORECARD {argv[2]} — {len(rows)} finding(s)")
    for r in rows:
        print(f"  {r['severity']}  {r['kind']:<20} {r['detail']}")
    return 0            # never non-zero: see the module docstring


if __name__ == "__main__":
    sys.exit(main(sys.argv))
