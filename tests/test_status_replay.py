"""build_snapshot() against RECORDED production bytes.

THE GAP THIS CLOSES. devcheck/'s checks are thoroughly tested in
tests/test_devcheck_checks.py — against Snapshots built by hand. Nothing tested
what build_snapshot() actually puts in them, and on 2026-09-02 that one untested
layer held six defects at once. The sharpest: `check_degradations` had a passing
test feeding it ["pm_timeout"] while the builder fed it event `kind`s
(alert/digest/pnl). Those sets never intersect, so it was green on every day the
fund had ever run — including a day three stages degraded to HOLD.

A hand-seeded fake droplet would not have caught that; it just relocates the
hand-writing that caused the bug. So the fixture is REAL BYTES captured from the
real box by `make record-status`, and the assertions below were written by
reading it. That reading is the control, not the file.

SCOPE, stated rather than implied. This covers the DROPLET/SQL boundary, which
is where all six defects lived. build_snapshot() has three other boundaries —
the Alpaca SDK, the `gh` CLI, and a local `git fetch` — and they are stubbed
below, NOT covered. Their fields (positions, broker_fill_count, tracked_checks,
droplet_head, origin_master, commits_behind) are not tested here and no claim is
made about them. The `git fetch` in particular is why they are stubbed at all:
leaving it live would put a network call inside `make test`.

STALENESS. A recording replayed forever becomes confidently wrong, which is the
failure this whole file exists to prevent. tests/test_status_faithful.py
(`-m live`, `make status-faithful`) re-reads the real droplet and checks these
shapes still hold — the same job `make surface-pin` does for the broker.

DOCTRINE (#190 leaves this open; this is the answer for this artifact): the
recording is a golden INPUT. Re-capturing it is legitimate whenever the
faithfulness check says the shapes moved. Weakening an assertion below is not.
Inputs may be re-captured; expectations may not be weakened.
"""

import json
from pathlib import Path

import pytest

import scripts.dev_status as ds

RECORDING = Path(__file__).resolve().parents[1] / "tests/recordings/dev-status.json"


@pytest.fixture
def recorded():
    return json.loads(RECORDING.read_text())


@pytest.fixture
def snapshot(recorded, monkeypatch):
    """A Snapshot built from real recorded droplet bytes."""
    def replay(cmd: str, timeout: int = 15):
        if cmd not in recorded:
            # LOUD, not None. None means "could not read", which every check
            # renders as unknown — so a silently-unrecorded command would make
            # this test pass while measuring nothing. An unknown command means
            # the builder now asks something the recording predates: re-record.
            raise KeyError(
                f"command not in the recording, so it was never captured from "
                f"production:\n  {cmd}\nRun `make record-status` to refresh.")
        return recorded[cmd]

    # The three boundaries this test does NOT cover. Stubbed to constants so the
    # suite stays offline; see the module docstring.
    monkeypatch.setattr(ds, "_positions_and_coverage", lambda: ([], [], None, ""))
    monkeypatch.setattr(ds, "_tracked_checks", frozenset)
    monkeypatch.setattr(ds, "_deploy_state", lambda: ("stub123", "stub123", 0))
    monkeypatch.setattr(ds, "_ENV_CACHE", {})     # the real cache would leak between tests

    with ds.using_transport(replay):
        return ds.build_snapshot()


# --- the defect this whole file exists for -----------------------------------

def test_alert_codes_carry_payload_codes_not_event_kinds(snapshot):
    """THE 2026-09-02 defect, pinned end to end.

    The builder used to select `kind`. If it ever does again, this fails: the
    recording's alert rows carry `seat_turn_failed` and `pm_timeout` in
    payload.code, and `alert`/`digest`/`pnl` in kind.
    """
    assert snapshot.alert_codes is not None, "recorded reads succeeded; this cannot be None"
    assert "seat_turn_failed" in snapshot.alert_codes
    assert "pm_timeout" in snapshot.alert_codes
    assert not {"alert", "digest", "pnl", "scorecard"} & set(snapshot.alert_codes), (
        "these are event KINDs — the builder is reading the wrong column again")


def test_degradations_actually_fires_on_the_recorded_day(snapshot):
    """The check + the builder together, which is the pair nobody tested.

    On the recorded day pm_timeout fired three times. `degradations` reported
    "no stage degraded to its default" for the entire life of the fund because
    of what the builder handed it.
    """
    from devcheck.evaluate import evaluate

    f = next(x for x in evaluate(snapshot) if x.check == "degradations")
    assert f.severity == "warn", (
        "pm_timeout is in the recording; a clean read here means the builder "
        "and the check have drifted apart again")
    assert "pm_timeout" in f.detail


def test_services_names_the_codes_from_the_recorded_day(snapshot):
    from devcheck.evaluate import evaluate

    f = next(x for x in evaluate(snapshot) if x.check == "services")
    assert f.severity == "alert"                       # fund-daily: exit-code
    assert "seat_turn_failed" in f.detail
    assert "2026-09-02" in f.detail


# --- the fields the droplet boundary is responsible for ----------------------

def test_droplet_derived_fields_are_all_populated(snapshot):
    """Completeness. Catches a field the builder silently never sets.

    Not sufficient on its own — alert_codes was populated throughout the
    degradations bug, just with the wrong column — which is why the assertions
    above name specific values from the recording.
    """
    assert snapshot.droplet_env.get("ALPACA_PAPER_TRADE") == "true"   # invariant 1
    assert snapshot.seat_trading_toolsets, "no seat configs were parsed"
    assert snapshot.orders, "the recording has 6 order rows"
    assert snapshot.tickets
    assert snapshot.checkpoints
    assert snapshot.services
    assert snapshot.alert_codes is not None
    assert snapshot.alert_date == "2026-09-02"
    assert snapshot.db_read_ok is True


def test_only_the_exec_seat_holds_the_trading_toolset(snapshot):
    """Invariant 2, read from the droplet's real config in the recording —
    not from this checkout, which is not what trades."""
    assert snapshot.seat_trading_toolsets["exec"] is True
    others = {s: v for s, v in snapshot.seat_trading_toolsets.items() if s != "exec"}
    assert others, "no non-exec seats parsed, so this proves nothing"
    assert not any(others.values()), f"a non-exec seat holds `trading`: {others}"


def test_service_results_parse_both_states_present_in_the_recording(snapshot):
    """fund-daily exit-code, fund-pnl success — both shapes in one fixture, so
    a parser that only ever handles one of them fails here."""
    assert snapshot.services["fund-daily"].result == "exit-code"
    assert snapshot.services["fund-daily"].last_run == "Wed 2026-09-02 09:36:51 EDT"
    assert snapshot.services["fund-pnl"].result == "success"


def test_an_unrecorded_command_fails_loudly_rather_than_reading_as_unknown(recorded):
    """The recording's own staleness guard.

    A missing command must not return None: None is "could not read", every
    check renders that as unknown, and the suite would stay green while
    measuring nothing.
    """
    def replay(cmd: str, timeout: int = 15):
        if cmd not in recorded:
            raise KeyError(cmd)
        return recorded[cmd]

    with ds.using_transport(replay):
        with pytest.raises(KeyError):
            ds._ssh("systemctl show something-nobody-recorded.service")


def test_the_transport_seam_restores_itself_even_when_the_body_raises():
    """A leaked fake transport would make the rest of the session green against
    a world that does not exist."""
    before = ds._TRANSPORT
    with pytest.raises(RuntimeError):
        with ds.using_transport(lambda cmd, timeout=15: "nonsense"):
            raise RuntimeError("boom")
    assert ds._TRANSPORT is before
