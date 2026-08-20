"""The invariant registry.

grade.py takes a plain dict, so a caller can grade any subset; REGISTRY is the
full Tier S set every trial is scored against. Adding an entry here
retroactively covers every trace ever recorded — that is the payoff the
run/grade split buys.

Tier S is blocking at 3/3. Requiring 2/3 on a containment property is
formally accepting a 33% violation rate; if one cannot hold 3/3, the finding
is either that the predicate is too tight or that the behaviour is unsafe.
Neither is fixed by loosening the threshold.
"""

from evals.invariants.i1_size import i1_size
from evals.invariants.i2_glob import i2_glob
from evals.invariants.i3_leak import i3_leak
from evals.invariants.i4_schema import i4_schema
from evals.invariants.i5_cost import i5_cost
from evals.invariants.n1_absence import n1_absence
from evals.invariants.n2_evidence import n2_evidence

REGISTRY = {"I1": i1_size, "I2": i2_glob, "I3": i3_leak,
            "I4": i4_schema, "I5": i5_cost}

# Seat-scoped invariants: real graders that are meaningless outside the seat
# that declares them. N1/N2 read `signals` and grade a retrieval job, so on a
# PM trace they would pass vacuously — a green that says nothing is worse than
# no verdict, because a reader counts it.
#
# Kept OUT of REGISTRY deliberately. REGISTRY is Tier S, blocking for every
# seat, and its membership is asserted in tests/test_evals_invariants.py.
# Growing it for a seat-specific rule would make that assertion a formality
# that gets edited whenever a seat is added, which is how a pin stops pinning.
SEAT_SCOPED = {"N1": n1_absence, "N2": n2_evidence}


def for_seat(seat) -> dict:
    """The seat-scoped graders `seat` declares in evals/seats/<name>.yaml.

    This is what makes that file's `invariants:` list load-bearing. Before
    2026-08-20 it was parsed into EvalSeat and then never read by anything —
    pm.yaml could have declared an invariant that does not exist, or omitted
    one that runs anyway, and nothing would have said so.

    Unknown names raise rather than being skipped: a typo'd invariant that
    silently does not run is a check you believe you have and do not.
    """
    unknown = [n for n in seat.invariants
               if n not in REGISTRY and n not in SEAT_SCOPED]
    if unknown:
        raise ValueError(
            f"seat {seat.name!r} declares unknown invariant(s) {unknown} —"
            f" known: {sorted(REGISTRY)} + {sorted(SEAT_SCOPED)}")
    return {n: SEAT_SCOPED[n] for n in seat.invariants if n in SEAT_SCOPED}
