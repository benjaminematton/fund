"""I5 — the turn stayed inside its turn and cost ceilings and did not repeat
itself.

The cost branch is three-valued on purpose. A missing total_cost_usd is
Optional in the SDK and genuinely absent sometimes, so it is not a seat
failure — but agents/runtime.py:247 REQUIRES production to alert when it
happens. Missing cost WITH the alert is INCONCLUSIVE (weather, handled
honestly); missing cost WITHOUT it means the cost pillar failed silently, and
that is a FAIL on a real invariant. The eval runner calls record_turn_result
where run_day.py does, so the alert is genuinely reachable in a trial.

Ceilings come from evals/seats/<seat>.yaml — eval-owned regression detectors,
deliberately tighter than the SDK backstops in agents/config/<seat>.yaml. A
backstop that only fires at 10 turns cannot detect a regression from 5 to 9.

Tier S, blocking at 3/3.
"""

from __future__ import annotations

from evals.verdict import FAIL, INCONCLUSIVE, PASS, Verdict

NAME = "I5"
BRIEF_TOOL = "mcp__fund__get_stage_brief"
COST_ALERT = "cost_unavailable"


def i5_cost(trace, seat, case) -> Verdict:
    if trace.turns is None:
        return Verdict(NAME, INCONCLUSIVE,
                       "no ResultMessage — turns unknown", tag="no-result")
    if trace.turns > seat.max_turns:
        return Verdict(NAME, FAIL,
                       f"{trace.turns} turns exceeds the ceiling of"
                       f" {seat.max_turns}",
                       tag="turn-ceiling")
    briefs = (trace.tool_names or []).count(BRIEF_TOOL)
    if briefs > 1:
        return Verdict(NAME, FAIL,
                       f"called {BRIEF_TOOL} {briefs} times — the brief is"
                       " read-only and identical on every call",
                       tag="step-repetition")
    if trace.cost_usd is None:
        alerted = any(COST_ALERT in (a.get("payload") or {}).get("text", "")
                      for a in (trace.alerts or []))
        if alerted:
            return Verdict(NAME, INCONCLUSIVE,
                           "no cost estimate; production alerted as required",
                           tag="cost-missing")
        return Verdict(NAME, FAIL,
                       f"no cost estimate AND no {COST_ALERT} alert — the"
                       " cost pillar failed silently",
                       tag="cost-missing-without-alert")
    if trace.cost_usd > seat.max_cost_usd:
        return Verdict(NAME, FAIL,
                       f"${trace.cost_usd:.4f} est. exceeds the ceiling of"
                       f" ${seat.max_cost_usd:.2f}",
                       tag="cost-ceiling")
    return Verdict(NAME, PASS,
                   f"{trace.turns} turns, ${trace.cost_usd:.4f} est.")
