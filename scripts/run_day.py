#!/usr/bin/env python3
"""Live composition root — the ONE place real time, real Slack, real Alpaca and
real LLM seats are wired together (design Appendix A; MVF plan T16).

    make live-day          # == python scripts/run_day.py

Everything below this file is injectable and offline-tested; everything real is
constructed here and passed DOWN. scripts/ is deliberately outside the purity
lint, which is why WallClock and time.sleep may be instantiated here — and only
here — and then injected.

Posture (invariant 4: the default is HOLD):
  * ALPACA_PAPER_TRADE != 'true'      -> exit 1 before a single client is built
  * a missing env var                 -> exit 1 naming every missing var
  * market closed / clock unreadable  -> log, exit 0, trade nothing
  * a seat turn that raises            -> one `alert`, then the stage's own
                                          default (neutral/0, pm_timeout hold)
  * anything the audit flags           -> `alert` posted to Slack, exit 1

One fire per market day (review P4). Checkpoint CAS makes a re-fire resume
rather than repeat, so a crashed day is recovered by running this again.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/run_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling audit_day

import yaml                                                        # noqa: E402

import audit_day                                                   # noqa: E402
from agents.exec_turn import check_tool_calls, run_seat_turn       # noqa: E402
from agents.runtime import record_turn_result                      # noqa: E402
from agents.seats import build_seat_options, load_seat_config      # noqa: E402
from agents.wallclock import WallClock                             # noqa: E402
from market.features import build_market_inputs                    # noqa: E402
from orchestrator.clock import et_run_date, iso                    # noqa: E402
from orchestrator.daily import StageCtx, run_day, run_pre_gate     # noqa: E402
from slackkit.outbox import append_event, drain                    # noqa: E402
from state.db import connect                                       # noqa: E402

# Core env. ALPACA_PAPER_TRADE is checked separately and first (invariant 1).
REQUIRED_ENV = ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                "FUND_DB", "SLACK_BOT_TOKEN")
REQUIRED_SERVERS = {"alpaca", "fund"}
WATCHLIST_YAML = ROOT / "config" / "watchlist.yaml"
SECTORS_YAML = ROOT / "config" / "sectors.yaml"
SEAT_CONFIG = ROOT / "agents" / "config"
# Stage -> (seat, config file). The exec seat is the only one carrying the
# `trading` toolset (invariant 2); every seat is built by build_seat_options,
# never hand-rolled here.
SEATS = {"research": "analyst", "decision": "pm", "execution": "exec"}


def log(msg: str) -> None:
    print(f"run_day: {msg}", flush=True)


# --- environment ------------------------------------------------------------

def paper_guard(environ: dict) -> None:
    """Invariant 1, checked before any client exists. Live trading is not a
    config flip; a wrong/absent value is a hard stop, never a default."""
    value = (environ.get("ALPACA_PAPER_TRADE") or "").strip().lower()
    if value != "true":
        raise SystemExit(
            f"run_day: ALPACA_PAPER_TRADE must be 'true', got {value!r} —"
            " this fund is paper-only (invariant 1). Refusing to start.")


def require_env(names, environ: dict) -> dict:
    """Fail fast, naming every missing var at once — a tired human at a
    terminal should need one round trip, not five."""
    missing = [n for n in names if not (environ.get(n) or "").strip()]
    if missing:
        raise SystemExit(
            f"run_day: missing required env var(s): {', '.join(missing)}."
            " Copy .env.example to .env and `set -a; source .env; set +a`.")
    return {n: environ[n].strip() for n in names}


def parse_channel_overrides(raw: str | None) -> dict:
    """'#pnl=#test-pnl,#risk=#test-risk' -> {'#pnl': '#test-pnl', ...}.

    Optional test-channel remap so a rehearsal day posts somewhere harmless.
    Blank/absent -> {} (post to the real channels). A malformed entry is a
    hard stop: silently posting a rehearsal to #pnl is worse than not
    starting."""
    overrides: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        src, sep, dst = part.partition("=")
        if not sep or not src.strip() or not dst.strip():
            raise SystemExit(
                f"run_day: malformed SLACK_CHANNEL_OVERRIDES entry {part!r} —"
                " expected '#from=#to' pairs separated by commas.")
        overrides[src.strip()] = dst.strip()
    return overrides


class RemappedSlack:
    """SlackPort decorator applying a channel remap. Unlisted channels pass
    through unchanged."""

    def __init__(self, slack, overrides: dict):
        self._slack = slack
        self._overrides = overrides

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        return self._slack.post(self._overrides.get(channel, channel), text,
                                thread_ts)


# --- market gate ------------------------------------------------------------

def market_is_open(source) -> bool:
    """Skip-if-closed guard. True ONLY when the broker's clock says so; an
    unreachable or unparseable clock reads as closed (invariant 4) — a day we
    could not verify is a day we do not trade."""
    try:
        return bool(source.market_clock()["is_open"])
    except Exception as exc:
        log(f"broker clock unreadable ({type(exc).__name__}: {exc}) —"
            " treating the market as CLOSED")
        return False


def load_watchlist(path: Path) -> list[str]:
    """Today's universe from yaml (human-committed; never hardcoded here)."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    tickers = [str(t).strip().upper() for t in (data.get("tickers") or [])
               if str(t).strip()]
    if not tickers:
        raise SystemExit(f"run_day: {path} lists no tickers — nothing to trade.")
    return tickers


# --- seat turns -------------------------------------------------------------

async def _seat_session(cfg: dict, db_path: str, clock, prompt: str):
    """One seat's live SDK session. Options ALWAYS via build_seat_options —
    the tool surface, settings isolation and order hooks are decided there,
    never here (tests/test_exec_seat_tool_surface.py pins them)."""
    from claude_agent_sdk import ClaudeSDKClient

    options = build_seat_options(cfg, db_path, clock)
    async with ClaudeSDKClient(options=options) as client:
        return await run_seat_turn(client, prompt, REQUIRED_SERVERS)


def make_turn(seat: str, cfg: dict, db_path: str, clock, conn, run_date: str,
              prompt: str):
    """The injected run_turn callable orchestrator/daily.py drives.

    Records the turn's cost after EVERY turn (the only production caller of
    record_turn_result) and never propagates: a seat that blows up leaves one
    `alert` and lets the stage's own default land (invariant 4). The cost is
    recorded BEFORE the exec seat's tool-call assertions run — a violating
    turn still spent real money."""

    def run() -> None:
        try:
            names, result = asyncio.run(
                _seat_session(cfg, db_path, clock, prompt))
        except Exception as exc:
            _alert(conn, clock,
                   f"{seat}_turn_failed — {type(exc).__name__}: {exc};"
                   " stage default applies (default is HOLD)")
            return
        record_turn_result(conn, run_date, seat, result, iso(clock.now()))
        if seat == "exec":
            try:
                check_tool_calls(names)
            except Exception as exc:
                _alert(conn, clock, f"exec_turn_violation — {exc}")

    return run


def _alert(conn, clock, text: str) -> None:
    log(f"ALERT {text}")
    append_event(conn, "alert", {"text": text}, iso(clock.now()))


# --- audit ------------------------------------------------------------------

def report_audit(conn, slack, db_path: str, run_date: str, clock) -> int:
    """Run the day's invariant audit; a violation becomes an `alert` event,
    is drained to Slack, and exits non-zero so launchd/cron surfaces it."""
    problems = audit_day.audit(db_path, run_date)
    if not problems:
        log(f"AUDIT CLEAN {run_date}")
        return 0
    now = iso(clock.now())
    _alert(conn, clock,
           f"audit {run_date} FAILED: " + "; ".join(problems))
    drain(conn, slack, now)
    return 1


# --- main -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import pandas as pd

    from market.source_alpaca import AlpacaSource
    from slackkit.real import RealSlack

    environ = os.environ
    paper_guard(environ)                     # invariant 1, before anything else
    env = require_env(REQUIRED_ENV, environ)

    clock = WallClock()                      # the one real clock, injected below
    source = AlpacaSource()                  # re-guards ALPACA_PAPER_TRADE
    if not market_is_open(source):
        log("market is closed — no stages run, nothing traded (exit 0)")
        return 0

    run_date = et_run_date(clock.now())
    db_path = env["FUND_DB"]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = parse_channel_overrides(environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = RemappedSlack(slack, overrides)

    watchlist = load_watchlist(WATCHLIST_YAML)
    sectors = yaml.safe_load(SECTORS_YAML.read_text()) or {}
    account = source.account_state()
    universe = sorted(set(watchlist) | set(account.get("positions") or {}))
    close_df = source.close_frame(universe, end=pd.Timestamp(clock.now()))
    market_inputs = build_market_inputs(watchlist, account, close_df, sectors)

    journals_root = Path(environ.get("FUND_JOURNALS") or (ROOT / "journals"))
    journals_root.mkdir(parents=True, exist_ok=True)

    ctx = StageCtx(conn=conn, run_date=run_date, clock=clock, slack=slack,
                   market_inputs=market_inputs,
                   id_factory=lambda: str(uuid.uuid4()),
                   journals_root=journals_root)

    # Pure recompute (no writes) purely to name today's active tickers in the
    # stage prompts; run_day computes its own inside the pre_gate checkpoint.
    active = run_pre_gate(ctx)
    log(f"{run_date}: watchlist {watchlist} -> active {active}")
    if active:
        tickers = ", ".join(active)
        ctx.run_turn = {
            "research": make_turn(
                SEATS["research"], load_seat_config(SEAT_CONFIG / "analyst.yaml"),
                db_path, clock, conn, run_date,
                f"Research turn. Today's active tickers: {tickers}. Follow"
                " your charter and end by calling submit_signal exactly once"
                " per ticker."),
            "decision": make_turn(
                SEATS["decision"], load_seat_config(SEAT_CONFIG / "pm.yaml"),
                db_path, clock, conn, run_date,
                f"Decision turn. Today's active tickers: {tickers}. Follow"
                " your charter and end by calling submit_decision exactly once"
                " per ticker."),
        }
    else:
        # Nothing is possible today: no LLM spend at all, but every stage still
        # runs, checkpoints and posts the digest (invariant 4).
        log("no active tickers — running the day with zero seat turns")

    execution_turn = make_turn(
        SEATS["execution"], load_seat_config(SEAT_CONFIG / "exec.yaml"),
        db_path, clock, conn, run_date,
        "Execution stage: execute all open tickets per your charter.")

    run_day(ctx, execution_turn=execution_turn, broker=source, sleep=time.sleep)
    return report_audit(conn, slack, db_path, run_date, clock)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
