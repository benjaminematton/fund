"""Issue #40: the order that landed at the broker and never reached SQLite.

The `orders` row is written submit-then-write, by a PostToolUse hook on the
place_* response. When that response never arrives (gateway 504) or comes back
as the duplicate-client_order_id 422, `_extract_order` returns None and NOTHING
is written — no order row, no ticket CAS. `reconcile_orders` selects `FROM
orders`, so it cannot see what has no row, and the books permanently record
that nothing traded while the broker holds a fill.

These tests pin the repair pass: `recover_lost_orders` asks the broker about
every still-open ticket with no order row, records what matches at
'submitted', and lets the already-tested `reconcile_orders` drive everything
else."""

import asyncio
import json

from agents.runtime import make_order_recorder
from gate.tickets import expire_open_tickets
from orchestrator.clock import iso
from orchestrator.daily import StageCtx, run_day
from orchestrator.reconcile import (_recoverable_qty, recover_lost_orders,
                                    reconcile_stage)
from slackkit.fake import FakeSlack
from tests.conftest import make_executor
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import LATER, TID, TID2, _seed, order


def _codes(conn):
    return [json.loads(r["payload"]).get("code") for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]


def _counts(conn):
    n = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
    fills = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"]
    return n, fills


class _Holding:
    """Broker holding exactly one order under `coid`, shape as the real wire
    has it: every numeric a STRING."""

    def __init__(self, coid=TID, **over):
        self.o = {"id": "alp-0001", "client_order_id": coid, "symbol": "NVDA",
                  "side": "buy", "qty": "67", "status": "accepted",
                  "filled_qty": "0", "filled_avg_price": None}
        self.o.update(over)
        self.coid = coid

    def get_order_by_client_order_id(self, coid):
        return dict(self.o) if coid == self.coid else None


class _Unreachable:
    def get_order_by_client_order_id(self, coid):
        raise ConnectionError("broker unreachable")


class _NoLookup:
    """A port with no get_order_by_client_order_id at all — the shape several
    existing test doubles (_FlatBroker, _NakedBroker, _QuietSource) have."""

    def open_positions(self):
        return []


# --- the headline case ------------------------------------------------------

def test_lost_place_response_is_recovered_and_settles(fund_db, sim_clock):
    """The 504, reproduced through the real hook path: the order is placed
    DIRECTLY at the broker (the request landed, no response came back, so the
    PostToolUse recorder never ran), then the seat retries and Alpaca 422s the
    duplicate client_order_id. The recorder writes nothing. reconcile_stage
    must recover the order and settle it exactly like a normal one."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14})
    broker.place_order(order())                       # the placement that landed

    execute = make_executor(lambda: fund_db, sim_clock, broker)
    resp = execute("mcp__alpaca__place_stock_order", order())   # the 422 retry
    rec = make_order_recorder(lambda: fund_db, sim_clock)
    asyncio.run(rec({"tool_name": "mcp__alpaca__place_stock_order",
                     "tool_input": order(), "tool_response": resp}, "t1", None))

    # The bug: the broker holds the order and SQLite records nothing.
    assert _counts(fund_db) == (0, 0)
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"
    assert broker.orders[TID]["status"] == "accepted"

    n = reconcile_stage(fund_db, clock=sim_clock, broker=broker,
                        sleep=lambda s: broker.tick(), poll_s=3.0,
                        max_wait_s=90.0)
    assert n == 1
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert (o["client_order_id"], o["symbol"], o["side"], o["qty"]) == (
        TID, "NVDA", "buy", 67)
    assert o["status"] == "filled"
    assert o["filled_qty"] == 67 and o["filled_avg_price"] == 180.14
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "consumed"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "executed"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 1
    assert "order_recovered" in _codes(fund_db)


# --- fail-closed paths ------------------------------------------------------

def test_no_broker_recovers_nothing_silently(fund_db, sim_clock):
    _seed(fund_db)
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=None) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == []
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


def test_port_without_the_lookup_method_recovers_nothing_silently(fund_db, sim_clock):
    """Feature-detected, not assumed: several existing doubles are position-
    only ports and would AttributeError."""
    _seed(fund_db)
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_NoLookup()) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == []


def test_raising_broker_alerts_and_leaves_the_ticket_open(fund_db, sim_clock):
    _seed(fund_db)
    assert recover_lost_orders(fund_db, clock=sim_clock,
                               broker=_Unreachable()) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_failed"]
    alert = fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert "ConnectionError" in alert["payload"]
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


def test_broker_never_got_the_order_is_a_silent_skip(fund_db, sim_clock):
    """The ordinary hold day: the turn placed nothing. The execution stage's
    ticket_open_after_exec alert already reports it; a second alert here would
    double-post on every such day."""
    _seed(fund_db)

    class _Empty:
        def get_order_by_client_order_id(self, coid):
            return None

    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_Empty()) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == []
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


def test_order_over_the_tickets_max_qty_is_a_mismatch(fund_db, sim_clock):
    """Recording it would put an unauthorized trade in the books."""
    _seed(fund_db)
    broker = _Holding(qty="99")
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=broker) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


def test_wrong_symbol_order_is_a_mismatch(fund_db, sim_clock):
    _seed(fund_db)
    broker = _Holding(symbol="AMD")
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=broker) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]


def test_fractional_qty_is_a_mismatch_not_floored(fund_db, sim_clock):
    """This fund is whole-share only and orders.qty is INTEGER."""
    _seed(fund_db)
    assert _recoverable_qty({"symbol": "NVDA", "side": "buy", "qty": "1.5"},
                            {"ticker": "NVDA", "side": "buy", "max_qty": 67}) is None
    broker = _Holding(qty="1.5")
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=broker) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]


# --- what _recoverable_qty accepts, field by field --------------------------

_TICKET = {"id": TID, "ticker": "NVDA", "side": "buy", "max_qty": 67}


def _o(**over):
    """A broker order that matches _TICKET, before the override under test."""
    base = {"id": "alp-0001", "client_order_id": TID, "symbol": "NVDA",
            "side": "buy", "qty": "67"}
    base.update(over)
    return base


def test_only_this_tickets_own_order_is_recoverable(fund_db, sim_clock):
    """client_order_id IS the ticket id (invariant 5) and is the key the
    lookup was made on — so it is checked, not assumed. An adapter that
    answers with some OTHER order (a stop leg, a fuzzy match) would otherwise
    get that order recorded under this ticket, with the wrong
    alpaca_order_id."""
    assert _recoverable_qty(_o(), _TICKET) == 67
    assert _recoverable_qty(_o(client_order_id="SOMEONE-ELSE"), _TICKET) is None
    unkeyed = _o()
    del unkeyed["client_order_id"]
    assert _recoverable_qty(unkeyed, _TICKET) is None   # cannot prove which order

    _seed(fund_db)
    broker = _Holding(client_order_id="a3f90000-0000-4000-8000-0000000000ff")
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=broker) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


def test_unparseable_qty_is_a_mismatch_and_never_raises(fund_db, sim_clock):
    """The docstring promises None, so 'nan' and 'inf' must deny, not raise:
    a raise reaches the caller's blanket except and files
    order_recovery_failed — 'could not be checked against the broker' — for a
    payload that was checked fine and simply did not match."""
    for bad in ("nan", "NaN", "inf", "-inf", "-1", ""):
        assert _recoverable_qty(_o(qty=bad), _TICKET) is None

    _seed(fund_db)
    assert recover_lost_orders(fund_db, clock=sim_clock,
                               broker=_Holding(qty="nan")) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]


def test_qty_coercion_is_no_laxer_than_its_sibling_coercers(fund_db, sim_clock):
    """gate/tickets.py:_as_share_count rejects bool and requires .isdigit();
    orchestrator/protection.py:_qty rejects bool. A bool reaching here would
    record a 1-share trade nobody placed."""
    assert _recoverable_qty(_o(qty=67), _TICKET) == 67       # the plain case
    assert _recoverable_qty(_o(qty=True), _TICKET) is None
    assert _recoverable_qty(_o(qty="  67  "), _TICKET) is None
    assert _recoverable_qty(_o(qty="6_7"), _TICKET) is None

    _seed(fund_db)
    assert recover_lost_orders(fund_db, clock=sim_clock,
                               broker=_Holding(qty=True)) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == ["order_recovery_mismatch"]
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "open"


# --- the write must actually land -------------------------------------------

def test_dropped_insert_neither_consumes_the_ticket_nor_claims_success(
        fund_db, sim_clock):
    """orders.alpaca_order_id is TEXT UNIQUE. When the broker's id is already
    in the books the INSERT OR IGNORE is dropped silently — and consuming the
    ticket on that would remove it from open_tickets_without_orders FOREVER,
    leaving a live broker order with no row anywhere, while the alert claims
    it was recorded. Fail closed: alert, leave the ticket open."""
    _seed(fund_db)                                   # TID / NVDA, still open
    _seed(fund_db, tid=TID2, ticker="AMD")           # TID2 already recorded
    fund_db.execute("UPDATE tickets SET status='consumed' WHERE id=?", (TID2,))
    fund_db.execute(
        "INSERT INTO orders (client_order_id, alpaca_order_id, symbol, side,"
        " qty, status, submitted_at) VALUES (?,?,'AMD','buy',67,'submitted',?)",
        (TID2, "alp-0001", iso(sim_clock.now())))     # same id _Holding returns
    fund_db.commit()

    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_Holding()) == 0
    assert [r["client_order_id"] for r in fund_db.execute(
        "SELECT client_order_id FROM orders")] == [TID2]
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID,)).fetchone()["status"] == "open"
    codes = _codes(fund_db)
    assert codes == ["order_recovery_unwritable"]
    assert "order_recovered" not in codes


# --- one issue per ticket, not one per code ---------------------------------

def _alerts(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind='alert' ORDER BY id")]


def test_two_mismatching_tickets_alert_with_their_own_ticker(fund_db, sim_clock):
    """scripts/file_alert_issues.py groups on ("alert:{code}",
    "ticker:{ticker}") and SKIPS a group whose labels already have an open
    issue. With no ticker= the second ticket's mismatch is filed as a
    duplicate of the first and silently dropped — on the one path meant to
    catch an order the fund cannot account for."""
    _seed(fund_db)                                   # NVDA
    _seed(fund_db, tid=TID2, ticker="AMD")           # AMD

    class _OverCap:
        def get_order_by_client_order_id(self, coid):
            return {"id": f"alp-{coid[-2:]}", "client_order_id": coid,
                    "symbol": "NVDA" if coid == TID else "AMD", "side": "buy",
                    "qty": "99", "status": "accepted", "filled_qty": "0",
                    "filled_avg_price": None}

    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_OverCap()) == 0
    alerts = _alerts(fund_db)
    assert [a["code"] for a in alerts] == ["order_recovery_mismatch"] * 2
    assert sorted(a["ticker"] for a in alerts) == ["AMD", "NVDA"]


def test_recovered_and_failed_alerts_both_carry_their_ticker(fund_db, sim_clock):
    _seed(fund_db)                                   # NVDA — the broker raises
    _seed(fund_db, tid=TID2, ticker="AMD")           # AMD  — recovers

    class _RaisesOnTheFirst:
        def get_order_by_client_order_id(self, coid):
            if coid == TID:
                raise TimeoutError("gateway timeout")
            return {"id": "alp-0002", "client_order_id": TID2, "symbol": "AMD",
                    "side": "buy", "qty": "67", "status": "accepted",
                    "filled_qty": "0", "filled_avg_price": None}

    assert recover_lost_orders(fund_db, clock=sim_clock,
                               broker=_RaisesOnTheFirst()) == 1
    by_code = {a["code"]: a for a in _alerts(fund_db)}
    assert by_code["order_recovery_failed"]["ticker"] == "NVDA"
    assert by_code["order_recovered"]["ticker"] == "AMD"


# --- the CAS gate ------------------------------------------------------------

class _ConsumesTheTicketMidWrite:
    """A connection that consumes the ticket between the order INSERT and the
    ticket CAS. Nothing inside one process can move a ticket under this loop —
    the predicate selected it as 'open' and only this loop touches it — so a
    concurrent writer is the only way to reach the losing branch of the CAS,
    and this stands in for one. Delegates everything else to the real
    connection; recover_lost_orders and everything it calls reach SQLite only
    through .execute/.commit."""

    def __init__(self, conn, tid):
        self._conn, self._tid, self._armed = conn, tid, True

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        if self._armed and sql.startswith("INSERT OR IGNORE INTO orders"):
            self._armed = False
            self._conn.execute("UPDATE tickets SET status='consumed'"
                               " WHERE id=?", (self._tid,))
            self._conn.commit()
        return cur

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_alert_and_count_fire_only_when_the_ticket_cas_wins(fund_db, sim_clock):
    """The recovery alert says a human should look at a recorder that failed,
    and the return value promises it cannot over-count. Both hang off the CAS
    winning — so a run that loses the CAS must announce nothing and count
    nothing, even though its INSERT landed."""
    _seed(fund_db)
    conn = _ConsumesTheTicketMidWrite(fund_db, TID)
    assert recover_lost_orders(conn, clock=sim_clock, broker=_Holding()) == 0
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1   # the row landed
    assert "order_recovered" not in _codes(fund_db)


# --- scope ------------------------------------------------------------------

def test_consumed_ticket_is_never_revisited(fund_db, sim_clock):
    _seed(fund_db)
    fund_db.execute("UPDATE tickets SET status='consumed'")
    fund_db.commit()
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_Holding()) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == []


def test_expired_ticket_is_left_alone(fund_db, sim_clock):
    """There is no legal edge out of 'expired', so an expired ticket is out of
    scope by construction — recording an order against it could never CAS."""
    _seed(fund_db)
    assert expire_open_tickets(fund_db, LATER) == [TID]
    assert recover_lost_orders(fund_db, clock=sim_clock, broker=_Holding()) == 0
    assert _counts(fund_db) == (0, 0)
    assert _codes(fund_db) == []
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "expired"


# --- idempotency and isolation ---------------------------------------------

def test_second_run_recovers_nothing_and_posts_no_second_alert(fund_db, sim_clock):
    """Invariant 5: INSERT OR IGNORE + a CAS-gated alert. On the second run the
    predicate's own result set is empty anyway."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14})
    broker.place_order(order())
    reconcile_stage(fund_db, clock=sim_clock, broker=broker,
                    sleep=lambda s: broker.tick(), poll_s=3.0, max_wait_s=90.0)
    before_events = fund_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    before_codes = _codes(fund_db)

    assert reconcile_stage(fund_db, clock=sim_clock, broker=broker,
                           sleep=lambda s: broker.tick(), poll_s=3.0,
                           max_wait_s=90.0) == 0
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 1
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 1
    assert fund_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == before_events
    assert _codes(fund_db) == before_codes
    assert before_codes.count("order_recovered") == 1


def test_one_raising_ticket_does_not_stop_the_next(fund_db, sim_clock):
    """Per-ticket isolation: the try/except is inside the loop."""
    _seed(fund_db)
    _seed(fund_db, tid=TID2, run_date="2026-07-07")

    class _RaisesOnFirst:
        def get_order_by_client_order_id(self, coid):
            if coid == TID:
                raise TimeoutError("gateway timeout")
            return {"id": "alp-0002", "client_order_id": TID2, "symbol": "NVDA",
                    "side": "buy", "qty": "67", "status": "accepted",
                    "filled_qty": "0", "filled_avg_price": None}

    assert recover_lost_orders(fund_db, clock=sim_clock,
                               broker=_RaisesOnFirst()) == 1
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID,)).fetchone()["status"] == "open"
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID2,)).fetchone()["status"] == "consumed"
    rows = fund_db.execute("SELECT client_order_id FROM orders").fetchall()
    assert [r["client_order_id"] for r in rows] == [TID2]
    assert _codes(fund_db) == ["order_recovery_failed", "order_recovered"]


# --- the semantic: a recovered order is not a second class of order ---------

class _Rejected:
    """The broker rejected both orders. cancel_order is a no-op (a rejected
    order is already dead), so the timeout path's re-query is what settles."""

    def __init__(self):
        self.cancel_attempts = []

    def get_order_by_client_order_id(self, coid):
        return {"id": f"alp-{coid[-1]}", "client_order_id": coid,
                "symbol": "NVDA", "side": "buy", "qty": "67",
                "status": "rejected", "filled_qty": "0",
                "filled_avg_price": None}

    def cancel_order(self, coid):
        self.cancel_attempts.append(coid)


def test_recovered_rejection_settles_exactly_like_a_recorded_one(fund_db, sim_clock):
    """The recovery pass records at 'submitted' even when the broker's order is
    already rejected, and lets reconcile_orders drive it through the state
    machine. Two tickets, same broker answer, one recovered and one recorded
    normally: the rows must end IDENTICAL. Two classes of order row would force
    anyone defining retry semantics later to handle both."""
    _seed(fund_db)                                       # TID: recovered
    _seed(fund_db, tid=TID2, run_date="2026-07-07")      # TID2: recorded normally
    fund_db.execute("UPDATE tickets SET status='consumed' WHERE id=?", (TID2,))
    fund_db.execute(
        "INSERT INTO orders (client_order_id, symbol, side, qty, status,"
        " submitted_at) VALUES (?,'NVDA','buy',67,'submitted',?)",
        (TID2, iso(sim_clock.now())))
    fund_db.commit()

    reconcile_stage(fund_db, clock=sim_clock, broker=_Rejected(),
                    sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)

    rows = {r["client_order_id"]: r for r in
            fund_db.execute("SELECT * FROM orders")}
    assert set(rows) == {TID, TID2}
    fields = ("status", "symbol", "side", "qty", "filled_qty",
              "filled_avg_price")
    assert ([rows[TID][f] for f in fields] == [rows[TID2][f] for f in fields])
    assert rows[TID]["status"] == "rejected"
    decisions = [r["status"] for r in fund_db.execute(
        "SELECT status FROM decisions ORDER BY id")]
    assert decisions == ["failed", "failed"]
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID,)).fetchone()["status"] == "consumed"


# --- the wiring: still exactly one stage ------------------------------------

def test_run_days_reconciliation_stage_recovers_the_lost_order(fund_db, sim_clock):
    """The repair only helps if the day actually runs it: run_day's single
    reconciliation stage must be reconcile_stage, not reconcile_orders."""
    _seed(fund_db)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14}, mode="instant")
    broker.place_order(order())
    ctx = StageCtx(conn=fund_db, run_date="2026-07-06", clock=sim_clock,
                   slack=FakeSlack(), market_inputs={},
                   research_seats=("analyst",))
    run_day(ctx, execution_turn=None, broker=broker, sleep=lambda s: None)
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o is not None and o["client_order_id"] == TID
    assert o["status"] == "filled" and o["qty"] == 67
    assert fund_db.execute("SELECT status FROM tickets").fetchone()["status"] == "consumed"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "executed"
    stages = [r["stage"] for r in fund_db.execute(
        "SELECT stage FROM checkpoints WHERE stage LIKE 'reconcil%'")]
    assert stages == ["reconciliation"]
