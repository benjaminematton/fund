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
