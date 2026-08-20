"""I4 — every structured submission validates and names a real ticker.

Validated against state.models, the CANONICAL pydantic contract, never a copy
declared here: a second schema would drift and the drift would read as an
agent regression.

The two failure tags are load-bearing. `silent-seat` (never submitted) and
`schema-reject` (submitted and was refused) both resolve to default hold/0 +
alert in production, so the DB end state cannot tell them apart. Tagging them
here is what keeps triage on a red case from starting with a transcript read.

Tier S, blocking at 3/3.
"""

from __future__ import annotations

from pydantic import ValidationError

from state.models import Decision, Signal

from evals.verdict import FAIL, PASS, Verdict

NAME = "I4"

# Seat -> (submit tool, write table, model). Config, not an if-branch, so a
# new seat is a dict entry rather than a grader edit. Each table's columns
# mirror its model's fields (signals carries `agent`, decisions does not), so
# a row passes through verbatim minus the DB-owned `status`.
SUBMISSIONS = {
    "pm": ("mcp__fund__submit_decision", "decisions", Decision),
    "analyst": ("mcp__fund__submit_signal", "signals", Signal),
    # Same contract as the analyst: both are research seats writing Signal
    # rows through submit_signal. Absent this entry a news trace raised
    # KeyError inside the grader, which grade_trace catches as INCONCLUSIVE —
    # so the seat was not merely ungraded, it was ungraded in the one outcome
    # a reader skims past.
    "news": ("mcp__fund__submit_signal", "signals", Signal),
}
DB_OWNED = ("status",)


def i4_schema(trace, seat, case) -> Verdict:
    tool, table, model = SUBMISSIONS[seat.name]
    rows = trace.rows_written.get(table) or []
    called = tool in (trace.tool_names or [])

    if not rows and not called:
        return Verdict(NAME, FAIL,
                       f"seat never called {tool} and wrote no {table} rows",
                       tag="silent-seat")

    missing = [t for t in case.tickers
               if not any(r["ticker"] == t for r in rows)]
    if missing:
        return Verdict(
            NAME, FAIL,
            f"called {tool} but no {table} row landed for {missing} —"
            " the handler refused the submission",
            tag="schema-reject")

    for row in rows:
        if row["ticker"] not in trace.brief_tickers:
            return Verdict(NAME, FAIL,
                           f"{row['ticker']} was never in the brief"
                           f" {trace.brief_tickers}",
                           tag="invented-ticker")
        payload = {k: v for k, v in row.items() if k not in DB_OWNED}
        try:
            model(run_date=case.clock.date(), **payload)
        except (ValidationError, AssertionError, TypeError) as exc:
            return Verdict(NAME, FAIL,
                           f"{table} row {row['ticker']} fails"
                           f" {model.__name__}: {exc}",
                           tag="schema-invalid")
    return Verdict(NAME, PASS, f"{len(rows)} valid row(s)")
