import pytest
from orchestrator.clock import iso
from orchestrator.reconcile import reconcile_orders
from slackkit.fake import FakeSlack
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
