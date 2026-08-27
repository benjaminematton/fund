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
  * a seat turn that HANGS             -> abandoned at SEAT_MAX_WALL_S, then
                                          the same `alert`-and-default path
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
import itertools
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
import score_day                                                   # noqa: E402
from agents.exec_turn import check_tool_calls, run_seat_turn       # noqa: E402
from gate.tickets import open_tickets                              # noqa: E402
from agents.runtime import record_turn_result                      # noqa: E402
from agents.seats import (build_seat_options, charter_text_for,   # noqa: E402
                          load_seat_config)
from evals.live import (build_trace, file_sink, git_sha,           # noqa: E402
                        rows_written)
from agents.wallclock import WallClock                             # noqa: E402
from market.features import (build_market_inputs, unmapped_holdings,  # noqa: E402
                             unpriceable_book_tickers)
from orchestrator.clock import et_run_date, iso                    # noqa: E402
from orchestrator.daily import (StageCtx, allowed_actions,        # noqa: E402
                                run_day)
from orchestrator.ingest_guard import account_snapshot             # noqa: E402
from orchestrator.preconditions import assert_account_config_unchanged  # noqa: E402
from slackkit.outbox import append_alert, drain                    # noqa: E402
from state.db import connect                                       # noqa: E402

# Core env. ALPACA_PAPER_TRADE is checked separately and first (invariant 1).
REQUIRED_ENV = ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                "FUND_DB", "SLACK_BOT_TOKEN")
REQUIRED_SERVERS = {"alpaca", "fund"}
WATCHLIST_YAML = ROOT / "config" / "watchlist.yaml"
SECTORS_YAML = ROOT / "config" / "sectors.yaml"
ACCOUNT_BASELINE_YAML = ROOT / "config" / "account_config_baseline.yaml"
SEAT_CONFIG = ROOT / "agents" / "config"
# Stage -> the seats that run it, in run order. ALWAYS a tuple, even for a
# single-seat stage: a str|tuple union reads fine here and then silently
# iterates characters at the first call site that forgets to check. Research
# runs two seats sequentially (design §3: staggered starts, API rate limits).
# The exec seat is the only one carrying `trading` (invariant 2); every seat is
# built by build_seat_options, never hand-rolled here.
SEATS = {"research": ("analyst", "news"),
         "decision": ("pm",),
         "execution": ("exec",)}
LOCK_NAME = "run_day.lock"
# Wall-clock ceiling for ONE seat turn (issue #44). max_turns and
# max_budget_usd bound turns and dollars; a stalled MCP tool call or model
# stream spends neither, so without this a hung turn hangs the whole day —
# holding the flock, so tomorrow's timer finds the lock and exits 0, which
# reads exactly like a market-closed day.
#
# Sized to fire BEFORE ops/fund-daily.service's TimeoutStartSec=30min, whose
# SIGTERM can land between the broker accepting a place_stock_order and the
# PostToolUse recorder committing the row. SEATS runs four turns a day
# (analyst, news, pm, exec) — a count fixed by the stages, not by how many
# tickers are active — so the worst case is 4 x 240s = 16min, leaving 14min of
# the unit's 30 for every non-turn thing underneath: the broker snapshot, the
# market-data fetch, reconcile's 90s wait, the audit and the drains.
#
# Beating that SIGTERM is also what makes an abandoned EXEC turn recoverable
# rather than lost, which is the strongest safety argument for this bound:
#   * agents/runtime.py's record_order is `async def` with NO await in its
#     body, so once the PostToolUse hook starts it runs to its commit —
#     cancelling the turn mid-flight cannot tear it in half.
#   * the window that IS real is a response lost between the broker accepting
#     the order and the hook seeing it. orchestrator/daily.py's run_day runs
#     run_execution and then the reconciliation stage IN THE SAME PROCESS, and
#     reconcile_stage calls reconcile.recover_lost_orders first — the issue-#40
#     repair pass for exactly that row.
#   * under the unit's SIGTERM the process DIES, so that repair never runs and
#     the order is permanently unrecorded. Under this bound the turn raises,
#     the day continues, and reconciliation recovers it. Abandoning a turn is
#     therefore strictly LESS harmful than the 30min timeout it pre-empts.
#
# ONE ceiling for every seat, applied unconditionally — there is no per-seat
# knob. Only half the seats have a measured turn cost, and an invented ceiling
# sitting beside a measured one reads as measured. #44's defect is UNBOUNDED,
# not mis-bounded, so one conservative bound closes it; per-seat tuning is #131.
SEAT_MAX_WALL_S = 240.0


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
                        snapshot, journals_root, expected_decision_id=None):
    """One seat's live SDK session. Options ALWAYS via build_seat_options —
    the tool surface, settings isolation and order hooks are decided there,
    never here (tests/test_exec_seat_tool_surface.py pins them).

    `expected_decision_id` is None for every seat but reflect; see
    build_seat_options."""
    from claude_agent_sdk import ClaudeSDKClient

    options = build_seat_options(cfg, db_path, clock, snapshot=snapshot,
                                 journals_root=journals_root,
                                 expected_decision_id=expected_decision_id)
    async with ClaudeSDKClient(options=options) as client:
        return await run_seat_turn(client, prompt, REQUIRED_SERVERS)


class SeatTurnTimeout(Exception):
    """A seat turn abandoned at its wall-clock ceiling (issue #44)."""


async def _bounded(coro, wall_s: float):
    """`coro` under a wall-clock ceiling, raising SeatTurnTimeout when it
    blows it. Wraps the COROUTINE, inside asyncio.run: a bound needs a running
    loop, so it cannot wrap asyncio.run itself.

    asyncio.timeout rather than asyncio.wait_for because `.expired()` is the
    only reliable way to tell OUR expiry from a TimeoutError raised INSIDE the
    turn. wait_for reports both as a bare TimeoutError and offers nothing to
    separate them: not the type, and not the message either — an inner one
    keeps its text where an expiry's is empty, but that is a property of
    whoever raised it, not a contract, so it is no discriminator. The
    consequence is already pinned: three tests in tests/test_run_day.py raise
    TimeoutError("session never connected") from inside the turn and assert
    the alert stays seat_turn_failed carrying that text, and a blanket
    `except TimeoutError` would relabel all three as hangs.

    Nothing under agents/, orchestrator/ or scripts/ raises TimeoutError today
    — agents/exec_turn.py's await_servers_connected raises ExecTurnViolation,
    not TimeoutError — but the SDK, an HTTP client or a broker call can
    (tests/test_order_recovery.py already models a gateway TimeoutError), so
    the discriminator has to be structural rather than a source audit. Note
    that await_servers_connected's 30s is not a wall-clock bound at all: it
    accumulates `elapsed += poll_s` across an injected sleep, so a
    get_mcp_status() that itself hangs never reaches the check. That is one
    more hang THIS bound is the only thing covering.

    NOT a hard ceiling: asyncio.timeout cancels ONCE. _seat_session exits
    through `async with ClaudeSDKClient(...)`, whose __aexit__ tears down the
    CLI subprocess and the MCP servers, and nothing re-arms the bound around
    that teardown — a teardown that ignored the cancel would run past wall_s
    (measured: 3.05s against a 0.05s bound). Left unbounded deliberately, on
    two measurements: the SDK's own transport close() bounds every await
    (~20s) and documents that its anyio shield does NOT hold against a raw
    asyncio cancel, so it aborts here rather than hanging; and for a teardown
    that truly refused cancellation no in-process bound helps, because
    asyncio.run's own shutdown re-awaits the same task — a task+grace rewrite
    measured 3.06s against the same 3.05s. The residual is a hang inside SDK
    teardown only; #44's defect — a stalled model stream or MCP tool call,
    the common case — is closed. The cost of that abort is a CLI child that
    skips the terminate/kill escalation; the SDK keeps it in _ACTIVE_CHILDREN
    for its atexit reaper, so at most one per timed-out turn survives to the
    end of the day's single process."""
    try:
        async with asyncio.timeout(wall_s) as bound:
            return await coro
    except TimeoutError:
        if not bound.expired():
            raise                       # the session's own, not ours
        raise SeatTurnTimeout(
            f"no result after {wall_s:g}s (SEAT_MAX_WALL_S ceiling); the turn"
            " was abandoned mid-flight") from None


def emit_trace_guarded(seat: str, cfg: dict, run_date: str, turn_seq,
                       snapshot, names, result, trace_sink, conn=None) -> None:
    """Record one seat turn as a Trace. Never costs the day.

    Same posture as record_cost_guarded: a trace is EVIDENCE, not control flow,
    and this runs after the seat may already have placed a real order. A failed
    write logs and returns — appending an alert here would be another write on
    a path that just failed.

    Placed beside the cost recorder deliberately: both are per-turn side
    effects that read the same ResultMessage, and splitting them across two
    seams would mean two places to remember a new seat in."""
    if trace_sink is None:
        return
    try:
        brief = snapshot() if callable(snapshot) else (snapshot or {})
        trace_sink(build_trace(
            seat=seat, run_date=run_date, turn_seq=next(turn_seq),
            git_sha=git_sha(), charter_text=charter_text_for(cfg),
            model=cfg.get("model", ""), snapshot=brief,
            # allowed_actions' key set IS run_pre_gate's active set (its own
            # docstring: a ticker where both shapes are 0 is absent entirely),
            # so these are the tickers the seat was actually shown.
            brief_tickers=sorted(brief.get("allowed_actions") or {}),
            tool_names=names or [], result=result,
            # Read AFTER the turn, so the rows are the ones it just wrote.
            # Without this every live trace grades as a seat that submitted
            # nothing — and I4 reports a schema-reject that did not happen.
            rows=rows_written(conn, seat, run_date) if conn is not None else {}))
    except Exception as exc:
        log(f"trace_write_failed {seat} — {type(exc).__name__}: {exc};"
            " trading continues")


def make_turn(seat: str, cfg: dict, db_path: str, clock, conn, run_date: str,
              prompt: str, snapshot=None, journals_root=None,
              trace_sink=None, turn_seq=None, expected_decision_id=None):
    """The injected run_turn callable orchestrator/daily.py drives.

    `snapshot`/`journals_root` are this day's get_stage_brief providers, passed
    DOWN from _trading_day rather than baked into `prompt` — per-run values
    belong in tools, never in prompt text (CLAUDE.md).

    `expected_decision_id` is scripts/reflect_day.py's binding for its
    submit_reflection call; every other caller leaves it None.

    Records the turn's cost after EVERY turn (the only production caller of
    record_turn_result) and never propagates: a seat that blows up leaves one
    `alert` and lets the stage's own default land (invariant 4). The cost is
    recorded BEFORE the exec seat's tool-call assertions run — a violating
    turn still spent real money."""
    def run() -> None:
        try:
            names, result = asyncio.run(_bounded(
                _seat_session(cfg, db_path, clock, prompt, snapshot,
                              journals_root,
                              expected_decision_id=expected_decision_id),
                SEAT_MAX_WALL_S))
        except SeatTurnTimeout as exc:
            # Its own code, because the alert filer opens one issue per code
            # and a hang and a crash want different fixes — the handler below
            # would file this beside genuine crashes. Its own text, because
            # the generic one reports only the exception class, while this
            # names the seat, the ceiling it blew and that the turn was
            # abandoned mid-flight.
            _alert(conn, clock, "seat_turn_timeout",
                   f"{seat}_turn_timeout — {exc}; stage default applies"
                   " (default is HOLD)")
            return
        except Exception as exc:
            _alert(conn, clock, "seat_turn_failed",
                   f"{seat}_turn_failed — {type(exc).__name__}: {exc};"
                   " stage default applies (default is HOLD)")
            return
        log_turn_result(seat, result, names)
        record_cost_guarded(conn, clock, run_date, seat, result,
                            cfg.get("model", ""))
        emit_trace_guarded(seat, cfg, run_date, turn_seq, snapshot, names,
                           result, trace_sink, conn)
        if seat == "exec":
            try:
                # counted AFTER the turn: a ticket the seat consumed is no
                # longer open, so only genuinely unexecuted ones can accuse it
                check_tool_calls(names, len(open_tickets(conn, iso(clock.now()))))
            except Exception as exc:
                _alert(conn, clock, "exec_turn_violation",
                       f"exec_turn_violation — {exc}")

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


def record_cost_guarded(conn, clock, run_date: str, seat: str, result,
                        configured_model: str = "") -> None:
    """Cost accounting must never take the trading day down (review Fix 6).

    record_turn_result never raises on a MISSING estimate — that path is an
    alert — but its DB writes can, and this is called after the seat may
    already have placed a real order. No alert on failure: appending one is
    another write to the same connection that just failed. The log line plus
    the audit's own 'no cost rows recorded' check are the surfacing path, and
    both are louder than a dead trading day.

    `configured_model` is the seat's yaml model, which record_turn_result
    compares against what actually served the turn. It is this caller's job to
    supply it: the runtime must not read config files per turn, and a caller
    that omits it gets no divergence check rather than a false one."""
    try:
        record_turn_result(conn, run_date, seat, result, iso(clock.now()),
                           configured_model=configured_model)
    except Exception as exc:
        log(f"ALERT cost_record_failed {seat} — {type(exc).__name__}: {exc};"
            " trading continues; the audit will flag the missing cost row")


def _alert(conn, clock, code: str, text: str, **payload) -> None:
    log(f"ALERT {text}")
    append_alert(conn, code, text, now_iso=iso(clock.now()), **payload)


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
    _alert(conn, clock, "missing_price_history",
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
    _alert(conn, clock, "unmapped_sector",
           f"no config/sectors.yaml entry for held {', '.join(gaps)} —"
           " sector book value is NaN, so every buy fails closed"
           " (gate_error) until the yaml names them",
           tickers=gaps)


# --- scorecard --------------------------------------------------------------

def post_scorecard(conn, slack, db_path: str, run_date: str, clock) -> None:
    """Append the day's scorecard and drain it, BEFORE report_audit runs.

    The ordering is load-bearing in both directions. run_stage drains after
    every stage, but this runs after the last one, so an append with no drain
    beside it would sit unposted — and the audit's undrained-outbox check is
    global, so it would redden the NEXT day rather than this one. Draining
    before the audit also means the audit verifies an outbox that includes the
    scorecard, instead of one it is about to dirty.

    Never fails the day. The scorecard is a reading aid; audit_day owns the
    exit code, and a report that could take down a trading day would invert
    what this is for (invariant 4)."""
    try:
        score_day.append_scorecard_event(conn, db_path, run_date,
                                         iso(clock.now()))
        drain(conn, slack, iso(clock.now()))
    except Exception as exc:
        log(f"scorecard_failed {run_date} — {type(exc).__name__}: {exc};"
            " the day is unaffected")


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
    _alert(conn, clock, "audit_failed",
           f"audit {run_date} FAILED: " + "; ".join(problems),
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
            append_alert(conn, "run_day_failed", text, now_iso=iso(clock.now()))
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
    # The gate sizes the ENTIRE day from this one payload, so it is validated
    # before anything reads it: an empty positions list does not fail
    # downstream, it sizes (held_qty 0 blocks every sell, the empty book takes
    # the permissive correlation tier). None means the payload could not be
    # trusted; the alert is already appended and no stage may run.
    account = account_snapshot(conn, source=source, now_iso=iso(clock.now()),
                               sleep=time.sleep)
    if account is None:
        # Drained here because run_day drains per stage and no stage will run,
        # so the alert would otherwise sit in the outbox until the next day —
        # the same reason the account-config precondition drains explicitly.
        log("ALERT positions_payload_lost — the broker's positions payload"
            " could not be trusted; no stages run, nothing traded (exit 1)")
        drain(conn, slack, iso(clock.now()))
        return 1
    universe = sorted(set(watchlist) | set(account.get("positions") or {}))
    close_df = source.close_frame(universe, end=pd.Timestamp(clock.now()))
    alert_missing_price_history(conn, clock, close_df,
                                account.get("positions") or {})
    alert_unmapped_sectors(conn, clock, account.get("positions") or {}, sectors)
    market_inputs = build_market_inputs(watchlist, account, close_df, sectors)

    journals_root = Path(environ.get("FUND_JOURNALS") or (ROOT / "journals"))
    journals_root.mkdir(parents=True, exist_ok=True)

    ctx = StageCtx(conn=conn, run_date=run_date, clock=clock, slack=slack,
                   research_seats=SEATS["research"],
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
    # One counter for the whole day: the turn sequence is the trace filename,
    # so two seats sharing a number would silently overwrite each other.
    # FUND_TRACES unset means no recording — deliberately not in REQUIRED_ENV,
    # so an older .env runs the day exactly as before rather than failing to
    # start over an evidence feature.
    turn_seq = itertools.count()
    traces_root = environ.get("FUND_TRACES")
    trace_sink = file_sink(traces_root) if traces_root else None
    if traces_root:
        log(f"{run_date}: recording seat traces under {traces_root}")

    if active:
        tickers = ", ".join(active)
        research_prompt = (
            f"Research turn. Today's active tickers: {tickers}. Start by"
            " calling get_stage_brief, then follow your charter and end by"
            " calling submit_signal exactly once per ticker.")
        research_turns = [
            make_turn(seat, load_seat_config(SEAT_CONFIG / f"{seat}.yaml"),
                      db_path, clock, conn, run_date, research_prompt,
                      snapshot=lambda: brief, journals_root=journals_root,
                      trace_sink=trace_sink, turn_seq=turn_seq)
            for seat in SEATS["research"]]

        def run_research_turns() -> None:
            """Sequential, in SEATS['research'] order — NOT asyncio.gather.
            make_turn wraps each seat in its own asyncio.run + client context,
            so sequential keeps exactly one ClaudeSDKClient subprocess alive at
            a time, and the 09:00->11:00 gap to the decision stage means there
            is no wall-clock reason to overlap them. make_turn also swallows
            and alerts per seat, so one seat's failure leaves the other's
            signal intact and its own neutral/0 default lands."""
            for run in research_turns:
                run()

        ctx.run_turn = {
            "research": run_research_turns,
            "decision": make_turn(
                SEATS["decision"][0], load_seat_config(SEAT_CONFIG / "pm.yaml"),
                db_path, clock, conn, run_date,
                f"Decision turn. Today's active tickers: {tickers}. Start by"
                " calling get_stage_brief, then follow your charter and end by"
                " calling submit_decision exactly once per ticker.",
                snapshot=lambda: brief, journals_root=journals_root,
                trace_sink=trace_sink, turn_seq=turn_seq),
        }
    else:
        # Nothing is possible today: no LLM spend at all, but every stage still
        # runs, checkpoints and posts the digest (invariant 4).
        log("no active tickers — running the day with zero seat turns")

    execution_turn = make_turn(
        SEATS["execution"][0], load_seat_config(SEAT_CONFIG / "exec.yaml"),
        db_path, clock, conn, run_date,
        "Execution stage: execute all open tickets per your charter.",
        trace_sink=trace_sink, turn_seq=turn_seq)

    # Before any stage: the gate's thresholds stand on broker-side account
    # settings nothing else reads back. Alert-only — a changed precondition is
    # not grounds to refuse to trade, but it must not go unnamed (2026-08-20
    # design). Drained explicitly because run_day drains per stage and this
    # runs before the first one.
    if assert_account_config_unchanged(
            conn, broker=source,
            baseline=yaml.safe_load(ACCOUNT_BASELINE_YAML.read_text()) or {},
            now_iso=iso(clock.now())):
        drain(conn, slack, iso(clock.now()))

    run_day(ctx, execution_turn=execution_turn, broker=source, sleep=time.sleep)
    post_scorecard(conn, slack, db_path, run_date, clock)
    return report_audit(conn, slack, db_path, run_date, clock)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
