"""Run ONE live eval trial and print what came back.

The cheap plumbing check before spending a full suite: proves the Alpaca MCP
server connects, the model id resolves, the fund tools are reachable, and a
decision row actually lands. ~$0.12.

Usage:  .venv/bin/python3 scripts/eval_one.py <case-id> [trial] [seat]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The keys an eval turn needs, and the whole of what .env.eval may carry.
# Deliberately NOT a subset of what a trading day needs: scripts/run_day.py:74
# also requires FUND_DB and SLACK_BOT_TOKEN, so a checkout credentialled for
# `make eval` still cannot run `make live-day` — it refuses on its own
# REQUIRED_ENV check. Pinned by tests/test_eval_env_cannot_trade.py.
EVAL_KEYS = ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
             "ALPACA_PAPER_TRADE")


def _primary_checkout_env() -> Path | None:
    """The primary checkout's .env, or None when git cannot say where it is."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None                      # no git, or it hung
    if proc.returncode != 0 or not proc.stdout.strip():
        return None                      # not a repo, or nothing to report
    # --git-common-dir may be relative to ROOT; ROOT / absolute is absolute.
    return (ROOT / proc.stdout.strip()).resolve().parent / ".env"


# .env.eval wins over .env when present. That is what lets eval credentials
# live on a host the fund must never TRADE from — the Mac after the 2026-08-18
# droplet cutover, where `.env` was renamed to `.env.MIGRATED-TO-VM` as one of
# two barriers against the fund resurrecting there (PROGRESS.md "The Mac after
# cutover"). Restoring a full `.env` to run evals would dissolve that barrier;
# a file that cannot trade keeps it, by construction rather than by memory.
# .env lives in the primary checkout; a worktree has none of its own, so we
# fall back to the primary checkout's .env — derived, never hardcoded. Git
# reports the main worktree's git dir as --git-common-dir (a linked worktree's
# own git dir is .git/worktrees/<name> underneath it), and the primary checkout
# is that dir's parent. Do not delete this as dead code: without it `make eval`
# cannot run from a worktree at all. A checkout with no .env anywhere is the
# normal fresh-clone case, not an error — main() reports it below.
EVAL_ENV = ROOT / ".env.eval"
ENV = EVAL_ENV if EVAL_ENV.exists() else ROOT / ".env"
if not ENV.exists():
    primary_env = _primary_checkout_env()
    if primary_env is not None and primary_env.exists():
        ENV = primary_env


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    if not ENV.exists():
        print(f"no .env at {ENV}", file=sys.stderr)
        return 2
    load_env(ENV)
    if os.environ.get("ALPACA_PAPER_TRADE") != "true":
        print("ALPACA_PAPER_TRADE is not 'true' — refusing", file=sys.stderr)
        return 2

    from evals.cases import load_cases
    from evals.grade import grade_trace, seat_registry
    from evals.runner import run_trial

    case_id = sys.argv[1] if len(sys.argv) > 1 else "a01"
    trial = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    seat = sys.argv[3] if len(sys.argv) > 3 else "pm"
    cases = {c.id: c for c in load_cases(ROOT / "evals/cases" / seat)}
    case = cases[case_id]

    print(f"running {case_id} trial {trial} live ...", flush=True)
    trace = run_trial(case.seat, case, trial)

    print(f"\nerror        {trace.error}")
    print(f"tools        {trace.tool_names}")
    print(f"turns        {trace.turns}")
    print(f"cost est.    {trace.cost_usd}")
    print(f"duration_ms  {trace.duration_ms}")
    print(f"rows         {trace.rows_written}")
    print(f"alerts       {[a['payload'] for a in trace.alerts]}")

    # The SEAT's registry, not every invariant — same set
    # scripts/eval_suite.py grades with. full_registry() here
    # applied I1 to the Critic, which has no allowed_actions to
    # size against, so a single-case probe reported an
    # INCONCLUSIVE the suite would never show: the two paths
    # disagreed about the same trace.
    result = grade_trace(trace, case, seat_registry(case.seat))
    print("\nverdicts:")
    for v in result.verdicts:
        print(f"  {v.invariant:<7} {v.outcome:<13} {v.tag or '':<28}"
              f" {v.detail[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
