#!/usr/bin/env python3
"""Hand-run spec registration — gives `strategy_specs` its producer (#198).

    make register-spec     # == python scripts/register_spec.py

`handle_submit_strategy_spec` and the `quant` seat exist; this is the caller
that assigns the turn, and per invariant 6 a workflow-critical turn is
assigned by code, never by a Slack message.

WHY IT IS HAND-RUN AND NOT A FIFTH SYSTEMD LEG (CEO ruling B1, 2026-08-29).
`specs/strategy.md:34` makes SPEC reachable only through *PM sponsors → SPEC*,
and no sponsorship mechanism exists in code: `IDEA` appears four times in the
repo, all prose, zero Python and zero SQL; there is no `ideas` table and no
`strategies` table in `state/schema.sql`. Putting spec production on a timer
would enter a lifecycle state by skipping the gate that guards entry to it,
every night, forever — and `INSERT OR IGNORE` on a content hash bounds nothing,
because fresh prose collides on nothing. The human invocation stands in for the
missing sponsorship gate. When a sponsorship mechanism ships, a timer becomes
arguable; until then it is not.

The daily timer was never an option either: `tests/test_run_day.py:888` pins
`turns_per_day == 4` off `run_day.SEATS`, so a fifth daily seat reddens it
outright. This seat is deliberately absent from `run_day.SEATS`.

WHY IT LOOKS DIFFERENT FROM ITS SIBLINGS. `critic_g1.py` drains
`specs_awaiting_critique` (capped at 3) and `reflect_day.py` drains due
decisions (capped at 25). Both are CONSUMERS and can ask how much work is
outstanding. This job is a PRODUCER with no queue to read, so:
  * it runs exactly ONE turn per invocation — a human decides there should be
    another by running the command again;
  * "did it work?" is a `strategy_specs` row COUNT either side of the turn,
    not a selector re-read. A duplicate registration therefore counts as
    "wrote nothing", which is literally true: the content hash collided,
    INSERT OR IGNORE wrote no row, and the outbox got no event;
  * there is no backlog alert, because there is no backlog.

WHAT THIS BUYS IMMEDIATELY: one hand-run seeds a real spec, and that evening's
existing 16:35 `critic_g1.py` leg drains it from `specs_awaiting_critique` — the
first live G1 night runs on a spec an agent actually wrote.

EXIT CODES ARE A CONTRACT, and they are NOT critic_g1's (invariant 4: no row
beats a wrong row).

  0  a spec was registered. Nothing else returns 0, ever.
  1  the run happened and produced no spec — a turn that raised, a turn that
     wrote nothing, a failure anywhere inside the guard, a bad env.
  2  the run did not happen, because a lock was held. Two different locks can
     cause it and the LOG LINES tell them apart; the code does not, because
     the operator's next action is the same either way: try again later.

  ALPACA_PAPER_TRADE != 'true'  -> exit 1 before a client is built
  a missing env var             -> exit 1 naming every missing var
  run_day holds its lock        -> exit 2, nothing built, nothing spent
  another register_spec running -> exit 2, nothing built, nothing spent
  a turn that raises            -> one alert, no row, exit 1
  a turn that writes nothing    -> one alert, no row, exit 1
  anything else                 -> one alert, exit 1

WHY 2 AND NOT critic_g1's 0. That job is a systemd ExecStart, where a nonzero
code is a RED UNIT and a page; contention there resolves itself correctly and
must not page, so 0 is right for it. This job is typed by a human at a shell
who is waiting to find out whether the fund has a new spec. For them, "another
process is holding the lock, I did nothing" and "a spec was registered" are the
two answers that must never share a code — and 1 would be wrong too, because
nothing failed. Hence a third code.

NO OnFailure= BEHIND IT, unlike `critic_g1.py`. That job is a systemd
ExecStart and its `_guarded` returning 1 fires `fund-alert@%n.service`, a
report path that does not share a failure mode with the job. This job has no
unit (ruling B1: `ops/` is untouched), so the drained alert and the nonzero
exit code in front of the human who typed the command are the entire report.
It still returns 1, because a `make` target that exits 0 on a failed turn is
indistinguishable from one that worked.

THE HANDLER'S TWO-TRANSACTION WRITE IS NOW LIVE. `handle_submit_strategy_spec`
commits the spec (inside `insert_strategy_spec`) and then commits the outbox
event separately; a crash between them leaves a registered spec that was never
projected to `#research` and never will be. Its own docstring says this "starts
to matter when #198 ships a driver." This is that driver. The fix is
`insert_strategy_spec`'s transaction handling, which is a shared write path
(`evals/fixtures.py` calls it too) and out of this lane's region — named here so
the next reader finds it rather than rediscovering it in an incident.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # `python scripts/register_spec.py` anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.clock import iso                     # noqa: E402
from slackkit.outbox import drain                      # noqa: E402
from state.specs import specs_awaiting_critique        # noqa: E402

# Identical to critic_g1's, and for the same reasons: this job runs a seat
# (ANTHROPIC_API_KEY) and drains (SLACK_BOT_TOKEN), and build_seat_options
# wires the alpaca MCP server unconditionally for every seat — which
# run_seat_turn then requires to be CONNECTED, even though the narrowed
# registration surface can reach none of its tools. That coupling is issue
# #108, not a property of this seat.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
                "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY")

# Its own lock. A shared one would let a hand-run at 16:30 hold the 16:35
# nightly legs out of their own window. main() checks run_day's lock too, and
# refuses under either — see its docstring.
LOCK_NAME = "register_spec.lock"

# How deep a G1 queue this job will count before it reports "N+". The canonical
# selector defaults to limit=1 (state/specs.py:48-49), so a DEPTH needs a limit
# argument rather than a second query carrying its own copy of the predicate —
# a duplicated selector is how the job and the tool come to disagree about what
# "pending" means. Same constant, same reason, as critic_g1's
# PENDING_REPORT_LIMIT (scripts/critic_g1.py:233-238).
QUEUE_REPORT_LIMIT = 50

SEAT = "quant"
SEAT_CONFIG = ROOT / "agents" / "config" / f"{SEAT}.yaml"

# The seat's surface for THIS turn, named CONCRETELY. agents/config/quant.yaml
# grants the glob ["mcp__fund__*"] and SEAT_CAPS["quant"] holds exactly one
# cap, so today this narrowing subtracts nothing — it spells out what the glob
# already resolves to. It is here anyway, because build_seat_options refuses a
# per-turn name the yaml does not grant and refuses a glob outright: the day
# the seat gains a second cap, this line is what keeps the REGISTRATION turn
# down to the one write instead of silently widening with the seat.
#
# The two locks agree by test, not by comment — and the test DRIVES
# build_seat_options rather than comparing this constant to SEAT_CAPS
# (tests/test_register_spec_job.py::test_the_turn_surface_is_exactly_the_one_cap_the_seat_holds).
REGISTER_TOOLS = ["mcp__fund__submit_strategy_spec"]


def log(msg: str) -> None:
    print(f"register_spec: {msg}", flush=True)


def _count_text(n: int) -> str:
    """'3' or '50+' — never a number that reads as exact once the selector
    behind it has saturated at QUEUE_REPORT_LIMIT.

    Same shape and reason as critic_g1.py's _count_text
    (scripts/critic_g1.py:256-259), kept as a separate copy rather than a
    shared import — issue #200 tracks the one helper these two jobs will
    eventually share."""
    return f"{n}+" if n >= QUEUE_REPORT_LIMIT else str(n)


def spec_count(conn) -> int:
    """How many specs are registered, full stop.

    A count, not a selector, and that is CHOSEN rather than forced. A narrower
    instrument does exist: handle_submit_strategy_spec stamps `created_at` from
    the same injected clock this job holds (it passes iso(clock.now()) into
    insert_strategy_spec, which writes it verbatim, state/specs.py:39-43), so
    "a spec created at this run's timestamp" is expressible. It is not used,
    for two reasons — a turn is not instantaneous and the wrapper stamps at
    call time, so an equality on the run's start would miss; and the count
    either side is correct under the fund's single-writer-per-turn assumption,
    which is the same assumption handle_submit_strategy_spec's own duplicate
    detection already rests on. Saying "forced" would have overclaimed, and an
    overclaim is what stops the next reader looking for the narrower one when
    single-writer stops being true.
    """
    return conn.execute("SELECT count(*) c FROM strategy_specs"
                        ).fetchone()["c"]


def queue_depth(conn) -> int:
    """How many registered specs are waiting for a G1 verdict, up to
    QUEUE_REPORT_LIMIT.

    THE POINT OF THIS JOB, measured. A registration that never reaches the
    Critic bought nothing, and the 16:35 critic_g1 leg is what collects it —
    so the number the operator wants either side of the turn is the DOWNSTREAM
    queue, not this job's own row count.

    state.specs.specs_awaiting_critique is called, never re-implemented. Its
    predicate ("no strategy_critiques row") is a known divergence from
    strategy-contracts.md §4's canonical `strategies.state == 'SPEC'`, recorded
    in its own docstring and at strategy-contracts.md:27, and the fix when
    `strategies` lands is to REPLACE that selector — which a second copy of the
    predicate here would silently survive.
    """
    return len(specs_awaiting_critique(conn, limit=QUEUE_REPORT_LIMIT))


def register_and_log(conn, slack, clock, run_turn) -> dict:
    """Run ONE registration turn, check it wrote, alert if it did not, drain.
    Returns the counts.

    `run_turn` takes no arguments. Every sibling's takes a job dict because
    every sibling is draining a queue and has a row to hand its turn; this one
    has no queue, so there is nothing to pass. What the turn is asked to
    register is a property of how the turn is BUILT (see _make_run_turn), not
    of a row this function selected.

    SUCCESS IS NEVER INFERRED FROM THE ABSENCE OF AN EXCEPTION.
    run_day.make_turn's own run() catches every exception and returns
    normally, so the likeliest real failure — a seat that never calls
    submit_strategy_spec, or calls it and gives up on {"ok": false} — would
    raise nothing here either. The count either side of the turn is the only
    thing that can tell.

    A DUPLICATE COUNTS AS "WROTE NOTHING", deliberately. The handler reports
    `duplicate: True` to the seat, but no row was written and no event was
    queued, so from this job's side nothing happened — and telling the human
    who typed the command that a spec was registered when none was would be
    fail-open (invariant 4).

    SO DOES A CORRECT DECLINE, and the alert has to say so. charters/quant.md's
    Mission sanctions "this family is tapped out, I am not proposing" as a
    legitimate output; this function cannot distinguish that from a seat that
    went dark, and pretending otherwise would be a guess (invariant 4). It
    names all FOUR causes instead — never called, refused, duplicate,
    correctly declined — so the operator knows to read the transcript rather
    than to open an incident.

    THE G1 QUEUE DEPTH IS REPORTED EITHER SIDE, through
    state.specs.specs_awaiting_critique. A registration that never reaches the
    Critic bought nothing, so the operator's real question is whether the
    16:35 leg has more to do than it did five minutes ago. "0 -> 1" is the
    success this job exists to produce; "2 -> 2" after a wrote-nothing turn is
    a different problem from "0 -> 0". The depth is read in `finally` as well
    as on the success path, because the success path may never run.

    A depth read that itself raises will propagate out of `finally` and be
    caught by _guarded as a register_spec_failed, losing the turn-level alert.
    Accepted rather than nested in another try: if the DB is what broke, the
    turn-level alert could not have been recorded either.

    The alert and the drain both run in `finally`, for reflect_day's N1
    reason: appending only after the turn meant a raise never QUEUED the alert
    at all, so Slack learned nothing. And draining alone was not enough — a
    freshly-appended alert with posted_at IS NULL has no date bound on the
    audit check that catches it, so it would redden every audit until the next
    drain.
    """
    counts = {"registered": 0, "failed": 0}
    failure: dict | None = None
    queue_before = queue_depth(conn)
    queue_after = queue_before
    try:
        before = spec_count(conn)
        try:
            run_turn()
        except Exception as exc:
            log(f"turn raised {type(exc).__name__}: {exc}; nothing registered")
            failure = {"why": "raised",
                       "detail": f"{type(exc).__name__}: {exc}"}
            counts["failed"] += 1
        else:
            if spec_count(conn) > before:
                counts["registered"] += 1
            else:
                log("the turn registered nothing — it returned without"
                    " calling submit_strategy_spec, the call was refused, it"
                    " re-registered content already on the books, or it"
                    " correctly declined to propose")
                failure = {"why": "wrote_nothing", "detail": ""}
                counts["failed"] += 1
        queue_after = queue_depth(conn)
        log(f"registered {counts['registered']} · failed {counts['failed']}"
            f" · G1 queue {_count_text(queue_before)} ->"
            f" {_count_text(queue_after)}")
    finally:
        # queue_after is re-read here as well as above: on the raising branch
        # the line above may never have run, and an alert carrying a stale
        # depth is worse than one carrying none.
        queue_after = queue_depth(conn)
        if failure and failure["why"] == "raised":
            run_day._alert(conn, clock, "register_spec_turn_failed",
                           f"register_spec_turn_failed —"
                           f" {failure['detail']}; no spec was registered."
                           f" G1 queue {_count_text(queue_before)} ->"
                           f" {_count_text(queue_after)}."
                           f" Nothing is queued and nothing retries: run"
                           f" `make register-spec` again when you want one")
        elif failure:
            run_day._alert(conn, clock, "register_spec_wrote_nothing",
                           "register_spec_wrote_nothing — the quant turn ran"
                           " and no new spec row appeared. FOUR causes, and"
                           " this alert cannot tell them apart: the seat"
                           " never called submit_strategy_spec; the call was"
                           " refused; it re-registered content already on the"
                           " books (a duplicate writes no row and queues no"
                           " event); or the seat correctly declined to"
                           " propose, which charters/quant.md sanctions"
                           " ('this family is tapped out, I am not"
                           " proposing') and which is not a fault. Read the"
                           " turn's transcript before treating this as one."
                           f" G1 queue {_count_text(queue_before)} ->"
                           f" {_count_text(queue_after)}."
                           " Nothing is queued and nothing retries")
        drain(conn, slack, iso(clock.now()))
    return counts


def _guarded(conn, slack, clock, body) -> int:
    """Run `body`; make sure a failure is never silent.

    RETURNS 1 ON FAILURE, and PASSES `body`'s own code through otherwise.
    Both halves are load-bearing here in a way they are not in the sibling:
    scripts/critic_g1.py's _body ends `return 0` unconditionally, so its
    pass-through can only ever carry 0 and the distinction is invisible. This
    job's _body returns 0 or 1 depending on whether a spec was registered
    (ruling 16), so a _guarded that swallowed the code would report a failed
    registration as a success at the shell. Pinned with a NONZERO sentinel
    (test_a_clean_run_returns_the_bodys_own_code) — `lambda: 0` asserted
    against 0 cannot tell pass-through from a swallow.

    There is no systemd unit behind this job (CEO ruling B1), so unlike
    scripts/critic_g1.py there is no OnFailure= second report path — the
    drained alert and this exit code, in front of the human who typed the
    command, are the whole report.

    SystemExit alongside Exception for run_day.guarded's reason: a config hard
    stop must still say so in Slack. The recovery is itself guarded — if the
    DB is what broke, the original failure is the one that matters.
    """
    try:
        return body()
    except (Exception, SystemExit) as exc:
        text = (f"register_spec_failed — {type(exc).__name__}: {exc}. The"
                " registration run stopped here; no spec was registered and"
                " nothing retries.")
        log(f"ALERT {text}")
        try:
            run_day._alert(conn, clock, "register_spec_failed", text)
            drain(conn, slack, iso(clock.now()))
        except Exception as inner:
            log(f"could not record/post that alert ({type(inner).__name__}:"
                f" {inner}) — the failure above is the one that matters")
        return 1


def _build_slack(env: dict, environ):
    """The Slack client _guarded needs in order to report anything, plus this
    run's channel remapping.

    A named seam so tests can drive main() without a network client.

    Copied from scripts/critic_g1.py:463-478 rather than shared. Hoisting it
    into scripts/run_day.py is issue #200 and is out of this lane's scope; that
    issue exists BECAUSE of this copy.
    """
    from slackkit.real import RealSlack

    slack = RealSlack(env["SLACK_BOT_TOKEN"])
    overrides = run_day.parse_channel_overrides(
        environ.get("SLACK_CHANNEL_OVERRIDES"))
    if overrides:
        log(f"channel overrides active: {overrides}")
        slack = run_day.RemappedSlack(slack, overrides)
    return slack
