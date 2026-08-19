#!/usr/bin/env python3
"""Nightly reflection job — writes `resolutions` for decisions that have
reached their horizon.

    make resolve           # == python scripts/resolve_day.py

design.md §7 makes Phase 2 the phase where memory becomes load-bearing, and
§8 names this job as its input half: the `resolutions` table and its consumer
(calibration/scoreboard.py) both existed, and nothing wrote the rows between
them. Scoring those rows into PM weights is Phase 5 — this job stops at the
data, and posts nothing.

WHY IT RIDES THE 16:35 TIMER. It shares close_pnl's one hard timing
constraint: close_frame shifts its end back SIP_DELAY (16 min), so a fire
before ~16:16 ET asks for a bar the closing auction has not written. The
horizon session then reads as absent and every due decision defers a day —
correct, but silently a day late, every day. 16:35 is already provisioned and
already past it, so this is a second ExecStart on fund-pnl.service rather than
a timer of its own.

NO SLACK, NO SEAT. The job needs the broker and the database and nothing else.
Requiring a Slack token or an Anthropic key would let an unrelated missing var
stop the fund's calibration record from ever being written.

Posture (invariant 4: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'   -> exit 1 before a client is built
  * a missing env var              -> exit 1 naming every missing var
  * horizon not yet reached        -> no row, counted `pending`, retried nightly
  * closes cannot support a number -> no row, counted `skipped`, retried nightly

Re-running is safe: a decision that already has a resolution is not selected
again (`resolutions.decision_id` is UNIQUE and the job's query honours it), so
a manual re-fire after a failed run resolves only what is still outstanding.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/resolve_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.resolve import resolve_due          # noqa: E402
from state.db import connect                          # noqa: E402

# No SLACK_BOT_TOKEN and no ANTHROPIC_API_KEY: this job posts nothing and runs
# no seat.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB")


def log(msg: str) -> None:
    print(f"resolve_day: {msg}", flush=True)


def resolve_and_log(conn, source, clock) -> dict:
    """Resolve what is due and report the counts.

    The counts are the only window an operator has on this job — it posts
    nothing. `skipped` is the one that matters: it means a decision reached
    its horizon and the closes still could not price it.
    """
    counts = resolve_due(conn, source, clock)
    log(f"resolved {counts['resolved']} · skipped {counts['skipped']}"
        f" · pending {counts['pending']}")
    return counts


def main(argv: list[str] | None = None) -> int:
    import os

    from market.source_alpaca import AlpacaSource

    from agents.wallclock import WallClock

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    resolve_and_log(connect(env["FUND_DB"]), AlpacaSource(), WallClock())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
