#!/usr/bin/env python3
"""Render one simulated trading day the way a human would read it.

    make sim-report SCENARIO=golden

Runs one of tests/simday.py's scenarios into a temporary directory — the same
composition `make sim-day` asserts on: injected clock, FakeSlack, recorded
LLM decisions, real gate/tools/DB — and prints, in fixed order:

  1. the Slack transcript as FakeSlack recorded it, by channel, in post order;
  2. the day's rows from signals, decisions, critiques, tickets, orders,
     events, checkpoints, costs, offered, in rowid order (alerts are events
     of kind 'alert');
  3. the fake broker's own record: place_attempts, open_positions(),
     open_orders();
  4. the scorecard, via scripts/score_day.py on the sim's database.

Every line comes from the DB, the fake Slack or the fake broker; nothing is
re-derived. stdout only, never a fixture: the suite already pins the numbers.
Run it on master and on a branch and diff the two — the scenarios are
deterministic, so the diff is the change.

Diff notes:
  (a) events.payload is printed as stored, so its key order follows the
      producing dict literal; a pure key reorder in production code shows as
      a diff, which is signal since the payload is the Slack projection
      contract.
  (b) NULL columns are omitted, so a value going NULL reads as a column
      vanishing, and a new nullable column is invisible until populated.
  (c) ORDER BY rowid is insertion order, so an insert-order change renders
      as a reorder.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))                              # from anywhere

from tests.fake_alpaca import FakeAlpaca                    # noqa: E402
from tests.simday import SCENARIOS, run_scenario            # noqa: E402

TABLES = ("signals", "decisions", "critiques", "tickets", "orders", "events",
          "checkpoints", "costs", "offered")
SCORE_DAY = ROOT / "scripts" / "score_day.py"


def render_slack(posts: dict[str, list[dict]]) -> list[str]:
    lines = ["== slack =="]
    for channel in sorted(posts):
        lines.append(f"-- {channel} --")
        for p in posts[channel]:
            who = f" [{p['username']}]" if p.get("username") else ""
            thread = f" (thread {p['thread_ts']})" if p.get("thread_ts") else ""
            lines.append(f"{p['ts']}{who}{thread}")
            lines.extend("    " + t for t in p["text"].splitlines())
    return lines


def render_tables(conn: sqlite3.Connection) -> list[str]:
    lines: list[str] = []
    for table in TABLES:
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        lines.append(f"== {table} ({len(rows)}) ==")
        for row in rows:
            lines.append("  " + "  ".join(
                f"{c}={row[c]!r}" for c in cols if row[c] is not None))
    return lines


def render_broker(broker: FakeAlpaca) -> list[str]:
    lines = ["== broker =="]
    attempts = broker.place_attempts
    lines.append(f"-- place_attempts ({len(attempts)}) --")
    for a in attempts:
        lines.append("  " + "  ".join(f"{k}={v!r}" for k, v in a.items()))
    positions = broker.open_positions()
    lines.append(f"-- open_positions ({len(positions)}) --")
    for p in positions:
        lines.append("  " + "  ".join(f"{k}={v!r}" for k, v in p.items()))
    orders = broker.open_orders()
    lines.append(f"-- open_orders ({len(orders)}) --")
    for o in orders:
        lines.append("  " + "  ".join(f"{k}={v!r}" for k, v in o.items()))
    return lines


def render(name: str, tmp: Path) -> str:
    sim = run_scenario(name, tmp)
    lines = [f"SIM-REPORT scenario={name} run_date={sim.run_date}"]
    lines += render_slack(sim.slack.posts)
    lines += render_tables(sim.conn)
    lines += render_broker(sim.broker)
    sim.conn.close()
    score = subprocess.run(
        [sys.executable, str(SCORE_DAY), str(tmp / "fund.sqlite"), sim.run_date],
        capture_output=True, text=True)
    if score.returncode != 0:
        raise RuntimeError(f"score_day exited {score.returncode}:\n{score.stderr}")
    lines.append("== scorecard ==")
    lines += score.stdout.rstrip("\n").splitlines()
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 and argv[1] else "golden"
    if name not in SCENARIOS:
        print(f"sim_report: unknown scenario {name!r}; valid:"
              f" {', '.join(sorted(SCENARIOS))}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        sys.stdout.write(render(name, Path(tmp)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
