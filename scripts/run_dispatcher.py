"""Composition root for the Lane B dispatcher (resident-seats design, R1).

R1 ships the loop with NO consumer registered — R2 registers spec_review — so
every claimed row fails loudly (work_failed alert, row failed, nothing
requeued). That is the honest R1 behaviour and the correct default: a row the
fund cannot act on must never sit claimed or silently vanish.

scripts/ is deliberately outside the purity lint, which is why WallClock and
time.sleep are instantiated here — and only here. Everything else is injected
into orchestrator.dispatch.run_dispatcher. Runs under ops/fund-dispatcher.service
(committed, NOT installed — installing units is a human act; installed units
are copies, see ops/README.md)."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/run_dispatcher.py` anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.clock import iso                    # noqa: E402
from orchestrator.dispatch import run_dispatcher as run_dispatcher_loop  # noqa: E402
from slackkit.outbox import drain                     # noqa: E402
from state.db import connect                          # noqa: E402

# No Alpaca, no Anthropic: R1 places no orders and runs no model.
REQUIRED_ENV = ("FUND_DB", "SLACK_BOT_TOKEN")
# Its own lock: two dispatchers on one queue would both claim and both sweep.
LOCK_NAME = "dispatcher.lock"


def log(msg: str) -> None:
    print(f"run_dispatcher: {msg}", flush=True)


class NoConsumer(RuntimeError):
    """R1 has no wake for any kind; R2 registers the first."""


def no_consumer(row: dict) -> None:
    raise NoConsumer(f"no consumer registered for kind {row['kind']!r}"
                     f" (work {row['work_id']}); R1 ships the loop only")


def _build_slack(env: dict, environ):
    """Named seam so tests can drive main() without a network client."""
    from slackkit.real import RealSlack
    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)
    return slack


def _guarded(conn, slack, clock, body) -> int:
    """Never a silent death. Exit 1 makes OnFailure=fund-alert@%n.service fire
    (the report path that shares no failure mode with this process); the
    drained alert covers the case where Slack works. Same posture as
    scripts/critic_g1._guarded."""
    try:
        return body()
    except (Exception, SystemExit) as exc:
        text = (f"dispatcher_failed — {type(exc).__name__}: {exc}. The"
                " dispatcher stopped; open rows expire on schedule (each with"
                " its own alert), nothing retries itself (invariant 4).")
        log(f"ALERT {text}")
        try:
            run_day._alert(conn, clock, "dispatcher_failed", text)
            drain(conn, slack, iso(clock.now()))
        except Exception as inner:
            log(f"could not record/post that alert ({type(inner).__name__}:"
                f" {inner}) — the failure above is the one that matters;"
                " systemd's OnFailure carries it out of the box")
        return 1


def main(argv: list[str] | None = None) -> int:
    from agents.wallclock import WallClock

    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=None,
                        help="stop after N cycles (tests); default runs until killed")
    args = parser.parse_args(argv)

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    db_path = env["FUND_DB"]
    lock = run_day.acquire_lock(Path(db_path).parent / LOCK_NAME)  # kept in scope
    if lock is None:
        log("another dispatcher holds the lock — exiting 0 rather than racing it")
        return 0

    clock = WallClock()
    conn = connect(db_path)
    slack = _build_slack(env, environ)

    def _body() -> int:
        cycles = run_dispatcher_loop(conn, slack, clock, no_consumer,
                                     sleep=time.sleep, max_cycles=args.cycles)
        log(f"stopped after {cycles} cycle(s)")
        return 0

    return _guarded(conn, slack, clock, _body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
