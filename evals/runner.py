"""run_trial: one seat, one case, one trial -> one Trace.

Seat-parameterised, never `if seat == "pm"`. Scores NOTHING — the runner's
only job is to produce an honest record of what happened. All judgement lives
in grade.py, which reads traces and never runs a turn. That split is what
makes a new invariant re-score every trace ever recorded for $0.

Options ALWAYS come from agents.seats.build_seat_options: the tool surface,
settings isolation and hooks are decided there and pinned by
tests/test_exec_seat_tool_surface.py. The rig must evaluate the seat
production actually runs, so it never assembles ClaudeAgentOptions itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Callable

from agents.runtime import record_turn_result
from agents.seats import build_seat_options, load_seat_config
from orchestrator.clock import SimClock, iso

from evals.cases import Case
from evals.config import PRODUCTION_CONFIG, load_eval_seat
from evals.fixtures import build_case_state
from evals.prompts import stage_prompt
from evals.trace import ROW_COLUMNS, WRITE_TABLES, Trace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACES = Path(__file__).resolve().parent / "traces"
REQUIRED_SERVERS = {"alpaca", "fund"}          # mirrors scripts/run_day.py:76

# WRITE_TABLES / ROW_COLUMNS now live in evals/trace.py, beside the field they
# populate. `evals/live.py` fills the same field for production turns and must
# not import THIS module — it would pull agents.seats, and the SDK with it,
# onto the live trading path. One definition, two writers.
#
# The reason rows are scoped to the seat's own write table is unchanged: a PM
# case seeds `signals` as input, and an unscoped scan would report fixture
# input as agent output.
#
# ROW_SCOPE and JSON_COLUMNS stay HERE because they are the RIG's answers, not
# the live path's. The rig gives every trial a fresh database, which is what
# makes an unscoped select mean "this trial"; a live database accumulates the
# whole day and live.py scopes by `seat` instead. Same tables, different
# question.
#
# How a table is scoped to THIS trial and ordered. The trade pipeline keys on
# run_date; strategy_critiques has no run_date column (a spec is reviewed once,
# not once per day), so its select is unscoped and depends ENTIRELY on the
# trial directory being wiped in run_trial. That was assumed rather than
# enforced once, and a full suite silently re-reported the previous suite's
# verdicts. If you remove the rmtree, this select stops meaning "this trial".
ROW_SCOPE = {"decisions": ("WHERE run_date = ?", "ticker"),
             "signals": ("WHERE run_date = ?", "ticker"),
             "strategy_critiques": ("", "spec_id")}
# Columns stored as JSON text, decoded HERE and only here. Every grader
# downstream then receives the value the pydantic model declares — a grader
# that has to ask "string or list?" is a grader carrying storage detail it has
# no business knowing, and the answer drifts per grader.
JSON_COLUMNS = frozenset({"objections"})


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _sdk_session(options, prompt, state):
    """The live session. Same client and same run_seat_turn call as
    scripts/run_day.py:_seat_session, with ONE deliberate difference: no
    wall-clock bound. run_day wraps its session in _bounded(SEAT_MAX_WALL_S)
    because a hung turn there hangs an unattended trading day while holding the
    flock, so the next day's timer exits 0 and the stall reads as a closed
    market (#44). This harness is manual: a hang here is in front of an
    operator, and Ctrl-C is the bound."""
    import asyncio

    from claude_agent_sdk import ClaudeSDKClient

    from agents.exec_turn import run_seat_turn

    async def go():
        async with ClaudeSDKClient(options=options) as client:
            return await run_seat_turn(client, prompt, REQUIRED_SERVERS)

    return asyncio.run(go())


def _rows(conn, seat: str, run_date: str) -> dict:
    out = {}
    for table in WRITE_TABLES[seat]:
        cols = ROW_COLUMNS[table]
        where, order = ROW_SCOPE[table]
        params = (run_date,) if where else ()
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} {where}"
            f" ORDER BY {order}", params).fetchall()
        if rows:
            out[table] = [{c: json.loads(v) if c in JSON_COLUMNS else v
                           for c, v in zip(cols, tuple(r))} for r in rows]
    return out


def _events(conn, watermark: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, kind, payload FROM events WHERE id > ? ORDER BY id",
        (watermark,)).fetchall()
    return [{"id": r["id"], "kind": r["kind"],
             "payload": json.loads(r["payload"])} for r in rows]


def run_trial(seat: str, case: Case, trial: int, *,
              mcp_servers: dict | None = None,
              session: Callable | None = None,
              workdir: Path | str | None = None,
              traces_root: Path | str | None = None) -> Trace:
    """One trial. `session` is the LLM seam — the real SDK by default, an
    offline callable in rig tests. `mcp_servers` is in the signature from day
    one because every seat after the PM draws evidence from the network."""
    if mcp_servers is not None:
        raise NotImplementedError(
            "run_trial(mcp_servers=...) has nowhere to go yet: agents/seats.py:49"
            " still hardcodes the server map. Wire it cfg-driven in Step 6"
            " (PLAN §6) before passing this. Refusing rather than silently"
            " dropping the override.")
    if case.seat != seat:
        raise ValueError(
            f"case {case.id!r} declares seat {case.seat!r} but was run as"
            f" {seat!r} — a case graded against the wrong seat's invariant set"
            " is a false result, not a warning")

    eval_seat = load_eval_seat(seat)
    cfg = load_seat_config(PRODUCTION_CONFIG / f"{seat}.yaml")
    clock = SimClock(case.clock)

    # Fresh DB + journals per TRIAL, never per case — and fresh per RUN, which
    # `exist_ok=True` alone did not deliver. The default workdir is a fixed
    # path, so a second suite over the same cases reopened the first suite's
    # database: an unchanged case still carried its earlier critique row, the
    # write-once guard refused the new submission, and `_rows` reported the OLD
    # verdict as this trial's result. A whole run graded the previous run.
    # Nothing caught it because every test and the dry run pass their own
    # tmp_path; only the real suite uses this path.
    #
    # THIS IS THE BLUNT FIX AND IT IS NOT THE INTENDED ONE. Wiping the
    # directory makes `_rows`' unscoped SELECT correct by enforcing a
    # precondition, so the correctness of a reported run still depends on a
    # side effect three lines above it. The agreed replacement removes the
    # dependency instead:
    #
    #   1. Take a watermark before the turn, the way `state.events_watermark`
    #      is already taken for events — for `strategy_critiques` that is
    #      `SELECT MAX(rowid)` (no autoincrement id column; `spec_id` is the
    #      PK), captured in build_case_state alongside events_watermark.
    #   2. `_rows` filters `WHERE rowid > ?` for the unscoped tables, which
    #      makes "this trial's rows" true by construction rather than by
    #      directory hygiene, and lets ROW_SCOPE stop carrying the caveat.
    #   3. The rmtree can then go, and reusing a workdir becomes harmless
    #      rather than silently wrong.
    #
    # Recorded here rather than in a scratchpad because a pickup that depends
    # on a design living only in a session's context loses it when that
    # session ends.
    base = Path(workdir) if workdir else Path(DEFAULT_TRACES).parent / "_work"
    trial_dir = base / case.id / f"t{trial}"
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    trial_dir.mkdir(parents=True)
    db_path, journals_root = trial_dir / "fund.sqlite", trial_dir / "journals"

    state = build_case_state(case, db_path, journals_root)
    # The rig is a composition root, so it binds the turn exactly as
    # scripts/critic_g1.py does (strategy-contracts.md §3.4). A spec-shaped
    # case's subject IS the id build_case_state registered, but only because
    # `Case.subjects` hashes the same dict state/specs.py does — the coerced
    # `StrategySpec.model_dump()`, not the raw YAML mapping. That is a real
    # second derivation, not an absence of one: it hashed the mapping until
    # 2026-08-29, and a case spelling `capacity_usd: 4000000` rather than
    # `4000000.0` bound an id nothing had registered. Pinned in
    # tests/test_evals_rig.py; if either side changes what it hashes, both
    # change or the pin reddens. Unbound (a ticker-shaped case) stays None:
    # those seats have no submit_spec_critique cap and the binding is inert
    # for them.
    #
    # Leaving this unbound is not a degraded posture, it is a silent zero:
    # the seat's submit_spec_critique hits the None refusal, no
    # strategy_critiques row is written, and every critic case grades as a
    # seat that produced nothing.
    bound_spec = case.subjects[0] if case.spec is not None else None
    options = build_seat_options(cfg, db_path, clock, snapshot=state.snapshot,
                                 journals_root=journals_root,
                                 expected_spec_id=bound_spec)
    prompt = stage_prompt(seat, case.tickers)

    tool_names, result, err = [], None, None
    try:
        tool_names, result = (session or _sdk_session)(options, prompt, state)
    except Exception as exc:      # invariant 4 in eval clothing: a blown turn
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        # is a recorded INCONCLUSIVE trial, never a suite that loses 17 others

    # Cost accounting exactly where scripts/run_day.py:240 does it: after the
    # turn, before anything else reads the DB. A turn whose ResultMessage
    # carried no total_cost_usd leaves a `cost_unavailable` alert instead of a
    # cost row — the alert I5 grades on. Without this call a missing estimate
    # would produce no alert and I5 would fail the SEAT for the RIG's omission.
    if err is None:
        record_turn_result(state.conn, state.run_date, seat, result,
                           iso(case.clock),
                           configured_model=cfg.get("model", ""))

    events = _events(state.conn, state.events_watermark)
    snapshot = state.snapshot()
    brief_tickers = sorted(set(case.tickers)
                           | set(snapshot.get("allowed_actions") or {})
                           | {s["ticker"] for s in case.signals})
    # Ticker-shaped cases keep their historical brief_tickers meaning; a
    # spec-shaped case has no tickers at all, so subjects is what I4 grades
    # an invented row against.
    brief_subjects = brief_tickers or list(case.subjects)

    trace = Trace(
        case=case.id, trial=trial, seat=seat, git_sha=git_sha(),
        charter_sha=eval_seat.charter_sha, charter_text=eval_seat.charter_text,
        model=eval_seat.model, snapshot=snapshot, brief_tickers=brief_tickers,
        brief_subjects=brief_subjects,
        tool_names=list(tool_names),
        rows_written=_rows(state.conn, seat, state.run_date),
        events=events, alerts=[e for e in events if e["kind"] == "alert"],
        permission_denials=list(getattr(result, "permission_denials", None)
                                or []),
        turns=getattr(result, "num_turns", None),
        cost_usd=_cost(result),
        duration_ms=getattr(result, "duration_ms", None),
        is_error=bool(err) or bool(getattr(result, "is_error", False)),
        error=err)
    state.conn.close()
    # A workdir scopes the WHOLE trial, traces included. Without this the rig's
    # own unit tests (which pass workdir= but not traces_root=) deposit junk
    # traces into evals/traces/, where make eval-report grades them as a real
    # suite run and git offers to commit them.
    trace.write(traces_root or (base / "traces" if workdir else DEFAULT_TRACES))
    return trace


def _cost(result) -> float | None:
    """None, never 0.0, when the SDK did not populate total_cost_usd — the
    same distinction agents/runtime.py:243 refuses to blur."""
    usd = getattr(result, "total_cost_usd", None)
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return None
    if usd != usd or usd in (float("inf"), float("-inf")):
        return None
    return float(usd)
