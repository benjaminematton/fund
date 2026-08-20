"""Case-specific expectations — the thin layer on top of the invariant grid.

Deliberately small and declarative: a handful of keys per seat, no expression
language. An expectation you cannot read at a glance is one you cannot trust
when it reddens.

A case with no expectations is INCONCLUSIVE, never a free pass — a case that
can only pass is documentation, not a test.
"""

from __future__ import annotations

from evals.verdict import FAIL, INCONCLUSIVE, PASS, Verdict

NAME = "EXPECT"

# Seat -> (write table, key column). Same shape as I4's SUBMISSIONS, and for
# the same reason: a new seat is a dict entry, not a new branch.
ROWS = {"pm": ("decisions", "ticker"),
        "analyst": ("signals", "ticker"),
        "critic": ("strategy_critiques", "spec_id")}


def _rows(trace, seat_name: str = "pm") -> dict:
    table, key = ROWS[seat_name]
    return {r[key]: r for r in (trace.rows_written.get(table) or [])}


def case_expectations(trace, seat, case) -> Verdict:
    if not case.expect:
        return Verdict(NAME, INCONCLUSIVE,
                       f"case {case.id} declares no expectation", tag="none")
    if case.seat == "critic":
        return _critic_expectations(trace, seat, case)
    return _decision_expectations(trace, seat, case)


def _critic_expectations(trace, seat, case) -> Verdict:
    """Two keys. `verdict` is the ground truth. `objection_mentions` is the
    guard against the failure this whole case set exists to detect: a Critic
    that returns `objections` on a misaligned spec while naming a defect that
    is not the misalignment has not caught anything — it has guessed, and a
    gate built on guessing blocks arbitrary specs."""
    rows = _rows(trace, seat.name)
    checked = 0
    for subject in case.subjects:
        row = rows.get(subject)
        if row is None:
            return Verdict(NAME, FAIL, f"no critique row for {subject}",
                           tag="missing-row")
        want = case.expect["verdict"]
        if row["verdict"] != want:
            return Verdict(NAME, FAIL,
                           f"{subject}: verdict {row['verdict']!r}, expected"
                           f" {want!r}",
                           tag="wrong-verdict")
        checked += 1
        mentions = [m.lower() for m in
                    (case.expect.get("objection_mentions") or [])]
        if not mentions:
            continue
        objections = row["objections"] or []
        text = " ".join(objections).lower()
        if not any(m in text for m in mentions):
            return Verdict(NAME, FAIL,
                           f"{subject}: objected, but none of {mentions} is"
                           f" named — right verdict, wrong reason:"
                           f" {objections}",
                           tag="wrong-reason")
    return Verdict(NAME, PASS, f"{checked} expectation(s) met")


def _decision_expectations(trace, seat, case) -> Verdict:
    rows = _rows(trace, "pm")
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
