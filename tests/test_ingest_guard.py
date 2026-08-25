"""Offline tests for the ingestion guard on the broker's positions payload
(issue #39).

The defect: scripts/run_day.py reads the account once, at the top of the day,
and every gate input for every ticker is computed from that one payload. An
EMPTY positions list does not fail — it sizes: held_qty 0 so no sell is
possible, position_count 0, an empty correlation book (the 1.10x tier), sector
book value 0. The day trades up, on a book it cannot see.
"""

from __future__ import annotations

from orchestrator.ingest_guard import account_snapshot, payload_fault


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


# ---- account_snapshot: read, nap once, re-read, refuse ---------------------

import json

import pytest

NOW = "2026-08-25T13:30:00+00:00"


class _Source:
    """A broker that answers account_state() from a scripted queue. The queue
    is popped per call, so a test can hand a stale payload first and a settled
    one second — the resumed-day settle lag account_snapshot naps for."""

    def __init__(self, *states):
        self.states = list(states)
        self.calls = 0

    def account_state(self) -> dict:
        self.calls += 1
        return self.states[min(self.calls - 1, len(self.states) - 1)]


@pytest.fixture
def naps():
    recorded: list[float] = []
    return recorded, recorded.append


def _alerts(conn) -> list[dict]:
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def _filled_buy(conn, symbol="NVDA", qty=80,
                tid="a3f90000-0000-4000-8000-000000000001",
                submitted_at="2026-08-17T19:59:00+00:00"):
    """A filled buy and the ticket behind it. `orders.client_order_id`
    REFERENCES tickets(id) with foreign keys ON (state/db.py:22), so an order
    row cannot exist without one — this mirrors tests/test_protection.py's
    _promised for the same reason."""
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " (?, ?, 'buy', ?, 't', 'i', NULL, 'executed', ?)",
        (submitted_at[:10], symbol, qty, submitted_at))
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " stop_price, expires_at, status, created_at)"
        " VALUES (?, ?, ?, 'buy', ?, NULL, ?, 'consumed', ?)",
        (tid, cur.lastrowid, symbol, qty, submitted_at, submitted_at))
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " filled_qty, submitted_at) VALUES (?, ?, 'buy', ?, 'filled', ?, ?)",
        (tid, symbol, qty, qty, submitted_at))
    conn.commit()


def test_a_trusted_payload_is_returned_without_napping(fund_db, naps):
    """The happy path pays nothing for the retry: one broker read, no wait."""
    recorded, sleep = naps
    source = _Source(_account({"NVDA": 40}, 8_594.0))
    assert account_snapshot(fund_db, source=source, now_iso=NOW,
                            sleep=sleep) is source.states[0]
    assert source.calls == 1
    assert recorded == []
    assert _alerts(fund_db) == []


def test_the_genuinely_empty_first_day_is_returned(fund_db, naps):
    """No orders, no long market value, no positions — the fund's first day.
    It must reach the stages, or the 1.10x empty-book tier can never be used."""
    recorded, sleep = naps
    source = _Source(_account({}, 0.0))
    assert account_snapshot(fund_db, source=source, now_iso=NOW,
                            sleep=sleep) is source.states[0]
    assert _alerts(fund_db) == []


def test_a_payload_that_settles_on_the_re_read_returns_the_FRESH_account(
        fund_db, naps):
    """A buy that just filled is recorded before the broker lists the
    position. The point of the re-read is not merely to avoid the alert — it
    is that the day must then be sized on the payload that HAS the position,
    never on the stale empty one that passed the second look by luck."""
    recorded, sleep = naps
    _filled_buy(fund_db)
    stale, fresh = _account({}, 8_594.0), _account({"NVDA": 80}, 17_188.0)
    source = _Source(stale, fresh)
    assert account_snapshot(fund_db, source=source, now_iso=NOW,
                            sleep=sleep) is fresh
    assert source.calls == 2
    assert recorded == [3.0]
    assert _alerts(fund_db) == []


def test_a_payload_still_lost_after_the_re_read_refuses_the_day(fund_db, naps):
    """Issue #39's case (a): the DB has filled positions, the broker returns
    no positions, and the account is not flat. HOLD, with an alert that names
    itself so scripts/file_alert_issues.py can key an issue on it."""
    recorded, sleep = naps
    _filled_buy(fund_db)
    source = _Source(_account({}, 8_594.0))
    assert account_snapshot(fund_db, source=source, now_iso=NOW,
                            sleep=sleep) is None
    assert source.calls == 2
    assert recorded == [3.0]
    payloads = _alerts(fund_db)
    assert len(payloads) == 1
    assert payloads[0]["code"] == "positions_payload_lost"
    assert "nothing traded" in payloads[0]["text"]


def test_a_broker_read_that_raises_is_not_swallowed(fund_db, naps):
    """Unlike protection.py, this one must NOT turn a broker failure into an
    alert-and-continue: the day has not started, and scripts/run_day.py's
    guarded() already turns a raise here into an alert, a drain and exit 1 —
    which is the same HOLD, recorded under the code that fits it."""
    _, sleep = naps

    class Dead:
        def account_state(self):
            raise ConnectionError("alpaca trading api unreachable")

    with pytest.raises(ConnectionError):
        account_snapshot(fund_db, source=Dead(), now_iso=NOW, sleep=sleep)


def test_the_sleep_argument_is_optional(fund_db):
    """An offline caller passes nothing and still gets the retry path — the
    same two reads and the same verdict — with a no-op nap.

    Named for what it checks. It does NOT prove no wall-clock wait occurred,
    and no unit test here can: that property is enforced statically instead,
    by scripts/check_purity.py, which forbids time.sleep() anywhere in
    orchestrator/. The default literally cannot be a real sleep."""
    _filled_buy(fund_db)
    source = _Source(_account({}, 8_594.0))
    assert account_snapshot(fund_db, source=source, now_iso=NOW) is None
    assert source.calls == 2        # the retry ran, with the default nap
