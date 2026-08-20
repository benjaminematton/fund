import asyncio
import json
import sqlite3

import pytest

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


DENIAL = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": None}}


def _denial(reason):
    out = {"hookSpecificOutput": dict(DENIAL["hookSpecificOutput"])}
    out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    return out


def test_order_gate_deny_appends_one_alert_naming_ticker_reason_and_ticket(
        fund_db, sim_clock):
    """D1: runbook abort criterion #1 is 'a hook deny on a live order', but on
    the first live day a deny wrote NOTHING — it was invisible in #risk and in
    the audit. Every deny must append exactly one alert naming the ticker, the
    reason, and the 8-char ticket id, and must still deny identically."""
    gate = make_order_gate(lambda: fund_db, sim_clock)  # no ticket seeded
    out = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                     "tool_input": order()}, "t1", None))
    reason = _deny_reason(out)
    assert out == _denial(reason)          # recording changed nothing returned
    assert "no gate ticket" in reason
    alerts = _alerts(fund_db)
    assert len(alerts) == 1
    assert "NVDA" in alerts[0]
    assert TID[:8] in alerts[0]
    assert reason in alerts[0]
    assert fund_db.execute(
        "SELECT created_at FROM events").fetchone()["created_at"] == NOW


def test_order_gate_allowed_order_appends_no_alert(fund_db, sim_clock):
    """Recording is deny-only: an allowed order and a non-place tool stay silent
    or #risk fills with noise and the audit reddens on every good day."""
    _seed(fund_db)
    gate = make_order_gate(lambda: fund_db, sim_clock)
    assert _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                      "tool_input": order()}, "t1", None)) == {}
    assert _run(gate({"tool_name": "mcp__fund__list_open_tickets",
                      "tool_input": {}}, "t2", None)) == {}
    assert _alerts(fund_db) == []


def test_order_gate_deny_alert_survives_malformed_tool_input(fund_db, sim_clock):
    """A malformed-input deny is the normal case: symbol/client_order_id may be
    missing or non-string. The alert must still be emitted, never raise."""
    gate = make_order_gate(lambda: fund_db, sim_clock)
    for bad in ({}, {"symbol": None, "client_order_id": 42}, None):
        out = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                         "tool_input": bad}, "t1", None))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert len(_alerts(fund_db)) == 3


# --- ungated broker verbs ----------------------------------------------------
#
# The gate used to return {} — allow — for every tool whose name did not start
# with `mcp__alpaca__place_`. The exec seat holds `alpaca_toolsets: trading`
# and `tools: ["mcp__fund__*", "mcp__alpaca__*"]`, so the trading toolset's
# other mutating verbs were reachable AND unexamined: cancel_order_by_id,
# cancel_all_orders, close_position, close_all_positions. No ticket, no
# max_qty, no `orders` row, no gate_approved event. `close_all_positions`
# would have liquidated the book.
#
# charters/exec.md rule 7 already forbids this ("You never modify, cancel, or
# work an order beyond the ticket's terms") — but a charter is a prompt, and
# the whole architecture is a deterministic gate BETWEEN the LLM and the
# broker. A rule with no enforcement is a rule the next model ignores.

# The mutating surface the exec seat can reach, introspected from the live
# server on 2026-08-20 (38 tools at `account,trading,stock-data`).
#
# THE COUNT WENT 4 -> 5 -> 7 -> 8 IN ONE AFTERNOON, three of those corrections
# landing after the fix had shipped, and the mechanism was right every time
# because it never depended on the number. That history is the argument for
# the design, so it is recorded here rather than in a commit message nobody
# will read.
#
# These are denied because they are NOT GATED, never because they appear in
# this list. The list is a regression guard; `_broker_verb_policy` is the
# mechanism. A ninth verb in the next upstream bump is already denied with
# nobody editing this file — which is the only reason the three corrections
# above cost a test edit instead of an incident.
MUTATORS = ["mcp__alpaca__cancel_order_by_id",
            "mcp__alpaca__cancel_all_orders",
            "mcp__alpaca__close_position",
            "mcp__alpaca__close_all_positions",
            "mcp__alpaca__replace_order_by_id",
            "mcp__alpaca__exercise_options_position",
            "mcp__alpaca__do_not_exercise_options_position",
            # from the `account` toolset, not `trading` — a different class
            # (it mutates account settings, not the book) but still a
            # broker mutation with no ticket behind it
            "mcp__alpaca__update_account_config"]

# All three, not just stock: the gated prefix has to cover the whole family or
# a crypto/option order routes around the ticket check.
PLACE_VERBS = ["mcp__alpaca__place_stock_order",
               "mcp__alpaca__place_crypto_order",
               "mcp__alpaca__place_option_order"]


@pytest.mark.parametrize("tool", PLACE_VERBS)
def test_every_place_verb_is_gated_not_just_the_stock_one(tool, fund_db,
                                                          sim_clock):
    from agents.runtime import _broker_verb_policy
    assert _broker_verb_policy(tool) == "gated"


@pytest.mark.parametrize("tool", MUTATORS)
def test_a_broker_mutation_with_no_gated_route_is_denied(tool, fund_db,
                                                         sim_clock):
    """Denied even with a VALID ticket seeded: a ticket authorizes the order it
    names, never a cancel or a liquidation."""
    _seed(fund_db)
    gate = make_order_gate(lambda: fund_db, sim_clock)
    out = _run(gate({"tool_name": tool, "tool_input": {}}, "t1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_an_unknown_broker_verb_is_denied_rather_than_waved_through(fund_db,
                                                                    sim_clock):
    """The direction that matters. A denylist of the four known verbs would
    fail OPEN the first time the toolset grows one nobody enumerated — which is
    exactly how these four arrived. Anything not explicitly routed is denied
    (invariant 4: ambiguity resolves to no action)."""
    gate = make_order_gate(lambda: fund_db, sim_clock)
    out = _run(gate({"tool_name": "mcp__alpaca__liquidate_everything",
                     "tool_input": {}}, "t1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("tool", [
    "mcp__alpaca__get_account_info", "mcp__alpaca__get_stock_latest_quote",
    "mcp__alpaca__get_stock_snapshot", "mcp__alpaca__get_stock_bars",
    "mcp__alpaca__get_stock_latest_trade"])
def test_read_only_verbs_still_pass_through(tool, fund_db, sim_clock):
    """Every alpaca verb in the whole recorded corpus — recordings and live
    traces — is one of these or place_stock_order. Denying a read would take
    the exec turn down for a quote."""
    gate = make_order_gate(lambda: fund_db, sim_clock)
    assert _run(gate({"tool_name": tool, "tool_input": {}}, "t1", None)) == {}


def test_a_non_broker_tool_is_never_the_gates_business(fund_db, sim_clock):
    gate = make_order_gate(lambda: fund_db, sim_clock)
    for tool in ("mcp__fund__list_open_tickets", "mcp__fund__submit_decision",
                 "Read", "Bash"):
        assert _run(gate({"tool_name": tool, "tool_input": {}},
                         "t1", None)) == {}


def test_a_denied_mutation_is_observable_not_silent(fund_db, sim_clock):
    """Same contract as an order deny: it reddens audit_day and reaches #risk.
    A silent block is indistinguishable from a seat that never tried."""
    gate = make_order_gate(lambda: fund_db, sim_clock)
    _run(gate({"tool_name": "mcp__alpaca__close_all_positions",
               "tool_input": {}}, "t1", None))
    alerts = _alerts(fund_db)
    assert len(alerts) == 1
    assert "close_all_positions" in alerts[0]


def test_gated_prefixes_is_the_one_place_a_new_verb_is_authorized(fund_db,
                                                                  sim_clock):
    """The extension point, pinned so the next verb is a one-line change.

    An amend path needs `replace_order_by_id` AUTHORIZED rather than denied.
    Adding its prefix to GATED_PREFIXES must route it to validate_order — the
    same ticket check place_* gets — not to a second bespoke branch.

    If you add a prefix here, the PostToolUse recorder also needs revisiting:
    it still matches PLACE_PREFIX alone, so a newly-gated verb would execute
    and leave no `orders` row behind."""
    from agents import runtime

    assert runtime.PLACE_PREFIX in runtime.GATED_PREFIXES
    seen = {}
    original = runtime.validate_order
    monkey = list(runtime.GATED_PREFIXES) + ["mcp__alpaca__replace_"]
    try:
        runtime.GATED_PREFIXES = tuple(monkey)
        runtime.validate_order = lambda *a, **k: (seen.setdefault("hit", True),
                                                  (True, ""))[1]
        gate = make_order_gate(lambda: fund_db, sim_clock)
        assert _run(gate({"tool_name": "mcp__alpaca__replace_order_by_id",
                          "tool_input": {}}, "t1", None)) == {}
    finally:
        runtime.GATED_PREFIXES = tuple(monkey[:-1])
        runtime.validate_order = original
    assert seen.get("hit"), "a gated prefix must reach validate_order"


class _NoEventWrites:
    """Reads like the real DB; every events INSERT explodes."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if "INTO events" in sql:
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def commit(self):
        return self._conn.commit()


def test_order_gate_deny_survives_an_unrecordable_alert(fund_db, sim_clock):
    """An unrecordable deny is still a deny: if the append itself raises, the
    denial must be returned unchanged (never let observability open the gate)."""
    gate = make_order_gate(lambda: _NoEventWrites(fund_db), sim_clock)
    out = _run(gate({"tool_name": "mcp__alpaca__place_stock_order",
                     "tool_input": order()}, "t1", None))
    assert out == _denial(_deny_reason(out))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _alerts(fund_db) == []


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


# --- model divergence --------------------------------------------------------
#
# decisions.model_id and signals.model_id hold the seat's CONFIGURED model,
# because the MCP handler that writes the row never sees the ResultMessage —
# it does not exist until the turn ends. That value is quietly wrong exactly
# when a fallback served the turn, and the divergence path is live on the
# primary table: analyst.yaml pins haiku with a sonnet fallback, and the
# analyst is what writes signals. These tests pin the alert that makes
# model_id trustworthy precisely when it is silent.


def _divergences(conn):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM events WHERE kind = 'model_fallback_used'"
        " ORDER BY id")]


def test_a_matching_model_raises_no_divergence(fund_db):
    """The canary. If this ever fails the alert has become always-on, which is
    worse than absent: a daily alert is one people learn to skip, and it is
    gone on the day it means something."""
    record_turn_result(fund_db, "2026-07-06", "pm",
                       _Result(total_cost_usd=0.01, session_id="s",
                               model_usage={"claude-sonnet-5": {"in": 10}}),
                       NOW, configured_model="claude-sonnet-5")
    assert _divergences(fund_db) == []


def test_a_resolved_id_still_matches_the_configured_alias(fund_db):
    """model_usage's keys come from the CLI unchanged, so they may be the alias
    the yaml pins or a resolved dated id. That is a match, not a fallback —
    and which form the CLI emits is not determinable from source, so the
    comparison must be right under either."""
    record_turn_result(
        fund_db, "2026-07-06", "pm",
        _Result(total_cost_usd=0.01, session_id="s",
                model_usage={"claude-sonnet-5-20250929": {"in": 10}}),
        NOW, configured_model="claude-sonnet-5")
    assert _divergences(fund_db) == []


def test_a_genuine_fallback_is_recorded(fund_db):
    """analyst pins haiku with a sonnet fallback — the live divergence path.
    The payload names what served, so the reader is not left diffing lists."""
    record_turn_result(fund_db, "2026-07-06", "analyst",
                       _Result(total_cost_usd=0.01, session_id="s",
                               model_usage={"claude-sonnet-5": {"in": 10}}),
                       NOW, configured_model="claude-haiku-4-5-20251001")
    assert _divergences(fund_db) == [
        {"seat": "analyst", "configured": "claude-haiku-4-5-20251001",
         "served": ["claude-sonnet-5"]}]


def test_a_mixed_turn_flags_only_the_unmatched_key(fund_db):
    """The test that separates the two quantifiers — every other case here is
    single-key or None and passes under both. A turn that ran haiku for most of
    it and fell back to sonnet for part must be recorded, naming only sonnet:
    `any(matches)` would see the haiku key and stay silent on exactly the case
    that motivated reading model_usage rather than a single field."""
    record_turn_result(
        fund_db, "2026-07-06", "analyst",
        _Result(total_cost_usd=0.01, session_id="s",
                model_usage={"claude-haiku-4-5-20251001": {"in": 90},
                             "claude-sonnet-5": {"in": 10}}),
        NOW, configured_model="claude-haiku-4-5-20251001")
    rows = _divergences(fund_db)
    assert len(rows) == 1
    assert rows[0]["served"] == ["claude-sonnet-5"]      # not both keys


def test_absent_model_usage_records_no_divergence(fund_db):
    """None is not a mismatch. The SDK marks the field Optional, and the turn
    that carries no usage is already covered by the cost alert — inventing a
    second event for it would double-count one failure."""
    record_turn_result(fund_db, "2026-07-06", "analyst",
                       _Result(total_cost_usd=0.01, session_id="s",
                               model_usage=None),
                       NOW, configured_model="claude-haiku-4-5-20251001")
    assert _divergences(fund_db) == []


def test_an_unstated_configured_model_records_no_divergence(fund_db):
    """Every production caller passes the seat's configured model. A caller
    that cannot — a test stub, an older path — must not manufacture a
    divergence against the empty string, which every key would 'mismatch'."""
    record_turn_result(fund_db, "2026-07-06", "analyst",
                       _Result(total_cost_usd=0.01, session_id="s",
                               model_usage={"claude-sonnet-5": {"in": 10}}),
                       NOW)
    assert _divergences(fund_db) == []


def test_a_divergence_is_not_an_alert(fund_db):
    """Its own kind, deliberately. scripts/audit_day.py fails the day on any
    alert event, and a fallback that served a turn is not a failed day — the
    fund traded correctly, only model_id is stale. The daily scorecard ranks
    it at severity 3; making it an alert would make every fallback an
    incident."""
    record_turn_result(fund_db, "2026-07-06", "analyst",
                       _Result(total_cost_usd=0.01, session_id="s",
                               model_usage={"claude-sonnet-5": {"in": 10}}),
                       NOW, configured_model="claude-haiku-4-5-20251001")
    assert _alerts(fund_db) == []
    assert len(_divergences(fund_db)) == 1


def test_a_divergence_is_recorded_even_when_the_cost_estimate_is_missing(fund_db):
    """The two checks are independent. A turn can lose its cost estimate AND
    have fallen back; the early return on the cost path must not swallow the
    divergence, or the alert is missing exactly on the messiest turns."""
    record_turn_result(fund_db, "2026-07-06", "analyst",
                       _Result(total_cost_usd=None, session_id="s",
                               model_usage={"claude-sonnet-5": {"in": 10}}),
                       NOW, configured_model="claude-haiku-4-5-20251001")
    assert len(_alerts(fund_db)) == 1              # cost_unavailable
    assert len(_divergences(fund_db)) == 1


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
