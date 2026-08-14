import asyncio
import json

from agents.runtime import (make_decision_recorder, make_order_gate,
                            make_order_recorder, record_cost,
                            record_turn_result)
from tests.test_tickets import TID, _seed, order

NOW = "2026-07-06T15:30:00+00:00"


def _deny_reason(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason")


def _run(coro):
    return asyncio.run(coro)


def test_order_gate_allows_valid_and_ignores_non_place_tools(fund_db, sim_clock):
    _seed(fund_db)
    gate = make_order_gate(lambda: fund_db, sim_clock)
    ok = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                    "tool_input": order()}, "t1", None))
    assert ok == {}
    passthrough = _run(gate({"tool_name": "mcp__fund__list_open_tickets",
                             "tool_input": {}}, "t2", None))
    assert passthrough == {}


def test_order_gate_denies_with_reason(fund_db, sim_clock):
    gate = make_order_gate(lambda: fund_db, sim_clock)  # no ticket seeded
    out = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                     "tool_input": order()}, "t1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no gate ticket" in _deny_reason(out)


def test_order_gate_fails_closed_on_internal_error(sim_clock):
    """Invariant 4: any internal error in the gate (e.g. a dead connection
    factory) must deny, never raise and never silently allow."""
    def broken_conn_factory():
        raise RuntimeError("db down")

    gate = make_order_gate(broken_conn_factory, sim_clock)
    out = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                     "tool_input": order()}, "t1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "db down" in _deny_reason(out)


def test_order_recorder_writes_once_and_projects(fund_db, sim_clock):
    did = _seed(fund_db)
    rec = make_order_recorder(lambda: fund_db, sim_clock)
    resp = {"id": "alp-0001", "client_order_id": TID, "symbol": "NVDA",
            "side": "buy", "qty": 67, "status": "filled", "filled_qty": 67,
            "filled_avg_price": 180.14}
    call = {"tool_name": "mcp__alpaca__place_stock_order",
            "tool_input": order(), "tool_response": resp}
    _run(rec(call, "t1", None))
    _run(rec(call, "t1", None))  # PostToolUse re-fired (retry) — idempotent
    rows = fund_db.execute("SELECT * FROM orders").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert (row["client_order_id"], row["status"], row["filled_qty"],
            row["filled_avg_price"]) == (TID, "filled", 67, 180.14)
    assert row["closed_at"] == NOW
    assert fund_db.execute("SELECT status FROM tickets WHERE id=?",
                           (TID,)).fetchone()["status"] == "consumed"
    assert fund_db.execute("SELECT status FROM decisions WHERE id=?",
                           (did,)).fetchone()["status"] == "executed"
    fills = fund_db.execute("SELECT * FROM events WHERE kind='fill'").fetchall()
    assert len(fills) == 1
    assert json.loads(fills[0]["payload"])["filled_avg_price"] == 180.14


def test_order_recorder_malformed_fill_null_price_leaves_submitted(fund_db, sim_clock):
    """CRITICAL regression (MVF T9 review): same parse-after-CAS bug already
    fixed in orchestrator/reconcile.py's _apply. A 'filled' ack with a null
    filled_avg_price must not commit status='filled' before the coercion is
    known to succeed — must not raise into the SDK, must leave the order
    'submitted' for reconcile to repair."""
    _seed(fund_db)
    rec = make_order_recorder(lambda: fund_db, sim_clock)
    resp = {"id": "alp-0001", "client_order_id": TID, "symbol": "NVDA",
            "side": "buy", "qty": 67, "status": "filled", "filled_qty": 67,
            "filled_avg_price": None}
    call = {"tool_name": "mcp__alpaca__place_stock_order",
            "tool_input": order(), "tool_response": resp}
    _run(rec(call, "t1", None))  # must not raise
    row = fund_db.execute("SELECT * FROM orders").fetchone()
    assert row["status"] == "submitted"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"


def test_order_recorder_malformed_fill_missing_price_leaves_submitted(fund_db, sim_clock):
    """Same CRITICAL regression, but the broker omits filled_avg_price entirely."""
    _seed(fund_db)
    rec = make_order_recorder(lambda: fund_db, sim_clock)
    resp = {"id": "alp-0001", "client_order_id": TID, "symbol": "NVDA",
            "side": "buy", "qty": 67, "status": "filled", "filled_qty": 67}
    call = {"tool_name": "mcp__alpaca__place_stock_order",
            "tool_input": order(), "tool_response": resp}
    _run(rec(call, "t1", None))  # must not raise
    row = fund_db.execute("SELECT * FROM orders").fetchone()
    assert row["status"] == "submitted"
    assert fund_db.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='fill'").fetchone()["c"] == 0
    assert fund_db.execute("SELECT status FROM decisions").fetchone()["status"] == "approved"


def test_order_recorder_skips_errors_and_foreign_tools(fund_db, sim_clock):
    _seed(fund_db)
    rec = make_order_recorder(lambda: fund_db, sim_clock)
    _run(rec({"tool_name": "mcp__alpaca__place_stock_order",
              "tool_input": order(),
              "tool_response": {"error": "client_order_id must be unique",
                                "status_code": 422}}, "t1", None))
    _run(rec({"tool_name": "mcp__slack__post", "tool_input": {},
              "tool_response": {"ok": True}}, "t2", None))
    assert fund_db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"] == 0


def test_decision_recorder_round_trips_jsonl(tmp_path):
    from agents.replay import load_recording

    path = tmp_path / "2026-07-06-exec-execution.jsonl"
    rec = make_decision_recorder(path, "exec")
    _run(rec({"tool_name": "mcp__fund__list_open_tickets", "tool_input": {}},
             "t1", None))
    _run(rec({"tool_name": "mcp__alpaca__place_stock_order",
              "tool_input": order()}, "t2", None))
    _run(rec({"tool_name": "Read", "tool_input": {"path": "x"}}, "t3", None))
    decisions = load_recording(path)
    assert [d["tool"] for d in decisions] == [
        "mcp__fund__list_open_tickets", "mcp__alpaca__place_stock_order"]
    assert decisions[1]["args"]["qty"] == 67 and decisions[1]["seat"] == "exec"


def test_record_cost_inserts_row(fund_db):
    record_cost(fund_db, "2026-07-06", "exec", "sess-1", 0.0123, NOW)
    row = fund_db.execute("SELECT * FROM costs").fetchone()
    assert row["agent"] == "exec" and row["usd_estimate"] == 0.0123


class _Result:
    """Stands in for the SDK's ResultMessage — record_turn_result must read it
    by attribute, never by isinstance, so the cost pillar is testable offline."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


def _costs(conn):
    return conn.execute("SELECT * FROM costs").fetchall()


def _alerts(conn):
    return [json.loads(r["payload"])["text"] for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id")]


def test_record_turn_result_writes_the_cost_row(fund_db):
    """The live wiring seam (scripts/run_day.py calls this after every seat
    turn): a ResultMessage with a populated estimate becomes exactly one row."""
    assert record_turn_result(fund_db, "2026-07-06", "analyst",
                              _Result(total_cost_usd=0.0123, session_id="s1"),
                              NOW) is True
    rows = _costs(fund_db)
    assert len(rows) == 1
    assert (rows[0]["run_date"], rows[0]["agent"], rows[0]["session_id"],
            rows[0]["usd_estimate"], rows[0]["recorded_at"]) == (
        "2026-07-06", "analyst", "s1", 0.0123, NOW)
    assert _alerts(fund_db) == []


def test_record_turn_result_none_cost_records_nothing_and_alerts(fund_db):
    """total_cost_usd is Optional in the SDK. A missing estimate must NOT
    become a 0.0 row — that would make real spend look free in the digest.
    No row, one alert, no crash (invariant 4)."""
    assert record_turn_result(fund_db, "2026-07-06", "pm",
                              _Result(total_cost_usd=None, session_id="s2"),
                              NOW) is False
    assert _costs(fund_db) == []
    assert _alerts(fund_db) == [
        "cost_unavailable pm — turn completed with no total_cost_usd estimate"
        " (session s2); the day's est. inference cost understates spend"]


def test_record_turn_result_missing_attributes_do_not_crash_the_day(fund_db):
    """A result object from an older/newer SDK that carries neither attribute
    must degrade to the same alert, never raise into the stage runner."""
    assert record_turn_result(fund_db, "2026-07-06", "exec", object(),
                              NOW) is False
    assert _costs(fund_db) == []
    assert len(_alerts(fund_db)) == 1


def test_record_turn_result_rejects_non_numeric_estimate(fund_db):
    """usd_estimate is REAL NOT NULL: a string or NaN must be refused at the
    seam, not written as garbage the digest then sums."""
    for bad in ("0.02", float("nan"), True):
        conn_rows_before = len(_costs(fund_db))
        assert record_turn_result(fund_db, "2026-07-06", "pm",
                                  _Result(total_cost_usd=bad,
                                          session_id="s3"), NOW) is False
        assert len(_costs(fund_db)) == conn_rows_before


def test_hooks_reuse_one_connection_per_factory_binding(fund_db, sim_clock):
    """C1: the hook factories must not open a fresh conn per call. Bind them
    to a counting factory and fire twice: exactly one connect."""
    calls = []
    def factory():
        calls.append(1)
        return fund_db
    gate = make_order_gate(factory, sim_clock)
    asyncio.run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                      "tool_input": {}}, "t1", None))
    asyncio.run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                      "tool_input": {}}, "t2", None))
    assert len(calls) == 1
