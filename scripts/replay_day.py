#!/usr/bin/env python3
"""Replay one recording's LLM decisions against current code — the CLI behind
`make replay REC=<recording.jsonl>` (CLAUDE.md; specs/design.md, Modes).

    .venv/bin/python3 scripts/replay_day.py tests/recordings/mvf_pm_hold.jsonl

The day is `make sim-day`'s own composition, tests/test_sim_day.py's
sim_day(): injected clock, FakeSlack, FakeAlpaca, a temp DB, and the REAL fund
tools, PreToolUse order gate, PostToolUse order recorder, risk math, ticket
store, stage machine and fill-poll. Not a second harness — the one the six
simulated day shapes already run on every `make test`.

The recording is the only input. Its lines ({"seat","tool","args"}, the shape
agents/runtime.py's make_decision_recorder writes) are partitioned by seat and
each seat's lines become that seat's turn: analyst and news -> research, pm ->
decision, exec -> execution. A seat the recording does not carry runs the
golden day's default recording for that seat, so a single seat-turn file from
tests/recordings/ replays as a whole day. The market is fixtures/golden-day.md's
book, exactly as tests/test_sim_day.golden_day. Any other seat is refused: a
Critic or Quant turn is a nightly job, not a stage of the trading day.

Offline, no keys, no LLM: nothing on this path constructs an SDK client, and
tests/test_replay_day.py pins that with every constructor patched to raise.
This cannot CREATE a recording — record mode is make_decision_recorder, which
nothing in production wires (#163).

Prints the stages reached, turn and row counts, every gate denial and alert,
the day's own digest, then scripts/audit_day.py's verdict.

Exit codes:
  0  the replayed day audits clean
  1  it does not — the findings are printed
  2  usage: no recording, an unreadable one, or a seat with no stage
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))                              # from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling audit_day

import audit_day                                           # noqa: E402
from agents.replay import load_recording                   # noqa: E402
from tests.test_sim_day import _nvda, sim_day              # noqa: E402

# seat -> the sim_day() keyword holding that seat's recording files.
SLOTS = {"analyst": "analyst_recs", "news": "news_recs", "pm": "pm_recs",
         "exec": "exec_recs"}


def _slots(rec: Path, tmp: Path) -> dict[str, tuple[Path]]:
    """One temp JSONL per seat, in the recording's own order, keyed by the
    sim_day keyword. Absolute paths: sim_day joins each file onto
    tests/recordings/, and pathlib keeps an absolute right-hand side."""
    by_seat: dict[str, list[dict]] = {}
    for d in load_recording(rec):
        by_seat.setdefault(d["seat"], []).append(d)
    unknown = sorted(set(by_seat) - set(SLOTS))
    if unknown:
        raise ValueError(f"seat(s) {', '.join(unknown)} have no stage in a"
                         f" simulated trading day; replayable seats are"
                         f" {', '.join(SLOTS)}")
    slots = {}
    for seat, lines in by_seat.items():
        path = tmp / f"{seat}.jsonl"
        path.write_text("".join(json.dumps(d) + "\n" for d in lines))
        slots[SLOTS[seat]] = (path,)
    return slots


def _summary(sim, rec: Path, seats: list[str]) -> list[str]:
    conn = sim.conn
    stages = dict(conn.execute(
        "SELECT stage, status FROM checkpoints WHERE run_date = ?",
        (sim.run_date,)).fetchall())
    counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
              for t in ("signals", "decisions", "tickets", "orders")}
    denied = [o for outs in sim.outcomes.values() for o in outs if "denied" in o]
    alerts = [json.loads(r["payload"])["text"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]
    digest = [json.loads(r["payload"])["text"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'digest' ORDER BY id")]

    lines = [f"replay: {rec} (seats: {', '.join(seats)}; run_date {sim.run_date})",
             "turns:  " + " ".join(f"{k}={v}" for k, v in sim.turns.items()),
             "stages: " + " ".join(f"{s}={stages.get(s, 'missing')}"
                                   for s in audit_day.STAGES),
             "rows:   " + " ".join(f"{k}={v}" for k, v in counts.items()),
             f"denied: {len(denied)}"]
    lines += [f"  {o['tool']}: {o['denied']}" for o in denied]
    lines.append(f"alerts: {len(alerts)}" if alerts else "alerts: none")
    lines += [f"  {t}" for t in alerts]
    lines.append("digest:" if digest else "digest: none")
    lines += ["  " + line for text in digest for line in text.splitlines()]
    return lines


def replay(rec: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        slots = _slots(rec, tmp)
        seats = [s for s, k in SLOTS.items() if k in slots]
        sim = sim_day(tmp, market={"NVDA": _nvda()}, **slots)
        print("\n".join(_summary(sim, rec, seats)))
        sim.conn.close()
        problems = audit_day.audit(str(tmp / "fund.sqlite"), sim.run_date)
    print("\n".join(problems) or f"AUDIT CLEAN {sim.run_date}")
    return 1 if problems else 0


def main(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: replay_day.py <recording.jsonl>", file=sys.stderr)
        return 2
    rec = Path(args[0])
    try:
        return replay(rec)
    except (OSError, ValueError, KeyError) as e:
        print(f"replay_day.py: {rec}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
