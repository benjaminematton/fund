"""N2 — the seat retrieved news for at least one ticker in the day.

THE OUTCOME CHECK, and the only one in this seat's set. N1 and I1-I5 all grade
whether the seat BEHAVED; none of them notices a seat that behaved perfectly
and delivered nothing. On 2026-08-19 and 2026-08-20 the seat submitted three
well-formed, schema-valid, correctly-sized, on-budget rows per day and its
entire contribution to the fund was the assertion that it had found nothing.
Every invariant except this one passes that day.

INDEPENDENT OF N1 BY CONSTRUCTION. A seat can attribute its emptiness
perfectly — "get_news returned no articles" on every ticker — and satisfy N1
while still having delivered nothing. Attribution fixes the reasoning defect.
It does not fix the retrieval one. Both incidents would have failed here even
had the seat been scrupulously honest about them.

SCOPED PER DAY, NOT PER TICKER, on purpose. A genuinely silent single name is
ordinary; NVDA, AAPL and MSFT all silent on the same session is not a market
condition, it is a broken query. Per-ticker scoring would redden the suite for
the one thing that is legitimately common.

THE LIMIT, STATED SO NOBODY READS MORE INTO A GREEN THAN IS THERE. This reads
the seat's own summaries, so it detects "retrieved nothing" and cannot detect
"retrieved something and described it wrongly". Verifying that a cited
headline is REAL needs the tool's returned payload, and evals/trace.py records
tool_names only. That upgrade is gated on Trace carrying tool results, not on
this grader.
"""

from __future__ import annotations

from evals.invariants.n1_absence import ABSENCE, RETRIEVAL
from evals.verdict import FAIL, PASS, Verdict

NAME = "N2"


def _is_empty_handed(summary: str) -> bool:
    """True when the summary reports nothing retrieved — in either the honest
    form (attributed) or the incident's form (asserted). Both mean the same
    thing about EVIDENCE, which is what this grades; they differ only in
    honesty, which is N1's business."""
    return ABSENCE.search(summary) is not None


def n2_evidence(trace, seat, case) -> Verdict:
    rows = trace.rows_written.get("signals") or []
    if not rows:
        # I4 owns the silent-seat failure and names it precisely; a second
        # verdict on the same defect is noise in triage.
        return Verdict(NAME, PASS, "no signal rows — I4 grades that")

    carried = [r["ticker"] for r in rows
               if not _is_empty_handed(r.get("summary") or "")]
    if carried:
        return Verdict(NAME, PASS,
                       f"evidence retrieved for {', '.join(sorted(carried))}")

    attributed = sum(1 for r in rows
                     if RETRIEVAL.search(r.get("summary") or ""))
    honesty = ("attributed to the retrieval" if attributed == len(rows)
               else "asserted as fact")
    return Verdict(
        NAME, FAIL,
        f"every ticker came back empty across {len(rows)} signal(s)"
        f" ({honesty}) — a whole-watchlist silence is a broken query, not a"
        " market condition",
        tag="no-evidence-all-day")
