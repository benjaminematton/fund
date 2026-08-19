"""One live seat turn -> a Trace the eval grader already knows how to read.

Why this exists: `evals/grade.py` is a pure function of a trace and a case, so
a new invariant written next year re-scores every trace ever recorded, for $0.
Production wrote none, which made that property worthless — a trace is not
reconstructable after the fact, and every day without one is a day of the
fund's judgment that can never be reviewed.

Pure by construction: no SDK import, no database, no clock, and no filesystem
(the writer is a separate factory). That is what lets the whole trace path be
tested offline, and it keeps this module importable from anywhere.

`case` and `trial` are the eval rig's provenance fields — a named scenario run
N times. A live turn has neither, so the run date carries a `live-` prefix and
the trial number is a per-day turn sequence. The prefix is deliberate: it keeps
the overload self-documenting, and it makes the live corpus separable with a
prefix test if Trace is ever split into a turn payload plus a provenance
discriminator. That split is deferred until a human has read a real corpus and
knows what they actually query — designing it beforehand is the same error as
writing evaluators before error analysis.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable

from evals.trace import Trace

_ROOT = Path(__file__).resolve().parents[1]


def git_sha() -> str:
    """This checkout's short sha, or 'unknown' if git cannot answer.

    Deliberately NOT evals/runner.py's version, which passes check=True and
    raises. The failure postures differ: an eval baseline whose sha is unknown
    is a baseline that cannot be compared, so raising is right there. A live
    trading day that refuses to record a turn because git is unavailable has
    made the wrong trade — a trace filed under 'unknown' is worth far more
    than no trace, and the day must not notice either way.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=_ROOT, capture_output=True, text=True)
        return out.stdout.strip() or "unknown" if out.returncode == 0 \
            else "unknown"
    except Exception:
        return "unknown"


def build_trace(*, seat: str, run_date: str, turn_seq: int, git_sha: str,
                charter_text: str, model: str, snapshot: dict,
                brief_tickers: list[str], tool_names: list[str],
                result: object | None) -> Trace:
    """Map one completed seat turn onto a Trace.

    `result` is the SDK's ResultMessage, read by attribute and never by
    isinstance — the same discipline agents/runtime.py's cost seam uses, so an
    offline stub exercises the code the live day runs.

    A None result is a turn that produced no ResultMessage: a timeout, or a
    session that died. That is recorded as an errored trace rather than raised.
    The day continues on its defaults (invariant 4), and an errored trace is
    INCONCLUSIVE to every grader rather than a failure manufactured out of API
    weather.

    cost_usd stays None when the SDK did not populate it. A fabricated 0.0
    would make real spend look free — the lie agents/runtime.py refuses to
    tell, and what invariant I5 pairs with the cost_unavailable alert.
    """
    return Trace(
        case=f"live-{run_date}",
        trial=turn_seq,
        seat=seat,
        git_sha=git_sha,
        # Derived, never passed in: computed the same way evals/config.py
        # computes it, from the text carried in this same trace. Two fields
        # that must agree cannot disagree if only one of them is an input.
        charter_sha=hashlib.sha256(charter_text.encode()).hexdigest(),
        charter_text=charter_text,
        model=model,
        snapshot=snapshot,
        brief_tickers=list(brief_tickers),
        tool_names=list(tool_names),
        turns=getattr(result, "num_turns", None),
        cost_usd=getattr(result, "total_cost_usd", None),
        duration_ms=getattr(result, "duration_ms", None),
        is_error=result is None or bool(getattr(result, "is_error", False)),
        error=None if result is not None else "no result message",
    )


def file_sink(root: str) -> Callable[[Trace], None]:
    """A sink writing each trace to <root>/<git_sha>/<case>/<trial>.json —
    exactly where evals.grade.grade_traces globs for them.

    Returned as a closure so the composition root injects a writer while tests
    inject a list's append: the trace path is then exercised offline, with no
    filesystem, by the same code production runs.
    """
    def _write(trace: Trace) -> None:
        trace.write(root)
    return _write
