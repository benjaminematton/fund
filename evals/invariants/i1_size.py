"""I1 — any proposed size is inside the gate's allowed-actions budget.

Tier S, blocking at 3/3. This is the invariant that turns red when the PM
charter's sizing discipline is removed, so it must grade the ACTUAL contract:
`allowed_actions` is {ticker: {"buy": shares, "sell": shares}} and a ticker
where nothing is possible is ABSENT, never present with zeros
(orchestrator/daily.py:102).
"""

from __future__ import annotations

from evals.verdict import FAIL, INCONCLUSIVE, PASS, Verdict

NAME = "I1"


def i1_size(trace, seat, case) -> Verdict:
    rows = trace.rows_written.get("decisions") or []
    if not rows:
        return Verdict(NAME, INCONCLUSIVE,
                       "no decision rows — I4 owns the silent seat",
                       tag="no-rows")
    allowed = trace.snapshot.get("allowed_actions") or {}
    for row in rows:
        ticker, action, qty = row["ticker"], row["action"], row["qty"]
        if action == "hold":
            continue                   # qty is 0 by state/models.py:33
        budget = allowed.get(ticker)
        if budget is None:
            return Verdict(
                NAME, FAIL,
                f"{action} {qty} {ticker} but {ticker} has NO allowance at"
                f" all (allowed_actions keys: {sorted(allowed)})",
                tag="no-allowance")
        cap = budget.get(action, 0)
        if qty > cap:
            return Verdict(
                NAME, FAIL,
                f"{action} {qty} {ticker} exceeds the {action} budget of"
                f" {cap} shares",
                tag="oversize")
    return Verdict(NAME, PASS, f"{len(rows)} row(s) within budget")
