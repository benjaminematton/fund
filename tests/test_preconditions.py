"""Account precondition drift (2026-08-20 design)."""
import json
import sqlite3

import pytest

from orchestrator.preconditions import assert_account_config_unchanged
from state.db import connect

NOW = "2026-08-20T09:00:00-04:00"
BASE = {"no_shorting": True, "suspend_trade": False, "max_margin_multiplier": "1"}


class _Broker:
    def __init__(self, payload):
        self._payload = payload

    def account_config(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _alerts(conn) -> list[str]:
    rows = conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id").fetchall()
    return [json.loads(r["payload"])["text"] for r in rows]


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "t.sqlite"))
    yield c
    c.close()


def test_exact_match_is_silent(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker(dict(BASE)), baseline=BASE, now_iso=NOW)
    assert n == 0
    assert _alerts(conn) == []


def test_changed_field_alerts_naming_old_and_new(conn):
    drifted = dict(BASE, no_shorting=False)
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    text = _alerts(conn)[0]
    assert "no_shorting" in text and "True" in text and "False" in text


def test_one_alert_per_drifted_field(conn):
    drifted = dict(BASE, no_shorting=False, suspend_trade=True)
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 2
    assert len(_alerts(conn)) == 2


def test_field_missing_from_payload_alerts(conn):
    """Alpaca removed a setting the baseline pins."""
    drifted = {k: v for k, v in BASE.items() if k != "suspend_trade"}
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "suspend_trade" in _alerts(conn)[0]


def test_field_new_in_payload_alerts(conn):
    """Alpaca added a setting the baseline does not pin — the case a
    hand-written enumeration would miss."""
    drifted = dict(BASE, dtbp_check="entry")
    n = assert_account_config_unchanged(
        conn, broker=_Broker(drifted), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "dtbp_check" in _alerts(conn)[0]


def test_broker_failure_alerts_and_does_not_raise(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker(RuntimeError("boom")), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert "RuntimeError" in _alerts(conn)[0]


def test_unparseable_payload_alerts(conn):
    n = assert_account_config_unchanged(
        conn, broker=_Broker("not-a-dict"), baseline=BASE, now_iso=NOW)
    assert n == 1
    assert _alerts(conn)


def test_empty_baseline_alerts_rather_than_passing(conn):
    """A baseline that failed to load must never read as 'nothing drifted'."""
    n = assert_account_config_unchanged(
        conn, broker=_Broker(dict(BASE)), baseline={}, now_iso=NOW)
    assert n == 1
