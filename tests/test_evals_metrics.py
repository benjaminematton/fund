"""Tier M metrics: measured per run, never blocking."""

from __future__ import annotations

import json

from evals.metrics import (names_price_level, stop_discipline,
                           stop_discipline_for)


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


def test_stop_discipline_for_scores_a_recorded_run_off_disk(tmp_path):
    """`make eval-report` re-scores recorded JSON with no Trace rehydration —
    the loader must walk <run>/<sha>/<case>/<trial>.json the way traces are
    actually laid out, not a flat directory."""
    for case, row in (("a01", _buy("closes below $205", 205.0)),
                      ("a02", _buy("capex guidance is cut"))):
        d = tmp_path / "4f42600" / case
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps(
            {"case": case, "rows_written": {"decisions": [row]}}))
    m = stop_discipline_for(tmp_path)
    assert (m.buys, m.priced, m.stopped) == (2, 1, 1)
    assert m.rate(m.priced) == "1/2"


def test_stop_discipline_for_an_empty_run_is_zero_not_a_crash(tmp_path):
    """A labelled run whose traces were cleaned up (make preflight does this)
    must report 0/0, never take the report down."""
    assert stop_discipline_for(tmp_path).buys == 0
