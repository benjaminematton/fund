"""The stage prompts, duplicated from scripts/run_day.py.

Duplicated rather than imported: run_day.py opens Slack and Alpaca at import
time, and Step 1 is not permitted to refactor production. The duplication is
therefore PINNED by tests/test_evals_runner.py, which greps run_day.py for
this exact wording — because a rig evaluating a prompt production no longer
sends is a rig measuring nothing.

Fold this into a shared constant when Step 6 touches seats.py anyway.
"""

from __future__ import annotations

PROMPT_TEMPLATES = {
    "analyst": ("Research turn. Today's active tickers: {tickers}. Start by"
                " calling get_stage_brief, then follow your charter and end by"
                " calling submit_signal exactly once per ticker."),
    # Byte-identical to the analyst's: scripts/run_day.py builds ONE
    # research_prompt and hands the same string to every seat in
    # SEATS["research"]. If that ever diverges per seat, these must diverge with
    # it — an eval that pins a prompt production no longer sends measures
    # nothing.
    "news": ("Research turn. Today's active tickers: {tickers}. Start by"
             " calling get_stage_brief, then follow your charter and end by"
             " calling submit_signal exactly once per ticker."),
    "pm": ("Decision turn. Today's active tickers: {tickers}. Start by"
           " calling get_stage_brief, then follow your charter and end by"
           " calling submit_decision exactly once per ticker."),
    # The rig has no exec cases and no evals/seats/exec.yaml, so nothing calls
    # stage_prompt("exec") today. It is pinned anyway: run_day.py sends this
    # wording, and the drift guard below derives its seat list from run_day's
    # own SEATS map, so an unpinned production prompt is a hole rather than an
    # exemption. Added when that guard was tightened and found this one.
    "exec": "Execution stage: execute all open tickets per your charter.",
    # G1 names no spec: the brief carries it. Constant across cases, which is
    # what keeps a recorded trial replayable (CLAUDE.md — no per-run values in
    # prompts). Not yet sent by scripts/run_day.py; the G1 review stage is the
    # separate G1 gate change, and tests/test_evals_runner.py pins each
    # template only for the seats run_day.py actually drives.
    "critic": ("G1 review turn. Start by calling get_spec_brief, then follow"
               " your charter and end by calling submit_spec_critique exactly"
               " once, for the spec in your brief."),
}


def stage_prompt(seat: str, tickers: list[str]) -> str:
    if seat not in PROMPT_TEMPLATES:
        raise ValueError(
            f"no stage prompt for seat {seat!r} — expected one of"
            f" {sorted(PROMPT_TEMPLATES)}")
    return PROMPT_TEMPLATES[seat].format(tickers=", ".join(tickers))
