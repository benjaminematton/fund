"""slackkit/redact.py — the in-band alert path must not leak secrets, must not
gut the alert to do it, and must never cost an alert.

Mirrors tests/test_ops_notify.py, which pins the same five rules on the
out-of-band shell path (ops/notify_failure.sh:25-32). The two implementations
are duplicated on purpose — that script is dependency-free of the fund — so
these two test modules move together.
"""
import json
import re

import pytest

from slackkit import redact as redact_mod
from slackkit.outbox import append_alert
from slackkit.redact import redact

SECRET = "aB3dEfGhIjKlMnOpQrSt9zZ"


# --- leaks (mirrors test_ops_notify.py's redaction cases) --------------------

def test_redacts_every_known_secret_prefix():
    """Mirrors test_ops_notify.py::test_redacts_every_known_secret_prefix."""
    leaky = (
        "Traceback: ANTHROPIC_API_KEY=sk-ant-api03-DEADBEEFdeadbeef\n"
        "SLACK_BOT_TOKEN=xoxb-9999-8888-abcdefgh\n"
        "SLACK_APP_TOKEN_EXEC=xapp-1-A099-77-cafebabe\n"
        "ALPACA_API_KEY=PKABCDEFGHIJKLMNOP01\n"
    )
    text = redact(leaky)
    for secret in ("sk-ant-api03-DEADBEEFdeadbeef", "xoxb-9999-8888-abcdefgh",
                   "xapp-1-A099-77-cafebabe", "PKABCDEFGHIJKLMNOP01"):
        assert secret not in text, f"leaked {secret}"
    assert "REDACTED" in text


def test_redacts_a_bare_prefixed_token_with_no_name_beside_it():
    """A token can reach an alert with no NAME= in front of it — a broker
    error that quotes the key it rejected, say. The prefix rules stand alone."""
    for token, keep in (("sk-ant-api03-DEADBEEFdeadbeef", "sk-ant-"),
                        ("xoxb-9999-8888-abcdefgh", "xoxb-"),
                        ("xapp-1-A099-77-cafebabe", "xapp-"),
                        ("PKABCDEFGHIJKLMNOP01", "PK-")):
        text = redact(f"broker rejected {token}")
        assert token not in text, f"leaked {token}"
        assert keep + "REDACTED" in text


def test_redacts_secret_key_with_no_recognized_value_prefix():
    """ALPACA_SECRET_KEY's value has none of the four known prefixes, so only
    name-based redaction catches it."""
    text = redact(f"ALPACA_SECRET_KEY={SECRET}")
    assert SECRET not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_every_real_env_var_in_this_project():
    """The name rule is anchored to ALL_CAPS env-var shape. Every credential
    this fund actually carries is that shape — see .env."""
    for name in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 "SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN_EXEC", "SLACK_APP_TOKEN_EXEC"):
        assert SECRET not in redact(f"{name}={SECRET}"), f"{name} leaked"


def test_redacts_python_os_environ_repr_form():
    """A dumped os.environ prints as a python dict repr — NAME': 'VALUE'."""
    text = redact("{'ALPACA_SECRET_KEY': '%s'}" % SECRET)
    assert SECRET not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_json_form():
    """A JSON-formatted log line pairs NAME and VALUE with a colon and double
    quotes rather than =."""
    text = redact('{"ALPACA_SECRET_KEY": "%s"}' % SECRET)
    assert SECRET not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_plain_colon_form():
    """NAME: VALUE with no quotes at all."""
    text = redact(f"ALPACA_SECRET_KEY: {SECRET}")
    assert SECRET not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_keeps_the_name_and_normalises_colon_to_equals():
    """The shell's replacement is `\\1=REDACTED`: the operator still learns
    WHICH credential the line was about, and both delimiters render the same."""
    assert redact(f"ALPACA_SECRET_KEY: {SECRET}") == "ALPACA_SECRET_KEY=REDACTED"
    assert redact(f"ALPACA_SECRET_KEY={SECRET}") == "ALPACA_SECRET_KEY=REDACTED"


# --- over-redaction (the alert must still say what broke) -------------------

def test_diagnostic_text_naming_a_credential_survives_readable():
    """market/source_alpaca.py uses os.environ["ALPACA_SECRET_KEY"], so a
    missing credential on a fresh host raises KeyError naming the variable —
    the ONE fact the operator needs. An earlier unanchored rule on the shell
    side turned it into `KeyError=REDACTED`; a Python port written as
    \\w*(KEY|TOKEN|...)\\w* would re-break it."""
    text = redact("KeyError: 'ALPACA_SECRET_KEY'")
    assert "KeyError" in text
    assert "ALPACA_SECRET_KEY" in text, f"variable name destroyed: {text}"


def test_does_not_redact_ordinary_assignment_that_is_not_a_credential():
    """A plain NAME=VALUE log line whose NAME isn't credential-shaped must
    survive verbatim: NAME=VALUE alone is not a trigger."""
    assert redact("run_day: universe=NVDA,MSFT,AAPL") == \
        "run_day: universe=NVDA,MSFT,AAPL"


def test_does_not_redact_ordinary_lines_with_colons_or_equals():
    """Redaction extended to colons must not start eating ordinary log lines
    that happen to contain a colon or an equals sign but no credential."""
    text = redact("run_day: market is closed\nqty=80 stop=215")
    assert "run_day: market is closed" in text
    assert "qty=80 stop=215" in text


def test_the_exec_turn_violation_alert_shape_survives_intact():
    """`exec_turn_violation — {exc}` is one of the three leaky sites. Its
    ordinary output is an exception type followed by a colon, which is exactly
    the shape a careless name rule eats."""
    text = redact("exec_turn_violation — ExecTurnViolation: alpaca failed")
    assert text == "exec_turn_violation — ExecTurnViolation: alpaca failed"


def test_the_seat_turn_failed_alert_keeps_its_diagnosis():
    """The whole point of that alert is naming the exception and the fallback."""
    original = ("pm_turn_failed — TimeoutError: seat exceeded 300s;"
                " stage default applies (default is HOLD)")
    assert redact(original) == original


# --- must never cost an alert ----------------------------------------------

class _Boom:
    """Stands in for a compiled pattern whose .sub() blows up."""

    def sub(self, replacement, text):
        raise RuntimeError("regex engine exploded")


def test_a_failing_rule_passes_the_original_text_through(monkeypatch, caplog):
    """append_alert deliberately validates nothing, because a raise on the
    alert path turns "something needs review" into a dead trading day
    (invariant 4). A redactor that can raise would reintroduce exactly that.
    A leaked alert is bad; a lost alert is worse."""
    monkeypatch.setattr(redact_mod, "_RULES", ((_Boom(), "x"),))
    original = f"ALPACA_SECRET_KEY={SECRET}"
    assert redact(original) == original
    assert "redact" in caplog.text.lower()


def test_a_failure_midway_through_the_rules_discards_partial_work(monkeypatch):
    """Not just "does not raise": the fallback must be the ORIGINAL text, not
    whatever half-redacted string the loop had reached."""
    monkeypatch.setattr(
        redact_mod, "_RULES",
        ((re.compile("closed"), "OPEN"), (_Boom(), "x")))
    assert redact("run_day: market is closed") == "run_day: market is closed"


def test_non_string_input_is_returned_unchanged(monkeypatch):
    """Fails open on a caller that hands over something unexpected rather than
    raising inside an `except` block."""
    assert redact(None) is None


def test_append_alert_still_records_the_alert_when_redaction_breaks(
        fund_db, monkeypatch):
    """The end-to-end version of the guarantee: the row lands, with the
    original text, even with redaction broken."""
    monkeypatch.setattr(redact_mod, "_RULES", ((_Boom(), "x"),))
    original = f"run_day_failed — RuntimeError: ALPACA_SECRET_KEY={SECRET}"
    rowid = append_alert(fund_db, "run_day_failed", original,
                         now_iso="2026-08-27T13:00:00+00:00")
    payload = json.loads(fund_db.execute(
        "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
    assert payload["text"] == original
    assert payload["code"] == "run_day_failed"


# --- wiring: the stored row is what BOTH egresses read ----------------------

def test_append_alert_stores_redacted_text(fund_db):
    """Slack reads the stored row via drain(); GitHub reads it via
    scripts/file_alert_issues.py, which writes it into an issue title and
    body. Redacting at the store covers both."""
    rowid = append_alert(
        fund_db, "run_day_failed",
        f"run_day_failed — RuntimeError: ALPACA_SECRET_KEY={SECRET}",
        now_iso="2026-08-27T13:00:00+00:00")
    payload = json.loads(fund_db.execute(
        "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
    assert SECRET not in payload["text"], payload["text"]
    assert "ALPACA_SECRET_KEY=REDACTED" in payload["text"]
    assert payload["text"].startswith("run_day_failed — RuntimeError:")


def test_append_alert_leaves_an_ordinary_alert_untouched(fund_db):
    """The overwhelming majority of alerts carry no credential. Redaction must
    be invisible to them — these are the exact texts other tests pin."""
    for code, text in (("unprotected_position", "NVDA 40 exposed"),
                       ("pm_timeout", "pm_timeout AAPL — defaulted to hold"),
                       ("accounting_shortfall", "NVDA agrees again at 40")):
        rowid = append_alert(fund_db, code, text,
                             now_iso="2026-08-27T13:00:00+00:00")
        payload = json.loads(fund_db.execute(
            "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
        assert payload["text"] == text


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "ALPACA_SECRET_KEY"])
def test_the_github_egress_title_cannot_carry_a_credential(fund_db, name):
    """scripts/file_alert_issues.py:43-46 slices the stored text into a public
    issue TITLE. Truncation is not redaction — the secret must already be gone
    by the time it is stored."""
    rowid = append_alert(fund_db, "seat_turn_failed",
                         f"seat_turn_failed — RuntimeError: {name}={SECRET}",
                         now_iso="2026-08-27T13:00:00+00:00")
    payload = json.loads(fund_db.execute(
        "SELECT payload FROM events WHERE id=?", (rowid,)).fetchone()["payload"])
    assert SECRET not in payload["text"]
