#!/usr/bin/env python3
"""Nightly G1 enforcement — gives `strategy_critiques` its producer (#169).

    make critic-g1         # == python scripts/critic_g1.py

strategy-contracts.md §2 records that nothing yet READS strategy_critiques.
The two MCP tools have shipped since 2026-08-20 and nothing has ever opened a
Critic session for them. This is the caller.

WHY IT RIDES THE 16:35 TIMER — not the trading day. The G1 queue holds
nothing time-sensitive (a spec's worst case is 30d-idle staleness), and a
fifth scheduled seat turn does not fit the day's wall-clock budget:
tests/test_run_day.py's ceiling test derives turns_per_day from run_day.SEATS
and 5 x SEAT_MAX_WALL_S = 1200s exceeds the 0.6 x 30min = 1080s it allows.
reflect_day.py is the precedent — a seat turn on the nightly job, outside
SEATS and outside design.md §3.

WHY FOURTH AND LAST, i.e. AFTER reflect. Three reasons, each checkable:

  1. This unit's own comment already states the principle: reflect is last
     because "it is the only leg that spends LLM budget and the only one that
     can fail on a missing ANTHROPIC_API_KEY or SLACK_BOT_TOKEN, so a failure
     here cannot cost the fund its P&L line or its calibration record." This
     leg spends LLM budget and needs the same two secrets. The principle the
     file already committed to therefore puts it behind reflect, not in front.
  2. PERISHABILITY. state/specs.py:specs_awaiting_critique selects on
     `c.spec_id IS NULL` with NO date bound: a spec skipped tonight is
     re-selected tomorrow night and every night after, forever. reflect's
     _DUE_WHERE bounds on resolved_at within REFLECT_LOOKBACK_DAYS=7, and
     _AGED_OUT_WHERE exists to alert on rows that fell below that window and
     will NEVER be written. G1 misses are recoverable; reflect misses are
     destroyed. The perishable leg goes first.
  3. Losing the window is NOT silent, so there is nothing to protect against
     by going early. ops/fund-pnl.service:4 is OnFailure=fund-alert@%n.service
     -> ops/notify_failure.sh, which posts by curl from /etc/fund/alert-env and
     deliberately shares no dependency with this job. An overrun, a nonzero
     exit and the guillotine all fail the UNIT and all alert.

(At current volume the question is close to moot: config/watchlist.yaml is
capped at 3 tickers, so reflect's realistic load is ~3 turns, not its
MAX_TURNS_PER_NIGHT=25 backstop.)

WHY THIS LEG EXITS NONZERO ON FAILURE, unlike an earlier design. Nothing runs
after it, so a red exit cannot cost the fund anything downstream — and going
quiet costs a lot: an alert appended to `events` is only visible once it
DRAINS, and if the failure IS Slack, the drain fails too and the night is
invisible. OnFailure= is the one report path that does not share a failure
mode with this job's own Slack client. So: alert + drain (best effort) AND
return 1, the same posture as run_day.guarded. See _guarded.

Posture (invariant 4: no row beats a wrong row):
  * ALPACA_PAPER_TRADE != 'true'  -> exit 1 before a client is built
  * a missing env var             -> exit 1 naming every missing var
  * another critic_g1 running     -> exit 0 rather than double-spend (not a
                                     failure: the other process is doing the
                                     work)
  * a turn that raises            -> one alert, NO row, night continues
                                     (defence in depth — not reachable through
                                     run_day.make_turn today)
  * a turn that writes nothing    -> one alert naming the spec AND how many
                                     specs are still pending, and the loop
                                     STOPS (see head-of-line, below)
  * more than MAX_G1_TURNS_PER_NIGHT pending -> take the cap, alert how many
                                     were left, the night continues
  * anything else after connect() -> one alert, drained best-effort, EXIT 1 so
                                     systemd's OnFailure reports it even if
                                     Slack is what broke

NEVER A DEFAULT ROW. This module SELECTS the queue and RE-READS the result;
the only INSERT into strategy_critiques anywhere is the seat's own
submit_spec_critique call. At G1 the absence of a row IS the not-advancing
signal (specs/strategy.md invariant 7), so a default row would silently
advance a spec nobody reviewed. Pinned by a source lint in
tests/test_critic_g1_job.py, the same instrument tests/test_state_specs.py:203
points at orchestrator/.

HEAD-OF-LINE BLOCKING IS STRUCTURAL, and the loop is shaped around it.
get_spec_brief takes NO arguments and always returns the OLDEST unreviewed
spec, so this job cannot point a turn at spec B while spec A sits uncritiqued.
Continuing after a turn that wrote nothing would buy MAX_G1_TURNS_PER_NIGHT
turns against the SAME spec and fail identically each time. So the loop
breaks, the spend is bounded at one turn, and the alert names the spec that is
blocking AND the number still pending behind it — a blocked head with four
specs queued behind it is a different operator problem from a blocked head
that is the whole queue, and one alert must be able to tell them apart.
Removing the block needs a spec_id argument in agents/tools/fund_server.py,
which is out of this lane's region.

THE VERDICT IS NOT BOUND TO THE SPEC THE TURN WAS SHOWN. handle_submit_spec_
critique builds SpecCritique(spec_id=args["spec_id"], ...) — the id comes from
the SEAT's tool arguments, and the handler only checks that the spec is
registered and unreviewed. The oldest-first selector binds what the seat is
SHOWN, never what it WRITES, so a turn shown spec A can write a verdict for
spec B; B then becomes permanently unreviewable (write-once) and A is still
pending. This job DETECTS that — has_verdict(shown_spec_id) is False, so the
night counts it as a failure and alerts — but it cannot PREVENT it. The fix is
a binding in fund_server.py; escalated, out of region.

INTERRUPT SEMANTICS. There are no checkpoints on the nightly path;
idempotency is row-level, exactly like reflect's `reflection IS NULL`.
  * killed before the tool call        -> no row; specs_awaiting_critique's
                                          `c.spec_id IS NULL` re-selects the
                                          same spec the next night
  * killed DURING the tool call        -> impossible to tear in half: the
                                          INSERT + append_event + commit are
                                          one commit inside the handler
  * killed between that commit and     -> the row and the event stand, only
    this job's re-read                    the counter is lost; the undrained
                                          event reddens the next audit (that
                                          check has no date bound) and the
                                          next drain posts it. A re-run
                                          cannot double-write: the verdict is
                                          PK-write-once and the spec is no
                                          longer selected.
  * killed mid-drain                   -> drain selects posted_at IS NULL;
                                          idempotent
  * a HUNG turn                        -> run_day.make_turn's _bounded fires
                                          at SEAT_MAX_WALL_S (240s), ~26min
                                          before the unit's SIGTERM, posts
                                          seat_turn_timeout, writes no row
  * the process dying with the flock   -> the kernel releases it with the open
                                          file description; tomorrow is never
                                          blocked

ALERT ARITHMETIC, so an operator is not surprised by the count.

  * run_day.make_turn posts its OWN seat_turn_failed/seat_turn_timeout, one per
    failing turn, before this job ever sees the turn return — from here that
    turn just looks like "wrote nothing". So a night whose single turn crashes
    posts TWO alert messages (one seat_turn_failed, one
    critic_g1_turn_wrote_nothing), not one.
  * A SUCCESSFUL turn also posts: the spec_critique event renders to #research
    (slackkit/render.py).
  * And expect a model_fallback_used post to #risk on top of that, roughly one
    per turn. agents/config/critic.yaml pins model: claude-sonnet-5, and this
    event is a KNOWN FALSE POSITIVE for Sonnet-configured seats — an SDK
    auxiliary haiku call shows up in model_usage and record_turn_result reads
    the extra key as a fallback. It is not a real fallback and does not mean
    the verdict was written by a model other than the configured one. It is
    deliberately not an `alert` kind (audit_day fails the day on any alert),
    so it does not redden the audit; it does cost a Slack post.

So the realistic count for a clean night with one pending spec is TWO posts
(#research verdict + #risk fallback), and for a crashed turn TWO alerts plus
whatever make_turn already posted.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # `python scripts/critic_g1.py` anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling run_day

import run_day                                        # noqa: E402
from orchestrator.clock import et_run_date, iso       # noqa: E402
from slackkit.outbox import drain                     # noqa: E402
from state.db import connect                          # noqa: E402
from state.specs import specs_awaiting_critique       # noqa: E402

# Identical to reflect_day's, and for the same reasons: this job runs a seat
# (ANTHROPIC_API_KEY) and drains (SLACK_BOT_TOKEN), and build_seat_options
# wires the alpaca MCP server unconditionally for every seat — which
# run_seat_turn then requires to be CONNECTED, even though the narrowed G1
# surface can reach none of its tools. That coupling is issue #108, not a
# property of this seat.
REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FUND_DB",
                "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY")

# Its own lock, NOT reflect's. A shared one would let a G1 turn hanging in SDK
# teardown (the documented residual of run_day._bounded) hold reflect out of
# its own night, and a hung reflect hold G1 out of the next one.
LOCK_NAME = "critic_g1.lock"

SEAT = "critic"
SEAT_CONFIG = ROOT / "agents" / "config" / f"{SEAT}.yaml"

# The Critic's surface for THIS turn only. agents/config/critic.yaml keeps its
# standing ["mcp__fund__*", "mcp__alpaca__*"] — that is what specs/design.md's
# seat table describes and what tests/test_exec_seat_tool_surface.py pins, and
# the seat legitimately needs stock-data for a trade turn. At G1 its charter
# says the opposite ("never at G1 — a spec is judged on its internal
# coherence"), so the narrowing belongs to the TURN. build_seat_options refuses
# any name critic.yaml does not already grant, so this can only subtract.
#
# Exactly the two capabilities SEAT_CAPS["critic"] carries: the two locks agree
# by test, not by comment.
G1_TOOLS = ["mcp__fund__get_spec_brief", "mcp__fund__submit_spec_critique"]

# Byte-identical to evals/prompts.py's "critic" template, and pinned to it by
# test. That file's own drift guard derives its seat list from run_day.SEATS,
# where the Critic deliberately is not — so nothing else would catch a prompt
# this job sends that the eval rig does not evaluate.
#
# It names NO spec. get_spec_brief's own oldest-first selector is what binds a
# turn to a spec; a spec id in the prompt would be a per-run value in prompt
# text, which breaks replay (CLAUDE.md).
G1_PROMPT = ("G1 review turn. Start by calling get_spec_brief, then follow"
             " your charter and end by calling submit_spec_critique exactly"
             " once, for the spec in your brief.")

# DERIVED, not inherited. reflect_day's MAX_TURNS_PER_NIGHT=25 is sized for one
# turn per resolved decision; taking that number here would ask for 25 x 240s =
# 100 minutes and 25 x $0.75 = $18.75 of worst case from the LAST position on a
# unit whose whole budget is 30 minutes, for a fund whose whole expected daily
# spend is under $0.50 — i.e. would guarantee this leg is cut. Three, because:
#   wall clock  3 x run_day.SEAT_MAX_WALL_S (240s) = 12 min, <= 40% of the
#               unit's TimeoutStartSec=30min, which fits behind two arithmetic
#               legs (seconds each) and reflect's realistic ~3 turns
#   cost        3 x critic.yaml max_budget_usd ($0.75) = $2.25 hard backstop;
#               against the measured Critic trial max of $0.1867
#               (evals/seats/critic.yaml) the expectation is <= $0.56/night
#   throughput  state/specs.py fixes the design at ONE turn per spec, and
#               there is no live submit_strategy_spec producer yet, so
#               steady-state arrival is <= 1 spec/night. Three drains any
#               realistic backlog in one night.
# Exceeding it is never silent — see critique_and_log's critic_g1_backlog_capped.
MAX_G1_TURNS_PER_NIGHT = 3

# How many pending specs an alert will count before it says "N+". The canonical
# selector defaults to limit=1 (state/specs.py), so a count needs a limit
# argument rather than a second query carrying its own copy of the predicate —
# a duplicated selector is how the job and the tool come to disagree about what
# "pending" means.
PENDING_REPORT_LIMIT = 50


def log(msg: str) -> None:
    print(f"critic_g1: {msg}", flush=True)


def pending_count(conn) -> int:
    """How many specs still await a verdict, saturating at
    PENDING_REPORT_LIMIT.

    Reported in the blocking and cap alerts. Without it, a blocked head with
    four specs queued behind it produces exactly the same Slack message as a
    blocked head that is the entire queue — and those are different operator
    problems with different urgency."""
    return len(specs_awaiting_critique(conn, limit=PENDING_REPORT_LIMIT))


def _count_text(n: int) -> str:
    """'4' or '50+' — never a number an operator would read as exact when it
    is a saturating count."""
    return f"{n}+" if n >= PENDING_REPORT_LIMIT else str(n)


def next_pending_spec(conn) -> str | None:
    """The spec id at the head of the G1 queue, or None if it is empty.

    Deliberately the SAME selector handle_get_spec_brief uses, with the same
    default limit=1: the seat is shown the head, so the job must re-read the
    head, or the row it checks is not the row the turn reviewed.

    DOES NOT DEGRADE TO None ON ERROR. A read failure raising is what makes it
    distinguishable from an empty queue — the same posture handle_get_spec_brief
    documents at length. _guarded turns the raise into an alert."""
    pending = specs_awaiting_critique(conn)
    return pending[0]["spec_id"] if pending else None


def has_verdict(conn, spec_id: str) -> bool:
    """Did the turn actually write? Success is never inferred from the absence
    of an exception: run_day.make_turn's own run() catches every exception and
    returns normally, so the likeliest real failure — a seat that never calls
    submit_spec_critique, or calls it and gives up on {"ok": false} — would
    raise nothing here either."""
    return conn.execute(
        "SELECT 1 FROM strategy_critiques WHERE spec_id = ?",
        (spec_id,)).fetchone() is not None


def critique_and_log(conn, slack, clock, run_turn) -> dict:
    """One turn per queue head, up to MAX_G1_TURNS_PER_NIGHT, then drain.
    Returns the counts.

    `run_turn` takes {"spec_id": ...}. That id is carried for the post-turn
    re-read and the log line ONLY — it never reaches the prompt.

    THERE IS NO BINDING, and that is a gap, not a design. Unlike reflect's
    expected_decision_id, nothing ties the verdict the seat writes to the spec
    it was shown: handle_submit_spec_critique takes spec_id from the seat's own
    tool arguments and only checks that it is registered and unreviewed. The
    oldest-first selector binds the SHOW, not the WRITE. The has_verdict()
    re-read below is therefore load-bearing — it is what turns "the turn wrote
    a verdict for some other spec" into a counted failure with an alert instead
    of a silent success. Adding the real binding is a fund_server change, out
    of region, escalated.

    The alerts and the drain both run in `finally`, for reflect_day's N1
    reason: a DB error on a LATER iteration must not skip either. Appending
    only after the loop meant such a raise never QUEUED the alert at all — not
    merely left it undrained — so Slack learned nothing. And draining alone
    was not enough: a freshly-appended alert with posted_at IS NULL has no date
    bound on the audit check that catches it, so it would redden every audit
    until the next drain."""
    counts = {"critiqued": 0, "failed": 0}
    stalled: dict | None = None
    capped = False
    remaining = 0
    try:
        for _ in range(MAX_G1_TURNS_PER_NIGHT):
            head = next_pending_spec(conn)
            if head is None:
                break
            try:
                run_turn({"spec_id": head})
            except Exception as exc:
                log(f"spec {head} — turn raised {type(exc).__name__}: {exc};"
                    " no verdict written")
                stalled = {"spec_id": head, "why": "raised",
                           "detail": f"{type(exc).__name__}: {exc}"}
                counts["failed"] += 1
                break
            if has_verdict(conn, head):
                counts["critiqued"] += 1
                continue
            log(f"spec {head} wrote no verdict — the turn returned without"
                " calling submit_spec_critique (or the call was refused, or it"
                " wrote a verdict for a DIFFERENT spec); stopping, since the"
                " next turn would be shown the same spec")
            # Counted HERE, not in `finally`: the head is still pending at this
            # moment, and a read in `finally` could raise on a broken DB and
            # mask the failure it is trying to describe.
            stalled = {"spec_id": head, "why": "wrote_nothing", "detail": "",
                       "pending": pending_count(conn)}
            counts["failed"] += 1
            break
        else:
            capped = next_pending_spec(conn) is not None
            remaining = pending_count(conn) if capped else 0
        log(f"critiqued {counts['critiqued']} · failed {counts['failed']}")
    finally:
        if stalled and stalled["why"] == "raised":
            run_day._alert(conn, clock, "critic_g1_turn_failed",
                           f"critic_g1_turn_failed spec"
                           f" {stalled['spec_id']} — {stalled['detail']};"
                           f" no verdict written, no default row, the spec"
                           f" stays pending for the next night")
        elif stalled:
            run_day._alert(conn, clock, "critic_g1_turn_wrote_nothing",
                           f"critic_g1_turn_wrote_nothing — spec"
                           f" {stalled['spec_id']} got a turn and no verdict;"
                           f" the G1 queue is oldest-first with no skip, so"
                           f" this spec blocks the queue until it clears."
                           f" {_count_text(stalled['pending'])} spec(s) are now"
                           f" pending, including this one. They stay pending"
                           f" for the next night")
        if capped:
            run_day._alert(conn, clock, "critic_g1_backlog_capped",
                           f"critic_g1_backlog_capped — the G1 queue still has"
                           f" {_count_text(remaining)} pending spec(s) after"
                           f" tonight's {MAX_G1_TURNS_PER_NIGHT}-turn cap"
                           f" (MAX_G1_TURNS_PER_NIGHT={MAX_G1_TURNS_PER_NIGHT});"
                           f" the rest stay pending for the next night")
        drain(conn, slack, iso(clock.now()))
    return counts
