"""I4 — every structured submission validates and names a real subject.

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

from state.models import Decision, SpecCritique, Signal

from evals.verdict import FAIL, PASS, Verdict

NAME = "I4"

# Seat -> (submit tool, write table, model, key column). Config, not an
# if-branch, so a new seat is a dict entry rather than a grader edit. Each
# table's columns mirror its model's fields (signals carries `agent`,
# decisions does not), so a row passes through verbatim minus the DB-owned
# `status`.
SUBMISSIONS = {
    "pm": ("mcp__fund__submit_decision", "decisions", Decision, "ticker"),
    "analyst": ("mcp__fund__submit_signal", "signals", Signal, "ticker"),
    "critic": ("mcp__fund__submit_spec_critique", "strategy_critiques",
               SpecCritique, "spec_id"),
}
DB_OWNED = ("status",)
# JSON columns are decoded in evals/runner.py:_rows, so a row reaches here in
# the shape its pydantic model declares. Nothing to unwrap.


def i4_schema(trace, seat, case) -> Verdict:
    tool, table, model, key = SUBMISSIONS[seat.name]
    rows = trace.rows_written.get(table) or []
    called = tool in (trace.tool_names or [])
    # Historical traces predate brief_subjects; for them the two are the same.
    allowed = trace.brief_subjects or trace.brief_tickers

    if not rows and not called:
        return Verdict(NAME, FAIL,
                       f"seat never called {tool} and wrote no {table} rows",
                       tag="silent-seat")

    missing = [s for s in case.subjects
               if not any(r[key] == s for r in rows)]
    if missing:
        return Verdict(
            NAME, FAIL,
            f"called {tool} but no {table} row landed for {missing} —"
            " the handler refused the submission",
            tag="schema-reject")

    for row in rows:
        if row[key] not in allowed:
            return Verdict(NAME, FAIL,
                           f"{row[key]} was never in the brief {allowed}",
                           tag="invented-subject")
        payload = {k: v for k, v in row.items() if k not in DB_OWNED}
        if "run_date" in model.model_fields:
            payload["run_date"] = case.clock.date()
        try:
            model(**payload)
        except (ValidationError, AssertionError, TypeError, ValueError) as exc:
            return Verdict(NAME, FAIL,
                           f"{table} row {row[key]} fails"
                           f" {model.__name__}: {exc}",
                           tag="schema-invalid")
    return Verdict(NAME, PASS, f"{len(rows)} valid row(s)")
