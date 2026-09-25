"""ops/notify_failure.sh — the alert path must not leak secrets or lie about success."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "notify_failure.sh"


def _fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return p


def _run(tmp_path, journal_text, curl_body, unit="fund-daily.service", extra_env=None):
    """Run the script with fake journalctl and fake curl; return (proc, payload)."""
    journalctl = _fake_bin(tmp_path, "journalctl", f"cat <<'EOF'\n{journal_text}\nEOF")
    # fake curl writes the request body it was handed to payload.json, then answers
    payload = tmp_path / "payload.json"
    curl = _fake_bin(
        tmp_path,
        "curl",
        f'for a in "$@"; do prev=$last; last=$a; '
        f'if [ "$prev" = "-d" ] || [ "$prev" = "--data" ]; then printf %s "$a" > {payload}; fi; done\n'
        f"cat <<'EOF'\n{curl_body}\nEOF",
    )
    env = {
        **os.environ,
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "FUND_ALERT_CHANNEL": "#risk",
        "FUND_ALERT_CURL": str(curl),
        "FUND_ALERT_JOURNALCTL": str(journalctl),
        **(extra_env or {}),
    }
    proc = subprocess.run([str(SCRIPT), unit], capture_output=True, text=True, env=env)
    body = json.loads(payload.read_text()) if payload.exists() else None
    return proc, body


def test_posts_unit_name_and_channel(tmp_path):
    proc, body = _run(tmp_path, "all fine", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert body["channel"] == "#risk"
    assert "fund-daily.service" in body["text"]


def test_redacts_every_known_secret_prefix(tmp_path):
    leaky = (
        "Traceback: ANTHROPIC_API_KEY=sk-ant-api03-DEADBEEFdeadbeef\n"
        "SLACK_BOT_TOKEN=xoxb-9999-8888-abcdefgh\n"
        "SLACK_APP_TOKEN_EXEC=xapp-1-A099-77-cafebabe\n"
        "ALPACA_API_KEY=PKABCDEFGHIJKLMNOP01\n"
    )
    proc, body = _run(tmp_path, leaky, '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    for secret in ("sk-ant-api03-DEADBEEFdeadbeef", "xoxb-9999-8888-abcdefgh",
                   "xapp-1-A099-77-cafebabe", "PKABCDEFGHIJKLMNOP01"):
        assert secret not in text, f"leaked {secret}"
    assert "REDACTED" in text


def test_nonzero_exit_when_slack_says_not_ok(tmp_path):
    """Slack returns HTTP 200 with ok:false on auth errors — curl --fail cannot see it."""
    proc, _ = _run(tmp_path, "boom", '{"ok":false,"error":"invalid_auth"}')
    assert proc.returncode != 0
    assert "invalid_auth" in (proc.stderr + proc.stdout)


def test_payload_is_valid_json_despite_quotes_in_journal(tmp_path):
    proc, body = _run(tmp_path, 'he said "hi" and \\ backslashed', '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert 'he said "hi"' in body["text"]


def test_redacts_secret_key_with_no_recognized_value_prefix(tmp_path):
    """ALPACA_SECRET_KEY's value has none of the four known prefixes, so only
    name-based redaction catches it — this is the reviewer's exact repro."""
    proc, body = _run(tmp_path, "ALPACA_SECRET_KEY=aB3dEfGhIjKlMnOpQrSt9zZ", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_every_real_env_var_in_this_project(tmp_path):
    """The name rule is anchored to ALL_CAPS env-var shape and to a keyword
    substring. The inventory is /etc/fund/env (ops/README.md), not .env:
    HC_PING_URL lives only there, carries no KEY/TOKEN/SECRET/PASSWORD, and
    was missed for exactly that reason (#146)."""
    for name in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 "SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN_EXEC", "SLACK_APP_TOKEN_EXEC",
                 "HC_PING_URL"):
        proc, body = _run(tmp_path, f"{name}=aB3dEfGhIjKlMnOpQrSt9zZ", '{"ok":true}')
        assert proc.returncode == 0, proc.stderr
        assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in body["text"], f"{name} leaked"


# --- HC_PING_URL (#146): bearer-equivalent, neither prefix-shaped nor
# credential-named. Holding it forges the fund's liveness heartbeat.
HC_UUID = "3f8e1c2a-9b4d-4e6f-8a7b-1c2d3e4f5a6b"
HC_URL = f"https://hc-ping.com/{HC_UUID}"


def test_redacts_hc_ping_url_by_name(tmp_path):
    """NAME=VALUE, the shape /etc/fund/env is written in."""
    proc, body = _run(tmp_path, f"HC_PING_URL={HC_URL}", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert HC_UUID not in body["text"], f"leaked ping url: {body['text']}"
    assert "HC_PING_URL=REDACTED" in body["text"]


def test_redacts_hc_ping_url_in_an_os_environ_dump(tmp_path):
    """The traceback shape the redactor exists for: environ({...}) repr."""
    proc, body = _run(
        tmp_path, f"environ({{'HC_PING_URL': '{HC_URL}'}})", '{"ok":true}'
    )
    assert proc.returncode == 0, proc.stderr
    assert HC_UUID not in body["text"], f"leaked ping url: {body['text']}"


def test_redacts_a_bare_hc_ping_url_inside_an_exception_message(tmp_path):
    """No NAME= in front of it — curl quoting the URL it could not reach. The
    name rule cannot see this; only a value-shaped rule can. The diagnosis
    around the URL must survive."""
    line = f"curl: (28) Connection timed out after 20001 ms for {HC_URL}/0"
    proc, body = _run(tmp_path, line, '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert HC_UUID not in text, f"leaked ping url: {text}"
    assert "curl: (28) Connection timed out after 20001 ms for" in text
    assert "https://hc-ping.com/REDACTED" in text


def test_redacts_hc_ping_url_with_uppercase_uuid_and_subdomain_host(tmp_path):
    """An uppercase-hex UUID does not trip PK[A-Z0-9]{16,} (the hyphens break
    the run), and healthchecks serves regional hosts under hc-ping.com."""
    upper = HC_UUID.upper()
    proc, body = _run(
        tmp_path, f"pinged https://eu.hc-ping.com/{upper}/fail", '{"ok":true}'
    )
    assert proc.returncode == 0, proc.stderr
    assert upper not in body["text"], f"leaked ping url: {body['text']}"
    assert "https://hc-ping.com/REDACTED" in body["text"]


def test_does_not_redact_an_alpaca_order_url(tmp_path):
    """The value rule is host-anchored on purpose. An alpaca-py traceback
    names the order it failed on by URL, and that UUID is the diagnosis."""
    line = ("HTTPError: 404 for https://paper-api.alpaca.markets/v2/orders/"
            "9a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d")
    proc, body = _run(tmp_path, line, '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert line in body["text"], body["text"]


def test_diagnostic_text_naming_a_credential_survives_readable(tmp_path):
    """The anchor exists for this case. market/source_alpaca.py uses
    os.environ["ALPACA_SECRET_KEY"], so a missing credential on a fresh host
    raises KeyError naming the variable — the ONE fact the operator needs.
    An earlier unanchored rule turned it into `KeyError=REDACTED`."""
    proc, body = _run(tmp_path, "KeyError: 'ALPACA_SECRET_KEY'", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert "KeyError" in body["text"]
    assert "ALPACA_SECRET_KEY" in body["text"], f"variable name destroyed: {body['text']}"


def test_does_not_redact_ordinary_assignment_that_is_not_a_credential(tmp_path):
    """Over-redaction would gut the alert's usefulness: a plain NAME=VALUE log
    line whose NAME isn't credential-shaped must survive verbatim."""
    proc, body = _run(tmp_path, "run_day: universe=NVDA,MSFT,AAPL", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert "run_day: universe=NVDA,MSFT,AAPL" in body["text"]


def test_redacts_python_os_environ_repr_form(tmp_path):
    """A dumped os.environ prints as a python dict repr — NAME': 'VALUE', not
    NAME=VALUE. This is the reviewer's exact colon-delimited-leak repro."""
    proc, body = _run(
        tmp_path, "{'ALPACA_SECRET_KEY': 'aB3dEfGhIjKlMnOpQrSt9zZ'}", '{"ok":true}'
    )
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_json_form(tmp_path):
    """A JSON-formatted log line also pairs NAME and VALUE with a colon and
    double quotes rather than =."""
    proc, body = _run(
        tmp_path, '{"ALPACA_SECRET_KEY": "aB3dEfGhIjKlMnOpQrSt9zZ"}', '{"ok":true}'
    )
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_redacts_plain_colon_form(tmp_path):
    """NAME: VALUE with no quotes at all — the plainest colon-delimited shape."""
    proc, body = _run(
        tmp_path, "ALPACA_SECRET_KEY: aB3dEfGhIjKlMnOpQrSt9zZ", '{"ok":true}'
    )
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in text, f"leaked secret: {text}"
    assert "REDACTED" in text


def test_headline_states_the_consequence_not_just_the_unit_name(tmp_path):
    """The operator reads this on a phone. `fund-daily.service failed / exit=1`
    is true but does not say what it cost — 2026-08-18's alert delivered
    correctly and still did not convey that the firm was not trading."""
    proc, body = _run(tmp_path, "boom", '{"ok":true}', unit="fund-daily.service")
    assert proc.returncode == 0, proc.stderr
    assert "did not trade" in body["text"].lower(), body["text"]


def test_headline_never_claims_positions_are_safe(tmp_path):
    """The script cannot know this. Default HOLD usually means no orders, but a
    failure AFTER an order lands exits identically. Asserting safety the alert
    has not verified is worse than saying nothing."""
    proc, body = _run(tmp_path, "boom", '{"ok":true}')
    text = body["text"].lower()
    for lie in ("no orders", "positions untouched", "nothing was placed"):
        assert lie not in text, f"alert asserts unverified safety: {lie}"


def test_fund_pnl_headline_does_not_assert_pnl_was_not_posted(tmp_path):
    """fund-pnl.service runs five ExecStart legs in order and only the first
    posts P&L. A failure in any later leg used to be headlined "No P&L was
    posted for today" — false, an hour after it posted (#183). The unit name
    cannot say which leg failed, so the headline must be true for every leg."""
    proc, body = _run(tmp_path, "boom", '{"ok":true}', unit="fund-pnl.service")
    assert proc.returncode == 0, proc.stderr
    text = body["text"].lower()
    assert "no p&l was posted" not in text, body["text"]
    assert "post-close" in text, body["text"]


def test_headline_falls_back_for_an_unrecognized_unit(tmp_path):
    proc, body = _run(tmp_path, "boom", '{"ok":true}', unit="fund-something-new.service")
    assert proc.returncode == 0, proc.stderr
    assert "fund-something-new.service" in body["text"]


def test_mention_is_prepended_when_configured(tmp_path):
    """A channel-preference notification is per-device and resets on reinstall.
    A real mention in the payload notifies regardless of client settings."""
    proc, body = _run(
        tmp_path, "boom", '{"ok":true}', extra_env={"FUND_ALERT_MENTION": "<@U123ABC>"}
    )
    assert proc.returncode == 0, proc.stderr
    assert body["text"].startswith("<@U123ABC>"), body["text"]


def test_mention_is_optional_and_leaves_no_stray_whitespace(tmp_path):
    """It must stay unset-safe: /etc/fund/alert-env on a fresh host has no
    FUND_ALERT_MENTION, and `set -u` is on."""
    proc, body = _run(tmp_path, "boom", '{"ok":true}')
    assert proc.returncode == 0, proc.stderr
    assert body["text"].startswith(":rotating_light:"), body["text"]


def test_journal_tail_survives_the_new_framing(tmp_path):
    """The headline is framing, not a replacement — the evidence must remain."""
    proc, body = _run(tmp_path, "ExecTurnViolation: alpaca failed", '{"ok":true}')
    assert "ExecTurnViolation: alpaca failed" in body["text"]


def test_does_not_redact_ordinary_lines_with_colons_or_equals(tmp_path):
    """Extending redaction to colons must not start eating ordinary log lines
    that happen to contain a colon or an equals sign but no credential."""
    proc, body = _run(
        tmp_path,
        "run_day: market is closed\nqty=80 stop=215",
        '{"ok":true}',
    )
    assert proc.returncode == 0, proc.stderr
    text = body["text"]
    assert "run_day: market is closed" in text
    assert "qty=80 stop=215" in text
