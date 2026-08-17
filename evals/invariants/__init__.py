"""The invariant registry.

grade.py takes a plain dict, so a caller can grade any subset; REGISTRY is the
full Tier S set every trial is scored against. Adding an entry here
retroactively covers every trace ever recorded — that is the payoff the
run/grade split buys.
"""

from evals.invariants.i1_size import i1_size
from evals.invariants.i2_glob import i2_glob
from evals.invariants.i3_leak import i3_leak
from evals.invariants.i4_schema import i4_schema

REGISTRY = {"I1": i1_size, "I2": i2_glob, "I3": i3_leak, "I4": i4_schema}
