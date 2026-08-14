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
    event is a day nobody was told about) AND nothing dead-lettered — a row
    whose render/post raised is marked posted by slackkit.outbox.drain()
    without ever reaching Slack, so an undrained-only check reads a Slack
    outage as a clean day
  * no alert events — an alert means something needed human review (a
    timed-out PM, a canceled order, a broker that went unreachable); a
    report of what a "clean" day looks like must not stay silent about them
  * at least one cost row (no cost rows means no turn ever completed)

Order and decision checks are scoped to the audited run_date; the outbox,
dead-letter, and alert checks are not, because the outbox is global and must
be fully and successfully drained end to end, not just for one day.
"""
from __future__ import annotations

import sqlite3
import sys

# orchestrator/daily.py's run_day, in order. Duplicated deliberately: this
# script must stay importable with nothing on sys.path but the stdlib.
STAGES = ("pre_gate", "research", "decision", "gate", "execution",
          "reconciliation", "close")


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

    # drain() marks a dead-lettered row posted_at without ever calling
    # slack.post() successfully — invisible to the undrained check above.
    dead_lettered = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'projection_error'"
        ).fetchone()["c"]
    if dead_lettered:
        bad.append(f"dead-lettered outbox events: {dead_lettered}")

    alerts = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind = 'alert'").fetchone()["c"]
    if alerts:
        bad.append(f"alert events raised: {alerts}")

    if not conn.execute("SELECT COUNT(*) c FROM costs WHERE run_date = ?",
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
