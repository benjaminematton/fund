"""charters/*.md conform to charters/_template.md structurally (#198).

NOT a review of what a charter SAYS — agents/seats.py sends these files
verbatim as system prompts and no test can judge a prompt. What is checkable
is the shape _template.md:3 already mandates: "exactly these seven sections,
in this order", a version in the header, and a changelog at the bottom.

THE REQUIRED HEADINGS ARE PARSED OUT OF _template.md, never typed here. A
second hand-maintained copy of the list is how the template and the charters
come to disagree with nobody noticing — and it would also let an editor
"fix" a failure by editing this file. The template is the spec; this reads it.

Red when written, on exactly one file: charters/quant.md shipped with ten
XML-ish tags instead of the seven sections and no changelog at all. The other
six charters pass unchanged, which is what makes this a conformance test
rather than a rewrite of the suite around one file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.seats import _parse_charter_version

CHARTERS = Path(__file__).resolve().parents[1] / "charters"
TEMPLATE = CHARTERS / "_template.md"


def _headings(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.startswith("## ")]


REQUIRED = _headings(TEMPLATE.read_text())
SHIPPED = sorted(p for p in CHARTERS.glob("*.md") if p.name != "_template.md")


def test_the_template_still_defines_seven_sections():
    """The instrument before the measurement. Every assertion below is
    derived from this list, so a template that stopped carrying seven
    headings would silently relax all of them to nothing."""
    assert len(REQUIRED) == 7
    assert REQUIRED[0] == "## Identity" and REQUIRED[-1] == "## Judgment"


def test_there_are_charters_to_check():
    """An empty glob passes every parametrized test below by vacuum. Pin the
    population so a moved directory reddens instead of going quiet."""
    assert len(SHIPPED) >= 7


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_a_charter_carries_the_templates_seven_sections_in_order(path):
    assert _headings(path.read_text()) == REQUIRED


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_a_charter_carries_a_changelog(path):
    """_template.md:3: "bump the header on any change and note it in the
    changelog at the bottom". A charter with no changelog cannot record why
    a prompt changed, and the prompt is the seat."""
    assert any(ln.startswith("changelog:")
               for ln in path.read_text().splitlines())


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_a_charter_header_carries_a_parseable_version(path):
    """_parse_charter_version returns 'unknown' rather than raising, by
    design (invariant 4: a formatting slip must not take a day down). That
    makes an unparseable header SILENT in production — the scoreboard just
    excludes the seat — so it has to be loud here instead."""
    assert _parse_charter_version(path.read_text()) != "unknown"


def test_the_quant_charter_and_the_model_name_the_same_families():
    """The seat has no read tools, so specs/strategy.md §3 is unreachable and
    this file is the only place it can learn the vocabulary. Binding it to the
    model means neither can drift alone: a charter code the model rejects, or
    a model that stops accepting a charter code, reddens here.

    No prose parsing: F-codes are tokenized and the MODEL is the authority."""
    import re

    from state.models import REGISTERED_FAMILIES, StrategySpec
    from tests.test_state_models import _spec

    text = (CHARTERS / "quant.md").read_text()
    codes = set(re.findall(r"\bF\d+\b", text))
    assert codes == set(REGISTERED_FAMILIES), (
        f"charter names {sorted(codes)}, model accepts"
        f" {sorted(REGISTERED_FAMILIES)}")
    assert "petition:" in text, "the escape hatch is unreachable to the seat"

    # Set equality above compares the charter against the CONSTANT. The
    # docstring claims the stronger thing — that the MODEL is the authority —
    # and only constructing a spec per code checks it: a validator that grew a
    # rule rejecting a registered code (say a deny-list ahead of the membership
    # check) leaves the set comparison green while the seat's turn is refused.
    for code in sorted(codes):
        StrategySpec(**_spec(family=code))

    # The charter also tells the seat what petition:<name> may and may not
    # look like: a plain name is fine, an F<digit>... shape is refused because
    # it shadows the reserved family-code namespace. Bind both halves of that
    # claim to the model, or a charter/model drift on the petition rule goes
    # undetected the same way the F-code drift above would.
    from pydantic import ValidationError

    StrategySpec(**_spec(family="petition:mean_reversion_v2"))
    with pytest.raises(ValidationError):
        StrategySpec(**_spec(family="petition:F9"))
