"""The lint's own negative controls. A lint whose negative control also
passes is not a lint — this repo has three documented instances of exactly
that, two caught by luck."""
import importlib.util, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_alert_codes.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_alert_codes", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_tree_is_clean():
    assert subprocess.run([sys.executable, str(SCRIPT)]).returncode == 0


def test_a_direct_append_event_alert_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_event(conn, "alert", {"text": "x"}, now)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "append_alert" in errors[0]


def test_an_interpolated_code_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_alert(conn, f"{seat}_turn_failed", "x", now_iso=n)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "string literal" in errors[0]


def test_a_shouty_code_is_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text('append_alert(conn, "PM Timeout", "x", now_iso=n)\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "lower_snake" in errors[0]


def test_a_good_call_is_accepted(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('append_alert(conn, "pm_timeout", "x", now_iso=n)\n')
    assert _load().check_file(p) == []


def test_a_non_alert_append_event_is_accepted(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('append_event(conn, "fill", {"text": "x"}, now)\n')
    assert _load().check_file(p) == []


def test_a_wrapper_forwarding_its_own_code_is_accepted(tmp_path):
    """scripts/run_day.py's _alert logs then delegates, so it forwards a
    variable by construction. That is a forwarder, not a dynamic code."""
    p = tmp_path / "good.py"
    p.write_text(
        "def _alert(conn, clock, code, text, **payload):\n"
        "    log(text)\n"
        "    append_alert(conn, code, text, now_iso=n, **payload)\n")
    assert _load().check_file(p) == []


def test_a_wrappers_own_callers_still_owe_a_literal(tmp_path):
    """The exemption must not leak to the call sites — otherwise routing
    through a wrapper would launder a dynamic code past the lint."""
    p = tmp_path / "bad.py"
    p.write_text('_alert(conn, clock, f"{seat}_turn_failed", "x")\n')
    errors = _load().check_file(p)
    assert len(errors) == 1 and "string literal" in errors[0]


def test_a_wrapper_call_site_with_a_literal_is_accepted(tmp_path):
    p = tmp_path / "good.py"
    p.write_text('_alert(conn, clock, "seat_turn_failed", "x")\n')
    assert _load().check_file(p) == []
