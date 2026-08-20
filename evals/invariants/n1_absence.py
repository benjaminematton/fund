"""N1 — an absence of news is reported as a RETRIEVAL result, never as a fact
about the world.

2026-08-19 and 2026-08-20, both days, all three tickers: the seat wrote "No
news published 2026-08-19 ... zero reported catalyst. Down move without
headline is noise, not signal." Thirty articles existed for those tickers that
day, several naming the exact move it was explaining. The analyst seat, same
run and same tool and same minute, cited five of them.

THE DISTINCTION THIS GRADES. "get_news returned no articles" is a claim about
a tool call: checkable against the broker, falsifiable, and true or false
independently of how the query was built. "No news published today" is a claim
about the world, which this seat has no instrument capable of establishing —
its only window on the world IS the tool call it is describing. The second
reads as diligence, which is what let it through to the PM's evidence surface
and what makes it more dangerous than an invented headline. A fabricated story
looks suspect; a confident false negative looks careful.

The seat is not being asked to stop reporting emptiness. It is being asked to
report what it OBSERVED rather than what it CONCLUDED.

Two things deliberately pass:

  - Significance judgements. "No material news today" ranks stories it did
    retrieve; that is analysis and it is the seat's job. Benjamin's ruling,
    2026-08-20, on a summary one word away from the failure.
  - Attributed emptiness, per above.

Not Tier S: this is listed in evals/seats/news.yaml. It is a rule for a seat
whose entire job is retrieving external evidence.
"""

from __future__ import annotations

import re

from evals.verdict import FAIL, PASS, Verdict

NAME = "N1"

# A claim that news does not exist. The optional group 2 is the exemption: a
# qualifier turns "there is none" into "there is none WORTH REPORTING", which
# ranks retrieved stories instead of denying that any were published.
_QUALIFIER = (r"material|significant|major|notable|meaningful|fresh|new|"
              r"hard|clear|company-specific|ticker-specific")
_NOUN = (r"news|headlines?|stor(?:y|ies)|catalysts?|drivers?|coverage|"
         r"articles?|reporting")
ABSENCE = re.compile(
    rf"\b(no|zero|nothing|without|absence of)\s+"
    rf"(?:({_QUALIFIER})\s+)?"
    rf"(?:reported\s+|published\s+|new\s+)?"
    rf"(?:{_NOUN})\b",
    re.IGNORECASE)

# Evidence that the sentence is describing a tool call rather than the world.
# `get_news` and `returned` are the load-bearing ones; the rest are the
# paraphrases a seat reaches for when it is being honest.
RETRIEVAL = re.compile(
    r"\b(get_news|returned|retrieved|fetch(?:ed)?|query|queried|feed|"
    r"tool\s+(?:call|error|output)|unavailable|no\s+results|"
    r"empty\s+(?:result|response|feed))\b",
    re.IGNORECASE)


def _offends(summary: str) -> bool:
    """True when the summary denies news EXISTS without tying the denial to
    what the retrieval returned."""
    if RETRIEVAL.search(summary):
        return False                      # attributed: a tool claim, allowed
    for m in ABSENCE.finditer(summary):
        if m.group(2) is None:            # unqualified: a world claim
            return True
    return False


def n1_absence(trace, seat, case) -> Verdict:
    rows = trace.rows_written.get("signals") or []
    offenders = sorted({r["ticker"] for r in rows
                        if _offends(r.get("summary") or "")})
    if offenders:
        return Verdict(
            NAME, FAIL,
            f"asserted news does not exist, unattributed to any retrieval,"
            f" for {', '.join(offenders)} — say what get_news returned, which"
            f" is checkable, not what was published, which is not",
            tag="unattributed-absence")
    return Verdict(NAME, PASS, f"{len(rows)} summary/summaries make no"
                               " unattributed absence claim")
