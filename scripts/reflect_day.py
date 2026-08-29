#!/usr/bin/env python3
"""Nightly reflection turns — gives `resolutions.reflection` its producer.

    make reflect           # == python scripts/reflect_day.py

design.md §3's Nightly row says deciding agents write reflections; §7 makes
memory load-bearing in Phase 2. orchestrator/reflect.py has shipped the frame
and the writer since 2026-08-19 and nothing called either. This is the caller.

WHY IT RIDES THE 16:35 TIMER, THIRD. Reflections need resolutions, and
resolve_day writes those at 16:35 — seven hours after the 09:35 trading day.
A reflection stage inside run_day would have nothing to reflect on. Type=oneshot
runs ExecStart lines in order and stops at the first failure, so this runs only
if close_pnl and resolve_day both succeeded — which is exactly right: if
nothing resolved, nothing is reflectable.

UNLIKE resolve_day, THIS JOB NEEDS SLACK AND AN ANTHROPIC KEY, and the
difference is deliberate rather than drift. resolve_day requires neither
because it posts nothing and runs no seat: an unrelated missing var must not
stop the calibration record being written. This job runs seats, which cost
money and can fail, and a failed turn appends an alert. audit_day's
undrained-events check has NO date bound, so an alert this job cannot drain
reddens tomorrow's audit. It runs last, so a missing token here cannot stop
close_pnl or resolve_day, both of which have already committed.

It is no longer the LAST leg: scripts/critic_g1.py (issue #169) runs after it,
placed there because a G1 miss is re-selected every future night
(state/specs.py has no date bound) while a reflection miss is destroyed after
REFLECT_LOOKBACK_DAYS. Nothing about this leg's own posture changes — it still
runs after everything that must not be blocked by a missing token.

Posture (invariant 4: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'  -> exit 1 before a client is built
  * a missing env var             -> exit 1 naming every missing var
  * another reflect_day running   -> exit 0 rather than double-spend
  * a turn that raises            -> one alert per turn, no row, night continues
                                      (defence in depth — not reachable through
                                      run_day.make_turn today; see below)
  * a turn that writes nothing    -> logged, no row; one alert for the whole
                                      night naming every such decision
  * a decision that ages out of the lookback window unreflected -> logged,
                                      one alert naming every such decision
                                      (reflect_aged_out — see due_reflections)
  * more than MAX_TURNS_PER_NIGHT is due -> take the cap, alert how many were
                                            left, the night continues

THIS JOB'S OWN alerting for a broken night is one rollup per failure kind,
never one Slack message per decision. That is NOT the same as one message per
broken night, total: run_day.make_turn (shared trading-day code, out of scope
for this fix wave) catches a seat session that fails and posts its OWN
seat_turn_failed alert, ONE PER FAILING TURN, before this job ever sees the
turn return — from here that turn just looks like "wrote nothing" and folds
into this job's rollup below. So three failing seat sessions in one night post
FOUR Slack messages (three seat_turn_failed, one reflect_turn_wrote_nothing
rollup), not one. An operator must expect that count, not be surprised by it.

A missed decision (a failed turn, a turn that wrote nothing, a systemd
timeout mid-loop, a missing token on an earlier run) is not lost after one
missed night: due_reflections selects on resolved_at over a window spanning
REFLECT_LOOKBACK_DAYS+1 calendar days ([D-REFLECT_LOOKBACK_DAYS 00:00 ET,
D+1 00:00 ET)), so a miss is retried on subsequent nights, for up to
REFLECT_LOOKBACK_DAYS more nights, before it ages out — and aging out is
alerted on explicitly (reflect_aged_out), never silent.

Re-running is safe and cheap: due_reflections selects only rows whose
`reflection` IS NULL, so a re-fire pays only for what is still outstanding.
That pre-check, not store_reflection's guard, is what saves the money — the
guard fires after the turn is already paid for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/reflect_day.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
import audit_day                                      # noqa: E402
from orchestrator.clock import et_run_date, iso       # noqa: E402
from orchestrator.reflect import reflection_frame     # noqa: E402
from slackkit.outbox import drain                     # noqa: E402
from state.db import connect                          # noqa: E402

# SLACK_BOT_TOKEN and ANTHROPIC_API_KEY are required here and deliberately not
# in resolve_day — see the module docstring. This job runs seats and drains.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
                "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY")

LOCK_NAME = "reflect_day.lock"

SEAT = "reflect"
SEAT_CONFIG = ROOT / "agents" / "config" / f"{SEAT}.yaml"

# How many nights a missed decision keeps coming back before it ages out. A
# one-night window (the previous design) meant any miss — a failed turn, a
# systemd timeout mid-loop, a missing token on an earlier ExecStart — was
# never selected again. Bounded rather than unbounded: an unbounded backfill
# would buy a turn for every historical decision on the first fire.
REFLECT_LOOKBACK_DAYS = 7

# Hard cap on how many turns one night's fire will pay for. Exceeding it is
# not silent — see reflect_and_log's reflect_backlog_capped alert.
MAX_TURNS_PER_NIGHT = 25

# Resolved within the trailing REFLECT_LOOKBACK_DAYS+1-calendar-day window
# ending run_date, not yet reflected on, and written by a seat.
#
# The resolved_at window rather than decisions.run_date: resolve_day resolves
# at horizon, so a decision resolved tonight was MADE about five sessions ago
# and a run_date filter would select nothing, every night, forever.
#
# reflection IS NULL is the money-saving predicate. store_reflection guards the
# write too, but that fires after the turn is paid for.
#
# charter_version <> 'none' excludes the orchestrator's own pm_timeout holds:
# no seat reasoned about them, so there is nothing to reflect on and the turn
# would be bought for nothing. Held and rejected decisions ARE included —
# resolve_due resolves them so the scoreboard is not a sample selected by the
# PM's own convictions, and dropping them here would reintroduce that bias one
# stage later. 'unknown' (the schema default, and _parse_charter_version's own
# fallback for an unparseable header) is NOT 'none' and stays included.
_DUE_WHERE = """
  FROM decisions d
  JOIN resolutions r ON r.decision_id = d.id
 WHERE r.reflection IS NULL
   AND r.resolved_at >= ? AND r.resolved_at < ?
   AND COALESCE(d.charter_version, '') <> 'none'
"""
_DUE = f"SELECT d.id AS decision_id, d.ticker, d.run_date {_DUE_WHERE} ORDER BY d.id LIMIT ?"
_DUE_COUNT = f"SELECT COUNT(*) AS n {_DUE_WHERE}"

# The mirror image of _DUE_WHERE's lower bound: a row that fell BELOW the
# window instead of within it. due_reflections can never select such a row
# again — every future run_date only pushes the window's lower bound later —
# so reflection IS NULL here means the reflection will NEVER be written. Same
# charter_version <> 'none' rule: a pm_timeout hold has nothing to reflect on
# whether it is due or aged out. This is what aged_out_reflections alerts on;
# without it, that permanent loss had no alert, no log line, and no audit
# check anywhere in the pipeline.
_AGED_OUT_WHERE = """
  FROM decisions d
  JOIN resolutions r ON r.decision_id = d.id
 WHERE r.reflection IS NULL
   AND r.resolved_at < ?
   AND COALESCE(d.charter_version, '') <> 'none'
"""
_AGED_OUT = f"SELECT d.id AS decision_id, d.ticker {_AGED_OUT_WHERE} ORDER BY d.id"


def log(msg: str) -> None:
    print(f"reflect_day: {msg}", flush=True)


def _window(run_date: str) -> tuple[str, str]:
    """[start of run_date - REFLECT_LOOKBACK_DAYS, end of run_date), in ET.
    Built from audit_day.et_day_window's own bounds rather than hand-rolled
    timezone arithmetic (CLAUDE.md)."""
    day = datetime.strptime(run_date, "%Y-%m-%d").date()
    lookback_date = (day - timedelta(days=REFLECT_LOOKBACK_DAYS)).isoformat()
    start, _ = audit_day.et_day_window(lookback_date)
    _, end = audit_day.et_day_window(run_date)
    return start, end


def due_reflections(conn, run_date: str) -> list[dict]:
    """The decisions resolved in the trailing REFLECT_LOOKBACK_DAYS+1
    calendar-day window ending `run_date` that still need a reflection,
    capped at MAX_TURNS_PER_NIGHT. Use `due_count` alongside this to know
    whether the cap actually bound."""
    start, end = _window(run_date)
    return [dict(r) for r in
            conn.execute(_DUE, (start, end, MAX_TURNS_PER_NIGHT))]


def due_count(conn, run_date: str) -> int:
    """The TRUE number of decisions due tonight, uncapped — what
    reflect_and_log compares against len(due_reflections(...)) to decide
    whether the cap bound and needs an alert."""
    start, end = _window(run_date)
    return conn.execute(_DUE_COUNT, (start, end)).fetchone()["n"]


def aged_out_reflections(conn, run_date: str) -> list[dict]:
    """Decisions that will NEVER be reflected on: `reflection IS NULL` and
    `resolved_at` already fell below the lookback window's lower bound.
    due_reflections' own WHERE requires `resolved_at >= ` that same bound, and
    every later run_date only pushes the bound forward — so once a row crosses
    it, no future fire will ever select it again. This is the permanent loss
    the module docstring warns about, made visible instead of silent."""
    start, _ = _window(run_date)
    return [dict(r) for r in conn.execute(_AGED_OUT, (start,))]


def reflect_and_log(conn, slack, clock, run_turn) -> dict:
    """One turn per due decision, then drain. Returns the counts.

    `run_turn` takes the whole job dict — decision_id, ticker and the computed
    frame — because the seat has no read tools and nothing to look anything up
    with. The frame is computed here AND again inside submit_reflection: this
    copy is what the seat is shown, the tool's copy is what is stored, and the
    tool never trusts the seat to hand its facts back.

    Success is never inferred from the absence of an exception —
    run_day.make_turn's own `run()` already catches every exception itself and
    returns normally, so the likeliest real failure (a seat that never calls
    submit_reflection, or calls it and gives up on {"ok": false}) would raise
    nothing here either. Every turn's row is re-read after it returns; only a
    row that actually carries a reflection counts as `reflected`. That row can
    itself have vanished between the turn and this re-read (same class as the
    frame vanishing before the turn ran); a missing row is treated as "not
    written", never as a crash. The try/except around run_turn stays as
    defence in depth for a run_turn that DOES raise (not reachable through
    make_turn today, costs nothing to keep).

    A turn that wrote nothing is logged per-decision to stdout (journald, not
    Slack) as it happens, but alerted only ONCE by THIS job, naming every
    decision that wrote nothing — the repo's established pattern for a
    per-entity failure (run_day.alert_missing_price_history's one alert
    naming every affected ticker). That rollup is this job's OWN alerting; it
    does not cover run_day.make_turn's separate seat_turn_failed alert, which
    fires once per failing turn and is out of this job's control (see the
    module docstring) — a fully broken night is not one Slack message, it is
    one seat_turn_failed per failing turn plus this one rollup.

    Also alerted once, but separately and unconditionally checked BEFORE any
    turn runs: a decision whose resolved_at has fallen below the lookback
    window's lower bound with no reflection ever written. due_reflections can
    never select it again, so this is a permanent loss, not a retry — see
    aged_out_reflections.

    That rollup alert, and the drain, both run in `finally`: reflection_frame
    or a DB error on a LATER decision must not skip either one. Appending the
    rollup only after the loop finished (inside the outer `try`) meant such a
    raise skipped the append entirely, not just left it undrained — the
    EARLIER decisions' wrote-nothing rollup was never queued, so drain() had
    nothing to post and Slack learned nothing about that turn. Draining in
    `finally` alone was not enough: a freshly-appended alert with
    posted_at IS NULL has no date bound on the audit check that catches it,
    so it would redden every audit until the next drain — but an alert that
    was never appended at all reddens nothing and simply vanishes.
    """
    run_date = et_run_date(clock.now())
    counts = {"reflected": 0, "failed": 0}
    wrote_nothing: list[dict] = []
    try:
        aged_out = aged_out_reflections(conn, run_date)
        if aged_out:
            named = ", ".join(f"{d['decision_id']} ({d['ticker']})"
                              for d in aged_out)
            log(f"aged out: {len(aged_out)} decision(s) will never be"
                f" reflected on — {named}")
            run_day._alert(conn, clock, "reflect_aged_out",
                           f"reflect_aged_out — {len(aged_out)} decision(s)"
                           f" fell out of the {REFLECT_LOOKBACK_DAYS}-night"
                           f" lookback window with no reflection ever"
                           f" written and will never be retried: {named}")
        total_due = due_count(conn, run_date)
        due = due_reflections(conn, run_date)
        if total_due > len(due):
            log(f"backlog capped: {total_due} decisions due tonight,"
                f" taking {len(due)} (MAX_TURNS_PER_NIGHT={MAX_TURNS_PER_NIGHT})")
            run_day._alert(conn, clock, "reflect_backlog_capped",
                           f"reflect_backlog_capped — {total_due} decisions"
                           f" were due tonight, only {len(due)} were taken;"
                           f" the rest stay due for up to"
                           f" {REFLECT_LOOKBACK_DAYS} more nights")
        for job in due:
            frame = reflection_frame(conn, job["decision_id"])
            if frame is None:             # resolved row vanished under us
                log(f"skip decision {job['decision_id']} ({job['ticker']}) —"
                    " resolved row vanished under us")
                counts["failed"] += 1
                continue
            try:
                run_turn({**job, "frame": frame})
            except Exception as exc:
                run_day._alert(conn, clock, "reflect_turn_failed",
                               f"reflect_turn_failed decision"
                               f" {job['decision_id']} ({job['ticker']}) —"
                               f" {type(exc).__name__}: {exc}; no reflection"
                               f" written, retried for up to"
                               f" {REFLECT_LOOKBACK_DAYS} more nights")
                counts["failed"] += 1
                continue
            row = conn.execute(
                "SELECT reflection IS NOT NULL AS written FROM resolutions"
                " WHERE decision_id = ?", (job["decision_id"],)).fetchone()
            if row is None:            # resolved row vanished under us, post-turn
                log(f"skip decision {job['decision_id']} ({job['ticker']}) —"
                    " resolved row vanished under us after the turn ran")
                counts["failed"] += 1
                continue
            if row["written"]:
                counts["reflected"] += 1
            else:
                log(f"decision {job['decision_id']} ({job['ticker']}) wrote"
                    " nothing — turn returned without calling"
                    " submit_reflection (or the call was refused)")
                wrote_nothing.append(job)
                counts["failed"] += 1
        log(f"reflected {counts['reflected']} · failed {counts['failed']}"
            f" · took {len(due)} of {total_due} due")
    finally:
        if wrote_nothing:
            named = ", ".join(f"{j['decision_id']} ({j['ticker']})"
                              for j in wrote_nothing)
            run_day._alert(conn, clock, "reflect_turn_wrote_nothing",
                           f"reflect_turn_wrote_nothing —"
                           f" {len(wrote_nothing)} turn(s) wrote no"
                           f" reflection: {named}; retried for up to"
                           f" {REFLECT_LOOKBACK_DAYS} more nights")
        drain(conn, slack, iso(clock.now()))
    return counts


def main(argv: list[str] | None = None) -> int:
    import os

    from agents.seats import load_seat_config
    from agents.wallclock import WallClock
    from slackkit.real import RealSlack

    environ = os.environ
    run_day.paper_guard(environ)             # invariant 1, before anything else
    env = run_day.require_env(REQUIRED_ENV, environ)

    db_path = env["FUND_DB"]
    lock_path = Path(db_path).parent / LOCK_NAME
    lock = run_day.acquire_lock(lock_path)   # must outlive the run; kept in scope
    if lock is None:
        log(f"another reflect_day holds {lock_path} — exiting 0 rather than"
            " racing it (two overlapping runs = two paid turns per decision)")
        return 0

    clock = WallClock()
    conn = connect(db_path)
    cfg = load_seat_config(SEAT_CONFIG)

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)

    run_date = et_run_date(clock.now())   # cost lands on the day the turn ran

    run_turn = _make_run_turn(SEAT, cfg, db_path, clock, conn, run_date)
    reflect_and_log(conn, slack, clock, run_turn)
    return 0


def _make_run_turn(seat: str, cfg: dict, db_path: str, clock, conn,
                   run_date: str):
    """Build the per-job `run_turn` callable `reflect_and_log` drives.

    A named factory rather than a closure inline in `main()` so this seam —
    a bound id reaching `run_day.make_turn` — is unit-testable without
    calling `main()`, which builds real clients (see the module docstring).
    """
    def run_turn(job: dict) -> None:
        prompt = ("Reflect on this closed decision. Call submit_reflection"
                  f" exactly once with your prose.\n\n{job['frame']}")
        # expected_decision_id binds the turn to job['decision_id']: the
        # seat's submit_reflection call carries no id of its own (see
        # handle_submit_reflection) — this binding is the only thing that
        # decides which row gets written.
        turn = run_day.make_turn(seat, cfg, db_path, clock, conn, run_date,
                                 prompt,
                                 expected_decision_id=job["decision_id"])
        turn()
    return run_turn


if __name__ == "__main__":
    sys.exit(main(sys.argv))
