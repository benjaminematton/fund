"""The invariant registry.

grade.py takes a plain dict, so a caller can grade any subset; REGISTRY is the
full Tier S set every trial is scored against. Adding an entry here
retroactively covers every trace ever recorded — that is the payoff the
run/grade split buys.
"""

from evals.invariants.i1_size import i1_size

REGISTRY = {"I1": i1_size}
