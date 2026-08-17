"""Eval rig for the LLM seats (docs/evals/PLAN.md).

NOT purity-linted and deliberately outside scripts/check_purity.py's
PURE_PACKAGES: this package exists to drive real LLM turns, so it imports both
claude_agent_sdk (runner) and the pure packages (fixtures, grading). Nothing
in gate/, orchestrator/, state/, stratgate/ or calibration/ may import FROM
here — the dependency runs one way only.
"""
