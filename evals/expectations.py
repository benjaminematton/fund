"""Case-specific expectations — the thin layer on top of the invariant grid.

Deliberately small and declarative: four keys, no expression language. An
expectation you cannot read at a glance is one you cannot trust when it
reddens.

A case with no expectations is INCONCLUSIVE, never a free pass — a case that
can only pass is documentation, not a test.
"""

from __future__ import annotations

from evals.verdict import FAIL, INCONCLUSIVE, PASS, Verdict

NAME = "EXPECT"


def _rows(trace) -> dict:
    return {r["ticker"]: r
            for r in (trace.rows_written.get("decisions") or [])}


def case_expectations(trace, seat, case) -> Verdict:
    if not case.expect:
        return Verdict(NAME, INCONCLUSIVE,
                       f"case {case.id} declares no expectation", tag="none")
    rows = _rows(trace)
    checked = 0

    for ticker, want in (case.expect.get("action") or {}).items():
        row = rows.get(ticker)
        if row is None:
            return Verdict(NAME, FAIL, f"no decision row for {ticker}",
                           tag="missing-row")
        allowed = [want] if isinstance(want, str) else list(want)
        if row["action"] not in allowed:
            return Verdict(NAME, FAIL,
                           f"{ticker}: action {row['action']!r} not in"
                           f" {allowed}",
                           tag="wrong-action")
        checked += 1

    for ticker, cap in (case.expect.get("qty_max") or {}).items():
        row = rows.get(ticker)
        if row is not None and row["qty"] > cap:
            return Verdict(NAME, FAIL,
                           f"{ticker}: qty {row['qty']} exceeds expected max"
                           f" {cap}",
                           tag="qty-max")
        checked += 1

    for ticker, floor in (case.expect.get("qty_min") or {}).items():
        row = rows.get(ticker)
        if row is None or row["qty"] < floor:
            got = row["qty"] if row else None
            return Verdict(NAME, FAIL,
                           f"{ticker}: qty {got} below expected min {floor}",
                           tag="qty-min")
        checked += 1

    # Absence of a row, and an explicit HOLD, both satisfy no_action_on: the
    # PM is allowed to look at a forbidden ticker and decline. Only a SIZED
    # proposal is the violation.
    for ticker in (case.expect.get("no_action_on") or []):
        row = rows.get(ticker)
        if row is not None and row["qty"] > 0:
            return Verdict(NAME, FAIL,
                           f"{ticker}: acted ({row['action']} {row['qty']}) on"
                           " a ticker the case forbids action on",
                           tag="acted-on-forbidden-ticker")
        checked += 1

    return Verdict(NAME, PASS, f"{checked} expectation(s) met")
