#!/usr/bin/env python3
"""Post-close P&L vs SPY — the half of the EOD digest that cannot be computed
in the morning.

    make close-pnl         # == python scripts/close_pnl.py

contracts §8 specifies the EOD digest as "P&L $ and % vs SPY, positions table,
decisions + outcomes, est. inference cost". run_close emits the last three and
always has. The first two were never emitted because they cannot be: under the
compressed MVF schedule the whole trading day — including the close stage —
runs at 09:35 ET, where `daily_pnl_pct` is ten minutes of session and
`close_frame` (end - SIP_DELAY = 09:24, pre-open) returns YESTERDAY's SPY bar.

So the digest is two messages at two times, because the fund's actions and the
fund's outcome happen at two times:

    09:40 ET  run_close   decisions, fills, est. cost, journals  (correct then)
    16:35 ET  this job    P&L $ and % vs SPY                     (correct then)

WHY 16:35 AND NOT 16:15: close_frame shifts its end back SIP_DELAY (16 min) to
stay off Alpaca's free-plan SIP blackout. A 16:15 fire asks for 15:59 — inside
the session, before the closing auction has written the bar. 16:35 asks for
16:19, safely past it. The same-session guard in orchestrator/pnl.py turns a
too-early fire into no post rather than a wrong one.

NO NEW STORAGE. Every number here is arithmetic over reads the day already
makes: account_state carries equity and last_equity (the pair the gate's
circuit breaker already consumes), close_frame carries SPY's closes. Nothing
is persisted but the outbox event, which is the projection record, not a
series. A since-inception NAV curve WOULD need storage — the broker exposes
only today and yesterday — and is deliberately not built here.

Posture (invariant 4: say nothing rather than say something wrong):
  * ALPACA_PAPER_TRADE != 'true'   -> exit 1 before a client is built
  * a missing env var              -> exit 1 naming every missing var
  * SPY's last bar is not today's  -> log, exit 0, post nothing (holiday, or
                                      fired before the close settled)
  * equity/last_equity unusable    -> log, exit 0, post nothing
  * already posted today           -> drain any stuck row, post nothing new

No flock, unlike run_day: this job places no orders and spends no LLM budget,
and the events-table guard below already makes a concurrent double-fire post
at most one line. An undrained row surfaces on the NEXT day's audit, whose
undrained-outbox check is global rather than day-scoped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/close_pnl.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                                    # noqa: E402
from orchestrator.clock import iso                                # noqa: E402
from orchestrator.pnl import PnlUnavailable, eod_pnl, format_line  # noqa: E402
from slackkit.outbox import append_event, drain                   # noqa: E402
from state.db import connect                                      # noqa: E402

# No ANTHROPIC_API_KEY: no seat runs here. Requiring it would let a missing
# key silence the P&L line over a dependency it never uses.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
                "SLACK_BOT_TOKEN")


def log(msg: str) -> None:
    print(f"close_pnl: {msg}", flush=True)


def post_eod_pnl(conn, slack, source, clock) -> int:
    """Compute today's P&L vs SPY, queue it, drain. Returns events posted.

    The drain runs whether or not this call queued anything, so a line that
    was appended yesterday-evening while Slack was down is retried rather than
    stranded by the idempotency guard.
    """
    now = iso(clock.now())
    try:
        pnl = eod_pnl(source, clock)
    except PnlUnavailable as exc:
        log(f"no P&L line today — {exc}")
        return 0

    # json_extract, not a LIKE on the serialized payload: a LIKE pattern only
    # matches because append_event's json.dumps emits a space after the colon
    # today, so a compaction change there would silently double-post forever
    # (audit_day.py makes the same argument about its self-alert marker).
    already = conn.execute(
        "SELECT 1 FROM events WHERE kind = 'pnl'"
        " AND json_extract(payload, '$.run_date') = ?",
        (pnl["run_date"],)).fetchone() is not None
    if already:
        log(f"P&L for {pnl['run_date']} already queued — draining only")
    else:
        text = f"{pnl['run_date']} close · {format_line(pnl)}"
        log(text)
        append_event(conn, "pnl", {"text": text, "run_date": pnl["run_date"]},
                     now)
    return drain(conn, slack, now)


def main(argv: list[str] | None = None) -> int:
    from market.source_alpaca import AlpacaSource

    from agents.wallclock import WallClock
    from slackkit.real import RealSlack

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    clock = WallClock()                      # the one real clock, injected below
    source = AlpacaSource()                  # re-guards ALPACA_PAPER_TRADE
    conn = connect(env["FUND_DB"])

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)

    post_eod_pnl(conn, slack, source, clock)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
