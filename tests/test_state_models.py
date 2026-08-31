import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from state.models import REGISTERED_FAMILIES, StrategySpec

ROOT = Path(__file__).resolve().parents[1]


def _spec(**over) -> dict:
    """A minimal valid spec payload; override one field per test."""
    base = dict(
        family="F1", seat="quant", hypothesis="h", mechanism_class="behavioral",
        universe={}, liquidity_bucket="small", signal_rule={}, param_ranges={},
        search_budget=1, holding_period_d=1, rebalance="daily",
        expected_turnover=0.0, exit_rule="x", invalidation="i",
        capacity_usd=1.0, predicted={}, llm_in_loop=0)
    base.update(over)
    return base


def test_the_registered_families_are_the_ones_the_spec_registers():
    """REGISTERED_FAMILIES is the authority other tests (and a later charter
    test) import, so something must anchor it to canon or it only ever agrees
    with itself. Derived from specs/strategy.md §3's own '### F<n>.' headings
    rather than copied, so a spec change is caught here too."""
    text = (ROOT / "specs" / "strategy.md").read_text()
    headings = re.findall(r"^### (F\d+)\.", text, re.MULTILINE)
    assert REGISTERED_FAMILIES == set(headings)


def test_the_payload_helper_itself_is_valid():
    """Guards every parametrized case below. Without this, a drift in _spec
    (a renamed field under extra="forbid", a tightened sibling Field) makes
    every reject case pass for the wrong reason while `family` silently
    reverts to a free string."""
    assert StrategySpec(**_spec()).family == "F1"


@pytest.mark.parametrize("ok", sorted(REGISTERED_FAMILIES) +
                         ["petition:overnight_gap", "petition:x",
                          "petition:Fx_thing"])
def test_registered_families_and_petitions_are_accepted(ok):
    assert StrategySpec(**_spec(family=ok)).family == ok


@pytest.mark.parametrize("bad", [
    "mean_reversion",                  # a plausible invention with no F-code
    "F1 - Short-term mean reversion",  # the code plus prose
    "f1",                              # case matters
    "F6", "F", "F1x",                  # off the menu / not a code
    "", "F1 ", " F1", "F1\n",          # empty and whitespace-padded
    "petition:",                       # prefix with no name
    "petition: ", "petition:x ",       # whitespace-only or padded name
    "PETITION:x",                      # prefix is case-sensitive
    "petition:F1",                     # shadows a registered family
    "petition:F9",                     # invents a code behind the prefix
    "petition:F1 - mean reversion",    # the rejected shape, laundered
    "petition:" + "a" * 200,           # a key, not prose
])
def test_a_family_off_the_menu_is_refused(bad):
    """`strategy_specs` is immutable with no delete path, and `family` is
    denormalized onto trial_registry as the multiple-testing denominator
    behind deflated Sharpe (state/schema.sql:236). A mis-keyed family
    under-deflates every trial in the real family, forever."""
    with pytest.raises(ValidationError) as exc:
        StrategySpec(**_spec(family=bad))
    # WHICH field failed, not merely that something did.
    assert exc.value.errors()[0]["loc"] == ("family",)


def test_constraining_family_did_not_move_the_spec_id():
    """The one frozen StrategySpec-derived id in the tree. It exists because
    nothing else would go red if a model change altered the hash: every other
    expected id is recomputed from the same payload at test time, so it agrees
    with any model. If this fails, a model change moved every spec_id in the
    fund — STOP and ask; do NOT re-record it."""
    from fundbt.hashing import spec_id
    assert spec_id(StrategySpec(**_spec()).model_dump()) == "spec_39997bfd29606bb9"
