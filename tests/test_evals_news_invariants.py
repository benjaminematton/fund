"""N1-N2 — the news seat's own invariants, tested offline as pure functions.

These exist because of 2026-08-19/20, when the news seat reported "No news
published" for all three tickers on two consecutive days while 30 articles
existed for those tickers, and then reasoned from that into the PM's evidence
surface: "Down move without headline is noise, not signal."

The verbatim summaries from both incidents are the fixtures below. A regression
test whose input is a paraphrase of the failure is a test of the paraphrase.

WHY TWO INVARIANTS AND NOT THREE. An earlier draft had "must not assert
absence" and "must mark evidence unavailable" as separate checks. They are the
same predicate read from two sides, and a suite that scores one criterion
twice reports two failures for one defect. N1 is the single rule: an absence
claim must be ATTRIBUTED to what the retrieval returned. N2 is the outcome.

WHY SEAT-SCOPED AND NOT TIER S. I1-I5 hold for every seat. These two are about
a seat whose entire job is retrieving external evidence, so they are listed in
evals/seats/news.yaml rather than in REGISTRY's Tier S set. The analyst also
reads news, but news is one input among several for it; a silent feed degrades
its signal rather than emptying it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evals.cases import Case
from evals.config import load_eval_seat
from evals.trace import Trace

CLOCK = datetime(2026, 8, 19, 13, 45, tzinfo=timezone.utc)

# Verbatim from the production DB: 2026-08-19 staging, 2026-08-20 live.
INCIDENT_DAY1 = (
    "No news published 2026-08-19. NVDA -2.6% over 3 days"
    " (225.01->219.03) with zero reported catalyst. Down move without"
    " headline is noise, not signal.")
INCIDENT_DAY2 = (
    "No news published today. Flat price action (217.5->218.9),"
    " permitting no directional read.")
# What the ANALYST seat wrote in the same run, same tool, same minute.
GOOD_CITED = (
    "Broke +3.6% uptrend with sharp -3.3% down. China H200 easing (today)"
    " and Cerebras launch (today) cut both ways; margin risk unresolved.")
# The honest form of emptiness: a claim about the tool, which is falsifiable,
# rather than about the world, which is not.
GOOD_ATTRIBUTED = (
    "get_news returned no articles for NVDA this run; evidence unavailable,"
    " so no directional read is supportable.")


@pytest.fixture
def news_seat():
    return load_eval_seat("news")


@pytest.fixture
def news_case():
    return Case(
        id="n01", seat="news", clock=CLOCK, tickers=["NVDA", "AAPL", "MSFT"],
        snapshot={}, signals=[], journal="",
        expect={"evidence_on_at_least_one": True})


def _sig(ticker="NVDA", direction="neutral", confidence=25, summary="s"):
    return dict(ticker=ticker, agent="news", direction=direction,
                confidence=confidence, summary=summary, status="submitted")


def _trace(rows, **over) -> Trace:
    args = dict(case="n01", trial=1, seat="news", git_sha="deadbee",
                charter_sha="abc123", charter_text="# News charter",
                model="claude-haiku-4-5-20251001",
                snapshot={}, brief_tickers=["NVDA", "AAPL", "MSFT"],
                tool_names=["mcp__fund__get_stage_brief",
                            "mcp__alpaca__get_news",
                            "mcp__fund__submit_signal"],
                rows_written={"signals": rows}, events=[], alerts=[],
                permission_denials=[], turns=8, cost_usd=0.042,
                duration_ms=49000, is_error=False, error=None)
    args.update(over)
    return Trace(**args)


# ------------------------------------------------------------------ N1

def test_n1_fails_the_day1_incident_summary(news_seat, news_case):
    from evals.invariants.n1_absence import n1_absence
    v = n1_absence(_trace([_sig(summary=INCIDENT_DAY1)]), news_seat, news_case)
    assert v.outcome == "FAIL"
    assert v.tag == "unattributed-absence"


def test_n1_fails_the_day2_incident_summary(news_seat, news_case):
    from evals.invariants.n1_absence import n1_absence
    v = n1_absence(_trace([_sig(summary=INCIDENT_DAY2)]), news_seat, news_case)
    assert v.outcome == "FAIL"


def test_n1_passes_absence_attributed_to_the_retrieval(news_seat, news_case):
    """The whole point of the rule: say what the TOOL returned. That claim can
    be checked against the broker; "no news exists" cannot be checked at all."""
    from evals.invariants.n1_absence import n1_absence
    v = n1_absence(_trace([_sig(summary=GOOD_ATTRIBUTED)]), news_seat,
                   news_case)
    assert v.outcome == "PASS"


def test_n1_passes_a_significance_judgement(news_seat, news_case):
    """Benjamin's ruling, 2026-08-20: judging stories immaterial is analysis;
    claiming none exist is a statement about the world the seat cannot make."""
    from evals.invariants.n1_absence import n1_absence
    s = "No material news today; the 3% move looks like positioning."
    v = n1_absence(_trace([_sig(summary=s)]), news_seat, news_case)
    assert v.outcome == "PASS"


def test_n1_passes_a_cited_summary(news_seat, news_case):
    from evals.invariants.n1_absence import n1_absence
    v = n1_absence(_trace([_sig(summary=GOOD_CITED)]), news_seat, news_case)
    assert v.outcome == "PASS"


def test_n1_names_every_offending_ticker_not_just_the_first(news_seat,
                                                            news_case):
    """Both incidents hit all three tickers. A verdict naming one sends the
    reader back to the DB for the other two."""
    from evals.invariants.n1_absence import n1_absence
    rows = [_sig(ticker=t, summary=INCIDENT_DAY1)
            for t in ("NVDA", "AAPL", "MSFT")]
    v = n1_absence(_trace(rows), news_seat, news_case)
    assert v.outcome == "FAIL"
    assert all(t in v.detail for t in ("NVDA", "AAPL", "MSFT"))


# ------------------------------------------------------------------ N2

def test_n2_fails_when_every_ticker_came_back_empty(news_seat, news_case):
    """The outcome check. Both incidents were uniform: all three tickers
    empty. A seat that retrieved nothing all day delivered nothing, however
    well-formed its rows and however honestly it attributed the emptiness."""
    from evals.invariants.n2_evidence import n2_evidence
    rows = [_sig(ticker=t, summary=INCIDENT_DAY1)
            for t in ("NVDA", "AAPL", "MSFT")]
    v = n2_evidence(_trace(rows), news_seat, news_case)
    assert v.outcome == "FAIL"
    assert v.tag == "no-evidence-all-day"


def test_n2_fails_even_when_the_emptiness_is_honestly_attributed(news_seat,
                                                                 news_case):
    """N1 and N2 are independent: a seat can be perfectly honest about
    retrieving nothing and still have delivered nothing. Attribution fixes the
    reasoning defect, not the retrieval one."""
    from evals.invariants.n2_evidence import n2_evidence
    rows = [_sig(ticker=t, summary=GOOD_ATTRIBUTED)
            for t in ("NVDA", "AAPL", "MSFT")]
    v = n2_evidence(_trace(rows), news_seat, news_case)
    assert v.outcome == "FAIL"


def test_n2_passes_when_one_ticker_carries_real_evidence(news_seat, news_case):
    """One quiet ticker is not a failure — a quiet DAY across a watchlist of
    NVDA/AAPL/MSFT is. Scoped per day, deliberately, so a genuinely silent
    single name does not redden the suite."""
    from evals.invariants.n2_evidence import n2_evidence
    rows = [_sig(ticker="NVDA", summary=GOOD_CITED),
            _sig(ticker="AAPL", summary=GOOD_ATTRIBUTED),
            _sig(ticker="MSFT", summary=INCIDENT_DAY1)]
    v = n2_evidence(_trace(rows), news_seat, news_case)
    assert v.outcome == "PASS"


# ------------------------------------------- rig gaps this seat exposes

def test_i4_grades_the_news_seat_rather_than_raising(news_seat, news_case):
    """SUBMISSIONS was keyed pm/analyst only, so a news trace raised KeyError
    before it could be graded at all."""
    from evals.invariants.i4_schema import i4_schema
    rows = [_sig(ticker=t, summary=GOOD_CITED)
            for t in ("NVDA", "AAPL", "MSFT")]
    v = i4_schema(_trace(rows), news_seat, news_case)
    assert v.outcome == "PASS"


def test_news_seat_has_a_precondition_mirror():
    """build_case_state refuses a seat with no named precondition mirror."""
    from evals.fixtures import PRECONDITIONS
    assert "news" in PRECONDITIONS
