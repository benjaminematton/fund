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


def _run(tmp_path, journal_text, curl_body, unit="fund-daily.service"):
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
    """The name rule is anchored to ALL_CAPS env-var shape. Every credential
    this fund actually carries is that shape — see .env."""
    for name in ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 "SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN_EXEC", "SLACK_APP_TOKEN_EXEC"):
        proc, body = _run(tmp_path, f"{name}=aB3dEfGhIjKlMnOpQrSt9zZ", '{"ok":true}')
        assert proc.returncode == 0, proc.stderr
        assert "aB3dEfGhIjKlMnOpQrSt9zZ" not in body["text"], f"{name} leaked"


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
