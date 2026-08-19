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
}


def stage_prompt(seat: str, tickers: list[str]) -> str:
    if seat not in PROMPT_TEMPLATES:
        raise ValueError(
            f"no stage prompt for seat {seat!r} — expected one of"
            f" {sorted(PROMPT_TEMPLATES)}")
    return PROMPT_TEMPLATES[seat].format(tickers=", ".join(tickers))
