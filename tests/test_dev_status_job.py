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
