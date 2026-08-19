"""Well-formedness pins for the G1 alignment case set, checked offline.

These are not the eval. They pin the properties that make the eval's NUMBER
mean something: that the set is balanced (a seat that always says one thing
cannot pass), that every case names the mechanism defect it is testing, and
that every spec is a state strategy_specs can actually hold.
"""

from __future__ import annotations

from pathlib import Path

from evals.cases import load_case, load_cases

CASES = Path(__file__).resolve().parents[1] / "evals/cases/critic"
ALLOWED_EXPECT_KEYS = {"verdict", "objection_mentions"}
MECHANISM_CLASSES = {"behavioral", "institutional", "risk_premium",
                     "liquidity_provision"}
DEV = {"m01", "a01", "m03", "m05", "h01", "a04"}
HOLDOUT = {"m02", "a02", "m04", "a03", "h02", "h03"}
SPEC_FIELDS = {
    "family", "seat", "hypothesis", "mechanism_class", "universe",
    "liquidity_bucket", "signal_rule", "param_ranges", "search_budget",
    "holding_period_d", "rebalance", "expected_turnover", "exit_rule",
    "invalidation", "capacity_usd", "predicted", "llm_in_loop",
}


def test_the_twelve_alignment_cases_exist():
    assert {c.id for c in load_cases(CASES)} == {
        "m01", "m02", "m03", "m04", "m05",
        "a01", "a02", "a03", "a04",
        "h01", "h02", "h03"}


def test_every_case_is_a_critic_case_with_a_spec_and_no_ticker_fields():
    for c in load_cases(CASES):
        assert c.seat == "critic", f"{c.id} is not a critic case"
        assert c.spec is not None, f"{c.id} carries no spec"
        assert c.tickers == [], f"{c.id} carries tickers — wrong case shape"
        assert c.snapshot == {}, f"{c.id} carries a snapshot — wrong case shape"


def test_every_spec_carries_exactly_the_registered_spec_fields():
    """strategy_specs (specs/strategy-contracts.md §2) is canonical. A case
    with an invented field would seed a row production could never hold."""
    for c in load_cases(CASES):
        assert set(c.spec) == SPEC_FIELDS, \
            f"{c.id} spec fields differ from the DDL: {set(c.spec) ^ SPEC_FIELDS}"
        assert c.spec["mechanism_class"] in MECHANISM_CLASSES
        assert len(c.spec["hypothesis"]) <= 500, f"{c.id} hypothesis too long"
        assert len(c.spec["invalidation"]) <= 500


def test_the_set_is_balanced_so_a_one_note_critic_cannot_pass():
    """6 objections / 6 clear. A seat that always objects and a seat that
    always clears both land at 6/12. Balance is necessary but NOT sufficient —
    it kills the degenerate seat, not the asymmetric one — which is why the
    gate is scored per class rather than in aggregate."""
    verdicts = [c.expect["verdict"] for c in load_cases(CASES)]
    assert verdicts.count("objections") == 6
    assert verdicts.count("clear") == 6


def test_the_split_is_declared_and_partitions_the_set():
    by_split = {}
    for c in load_cases(CASES):
        assert c.split in ("dev", "holdout"), \
            f"{c.id} declares split {c.split!r}"
        by_split.setdefault(c.split, set()).add(c.id)
    assert by_split["dev"] == DEV
    assert by_split["holdout"] == HOLDOUT


def test_each_half_is_balanced_three_and_three():
    """The gate is scored per class ON THE HOLDOUT, 3 cases x 3 trials = 9
    trials per class. A half that skewed 4/2 would move the count gate by a
    third for a reason that has nothing to do with the seat."""
    for split in ("dev", "holdout"):
        verdicts = [c.expect["verdict"] for c in load_cases(CASES)
                    if c.split == split]
        assert verdicts.count("objections") == 3, split
        assert verdicts.count("clear") == 3, split


def test_each_half_keeps_a_matched_pair():
    """Paired evaluation on identical inputs is the recommended comparison
    design, and these pairs are what isolate the single varied clause: m01/a01
    differ only in the turnover filter, m04/a03 only in the sizing denominator.
    Splitting a pair across halves would destroy the isolation."""
    cases = {c.id: c for c in load_cases(CASES)}
    for lo, hi in (("m01", "a01"), ("m04", "a03")):
        assert cases[lo].split == cases[hi].split, f"{lo}/{hi} split apart"
        assert cases[lo].spec["hypothesis"] == cases[hi].spec["hypothesis"], \
            f"{lo}/{hi} no longer share a hypothesis — the pair tests nothing"
        assert cases[lo].expect["verdict"] == "objections"
        assert cases[hi].expect["verdict"] == "clear"


def test_no_case_restates_a_code_invariant():
    for c in load_cases(CASES):
        assert set(c.expect) <= ALLOWED_EXPECT_KEYS, \
            f"{c.id} declares unsupported expectation keys: {set(c.expect)}"


def test_every_objections_case_names_the_defect_it_expects_to_be_caught():
    """The whole failure mode this set exists to detect is a Critic that
    objects for the WRONG reason — right verdict, no understanding. Every
    misaligned case must therefore pin substrings the objection has to name."""
    for c in load_cases(CASES):
        if c.expect["verdict"] == "objections":
            mentions = c.expect.get("objection_mentions") or []
            assert mentions, f"{c.id} expects objections but names no defect"
            assert all(m == m.lower() for m in mentions), \
                f"{c.id} objection_mentions must be lowercase (matched case-insensitively)"


def test_no_long_only_spec_carries_a_borrow_filter():
    """A borrow-availability screen is inert on a long-only sleeve — you need
    borrow to short, not to buy. An inert clause in a CLEAR case hands a
    competent Critic a legitimate objection ("this filter does nothing") on a
    case that scores objecting as failure, which is the worst possible
    grading error: it marks real insight wrong.

    Narrow by design. The general property — every clause in a CLEAR case's
    rule must actually do something — is not mechanically checkable, and the
    real guard is reading the cases. This pins the one instance that already
    got past a self-review."""
    for c in load_cases(CASES):
        rule = str(c.spec["signal_rule"]).lower()
        if "long only" in rule or "long-only" in rule:
            assert "borrow" not in rule, \
                f"{c.id}: borrow filter on a long-only rule is inert"


def test_clear_cases_never_declare_objection_mentions():
    for c in load_cases(CASES):
        if c.expect["verdict"] == "clear":
            assert "objection_mentions" not in c.expect, \
                f"{c.id} expects CLEAR but names objections to find"


def test_every_case_explains_in_notes_why_it_is_or_is_not_misaligned():
    """A misaligned case whose defect is not written down cannot be reviewed,
    and an unreviewable case is not an acceptance criterion."""
    for c in load_cases(CASES):
        assert len(c.notes.split()) >= 25, f"{c.id} notes are too thin"


def test_subjects_is_the_spec_id_for_a_spec_shaped_case():
    c = load_case(CASES / "m01.yaml")
    assert len(c.subjects) == 1
    assert c.subjects[0].startswith("spec_")


def test_a_case_declaring_both_shapes_is_refused(tmp_path):
    import pytest
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        'id: bad\nseat: critic\nclock: "2026-07-06T13:45:00+00:00"\n'
        'tickers: [NVDA]\nsnapshot: {}\nspec: {}\nexpect: {verdict: clear}\n')
    with pytest.raises(ValueError, match="exactly one of"):
        load_case(bad)


def test_a_case_declaring_neither_shape_is_refused(tmp_path):
    import pytest
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        'id: bad\nseat: critic\nclock: "2026-07-06T13:45:00+00:00"\n'
        'expect: {verdict: clear}\n')
    with pytest.raises(ValueError, match="exactly one of"):
        load_case(bad)
