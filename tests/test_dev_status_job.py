"""Offline tests for the dev-status job's seams.

scripts/dev_status.py is a composition root like scripts/resolve_day.py, so
main() is never called here — it opens ssh connections and a broker client.
What is pinned is what the job DEPENDS on: every dependency it declares is a
way for the job to go silent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("dev_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists():
    assert SCRIPT.exists()


def test_exposes_build_snapshot_and_main():
    m = _load()
    assert callable(m.build_snapshot)
    assert callable(m.main)


def test_reads_suppression_from_health_descriptor(tmp_path):
    """The descriptor's front matter is the only source of suppression."""
    m = _load()
    health = tmp_path / "health.md"
    health.write_text(
        "---\n"
        "health_command: make dev-status\n"
        "suppress:\n"
        "  - degradations\n"
        "---\n\n"
        "# prose\n"
    )
    assert m.read_suppressed(health) == frozenset({"degradations"})


def test_missing_descriptor_suppresses_nothing(tmp_path):
    """Negative control: no file means no suppression, never a crash."""
    m = _load()
    assert m.read_suppressed(tmp_path / "absent.md") == frozenset()


# --- the builder's parsing, which had no tests until 2026-09-02 ---------------
# The bug this covers: `_scorecard_codes` selected `kind` while its docstring
# claimed alert codes. `check_degradations` filters for gate_error/pm_timeout,
# `kind` is alert/digest/pnl, so the sets never intersected and `degradations`
# was green on every day the fund had ever run. The CHECK was tested and
# correct; nothing tested what the builder fed it. Payloads below are real rows
# from 2026-09-02.

def test_parse_alert_codes_reads_the_code_not_the_kind():
    from scripts.dev_status import parse_alert_codes

    rows = [
        {"payload": '{"text": "pm_timeout AAPL \\u2014 defaulted to hold", "code": "pm_timeout"}'},
        {"payload": '{"text": "analyst_turn_failed \\u2014 ExecTurnViolation: required MCP '
                    'server(s) not connected", "code": "seat_turn_failed"}'},
        {"payload": '{"text": "audit 2026-09-02 FAILED", "code": "audit_failed"}'},
    ]
    codes = parse_alert_codes(rows)
    assert codes == ["pm_timeout", "seat_turn_failed", "audit_failed"]
    # The precise regression: none of these is an event `kind`.
    assert not {"alert", "digest", "pnl", "scorecard"} & set(codes)


def test_parse_alert_codes_keeps_a_broken_row_as_an_alert():
    """An unparsable payload is still an alert that happened. Dropping it makes
    the day read quieter than it was, which is the direction that hides things."""
    from scripts.dev_status import parse_alert_codes

    codes = parse_alert_codes([
        {"payload": "not json at all"},
        {"payload": '{"text": "no code field here"}'},
        {"payload": ""},
    ])
    assert codes == ["unparsable_payload", "uncoded_alert"]


def test_parse_alert_codes_counts_repeats_rather_than_deduping():
    """`pm_timeout` fired three times on 2026-09-02, once per ticker. Collapsing
    to a set would report one degraded stage where there were three."""
    from scripts.dev_status import parse_alert_codes

    rows = [{"payload": '{"code": "pm_timeout"}'}] * 3
    assert parse_alert_codes(rows) == ["pm_timeout"] * 3


# --- the broker boundary of _positions_and_coverage (#140) -------------------
# The module docstring promises EXIT 0 ALWAYS: a check that cannot run renders
# as a finding. Two things broke that in opposite directions — a nan qty raised
# straight out of the job, and a bare `except` dressed a code defect as an
# outage. The broker is swapped at the class the function imports, which is
# the only seam it has; nothing below opens a socket.

import sys

import pytest

import scripts.dev_status as ds
from devcheck.checks import check_position_coverage


def _snap_with(positions, broker_error=""):
    from devcheck.model import Snapshot

    return Snapshot(
        droplet_env={}, seat_trading_toolsets={}, orders=[], tickets={},
        events_unposted=0, broker_fill_count=None, checkpoints=[],
        journals_written=set(), seats_participating=set(), alert_codes=[],
        positions=positions, open_orders=[], due_unresolved=[],
        droplet_head="", origin_master="", commits_behind=0, services={},
        broker_error=broker_error,
    )


@pytest.fixture
def fake_broker(monkeypatch):
    """Install a broker whose reads are whatever the test hands back — a list,
    or an exception instance to raise. Credentials are set so the function
    gets past its own missing-key check and reaches the broker."""
    import market.source_alpaca as sa

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")

    def install(positions, orders=()):
        class Fake:
            def open_positions(self):
                if isinstance(positions, BaseException):
                    raise positions
                return list(positions)

            def open_orders(self):
                return list(orders)

        monkeypatch.setattr(sa, "AlpacaSource", Fake)

    return install


def test_a_nan_qty_renders_as_a_finding_rather_than_escaping_the_job(fake_broker):
    """Defect 1. Before: ValueError ('cannot convert float NaN to integer')
    propagated out of the job and it exited non-zero — the one thing its
    docstring forbids. After: the position is unreadable, which fails closed
    into an alert naming the symbol."""
    fake_broker([{"symbol": "NVDA", "qty": "nan", "side": "long"}])

    positions, _orders, _fills, error = ds._positions_and_coverage()

    assert error == "", "the read itself succeeded; this is not a broker error"
    assert positions is not None and [p.symbol for p in positions] == ["NVDA"]
    finding = check_position_coverage(_snap_with(positions))
    assert finding.severity == "alert"
    assert "NVDA" in finding.detail


def test_a_code_defect_in_the_read_is_named_not_dressed_as_an_outage(fake_broker):
    """Defect 2. A TypeError is a bug in this program, fixable in two lines and
    reproducible on any machine. Reported as 'broker read failed' it reads as
    a network problem and invites waiting rather than fixing — which is how
    #119 went unnoticed. Still exit 0: the type is in the row, not a trace."""
    fake_broker(TypeError("'NoneType' object is not iterable"))

    positions, _orders, _fills, error = ds._positions_and_coverage()

    assert positions is None
    assert error.startswith("check crashed: TypeError: "), error
    assert "broker" not in error.split(":")[0]
    finding = check_position_coverage(_snap_with(positions, error))
    assert finding.severity == "alert"
    assert "check crashed: TypeError" in finding.detail


def test_a_broker_api_error_still_renders_as_a_broker_read_failure(fake_broker):
    """Negative control for the test above: narrowing the handler must not
    turn a real outage into 'check crashed'. alpaca-py raises APIError for
    every non-2xx, including 401/403."""
    from alpaca.common.exceptions import APIError

    fake_broker(APIError("401 unauthorized"))

    positions, _orders, _fills, error = ds._positions_and_coverage()

    assert positions is None
    assert error.startswith("broker read failed: "), error


def test_an_import_error_is_a_code_defect_not_an_unavailable_client(monkeypatch):
    """The exact mechanism of #119: a broken import rendered as 'broker client
    unavailable', which reads as credentials or network. A None entry in
    sys.modules makes the import raise ModuleNotFoundError (an ImportError),
    and the row names that concrete type — not a category, not an outage."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setitem(sys.modules, "market.source_alpaca", None)

    positions, _orders, _fills, error = ds._positions_and_coverage()

    assert positions is None
    assert error.startswith("check crashed: ModuleNotFoundError: "), error
    assert "unavailable" not in error


# --- the droplet reads behind units_installed (#220) and g1_backlog (#185) ----
# Neither command is in tests/recordings/dev-status.json: the recording predates
# them and is re-captured only by `make record-status` against the live box. So
# tests/test_status_replay.py stubs both readers, and these pin the parsing on
# bytes in the documented shapes instead — sha256sum's `<hash>  <path>` lines,
# and `sqlite3 -json` rows produced by running the builder's own query against
# a real local fund DB.

import json
import sqlite3
from dataclasses import replace

from devcheck.checks import check_g1_backlog, check_units_installed
from devcheck.model import UnitCopy


def test_parse_unit_hashes_pairs_each_deployed_unit_with_its_installed_copy():
    """A unit in ops/ with no installed copy is carried with installed=None —
    never dropped, which would read as 'nothing to compare'. A stray installed
    unit the repo no longer ships is not this check's question and is ignored."""
    raw = (
        "aaaa  /opt/fund/ops/fund-daily.service\n"
        "bbbb  /opt/fund/ops/fund-pnl.service\n"
        "cccc  /opt/fund/ops/fund-alert@.service\n"
        "aaaa  /etc/systemd/system/fund-daily.service\n"
        "stale  /etc/systemd/system/fund-pnl.service\n"
        "zzzz  /etc/systemd/system/fund-old.service\n"
    )
    assert ds.parse_unit_hashes(raw) == [
        UnitCopy("fund-alert@.service", "cccc", None),
        UnitCopy("fund-daily.service", "aaaa", "aaaa"),
        UnitCopy("fund-pnl.service", "bbbb", "stale"),
    ]


def test_a_stale_installed_unit_reads_red_naming_it():
    """#220 end to end through the builder: the droplet answers with one
    installed hash that differs from its ops/ source."""
    raw = ("bbbb  /opt/fund/ops/fund-pnl.service\n"
           "aaaa  /opt/fund/ops/fund-daily.service\n"
           "aaaa  /etc/systemd/system/fund-daily.service\n"
           "stale  /etc/systemd/system/fund-pnl.service\n")
    with ds.using_transport(lambda cmd, timeout=15: raw):
        units = ds._units_installed()
    f = check_units_installed(replace(_snap_with([]), units=units))
    assert f.severity == "alert"
    assert "fund-pnl.service" in f.detail
    assert "fund-daily.service" not in f.detail


def test_units_installed_is_none_when_the_droplet_did_not_answer():
    with ds.using_transport(lambda cmd, timeout=15: None):
        assert ds._units_installed() is None


def test_units_installed_is_empty_not_none_when_no_unit_was_found():
    """An empty reply is a successful read that found nothing — the check
    alerts on it. None is reserved for 'could not read'."""
    with ds.using_transport(lambda cmd, timeout=15: ""):
        assert ds._units_installed() == []


def _local_db_transport(db_path):
    """Answer the builder's droplet reads from a LOCAL fund DB: FUND_DB from
    the env read, and each `sqlite3 -json` command by running its query here."""
    def transport(cmd, timeout=15):
        if cmd.startswith("grep -h '^FUND_DB='"):
            return f"FUND_DB={db_path}\n"
        if cmd.startswith("sqlite3 -json"):
            query = cmd.split('"', 2)[1]
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute(query)]
            c.close()
            return json.dumps(rows) + "\n" if rows else ""
        raise KeyError(cmd)
    return transport


def test_g1_pending_mirrors_the_selector_on_real_rows(tmp_path, monkeypatch):
    """The builder cannot call state.specs.specs_awaiting_critique against a
    DB it reaches over ssh, so it carries a copy of the predicate — and a second
    copy of that predicate is what tests/test_register_spec_job.py warns about.
    This holds the copy to the original on real rows: one spec pending, one
    critiqued, one advanced past SPEC. Only the first awaits critique."""
    from state.db import connect
    from state.models import StrategySpec
    from state.specs import (advance_to_backtest, insert_strategy_spec,
                             specs_awaiting_critique)
    from tests.test_state_specs import CRITIQUE_SQL, SPEC

    db = tmp_path / "fund.sqlite"
    conn = connect(db)
    # 02:30 UTC on the 2nd is 22:30 ET on the 1st — registered_on is the ET date.
    pending = insert_strategy_spec(conn, StrategySpec(**SPEC), "2026-09-02T02:30:00+00:00")
    reviewed = insert_strategy_spec(
        conn, StrategySpec(**{**SPEC, "hypothesis": "Momentum pays for bearing crash risk."}),
        "2026-09-02T15:00:00+00:00")
    advanced = insert_strategy_spec(
        conn, StrategySpec(**{**SPEC, "hypothesis": "Carry pays for bearing funding risk."}),
        "2026-09-02T15:00:00+00:00")
    conn.execute(CRITIQUE_SQL, (reviewed, "2026-09-03T00:00:00+00:00"))
    assert advance_to_backtest(conn, advanced, expected_state_version=0,
                               now_iso="2026-09-03T00:00:00+00:00")
    for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
        conn.execute("INSERT INTO checkpoints (run_date, stage, status, updated_at)"
                     " VALUES (?, 'research', 'done', ?)", (d, d + "T14:00:00+00:00"))
        conn.execute("INSERT INTO checkpoints (run_date, stage, status, updated_at)"
                     " VALUES (?, 'gate', 'done', ?)", (d, d + "T14:00:00+00:00"))
    conn.commit()
    expected = [p["spec_id"] for p in specs_awaiting_critique(conn, limit=10)]
    conn.close()
    assert expected == [pending], "the fixture must leave exactly one spec pending"

    monkeypatch.setattr(ds, "_ENV_CACHE", {})
    with ds.using_transport(_local_db_transport(db)):
        got, run_dates = ds._g1_state()

    assert [p.spec_id for p in got] == expected
    assert got[0].registered_on == "2026-09-01"
    assert run_dates == ["2026-09-01", "2026-09-02", "2026-09-03"]   # distinct, ordered


def test_g1_state_is_unread_when_either_query_fails():
    """A pending list beside an unread calendar would age every spec as zero,
    which is the quiet direction. Both or neither."""
    def only_env(cmd, timeout=15):
        return "FUND_DB=/x\n" if cmd.startswith("grep -h '^FUND_DB='") else None

    with ds.using_transport(only_env):
        pending, run_dates = ds._g1_state()
    assert pending is None and run_dates == []
    f = check_g1_backlog(replace(_snap_with([]), g1_pending=pending, run_dates=run_dates))
    assert f.severity == "warn" and "not read" in f.detail


def test_registered_on_et_converts_and_never_raises():
    """created_at is ISO-8601 UTC (schema.sql). A malformed one yields "", which
    the check reports rather than ageing the spec as zero."""
    assert ds.registered_on_et("2026-09-02T02:30:00+00:00") == "2026-09-01"
    assert ds.registered_on_et("2026-09-02T15:00:00+00:00") == "2026-09-02"
    assert ds.registered_on_et("not a timestamp") == ""
    assert ds.registered_on_et("2026-09-02T15:00:00") == ""     # naive: rejected, not assumed UTC
