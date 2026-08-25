"""Offline tests for the ingestion guard on the broker's positions payload
(issue #39).

The defect: scripts/run_day.py reads the account once, at the top of the day,
and every gate input for every ticker is computed from that one payload. An
EMPTY positions list does not fail — it sizes: held_qty 0 so no sell is
possible, position_count 0, an empty correlation book (the 1.10x tier), sector
book value 0. The day trades up, on a book it cannot see.
"""

from __future__ import annotations

from orchestrator.ingest_guard import payload_fault


def _account(positions=None, long_market_value=0.0, **extra) -> dict:
    """account_state()'s shape, minus the keys this guard never reads."""
    return {"equity": 100_000.0, "cash": 30_000.0, "last_equity": 100_000.0,
            "daily_pnl_pct": 0.0, "prices": {},
            "positions": positions if positions is not None else {},
            "long_market_value": long_market_value, **extra}


# ---- a payload that lists positions is never this module's business --------

def test_a_populated_payload_is_trusted():
    assert payload_fault(_account({"NVDA": 40}, 8_594.0), {"NVDA": 40}) is None


def test_a_per_symbol_disagreement_is_not_a_fault_here():
    """The direction orchestrator/protection.py:352-357 already settled: the
    broker is the authority on what is held, and this lane does not reverse
    it. Records say 80, the broker shows 40 — assert_positions_accounted
    alerts once at the close and the day trades. Halting here would stop the
    fund on every hand-placed intervention those alerts exist to ask for."""
    assert payload_fault(_account({"NVDA": 40}, 8_594.0), {"NVDA": 80}) is None


# ---- an empty payload: the whole design is telling the two cases apart -----

def test_the_genuinely_empty_first_day_is_trusted():
    """The fund has never traded. No records, and the broker says it carries
    no long value. market/features.py's empty-book 1.10x tier is the CORRECT
    answer here (tests/test_features.py:335) and must keep being reached."""
    assert payload_fault(_account({}, 0.0), {}) is None


def test_a_flat_broker_is_trusted_even_when_the_records_disagree():
    """The false positive that a records-only detector would ship. An OTO stop
    leg has no `orders` row by construction (protection.py:347), so a fund
    stopped out overnight has non-empty records and an honestly empty book —
    the state tests/test_protection.py:530 pins as alert-once-and-keep-
    trading. Halting on it would halt every day after it too, because the
    condition is permanent until a human reconciles."""
    assert payload_fault(_account({}, 0.0), {"NVDA": 80}) is None


def test_an_empty_payload_on_a_funded_book_is_a_fault():
    """The issue-39 case. The broker reports long market value and lists no
    positions: the payload was lost, not empty."""
    fault = payload_fault(_account({}, 8_594.0), {"NVDA": 80})
    assert fault is not None
    assert "8594" in fault              # the operator needs the actual number
    assert "no positions" in fault.lower()


def test_a_lost_payload_is_a_fault_even_with_no_records_to_corroborate():
    """A position opened by hand has no `orders` row, so records cannot
    corroborate. The broker's own market value is enough on its own."""
    assert payload_fault(_account({}, 8_594.0), {}) is not None


# ---- the unreadable case: records are the tie-breaker ----------------------

def test_an_unreadable_market_value_with_records_is_a_fault():
    """Ambiguity resolves to no action (invariant 4): we cannot tell an empty
    book from a lost payload, and the fund's own orders say it holds
    something."""
    fault = payload_fault(_account({}, float("nan")), {"NVDA": 80})
    assert fault is not None
    assert "NVDA" in fault and "80" in fault


def test_a_missing_market_value_key_reads_as_unreadable():
    account = _account({}, 0.0)
    del account["long_market_value"]
    assert payload_fault(account, {"NVDA": 80}) is not None
    assert payload_fault(account, {}) is None


def test_an_unreadable_market_value_with_no_records_is_trusted():
    """The fund's first day must not be blocked by a broker that will not
    report a market value. Nothing anywhere suggests a book exists."""
    assert payload_fault(_account({}, float("nan")), {}) is None


def test_records_that_net_to_zero_or_below_are_not_a_book():
    """recorded_holdings signs by side and does not filter; a fully sold
    symbol nets to 0 and a mis-recorded one can net negative. Neither is a
    holding, and neither may block a day."""
    account = _account({}, float("nan"))
    assert payload_fault(account, {"NVDA": 0}) is None
    assert payload_fault(account, {"NVDA": -5}) is None
    assert payload_fault(account, {"NVDA": 0, "AAPL": 10}) is not None


def test_a_none_positions_value_reads_as_empty():
    """account.get('positions') can be absent or None on a degraded payload;
    both mean 'the list said nothing', which is the case this guards."""
    account = _account({}, 8_594.0)
    account["positions"] = None
    assert payload_fault(account, {}) is not None
