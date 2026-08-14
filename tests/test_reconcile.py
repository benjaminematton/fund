from orchestrator.clock import iso
from orchestrator.reconcile import _apply, reconcile_orders
from state.transition import try_transition
from tests.fake_alpaca import FakeAlpaca
from tests.test_tickets import TID, _seed

def _submitted_order(conn, now, qty=67):
    conn.execute("INSERT INTO orders (client_order_id, symbol, side, qty,"
                 " status, submitted_at) VALUES (?,?,?,?,'submitted',?)",
                 (TID, "NVDA", "buy", qty, now))
    conn.commit()

def _poll(conn, clock, broker, ticks_per_sleep=1):
    sleeps = []
    def sleep(s):
        sleeps.append(s)
        for _ in range(ticks_per_sleep): broker.tick()
    n = reconcile_orders(conn, clock=clock, broker=broker, sleep=sleep,
                         poll_s=3.0, max_wait_s=90.0)
    return n, sleeps

def test_fill_lands_and_projects(fund_db, sim_clock):
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14})
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    n, _ = _poll(fund_db, sim_clock, broker)
    assert n == 1
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "filled" and o["filled_avg_price"] == 180.14
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "executed"
    ev = fund_db.execute("SELECT kind FROM events ORDER BY id DESC").fetchone()
    assert ev["kind"] == "fill"

def test_never_fills_within_cap_decision_failed_alert(fund_db, sim_clock):
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    n, sleeps = _poll(fund_db, sim_clock, broker)
    assert n == 0 and len(sleeps) == 30            # 90s / 3s
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "canceled"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "failed"
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='alert'").fetchone()["c"] == 1
    # Broker-shape assertion (owner ruling): an unfilled order's
    # filled_avg_price comes back as real None, never the string "None".
    o = broker.get_order_by_client_order_id(TID)
    assert o["filled_avg_price"] is None

def test_partial_fill_left_submitted_with_alert(fund_db, sim_clock):
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now, qty=10)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="partial")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 10})
    def one_tick(s): broker.tick()
    reconcile_orders(fund_db, clock=sim_clock, broker=broker, sleep=one_tick,
                     poll_s=3.0, max_wait_s=3.0)   # one poll then stop
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "partially_filled"
    assert fund_db.execute("SELECT COUNT(*) c FROM events WHERE kind='alert'").fetchone()["c"] == 1

def test_broker_error_fails_closed_no_transition(fund_db, sim_clock):
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    class Boom:
        def get_order_by_client_order_id(self, coid): raise ConnectionError()
    reconcile_orders(fund_db, clock=sim_clock, broker=Boom(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=6.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"

def test_malformed_fill_none_values_leaves_submitted_no_transition(fund_db, sim_clock):
    """CRITICAL regression: broker says 'filled' but the numbers are None
    (a real observed shape). Must not commit filled before parsing them."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    class NullFillBroker:
        def get_order_by_client_order_id(self, coid):
            return {"status": "filled", "filled_qty": None, "filled_avg_price": None}
    reconcile_orders(fund_db, clock=sim_clock, broker=NullFillBroker(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0

def test_malformed_fill_missing_keys_leaves_submitted_no_transition(fund_db, sim_clock):
    """Same CRITICAL regression, but the broker omits the fill keys entirely."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    class BareFillBroker:
        def get_order_by_client_order_id(self, coid):
            return {"status": "filled"}
    reconcile_orders(fund_db, clock=sim_clock, broker=BareFillBroker(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0

def test_broker_unreachable_at_cap_alerts_loudly(fund_db, sim_clock):
    """Fix 2/3: the fail-closed skip must not be silent, and must name the
    exception type so a broken adapter is diagnosable."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    class Boom:
        def get_order_by_client_order_id(self, coid): raise ConnectionError()
    reconcile_orders(fund_db, clock=sim_clock, broker=Boom(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=6.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    alert = fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None
    assert "ConnectionError" in alert["payload"]
    assert "unresolved" in alert["payload"]

def test_apply_returns_false_on_cas_noop_already_filled(fund_db, sim_clock):
    """Fix 5: _apply must not report a fill when another writer already
    terminalized the order — the CAS no-op path, actually exercised."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    row = fund_db.execute(
        "SELECT client_order_id, symbol, side FROM orders").fetchone()
    assert try_transition(fund_db, "orders", {"client_order_id": row["client_order_id"]},
                          "submitted", "filled", now)
    before = fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"]
    o = {"status": "filled", "filled_qty": "67", "filled_avg_price": "180.14"}
    moved = _apply(fund_db, row, o, now)
    assert moved is False
    after = fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"]
    assert after == before

def test_malformed_fill_non_positive_qty_and_price_fails_closed(fund_db, sim_clock):
    """Fix 2: {"status":"filled","filled_qty":"0","filled_avg_price":"0"} is a
    malformed payload, not a real 0@0 fill — must not terminalize the order."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    class ZeroFillBroker:
        def get_order_by_client_order_id(self, coid):
            return {"status": "filled", "filled_qty": "0", "filled_avg_price": "0"}
    reconcile_orders(fund_db, clock=sim_clock, broker=ZeroFillBroker(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0
    alert = fund_db.execute("SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None and "ValueError" in alert["payload"]


def test_partial_fill_records_filled_numbers(fund_db, sim_clock):
    """Fix 3: the partial-fill branch must record filled_qty/filled_avg_price
    — the books must not read zero shares while the broker holds some."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now, qty=10)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="partial")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 10})
    def one_tick(s): broker.tick()
    reconcile_orders(fund_db, clock=sim_clock, broker=broker, sleep=one_tick,
                     poll_s=3.0, max_wait_s=3.0)   # one poll then stop
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "partially_filled"
    assert o["filled_qty"] == 5
    assert o["filled_avg_price"] == 180.0


def test_fill_with_decision_not_approved_alerts_and_does_not_transition(fund_db, sim_clock):
    """Fix 4: the order fills but the decision isn't in 'approved' anymore
    (e.g. it already expired) — must alert, not silently strand it."""
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    fund_db.execute("UPDATE decisions SET status='expired'"); fund_db.commit()
    _submitted_order(fund_db, now)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14}, mode="instant")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    reconcile_orders(fund_db, clock=sim_clock, broker=broker,
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "filled"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "expired"
    alert = fund_db.execute("SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None
    assert "expired" in alert["payload"] and "not" in alert["payload"]


def _pending(conn, sim_clock, qty=67):
    """Seed a consumed ticket + a submitted order, ready for the poll loop."""
    _seed(conn)
    conn.execute("UPDATE tickets SET status='consumed'"); conn.commit()
    _submitted_order(conn, iso(sim_clock.now()), qty=qty)


def test_timeout_cancels_at_the_broker_before_writing_canceled(fund_db, sim_clock):
    """C2: the timeout path must actually cancel at the broker, not just write
    'canceled' into SQLite and hope (invariant 6)."""
    _pending(fund_db, sim_clock)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    _poll(fund_db, sim_clock, broker)
    assert broker.cancel_attempts == [TID]
    assert broker.orders[TID]["status"] == "canceled"    # broker-side, not just DB
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "canceled"


def test_fill_during_cancel_race_records_the_fill_not_a_cancel(fund_db, sim_clock):
    """C2 headline: the order fills while the cancel is in flight. The DB must
    record the TRUE terminal state — filled/executed with a fill event — never
    canceled/failed over shares the fund actually holds."""
    _pending(fund_db, sim_clock)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14},
                        mode="fill_during_cancel")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    n, _ = _poll(fund_db, sim_clock, broker)
    assert broker.cancel_attempts == [TID]
    assert n == 1
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "filled"
    assert o["filled_qty"] == 67 and o["filled_avg_price"] == 180.14
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "executed"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 1


def test_cancel_error_and_still_open_leaves_order_submitted(fund_db, sim_clock):
    """Cancel request errored and the broker still reports the order working:
    ambiguous, so fail closed — no terminal state the broker hasn't
    confirmed, row left for the next run, alert raised."""
    _pending(fund_db, sim_clock)
    class CancelBoom:
        def get_order_by_client_order_id(self, coid): return {"status": "accepted"}
        def cancel_order(self, coid): raise ConnectionError()
    reconcile_orders(fund_db, clock=sim_clock, broker=CancelBoom(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    alert = fund_db.execute("SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None and "cancel unconfirmed" in alert["payload"]


def test_requery_error_after_cancel_leaves_order_submitted(fund_db, sim_clock):
    """The cancel went out but the confirming re-query failed: we do not know
    the terminal state, so write nothing (invariant 4)."""
    _pending(fund_db, sim_clock)
    class RequeryBoom:
        def __init__(self): self.canceled = []
        def get_order_by_client_order_id(self, coid):
            if self.canceled: raise ConnectionError()
            return {"status": "accepted"}
        def cancel_order(self, coid): self.canceled.append(coid)
    broker = RequeryBoom()
    reconcile_orders(fund_db, clock=sim_clock, broker=broker,
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert broker.canceled == [TID]
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    alert = fund_db.execute("SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None and "ConnectionError" in alert["payload"]


def test_pending_cancel_is_not_a_confirmed_cancel(fund_db, sim_clock):
    """'pending_cancel' is the broker still deciding — not a terminal state.
    The row stays 'submitted' so the next run re-polls and re-cancels."""
    _pending(fund_db, sim_clock)
    class PendingCancel:
        def __init__(self): self.canceled = []
        def get_order_by_client_order_id(self, coid):
            return {"status": "pending_cancel"} if self.canceled else {"status": "accepted"}
        def cancel_order(self, coid): self.canceled.append(coid)
    reconcile_orders(fund_db, clock=sim_clock, broker=PendingCancel(),
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=3.0)
    assert fund_db.execute("SELECT status FROM orders").fetchone()["status"] == "submitted"
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"
    alert = fund_db.execute("SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None and "pending_cancel" in alert["payload"]


def test_second_run_after_cancel_is_a_noop(fund_db, sim_clock):
    """Idempotency (invariant 5): once the order is canceled the second run
    must not re-cancel or re-alert."""
    _pending(fund_db, sim_clock)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    _poll(fund_db, sim_clock, broker)
    before = fund_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    _poll(fund_db, sim_clock, broker)
    assert broker.cancel_attempts == [TID]
    assert fund_db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == before


class _PartialThenCanceled:
    """accepted -> partially_filled -> canceled at the broker, all inside one
    run: 5 of 10 shares really are held and the rest is canceled away. Numbers
    come back as strings, like the real wire (source_alpaca)."""

    def __init__(self, part: int = 5, price: float = 180.0) -> None:
        self.part, self.price, self.polls = part, price, 0
        self.cancel_attempts: list[str] = []

    def get_order_by_client_order_id(self, coid):
        self.polls += 1
        if self.polls == 1:
            return {"status": "accepted", "filled_qty": "0",
                    "filled_avg_price": None}
        status = "partially_filled" if self.polls == 2 else "canceled"
        return {"status": status, "filled_qty": str(self.part),
                "filled_avg_price": str(self.price)}

    def cancel_order(self, coid):
        self.cancel_attempts.append(coid)


def test_partial_then_canceled_records_the_shares_held(fund_db, sim_clock):
    """The DB must tell the truth about what's held: a partial fill that was
    then canceled leaves a REAL position, so the order goes partially_filled
    -> canceled with the true numbers, the decision goes 'executed', a fill
    event covers the partial qty, and the alert names it."""
    _pending(fund_db, sim_clock, qty=10)
    broker = _PartialThenCanceled()
    reconcile_orders(fund_db, clock=sim_clock, broker=broker,
                     sleep=lambda s: None, poll_s=3.0, max_wait_s=6.0)
    assert broker.cancel_attempts == [TID]
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "canceled"
    assert o["filled_qty"] == 5 and o["filled_avg_price"] == 180.0
    assert fund_db.execute(
        "SELECT status FROM decisions").fetchone()["status"] == "executed"
    fills = fund_db.execute(
        "SELECT payload FROM events WHERE kind='fill'").fetchall()
    assert len(fills) == 1 and '"filled_qty": 5' in fills[0]["payload"]
    alerts = [r["payload"] for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'")]
    assert any("partial 5 of 10 then canceled" in a for a in alerts), alerts


def test_zero_fill_cancel_fails_the_decision_and_writes_no_fill(fund_db, sim_clock):
    """The other half: nothing was filled, so the decision is 'failed' and no
    fill event is written (the pre-existing path, pinned)."""
    _pending(fund_db, sim_clock)
    broker = FakeAlpaca({"NVDA": 180.0}, mode="never_fill")
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    _poll(fund_db, sim_clock, broker)
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "canceled" and o["filled_qty"] == 0
    assert fund_db.execute(
        "SELECT status FROM decisions").fetchone()["status"] == "failed"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0
    alert = fund_db.execute(
        "SELECT payload FROM events WHERE kind='alert'").fetchone()
    assert alert is not None and "decision failed" in alert["payload"]


def test_idempotent_second_run_no_double_event(fund_db, sim_clock):
    _seed(fund_db)
    now = iso(sim_clock.now())
    fund_db.execute("UPDATE tickets SET status='consumed'"); fund_db.commit()
    _submitted_order(fund_db, now)
    broker = FakeAlpaca({"NVDA": 180.0}, {"NVDA": 180.14})
    broker.place_order({"client_order_id": TID, "symbol": "NVDA",
                        "side": "buy", "qty": 67})
    n1, _ = _poll(fund_db, sim_clock, broker)
    assert n1 == 1
    # Second run: the order is already terminal, decision already executed.
    # CAS transitions must no-op — no double fill event, no re-transition.
    n2, _ = _poll(fund_db, sim_clock, broker)
    assert n2 == 0
    o = fund_db.execute("SELECT * FROM orders").fetchone()
    assert o["status"] == "filled"
    fill_count = fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"]
    assert fill_count == 1
