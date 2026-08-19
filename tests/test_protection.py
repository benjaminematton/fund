"""The 2026-08-19 missing-stop assertion. Every test here is a case that must
ALERT rather than pass quietly — a check that can pass while lying is the exact
failure of all three incidents, so 'fails closed' is a test, not a comment."""

import json

from orchestrator.protection import assert_positions_protected

NOW = "2026-08-19T20:05:00+00:00"


def _alerts(conn):
    return [json.loads(r["payload"])["text"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id").fetchall()]


class Broker:
    """A broker with an exact answer for both reads."""
    def __init__(self, positions=(), orders=()):
        self._positions, self._orders = list(positions), list(orders)

    def open_positions(self): return self._positions

    def open_orders(self): return self._orders


class SlowLeg(Broker):
    """A broker whose protective order only becomes visible on the second
    read — the real shape of an OTO child just after its parent fills."""
    def __init__(self, positions, orders):
        super().__init__(positions, orders)
        self.reads = 0

    def open_orders(self):
        self.reads += 1
        return self._orders if self.reads > 1 else []


def _long(symbol="NVDA", qty="80"):
    return {"symbol": symbol, "qty": qty, "side": "long"}


def _stop(symbol="NVDA", qty="80", type="stop"):
    return {"symbol": symbol, "side": "sell", "qty": qty, "type": type,
            "status": "new"}


def _promised(conn, *, symbol="NVDA", stop_price=215.0, qty=80,
              tid="a3f90000-0000-4000-8000-000000000001",
              submitted_at="2026-08-17T19:59:00+00:00",
              status="filled", filled_qty=None):
    """A filled buy order and the ticket behind it — the fund's own record of
    what it opened and what protection it promised. `stop_price=None` is the
    charter-sanctioned stopless buy (charters/pm.md:25).

    The ticket lands as 'consumed', the real post-execution state
    (state/transition.py:13 — open -> consumed | expired). There is no
    'filled' ticket state, and a fixture must not invent one."""
    # run_date from the order's own day: decisions is UNIQUE (run_date,
    # ticker), and a symbol re-bought later is by definition a different day.
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, stop_price, status, created_at) VALUES"
        " (?, ?, 'buy', ?, 't', 'i', ?, 'executed', ?)",
        (submitted_at[:10], symbol, qty, stop_price, submitted_at))
    conn.execute(
        "INSERT INTO tickets (id, decision_id, ticker, side, max_qty,"
        " stop_price, expires_at, status, created_at)"
        " VALUES (?, ?, ?, 'buy', ?, ?, ?, 'consumed', ?)",
        (tid, cur.lastrowid, symbol, qty, stop_price, submitted_at,
         submitted_at))
    conn.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " filled_qty, submitted_at) VALUES (?, ?, 'buy', ?, ?, ?, ?)",
        (tid, symbol, qty, status, qty if filled_qty is None else filled_qty,
         submitted_at))
    conn.commit()


def test_a_covered_position_is_silent(fund_db):
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [_stop()]), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_no_positions_is_silent(fund_db):
    n = assert_positions_protected(fund_db, broker=Broker(), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_a_promised_stop_that_is_gone_alerts(fund_db):
    """THE incident. NVDA 80 was ticketed with a stop at 215, the OTO leg
    expired at the bell on 2026-08-17, and for two sessions the database said
    'stop at 215' while the broker held no order at all. Nobody compared them.
    This is that comparison."""
    _promised(fund_db, stop_price=215.0)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 1
    assert "NVDA" in _alerts(fund_db)[0]
    assert "80" in _alerts(fund_db)[0]


def test_a_position_that_was_never_promised_a_stop_is_silent(fund_db):
    """charters/pm.md:25 makes a stopless buy sanctioned and normal — the PM
    passes stop_price only for a hard price invalidation. Alerting here would
    red the audit (scripts/audit_day.py:148) every day on a correct day, and
    an alert channel that cries wolf daily protects nothing. Standing exposure
    is state, not a fault; it belongs in the digest (next branch)."""
    _promised(fund_db, stop_price=None)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 0
    assert _alerts(fund_db) == []


def test_a_position_the_fund_has_no_record_of_alerts(fund_db):
    """No filled buy order for this symbol: a manual or pre-existing holding.
    Provenance unknown means the promise cannot be read, so it fails closed
    rather than being assumed sanctioned."""
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 1


def test_the_most_recent_buy_decides_the_promise(fund_db):
    """A symbol sold and re-bought without a stop is read on its CURRENT
    terms. Inheriting the older promise would alert forever on a position
    deliberately held stopless."""
    _promised(fund_db, stop_price=215.0,
              submitted_at="2026-08-17T19:59:00+00:00")
    _promised(fund_db, stop_price=None,
              tid="b4f90000-0000-4000-8000-000000000002",
              submitted_at="2026-08-19T14:00:00+00:00")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW)
    assert n == 0


def test_a_partial_fill_that_was_canceled_still_counts_as_opening_it(fund_db):
    """orchestrator/reconcile.py:197-206 records a timed-out partial as
    CANCELED with filled_qty > 0, commenting that filled_qty > 0 is a REAL
    position. Matching only on status='filled' would call that position
    unknown-provenance and print an alert claiming the fund has no record of
    opening it — which is false."""
    _promised(fund_db, stop_price=215.0, status="canceled", filled_qty=30)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="30")], []), now_iso=NOW)
    assert n == 1
    assert "stop at 215" in _alerts(fund_db)[0]
    assert "no fund record" not in _alerts(fund_db)[0]


def test_a_sell_limit_is_not_protection(fund_db):
    """A take-profit caps the upside, not the loss. Treating it as protection
    would report the position safe while it is entirely exposed downward."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [_stop(type="limit")]), now_iso=NOW)
    assert n == 1


def test_a_stop_for_fewer_shares_than_held_alerts(fund_db):
    """Partial cover is not cover: 30 of 80 shares stopped leaves 50 naked."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="80")], [_stop(qty="30")]),
        now_iso=NOW)
    assert n == 1
    assert "only 30 of 80" in _alerts(fund_db)[0]


def test_stops_across_several_orders_sum(fund_db):
    """Two stop orders of 40 do protect 80 — denying that would be a false
    alarm that trains the reader to ignore the channel."""
    _promised(fund_db)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="80")],
                               [_stop(qty="40"), _stop(qty="40")]),
        now_iso=NOW)
    assert n == 0


def test_a_stop_on_another_symbol_does_not_count(fund_db):
    _promised(fund_db, symbol="NVDA")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long("NVDA")], [_stop("MSFT")]), now_iso=NOW)
    assert n == 1


def test_a_covered_symbol_and_a_naked_one_alert_exactly_once(fund_db):
    """A mixed book is where cross-symbol matching bugs show up: MSFT's stop
    must not cover NVDA, and NVDA's absence must not implicate MSFT."""
    _promised(fund_db, symbol="NVDA")
    _promised(fund_db, symbol="MSFT",
              tid="c5f90000-0000-4000-8000-000000000003")
    n = assert_positions_protected(
        fund_db,
        broker=Broker([_long("NVDA"), _long("MSFT")], [_stop("MSFT")]),
        now_iso=NOW)
    assert n == 1
    assert "NVDA" in _alerts(fund_db)[0]
    assert "MSFT" not in _alerts(fund_db)[0]


def test_a_buy_order_does_not_protect_a_long(fund_db):
    """Only the closing side protects. A resting BUY adds risk."""
    _promised(fund_db)
    order = _stop()
    order["side"] = "buy"
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [order]), now_iso=NOW)
    assert n == 1


def test_stop_limit_and_trailing_stop_both_count(fund_db):
    _promised(fund_db)
    for kind in ("stop_limit", "trailing_stop"):
        before = len(_alerts(fund_db))
        n = assert_positions_protected(
            fund_db, broker=Broker([_long()], [_stop(type=kind)]), now_iso=NOW)
        assert n == 0, f"{kind} was not counted as protection"
        assert len(_alerts(fund_db)) == before


def test_an_unreadable_position_qty_alerts(fund_db):
    """Unreadable is checked BEFORE the promise lookup: a position whose qty
    is gibberish cannot be classified either way."""
    _promised(fund_db, stop_price=None)
    n = assert_positions_protected(
        fund_db, broker=Broker([_long(qty="eighty")], []), now_iso=NOW)
    assert n == 1


def test_a_short_position_alerts(fund_db):
    """The fund is long-only; stops guard new or added longs (state/models.py).
    A short at the broker is a position this check cannot classify, so it
    fails closed rather than deciding what protects it."""
    _promised(fund_db, stop_price=None)
    position = _long()
    position["side"] = "short"
    n = assert_positions_protected(
        fund_db, broker=Broker([position], []), now_iso=NOW)
    assert n == 1


def test_an_unreadable_order_alerts_rather_than_being_skipped(fund_db):
    """An order whose qty will not parse might be the protective one. Skipping
    it would silently downgrade cover; the position is reported unproven. This
    fires even on a never-promised position: 'I could not read the book' is a
    different statement from 'nothing was promised'."""
    _promised(fund_db, stop_price=None)
    bad = _stop(qty="many")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], [bad]), now_iso=NOW)
    assert n == 1


def test_a_positions_read_that_raises_alerts(fund_db):
    class Down(Broker):
        def open_positions(self): raise ConnectionError("broker down")
    n = assert_positions_protected(fund_db, broker=Down(), now_iso=NOW)
    assert n == 1
    assert "ConnectionError" in _alerts(fund_db)[0]


def test_an_orders_read_that_raises_alerts(fund_db):
    """Positions read fine, orders did not — so cover is UNKNOWN, which must
    never read as covered. The message carries the broker's own words, not
    just the exception type: '401 unauthorized' is actionable at 16:05."""
    class Down(Broker):
        def open_orders(self): raise ConnectionError("401 unauthorized")
    n = assert_positions_protected(
        fund_db, broker=Down([_long()]), now_iso=NOW)
    assert n == 1
    assert "ConnectionError" in _alerts(fund_db)[0]
    assert "401 unauthorized" in _alerts(fund_db)[0]


def test_a_missing_broker_alerts(fund_db):
    """A None broker in production is a wiring bug. It must scream, not skip:
    'no broker, so no check, so no alert' is the silent pass this whole module
    exists to make impossible."""
    n = assert_positions_protected(fund_db, broker=None, now_iso=NOW)
    assert n == 1


def test_it_never_raises_no_matter_how_broken_the_broker_is(fund_db):
    """Invariant 4: the assertion runs at the end of a real trading day. It
    reports, it does not take the day down."""
    class Chaos:
        def open_positions(self): raise RuntimeError("boom")
        def open_orders(self): raise RuntimeError("boom")
    assert assert_positions_protected(fund_db, broker=Chaos(), now_iso=NOW) == 1


def test_one_alert_per_unprotected_position(fund_db):
    _promised(fund_db, symbol="NVDA")
    _promised(fund_db, symbol="MSFT",
              tid="c5f90000-0000-4000-8000-000000000003")
    n = assert_positions_protected(
        fund_db, broker=Broker([_long("NVDA"), _long("MSFT")], []), now_iso=NOW)
    assert n == 2
    assert len(_alerts(fund_db)) == 2


# ---- the re-read: an OTO leg is created 'held' and can lag its parent ----

def test_a_leg_that_appears_on_the_second_read_is_not_an_alert(fund_db):
    """Without this, the fund alerts on every position it correctly protects,
    on every day it actually trades — the alert channel would be dead inside
    a week."""
    _promised(fund_db)
    naps = []
    n = assert_positions_protected(
        fund_db, broker=SlowLeg([_long()], [_stop()]), now_iso=NOW,
        sleep=naps.append)
    assert n == 0
    assert _alerts(fund_db) == []
    assert naps == [3.0]


def test_a_leg_that_is_still_missing_after_the_re_read_alerts(fund_db):
    _promised(fund_db)
    naps = []
    n = assert_positions_protected(
        fund_db, broker=Broker([_long()], []), now_iso=NOW, sleep=naps.append)
    assert n == 1
    assert naps == [3.0]


def test_the_re_read_waits_once_per_run_not_once_per_position(fund_db):
    """Three naked positions must not become three sequential waits inside a
    live trading day."""
    for i, sym in enumerate(("NVDA", "MSFT", "AAPL")):
        _promised(fund_db, symbol=sym,
                  tid=f"d{i}f90000-0000-4000-8000-00000000000{i}")
    naps = []
    n = assert_positions_protected(
        fund_db,
        broker=Broker([_long("NVDA"), _long("MSFT"), _long("AAPL")], []),
        now_iso=NOW, sleep=naps.append)
    assert n == 3
    assert naps == [3.0]
