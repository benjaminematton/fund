"""Tier M metrics: measured per run, never blocking."""

from __future__ import annotations

from evals.metrics import names_price_level, stop_discipline


def _buy(invalidation, stop_price=None):
    return {"ticker": "NVDA", "action": "buy", "qty": 10, "thesis": "t",
            "invalidation": invalidation, "stop_price": stop_price,
            "status": "submitted"}


def test_a_dollar_level_is_a_price_level():
    assert names_price_level("NVDA closes below $205")


def test_a_narrative_condition_is_not_a_price_level():
    assert not names_price_level(
        "DC capex guidance is cut or delayed in the next two prints")


def test_a_vague_relative_level_is_not_a_price_level():
    """The exact regression the secondary probe produced: reads like an
    invalidation, cannot be enforced by a broker."""
    assert not names_price_level(
        "NVDA closes back below its pre-guidance-raise level")


def test_a_date_is_not_mistaken_for_a_price():
    assert not names_price_level("breaks the Aug 11 swing low")


def test_stop_discipline_rates_only_count_buys():
    rows = [_buy("closes below $205", 205.0),
            _buy("capex guidance is cut"),
            {"ticker": "MSFT", "action": "hold", "qty": 0, "thesis": "t",
             "invalidation": "n/a", "stop_price": None, "status": "submitted"}]
    m = stop_discipline([{"rows_written": {"decisions": rows}}])
    assert m.buys == 2
    assert m.priced == 1
    assert m.stopped == 1
