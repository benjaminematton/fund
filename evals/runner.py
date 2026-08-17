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
from evals.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACES = Path(__file__).resolve().parent / "traces"
REQUIRED_SERVERS = {"alpaca", "fund"}          # mirrors scripts/run_day.py:76

# The table each seat WRITES. A PM case seeds `signals` as input, so scoping
# rows_written to the seat's own write table is what keeps fixture input from
# being reported as agent output.
WRITE_TABLES = {"pm": ["decisions"], "analyst": ["signals"]}
ROW_COLUMNS = {
    "decisions": ["ticker", "action", "qty", "thesis", "invalidation",
                  "stop_price", "status"],
    "signals": ["agent", "ticker", "direction", "confidence", "summary"],
}


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _sdk_session(options, prompt, state):
    """The live session. Mirrors scripts/run_day.py:_seat_session exactly."""
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
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} WHERE run_date = ?"
            " ORDER BY ticker", (run_date,)).fetchall()
        if rows:
            out[table] = [dict(zip(cols, tuple(r))) for r in rows]
    return out


def _events(conn, watermark: int) -> list[dict]:
    import json
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

    # Fresh DB + journals per TRIAL, never per case.
    base = Path(workdir) if workdir else Path(DEFAULT_TRACES).parent / "_work"
    trial_dir = base / case.id / f"t{trial}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    db_path, journals_root = trial_dir / "fund.sqlite", trial_dir / "journals"

    state = build_case_state(case, db_path, journals_root)
    options = build_seat_options(cfg, db_path, clock, snapshot=state.snapshot,
                                 journals_root=journals_root)
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
                           iso(case.clock))

    events = _events(state.conn, state.events_watermark)
    snapshot = state.snapshot()
    brief_tickers = sorted(set(case.tickers)
                           | set(snapshot.get("allowed_actions") or {})
                           | {s["ticker"] for s in case.signals})

    trace = Trace(
        case=case.id, trial=trial, seat=seat, git_sha=git_sha(),
        charter_sha=eval_seat.charter_sha, charter_text=eval_seat.charter_text,
        model=eval_seat.model, snapshot=snapshot, brief_tickers=brief_tickers,
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
    trace.write(traces_root or DEFAULT_TRACES)
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
