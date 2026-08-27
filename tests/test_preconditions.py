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


def _payloads(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT payload FROM events WHERE kind = 'alert' ORDER BY id").fetchall()
    return [json.loads(r["payload"]) for r in rows]


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
    payload = _payloads(conn)[0]
    assert payload["code"] == "account_precondition_drift"
    assert payload["drift"] == {"field": "no_shorting", "expected": True,
                                "actual": False}


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


def test_fake_alpaca_reports_a_default_account_config(conn):
    """sim-day and the offline suite need the surface, and its default must
    match its own baseline so a plain fake is silent."""
    from tests.fake_alpaca import DEFAULT_ACCOUNT_CONFIG, FakeAlpaca

    fake = FakeAlpaca(prices={"NVDA": 100.0})
    assert fake.account_config() == DEFAULT_ACCOUNT_CONFIG
    n = assert_account_config_unchanged(
        conn, broker=fake, baseline=DEFAULT_ACCOUNT_CONFIG, now_iso=NOW)
    assert n == 0


def test_fake_alpaca_account_config_is_overridable(conn):
    from tests.fake_alpaca import DEFAULT_ACCOUNT_CONFIG, FakeAlpaca

    # Must differ from DEFAULT_ACCOUNT_CONFIG's own value (False), or there is
    # no drift to detect and the test asserts nothing.
    fake = FakeAlpaca(prices={"NVDA": 100.0},
                      account_config=dict(DEFAULT_ACCOUNT_CONFIG,
                                          no_shorting=True))
    n = assert_account_config_unchanged(
        conn, broker=fake, baseline=DEFAULT_ACCOUNT_CONFIG, now_iso=NOW)
    assert n == 1
    assert "no_shorting" in _alerts(conn)[0]


def test_baseline_value_types_match_the_installed_alpaca_pys_model():
    """Nothing else pins the checked-in YAML's value TYPES against what
    account_config() actually produces. `max_margin_multiplier: "4"` is
    quoted only by an author's care — an innocent edit to the unquoted `4`
    makes it an int, which would drift every single morning (a str baseline
    value never equals an int broker value), training everyone to ignore the
    alert. This walks alpaca.trading.models.AccountConfiguration's own field
    annotations rather than a second hand-written list, so it stays honest
    against whatever alpaca-py version is actually installed."""
    from enum import Enum

    import yaml
    from alpaca.trading.models import AccountConfiguration

    from scripts.run_day import ACCOUNT_BASELINE_YAML

    baseline = yaml.safe_load(ACCOUNT_BASELINE_YAML.read_text())

    # Genuinely null on this account (see the baseline file's own header) —
    # None must be accepted alongside the field's real type for these three.
    NULLABLE = {"dtbp_check", "pdt_check", "max_options_trading_level"}

    for name, field in AccountConfiguration.model_fields.items():
        assert name in baseline, f"{name}: in the model but missing from" \
            " the baseline"
        value = baseline[name]
        py_type = next(a for a in getattr(field.annotation, "__args__",
                                          (field.annotation,))
                       if a is not type(None))
        if isinstance(py_type, type) and issubclass(py_type, Enum):
            # account_config() (market/source_alpaca.py) coerces every enum
            # member to its plain str value before this ever gets diffed.
            py_type = str

        if value is None:
            assert name in NULLABLE, (
                f"{name}: baseline is null, but this field is not one of"
                f" the account's genuinely-null fields {sorted(NULLABLE)}")
        else:
            assert isinstance(value, py_type), (
                f"{name}: baseline holds a {type(value).__name__}"
                f" ({value!r}), but AccountConfiguration expects"
                f" {py_type.__name__}")
