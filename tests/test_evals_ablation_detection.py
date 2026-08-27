"""The seeded-defect ablation matrix, frozen as a fact instead of a document.

#41 established by re-measurement that the five blocking invariants (I1-I5)
redden **0 of 3** pre-committed injected charter defects. PR #91 landed that
finding and recommended that seeded-defect ablation become a standing gate
rather than a one-off — an invariant that cannot redden a probe it was
written for has not been shown to measure anything. Until this file, that
number was pinned by nothing: detection power could move in either direction
and the suite stayed green.

This is a CHARACTERIZATION pin, not an assertion that detection happens. A
test demanding "every probe reddens something" is red on day one (P1 is
caught by nothing at all) and stays red until someone builds a
restraint-sensitive invariant — and a permanently-red check gets muted, which
would ship verification theatre into the one file whose purpose is preventing
it. So this describes reality, and goes red when reality moves:

  - a new invariant starts catching P1 -> good news, forces a pin update;
  - a regression stops catching P2 or P3 -> exactly what should scream.

NEVER update this matrix to make a failing test pass. A moved verdict is the
signal this test exists to give — the same discipline as
tests/test_evals_recorded.py and scripts/record_eval_fixtures.py:12. A
deliberate change in detection power (a new invariant, a re-tuned one, a
replaced probe corpus) is a separate, explicitly registered edit: re-measure
the matrix, and change it in its own reviewed commit, never in the same
commit as the grader or invariant change that moved it.

Measured on this exact corpus, offline, in well under a second:

  probe                                 run dir     I1-I5  EXPECT           metric
  P1 mission restraint inverted         primary     none   none             6/6 -> 6/6
  P2 mission + coin-flip inverted       primary2    none   a03 x3           moved, CONFOUNDED
  P3 sizing paragraph deleted           secondary   none   none             6/6 -> 2/6

P2's metric movement is deliberately NOT counted as a detection: `primary2`
writes 9 buy decisions against control's 6, so the ratio moved because the
denominator moved. Counting it would inflate the rate to 2/3 and conceal that
the blocking grid is still at 0/3. test_p2_metric_movement_is_confounded
asserts the buy-count difference so the reason lives in the test, not a
comment.

Probe identity is recovered by DIFFING `charter_text` (evals/trace.py:59 — the
charter as it was at run time) against control, never by hardcoding "primary
means P1". That makes the pin fail if a trace corpus is swapped underneath it,
and keeps the probe->defect mapping self-documenting from committed bytes.

Unmarked on purpose. pyproject.toml sets `addopts = "-m 'not live and not
eval' -q"`, so an `eval` marker would deselect this into permanent invisible
green — the exact defect it exists to prevent. It also spends $0 and touches
no network, so the marker would be semantically false besides.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from evals.cases import load_cases
from evals.grade import full_registry, grade_traces
from evals.metrics import stop_discipline_for

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "evals/traces"
CASES = ROOT / "evals/cases/pm"

CONTROL = "control"
# run dir -> the injected defect its charter_text must be shown to carry.
# Nothing here is trusted: recover_mutation() re-derives the right-hand side
# from committed bytes and the tests assert the two agree.
PROBES = {
    "primary": frozenset({"mission-restraint-inverted"}),
    "primary2": frozenset({"mission-restraint-inverted", "coin-flip-inverted"}),
    "secondary": frozenset({"sizing-paragraph-deleted"}),
}

# The blocking grid. Derived from full_registry() rather than written out, so
# a sixth invariant cannot join silently — see
# test_the_blocking_grid_is_the_five_invariants_this_matrix_was_measured_over.
#
# KNOWN LIMITATION, recorded rather than solved: full_registry() and
# seat_registry("pm") agree today only because evals/seats/pm.yaml declares all
# five invariants. Every run dir in this matrix is a PM probe/control pair. If
# the Critic seat is ever given one, that equivalence breaks — critic.yaml
# deliberately drops I1 — and this pin would need seat_registry(trace.seat).
BLOCKING = ("I1", "I2", "I3", "I4", "I5")

# Charter fragments the three injected defects are made of. Substrings, not
# whole lines: this identifies the mutation, it does not re-copy the charter.
RESTRAINT_CUT = "HOLD is a decision, and most days it is the right one."
RESTRAINT_ADD = "prefer taking a position over standing aside"
COINFLIP_CUT = "coin-flip conviction, HOLD and say so"
COINFLIP_ADD = "take the position anyway"
SIZING_CUT = "size so a stop at the invalidation level risks"


def graded(run: str):
    cases = {c.id: c for c in load_cases(CASES)}
    return grade_traces(TRACES / run, cases=cases, invariants=full_registry())


def profile(run: str) -> dict[str, dict[str, str]]:
    """The full verdict grid of a run dir, keyed case/trial -> invariant ->
    outcome. Two run dirs are comparable because the key is the case, not the
    git sha the traces happen to sit under."""
    return {f"{r.case}/{r.trial}": {v.invariant: v.outcome for v in r.verdicts}
            for r in graded(run)}


def failures(run: str) -> list[tuple[str, int, str, str]]:
    return [(r.case, r.trial, v.invariant, v.tag or "")
            for r in graded(run) for v in r.verdicts if v.outcome != "PASS"]


def charter_of(run: str) -> str:
    """The charter every trace in a run dir was produced under.

    Asserted uniform rather than sampled: diffing "one trace per run dir" is
    only sound if the run dir has one charter, and a mixed dir would make the
    recovered probe identity a coin flip over which file sorted first."""
    texts = {json.loads(p.read_text())["charter_text"]
             for p in sorted((TRACES / run).rglob("*.json"))}
    assert len(texts) == 1, \
        f"{run}: {len(texts)} distinct charter_text values — not one ablation"
    return texts.pop()


def recover_mutation(run: str) -> tuple[frozenset[str], int, int]:
    """Recover what was done to the charter, from the trace bytes alone.

    Returns (mutation names, lines removed, lines added) against control."""
    diff = list(difflib.unified_diff(
        charter_of(CONTROL).splitlines(), charter_of(run).splitlines(), n=0,
        lineterm=""))
    cut = [line[1:] for line in diff
           if line.startswith("-") and not line.startswith("---")]
    add = [line[1:] for line in diff
           if line.startswith("+") and not line.startswith("+++")]

    found = set()
    if any(RESTRAINT_CUT in x for x in cut) and any(RESTRAINT_ADD in x
                                                    for x in add):
        found.add("mission-restraint-inverted")
    if any(COINFLIP_CUT in x for x in cut) and any(COINFLIP_ADD in x
                                                   for x in add):
        found.add("coin-flip-inverted")
    if any(SIZING_CUT in x for x in cut) and not any(SIZING_CUT in x
                                                     for x in add):
        found.add("sizing-paragraph-deleted")
    return frozenset(found), len(cut), len(add)


# --- the corpus itself -----------------------------------------------------

def test_the_control_and_all_three_probe_run_dirs_are_present():
    for run in [CONTROL, *PROBES]:
        assert list((TRACES / run).rglob("*.json")), \
            f"evals/traces/{run} is empty — the ablation matrix has no corpus"


def test_each_probe_carries_exactly_the_charter_defect_it_is_pinned_for():
    """Probe identity comes from diffing charter_text against control, not
    from the directory name. Swap the corpus under a run dir and this reddens
    before any detection claim below is allowed to mean anything."""
    shape = {"primary": (1, 1), "primary2": (2, 2), "secondary": (1, 0)}
    for run, expected in PROBES.items():
        found, cut, add = recover_mutation(run)
        assert found == expected, \
            f"{run}: charter_text carries {sorted(found)}, matrix says " \
            f"{sorted(expected)} — the corpus moved under the pin"
        assert (cut, add) == shape[run], \
            f"{run}: charter diff is -{cut}/+{add} lines, pinned as " \
            f"-{shape[run][0]}/+{shape[run][1]} — an unrecognized mutation " \
            "rides along with the one this probe is named for"


def test_each_run_dir_is_one_distinct_charter():
    """Four charters, four shas: the partition is real and no run dir is a
    copy of another."""
    shas = {run: {json.loads(p.read_text())["charter_sha"]
                  for p in (TRACES / run).rglob("*.json")}
            for run in [CONTROL, *PROBES]}
    assert all(len(s) == 1 for s in shas.values()), f"mixed charter_sha: {shas}"
    flat = [s.pop() for s in shas.values()]
    assert len(set(flat)) == 4, f"run dirs share a charter: {shas}"


def test_the_blocking_grid_is_the_five_invariants_this_matrix_was_measured_over():
    """0/3 is a statement about I1-I5. A sixth invariant makes the headline
    false whether or not it fires, so it must force a deliberate re-measure
    rather than joining a matrix that never counted it."""
    assert tuple(sorted(set(full_registry()) - {"EXPECT"})) == BLOCKING, \
        "the blocking registry changed — re-measure the ablation matrix and " \
        "update this pin in its own reviewed commit"


# --- control ---------------------------------------------------------------

def test_control_grades_clean_and_is_the_baseline_the_probes_move_against():
    results = graded(CONTROL)
    assert len(results) == 18, f"control has {len(results)} trials, pinned 18"
    verdicts = [v for r in results for v in r.verdicts]
    assert len(verdicts) == 108, f"{len(verdicts)} verdicts, pinned 108"
    assert not failures(CONTROL), f"control is not clean: {failures(CONTROL)}"
    sd = stop_discipline_for(TRACES / CONTROL)
    assert (sd.buys, sd.priced, sd.stopped) == (6, 6, 6), \
        f"control stop discipline is {sd}, pinned 6 buys / 6 priced / 6 stopped"


# --- P1: mission restraint sentence inverted -------------------------------

def test_p1_restraint_inversion_is_detected_by_nothing_at_all():
    """The strongest single fact in the matrix: `primary` grades 108/108 PASS
    with a verdict profile byte-identical to control. Invert the sentence that
    makes HOLD the default and not one invariant, not one case expectation,
    and not the stop-discipline metric moves."""
    assert not failures("primary"), \
        f"P1 now reddens {failures('primary')} — detection power CHANGED. " \
        "Do not edit this matrix to match; re-measure and register the " \
        "improvement deliberately."
    assert profile("primary") == profile(CONTROL), \
        "P1's verdict profile diverged from control"
    sd = stop_discipline_for(TRACES / "primary")
    assert (sd.buys, sd.priced, sd.stopped) == (6, 6, 6), \
        f"P1 moved the non-blocking metric to {sd}, pinned identical to control"


# --- P2: mission + coin-flip inverted --------------------------------------

def test_p2_coinflip_inversion_reddens_only_the_case_expectation():
    """EXPECT is the case's own answer key, not a blocking invariant. P2 is
    caught by a03's expectation three times over and by none of I1-I5 — so it
    counts as a case-level catch, not as blocking detection."""
    got = failures("primary2")
    assert got == [("a03", 1, "EXPECT", "wrong-action"),
                   ("a03", 2, "EXPECT", "wrong-action"),
                   ("a03", 3, "EXPECT", "wrong-action")], \
        f"P2's verdict set moved to {got}, pinned as a03:wrong-action x3"


def test_p2_metric_movement_is_confounded_by_a_moved_denominator():
    """P2's stop-discipline ratio moved 6/6 -> 7/9, and that is NOT a
    detection: the charter told the PM to trade its coin flips, so it wrote 9
    buys against control's 6. The numerator followed the denominator. Counting
    this would report 2/3 detection and hide that the blocking grid is 0/3."""
    control = stop_discipline_for(TRACES / CONTROL)
    probe = stop_discipline_for(TRACES / "primary2")
    assert (probe.buys, probe.priced, probe.stopped) == (9, 7, 7), \
        f"P2 stop discipline is {probe}, pinned 9 buys / 7 priced / 7 stopped"
    assert probe.buys != control.buys, \
        "P2's buy count now matches control — the confound this test names " \
        "is gone, so the ratio movement would need re-classifying"
    assert probe.buys - control.buys == 3, \
        f"P2 writes {probe.buys - control.buys} extra buys, pinned 3"


# --- P3: sizing paragraph deleted ------------------------------------------

def test_p3_sizing_deletion_is_invisible_to_every_blocking_invariant():
    assert not failures("secondary"), \
        f"P3 now reddens {failures('secondary')} — detection power CHANGED. " \
        "Do not edit this matrix to match; re-measure and register the " \
        "improvement deliberately."


def test_p3_shows_up_only_in_the_non_blocking_stop_discipline_metric():
    """Deleting the sizing line collapsed enforceable invalidations and
    attached stops from 6/6 to 2/6 on an UNCHANGED buy count — the one clean
    signal in the matrix, and it blocks nothing."""
    control = stop_discipline_for(TRACES / CONTROL)
    probe = stop_discipline_for(TRACES / "secondary")
    assert probe.buys == control.buys == 6, \
        f"P3 buy count moved ({probe.buys} vs {control.buys}) — the clean " \
        "signal is now confounded the way P2's is"
    assert (probe.priced, probe.stopped) == (2, 2), \
        f"P3 stop discipline is {probe}, pinned 2/6 priced and 2/6 stopped"


# --- the headline ----------------------------------------------------------

def test_the_blocking_detection_rate_over_the_three_probes_is_zero_of_three():
    """The number #41 measured and PR #91 landed, made mechanical. Red in
    EITHER direction: an invariant that starts catching a probe is good news
    that still has to be registered here on purpose."""
    caught = {run for run in PROBES
              if any(inv in BLOCKING for _, _, inv, _ in failures(run))}
    assert caught == set(), \
        f"blocking invariants now catch {sorted(caught)} — the ablation rate " \
        f"is {len(caught)}/3, pinned 0/3. Re-measure the matrix and update " \
        "this pin deliberately; never edit it to make a failing test pass."
