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
  * another run_day already running   -> log, exit 0, touch nothing
  * market closed / clock unreadable  -> log, exit 0, trade nothing
  * a seat turn that raises            -> one `alert`, then the stage's own
                                          default (neutral/0, pm_timeout hold)
  * anything the audit flags           -> `alert` posted to Slack, exit 1
  * anything ELSE that raises after    -> one `alert`, drained to Slack, then
    connect() (a stage body, the           exit 1 — nobody is watching a
    watchlist yaml, the market feed)       scheduled run, and a silent stop is
                                           the worst outcome of all

    NOTE: "after connect()" is where the alert-and-drain guard (`guarded()`,
    wired around `_trading_day` in main()) actually starts. paper_guard(),
    require_env(), acquire_lock(), WallClock()/AlpacaSource() construction,
    market_is_open(), RealSlack(...) construction, and
    parse_channel_overrides(...) all run BEFORE connect() and are NOT covered
    — a malformed SLACK_CHANNEL_OVERRIDES, for example, still exits via an
    uncaught SystemExit with nothing posted to Slack. That is acceptable: no
    order can have been placed by that point, and the exit is non-zero with a
    descriptive stderr message, so it is a visible failure, just not a Slack
    one.

One fire per market day (review P4). Checkpoint CAS makes a re-fire resume
rather than repeat, so a crashed day is recovered by running this again —
SEQUENTIALLY. Two OVERLAPPING processes would both re-run a stage whose
checkpoint is 'running': two seat turns' LLM spend, two drains' Slack posts.
That is what acquire_lock() below exists to prevent.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/run_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling audit_day

import yaml                                                        # noqa: E402

import audit_day                                                   # noqa: E402
from agents.exec_turn import check_tool_calls, run_seat_turn       # noqa: E402
from gate.tickets import open_tickets                              # noqa: E402
from agents.runtime import record_turn_result                      # noqa: E402
from agents.seats import build_seat_options, load_seat_config      # noqa: E402
from agents.wallclock import WallClock                             # noqa: E402
from market.features import (build_market_inputs, unmapped_holdings,  # noqa: E402
                             unpriceable_book_tickers)
from orchestrator.clock import et_run_date, iso                    # noqa: E402
from orchestrator.daily import (StageCtx, allowed_actions,        # noqa: E402
                                run_day)
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
LOCK_NAME = "run_day.lock"


def log(msg: str) -> None:
    print(f"run_day: {msg}", flush=True)


# --- single instance --------------------------------------------------------

def acquire_lock(path: Path):
    """Non-blocking exclusive flock, or None if another run_day holds it.

    Sequential re-fires are safe (checkpoint CAS resumes them); OVERLAPPING
    ones are not — `make live-day` on top of a running scheduled job would put
    two processes through the same 'running' stage body, doubling the LLM spend
    and the Slack posts. flock is held by the open file description, so the
    kernel releases it the instant the process dies: a crash can never leave a
    lock that blocks tomorrow's run, and there is no pid-liveness guessing.
    The caller must keep the returned handle alive for the run's duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")           # 'a+', not 'w': never truncate a lock
    try:                                # file another process is holding
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


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

    def post(self, channel: str, text: str, thread_ts: str | None = None,
             blocks: list[dict] | None = None, username: str | None = None,
             icon_emoji: str | None = None) -> str:
        return self._slack.post(self._overrides.get(channel, channel), text,
                                thread_ts, blocks, username, icon_emoji)


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

async def _seat_session(cfg: dict, db_path: str, clock, prompt: str,
                        snapshot, journals_root):
    """One seat's live SDK session. Options ALWAYS via build_seat_options —
    the tool surface, settings isolation and order hooks are decided there,
    never here (tests/test_exec_seat_tool_surface.py pins them)."""
    from claude_agent_sdk import ClaudeSDKClient

    options = build_seat_options(cfg, db_path, clock, snapshot=snapshot,
                                 journals_root=journals_root)
    async with ClaudeSDKClient(options=options) as client:
        return await run_seat_turn(client, prompt, REQUIRED_SERVERS)


def make_turn(seat: str, cfg: dict, db_path: str, clock, conn, run_date: str,
              prompt: str, snapshot=None, journals_root=None):
    """The injected run_turn callable orchestrator/daily.py drives.

    `snapshot`/`journals_root` are this day's get_stage_brief providers, passed
    DOWN from _trading_day rather than baked into `prompt` — per-run values
    belong in tools, never in prompt text (CLAUDE.md).

    Records the turn's cost after EVERY turn (the only production caller of
    record_turn_result) and never propagates: a seat that blows up leaves one
    `alert` and lets the stage's own default land (invariant 4). The cost is
    recorded BEFORE the exec seat's tool-call assertions run — a violating
    turn still spent real money."""

    def run() -> None:
        try:
            names, result = asyncio.run(
                _seat_session(cfg, db_path, clock, prompt, snapshot,
                              journals_root))
        except Exception as exc:
            _alert(conn, clock,
                   f"{seat}_turn_failed — {type(exc).__name__}: {exc};"
                   " stage default applies (default is HOLD)")
            return
        log_turn_result(seat, result, names)
        record_cost_guarded(conn, clock, run_date, seat, result)
        if seat == "exec":
            try:
                # counted AFTER the turn: a ticket the seat consumed is no
                # longer open, so only genuinely unexecuted ones can accuse it
                check_tool_calls(names, len(open_tickets(conn, iso(clock.now()))))
            except Exception as exc:
                _alert(conn, clock, f"exec_turn_violation — {exc}")

    return run


def log_turn_result(seat: str, result, tool_names=None) -> None:
    """num_turns right-sizes each seat's max_turns (HANDOFF-LIVE §7) and is
    persisted nowhere, so log it explicitly rather than leaving the SDK's own
    stdout formatting as the owner's only capture path.

    The tool-call list is logged for the same reason and costs nothing: on
    2026-08-17 an exec turn billed four turns, placed no order, and left no
    record of what it HAD called — diagnosing it took introspecting the
    broker's MCP schema. One line here would have made it a grep."""
    log(f"{seat} turn done: num_turns={getattr(result, 'num_turns', 'n/a')}"
        f" est_cost_usd={getattr(result, 'total_cost_usd', 'n/a')}"
        f" tools={list(tool_names) if tool_names is not None else 'n/a'}")


def record_cost_guarded(conn, clock, run_date: str, seat: str, result) -> None:
    """Cost accounting must never take the trading day down (review Fix 6).

    record_turn_result never raises on a MISSING estimate — that path is an
    alert — but its DB writes can, and this is called after the seat may
    already have placed a real order. No alert on failure: appending one is
    another write to the same connection that just failed. The log line plus
    the audit's own 'no cost rows recorded' check are the surfacing path, and
    both are louder than a dead trading day."""
    try:
        record_turn_result(conn, run_date, seat, result, iso(clock.now()))
    except Exception as exc:
        log(f"ALERT cost_record_failed {seat} — {type(exc).__name__}: {exc};"
            " trading continues; the audit will flag the missing cost row")


def _alert(conn, clock, text: str, **payload) -> None:
    log(f"ALERT {text}")
    append_event(conn, "alert", {"text": text, **payload}, iso(clock.now()))


# --- market-data gaps -------------------------------------------------------

def alert_missing_price_history(conn, clock, close_df, positions) -> None:
    """Name every held ticker the feed gave no usable history for.

    market/features.py drops those from the book-correlation basket so ONE
    unpriceable holding cannot NaN-poison every candidate and cost the whole
    trading day — but a shrunken basket understates correlation and therefore
    sizes UP, so the exclusion must be visible. features.py is pure compute
    with no event access, which is why the alert is appended HERE, in the
    composition root that assembles the market snapshot."""
    gaps = unpriceable_book_tickers(close_df, positions)
    if not gaps:
        return
    # every holding dark is a data outage, not a shrunken basket: features
    # returns NaN there and the gate rejects, so say which one happened.
    total_blackout = set(gaps) >= set(positions)
    consequence = ("no book member is priceable at all, so correlation is"
                   " unmeasurable and every buy today fails the gate closed"
                   if total_blackout else
                   "today's correlations are understated and sizing is looser"
                   " than it should be")
    _alert(conn, clock,
           f"no usable price history for held {', '.join(gaps)} — excluded"
           f" from the book-correlation basket: {consequence}, until the"
           " feed recovers",
           tickers=gaps)


def alert_unmapped_sectors(conn, clock, positions, sectors) -> None:
    """Name every held ticker missing from config/sectors.yaml.

    sector_book_value fails closed on those (invariant 4), so the whole day's
    buys come back gate_error until the yaml names them — a one-line commit,
    but only if the alert says which ticker. Appended here for the same
    reason as alert_missing_price_history: features.py is pure compute."""
    gaps = unmapped_holdings(positions, sectors)
    if not gaps:
        return
    _alert(conn, clock,
           f"no config/sectors.yaml entry for held {', '.join(gaps)} —"
           " sector book value is NaN, so every buy fails closed"
           " (gate_error) until the yaml names them",
           tickers=gaps)


# --- audit ------------------------------------------------------------------

def report_audit(conn, slack, db_path: str, run_date: str, clock) -> int:
    """Run the day's invariant audit; a violation becomes an `alert` event,
    is drained to Slack, and exits non-zero so launchd/cron surfaces it.

    That alert carries audit_day.SELF_ALERT_KEY so the audit cannot poison
    itself: without the marker, a crash-resume re-fire of the SAME day would
    count this attempt's alert as a fresh violation on the next one."""
    problems = audit_day.audit(db_path, run_date)
    if not problems:
        log(f"AUDIT CLEAN {run_date}")
        return 0
    now = iso(clock.now())
    _alert(conn, clock, f"audit {run_date} FAILED: " + "; ".join(problems),
           **{audit_day.SELF_ALERT_KEY: True})
    drain(conn, slack, now)
    return 1


def guarded(conn, slack, clock, body: Callable[[], int]) -> int:
    """Run `body`, and make sure a failure is never silent (review Fixes 2/3).

    Without this, any raise inside a stage body — a DB error in run_research's
    insert, a journals permission failure in run_close — propagated out of
    main() and skipped report_audit entirely: no alert appended, nothing
    drained, Slack never told. That is reachable AFTER the gate minted a ticket
    and the exec seat placed an order, so #risk shows a ticket, #trade-log
    shows nothing, and the owner of an unattended run learns nothing until
    they read logs/run_day.err.log.

    SystemExit is caught alongside Exception on purpose: load_watchlist's
    "lists no tickers" hard stop is a BaseException, and a scheduled job that
    stops on a config error must still say so in Slack. The recovery is itself
    guarded — if the DB is what broke, the original failure is the one that
    matters and it is already in the log."""
    try:
        return body()
    except (Exception, SystemExit) as exc:
        text = (f"run_day_failed — {type(exc).__name__}: {exc}. The day"
                " stopped here and the audit did not run; nothing further was"
                " traded (default is HOLD).")
        log(f"ALERT {text}")
        try:
            append_event(conn, "alert", {"text": text}, iso(clock.now()))
            drain(conn, slack, iso(clock.now()))
        except Exception as inner:
            log(f"could not record/post that alert ({type(inner).__name__}:"
                f" {inner}) — the failure above is the one that matters")
        return 1


# --- main -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from market.source_alpaca import AlpacaSource
    from slackkit.real import RealSlack

    environ = os.environ
    paper_guard(environ)                     # invariant 1, before anything else
    env = require_env(REQUIRED_ENV, environ)

    db_path = env["FUND_DB"]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(db_path).parent / LOCK_NAME
    lock = acquire_lock(lock_path)           # must outlive the run; kept in scope
    if lock is None:
        log(f"another run_day holds {lock_path} — exiting 0 rather than racing"
            " it (two overlapping runs = two seat turns and two drains)")
        return 0

    clock = WallClock()                      # the one real clock, injected below
    source = AlpacaSource()                  # re-guards ALPACA_PAPER_TRADE
    if not market_is_open(source):
        log("market is closed — no stages run, nothing traded (exit 0)")
        return 0

    run_date = et_run_date(clock.now())
    conn = connect(db_path)

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = parse_channel_overrides(environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = RemappedSlack(slack, overrides)

    # From here (after connect(), RealSlack construction and channel-override
    # parsing) onward nothing may die silently: the guard covers the
    # watchlist/sectors load, the market-data fetch, every stage body, and the
    # audit itself. Anything BEFORE this point (paper_guard, require_env,
    # acquire_lock, market_is_open, RealSlack(...), parse_channel_overrides)
    # is not covered — see the module docstring's posture note.
    return guarded(conn, slack, clock,
                   lambda: _trading_day(conn, slack, clock, source, run_date,
                                        db_path, environ))


def _trading_day(conn, slack, clock, source, run_date: str, db_path: str,
                 environ: dict) -> int:
    """Everything between the DB connection and the audit's verdict. Split out
    of main() so guarded() wraps the whole of it in one place — and so the
    pre-write failure paths (watchlist, sectors yaml, the market-data fetch)
    are exercisable offline without building a real client."""
    import pandas as pd

    watchlist = load_watchlist(WATCHLIST_YAML)
    sectors = yaml.safe_load(SECTORS_YAML.read_text()) or {}
    account = source.account_state()
    universe = sorted(set(watchlist) | set(account.get("positions") or {}))
    close_df = source.close_frame(universe, end=pd.Timestamp(clock.now()))
    alert_missing_price_history(conn, clock, close_df,
                                account.get("positions") or {})
    alert_unmapped_sectors(conn, clock, account.get("positions") or {}, sectors)
    market_inputs = build_market_inputs(watchlist, account, close_df, sectors)

    journals_root = Path(environ.get("FUND_JOURNALS") or (ROOT / "journals"))
    journals_root.mkdir(parents=True, exist_ok=True)

    ctx = StageCtx(conn=conn, run_date=run_date, clock=clock, slack=slack,
                   research_seats=(SEATS["research"],),
                   market_inputs=market_inputs,
                   id_factory=lambda: str(uuid.uuid4()),
                   journals_root=journals_root)

    # Pure recompute (no writes) purely to name today's active tickers in the
    # stage prompts and to build the seats' stage brief; run_day computes its
    # own active set inside the pre_gate checkpoint.
    actions = allowed_actions(market_inputs)
    active = list(actions)
    # The ONE injected stage-brief provider for the day. Everything per-run
    # (the book, the gate's budget, the journals) reaches a seat through this
    # and get_stage_brief — never through prompt text, which would bake
    # today's numbers into a string and break replay.
    brief = {"cash": account.get("cash"),
             "positions": account.get("positions") or {},
             "allowed_actions": actions}
    log(f"{run_date}: watchlist {watchlist} -> active {active}"
        f" -> allowed {actions}")
    if active:
        tickers = ", ".join(active)
        ctx.run_turn = {
            "research": make_turn(
                SEATS["research"], load_seat_config(SEAT_CONFIG / "analyst.yaml"),
                db_path, clock, conn, run_date,
                f"Research turn. Today's active tickers: {tickers}. Start by"
                " calling get_stage_brief, then follow your charter and end by"
                " calling submit_signal exactly once per ticker.",
                snapshot=lambda: brief, journals_root=journals_root),
            "decision": make_turn(
                SEATS["decision"], load_seat_config(SEAT_CONFIG / "pm.yaml"),
                db_path, clock, conn, run_date,
                f"Decision turn. Today's active tickers: {tickers}. Start by"
                " calling get_stage_brief, then follow your charter and end by"
                " calling submit_decision exactly once per ticker.",
                snapshot=lambda: brief, journals_root=journals_root),
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
