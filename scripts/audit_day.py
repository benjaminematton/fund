#!/usr/bin/env python3
"""Nightly invariant audit (MVF T4). Exit 1 on any violation; prints findings.

Zero dependencies (stdlib sqlite3 only) and argv-driven, so it runs against a
live DB with nothing installed:

    python3 scripts/audit_day.py state/fund.sqlite 2026-07-06

What a clean day means:
  * every stage of the day reached 'done' — and every stage RAN (a day with no
    checkpoint rows at all is a day that never happened, not a clean one)
  * every ticker the analysts covered got a decision row
  * no decision left mid-flight in 'submitted'/'approved', no order left
    'submitted' — those are the shapes that mean "we don't know what the
    broker did"
  * the outbox is drained (invariant 6: Slack is a projection; an undrained
    event is a day nobody was told about) AND nothing dead-lettered TODAY — a
    row whose render/post raised is marked posted by slackkit.outbox.drain()
    without ever reaching Slack, so an undrained-only check reads a Slack
    outage as a clean day
  * no alert events TODAY — an alert means something needed human review (a
    timed-out PM, a canceled order, a broker that went unreachable); a
    report of what a "clean" day looks like must not stay silent about them
  * at least one cost row, on any day that actually scheduled a seat turn (a
    day with no active tickers runs no turns and correctly costs nothing)

Every count is scoped to the audited day. The alert and dead-letter counts
used to be global, which made this script self-poisoning: run_day's
report_audit appends its own failure as an `alert`, so one imperfect day
reddened every later day forever, growing by one each morning. The undrained
count stays global on purpose — an unposted event is unposted regardless of
which day wrote it, and drain() clears it the moment Slack works again, so it
cannot ratchet.

events has no run_date column, only created_at (ISO8601 UTC, seconds
precision, written by orchestrator/clock.iso). run_date is an ET calendar
date (schema.sql), so the window is computed in ET via stdlib zoneinfo and
formatted exactly like created_at — same fixed-width UTC layout, so the
BETWEEN is a plain string comparison and sqlite needs no timezone support.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# orchestrator/daily.py's run_day, in order. Duplicated deliberately: this
# script must stay importable with nothing on sys.path but the stdlib.
STAGES = ("pre_gate", "research", "decision", "gate", "execution",
          "reconciliation", "close")

_ET = ZoneInfo("America/New_York")

# scripts/run_day.py's report_audit stamps its own `alert` payload with this
# key. Excluded below so a crash-resume re-fire (or HANDOFF-LIVE §3's second,
# explicit audit run) does not count the previous attempt's audit alert as a
# fresh violation — day scoping alone cannot, because that alert is raised on
# the very day it audits.
SELF_ALERT_KEY = "audit_report"
_SELF_ALERT = f'%"{SELF_ALERT_KEY}": true%'


def et_day_window(run_date: str) -> tuple[str, str]:
    """[start, end) of the ET calendar day `run_date`, in events.created_at's
    own format. DST-safe: aware-datetime + timedelta is wall-clock arithmetic,
    so the second bound is midnight the next ET day whatever the UTC offset
    did in between."""
    day = datetime.strptime(run_date, "%Y-%m-%d").date()
    start = datetime.combine(day, time(0, 0), tzinfo=_ET)
    return (_stamp(start), _stamp(start + timedelta(days=1)))


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _count_today(conn, where: str, window: tuple[str, str], *args) -> int:
    return conn.execute(
        f"SELECT COUNT(*) c FROM events WHERE {where}"
        " AND created_at >= ? AND created_at < ?",
        (*args, *window)).fetchone()["c"]


def audit(db_path: str, run_date: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bad: list[str] = []

    seen = {}
    for r in conn.execute("SELECT stage, status FROM checkpoints"
                          " WHERE run_date = ?", (run_date,)):
        seen[r["stage"]] = r["status"]
        if r["status"] != "done":
            bad.append(f"checkpoint {r['stage']} = {r['status']}")
    for stage in STAGES:
        if stage not in seen:
            bad.append(f"checkpoint {stage} missing")

    for r in conn.execute(
            "SELECT ticker, status FROM decisions WHERE run_date = ?"
            " AND status IN ('submitted', 'approved') ORDER BY ticker",
            (run_date,)):
        bad.append(f"decision {r['ticker']} stuck at {r['status']}")

    for r in conn.execute(
            "SELECT DISTINCT s.ticker FROM signals s WHERE s.run_date = ?"
            " AND NOT EXISTS (SELECT 1 FROM decisions d"
            " WHERE d.run_date = s.run_date AND d.ticker = s.ticker)"
            " ORDER BY s.ticker", (run_date,)):
        bad.append(f"no decision row for {r['ticker']}")

    for r in conn.execute(
            "SELECT o.client_order_id FROM orders o"
            " JOIN tickets t ON t.id = o.client_order_id"
            " JOIN decisions d ON d.id = t.decision_id"
            " WHERE d.run_date = ? AND o.status = 'submitted'"
            " ORDER BY o.client_order_id", (run_date,)):
        bad.append(f"order {r['client_order_id'][:8]} stuck submitted")

    undrained = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE posted_at IS NULL").fetchone()["c"]
    if undrained:
        bad.append(f"undrained outbox events: {undrained}")

    window = et_day_window(run_date)

    # drain() marks a dead-lettered row posted_at without ever calling
    # slack.post() successfully — invisible to the undrained check above.
    dead_lettered = _count_today(conn, "kind = 'projection_error'", window)
    if dead_lettered:
        bad.append(f"dead-lettered outbox events: {dead_lettered}")

    alerts = _count_today(conn, "kind = 'alert' AND payload NOT LIKE ?",
                          window, _SELF_ALERT)
    if alerts:
        bad.append(f"alert events raised: {alerts}")

    # A day with no active tickers schedules no seat turn, so zero cost rows is
    # its correct shape, not a violation (HANDOFF-LIVE §2 calls it a legitimate
    # outcome). run_research writes a default signal row for EVERY active
    # ticker, so "research reached done and wrote no signal" is exactly "there
    # was nothing to work on". Any other shape — signals present, or a research
    # stage that never finished, including a day with no checkpoints at all —
    # still owes at least one cost row, so a day that SHOULD have burned turns
    # and recorded nothing still fails.
    covered = conn.execute("SELECT COUNT(*) c FROM signals WHERE run_date = ?",
                           (run_date,)).fetchone()["c"]
    if (covered or seen.get("research") != "done") and not conn.execute(
            "SELECT COUNT(*) c FROM costs WHERE run_date = ?",
            (run_date,)).fetchone()["c"]:
        bad.append("no cost rows recorded")

    conn.close()
    return bad


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <db_path> <run_date>", file=sys.stderr)
        return 2
    problems = audit(argv[1], argv[2])
    print("\n".join(problems) or f"AUDIT CLEAN {argv[2]}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
