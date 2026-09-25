"""scripts/replay_day.py, the CLI behind `make replay REC=<recording>` (#163).

One recording, replayed through tests/test_sim_day.py's composition — the
same injected clock, FakeSlack, FakeAlpaca, temp DB and REAL tools, gate and
hooks that `make sim-day` runs — with a printed summary and the audit's exit
code. The recordings are the tracked corpus in tests/recordings/, so the
expected fill below is the golden day's (fixtures/golden-day.md), not a value
the wrapper computes."""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "replay_day.py"
RECORDINGS = Path(__file__).with_name("recordings")
GOLDEN_EXEC = RECORDINGS / "mvf_exec.jsonl"

# Every credential .env.example names. The replay must not need one.
SECRETS = ("ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
           "SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN_EXEC")


def _load():
    """scripts/ is not a package — same loader as tests/test_audit_day.py."""
    spec = importlib.util.spec_from_file_location("replay_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if k not in SECRETS and k != "REC"}


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT,
                          env=_scrubbed_env())


def _make(*args) -> subprocess.CompletedProcess:
    """`-o deps` marks the phony bootstrap prerequisite up to date, so the
    target under test runs without touching .venv; PYTHON is overridden to
    this interpreter for the same reason."""
    return subprocess.run(["make", "-C", str(ROOT), "-o", "deps",
                           f"PYTHON={sys.executable}", "replay", *args],
                          capture_output=True, text=True, env=_scrubbed_env())


# --- the replay itself --------------------------------------------------------

def test_cli_replays_the_golden_exec_turn_to_its_fill():
    """The golden day's exec recording, replayed as the execution turn of a
    full simulated day: the gate caps the PM's 80 at 66, the broker fills at
    180.14, the day audits clean, exit 0. The fill line is the day's OWN
    digest text, rendered by production code, not re-derived here."""
    done = _run(str(GOLDEN_EXEC))
    assert done.returncode == 0, done.stderr
    assert "fills: NVDA buy 66@180.14" in done.stdout
    assert "AUDIT CLEAN 2026-07-06" in done.stdout


def test_cli_exits_nonzero_when_the_day_does_not_audit_clean():
    """mvf_exec_two.jsonl also sells 40 MSFT, which the default day never
    ticketed: the gate denies that leg, the denial is an alert, and an alert
    fails the audit (scripts/audit_day.py). A replay that cannot go red says
    nothing about current code."""
    done = _run(str(RECORDINGS / "mvf_exec_two.jsonl"))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "denied: 1" in done.stdout
    assert "AUDIT CLEAN" not in done.stdout


def test_replay_constructs_no_llm_client_and_opens_no_socket(monkeypatch):
    """The LLM is the only thing the recording replaces (agents/replay.py), so
    the whole path must run with every SDK client constructor, the resolver
    and every credential removed. Patched, not merely observed: an assertion
    that nothing was called needs the call to be impossible."""
    import claude_agent_sdk

    def forbidden(*args, **kwargs):
        raise AssertionError("the replay path reached the network or an LLM client")

    monkeypatch.setattr(claude_agent_sdk.ClaudeSDKClient, "__init__", forbidden)
    monkeypatch.setattr(claude_agent_sdk, "query", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for var in SECRETS:
        monkeypatch.delenv(var, raising=False)

    assert _load().main([str(GOLDEN_EXEC)]) == 0
    assert "anthropic" not in sys.modules


# --- usage ---------------------------------------------------------------------

def test_cli_usage_without_a_recording():
    done = _run()
    assert done.returncode == 2
    assert "usage:" in done.stderr


def test_cli_refuses_a_seat_that_has_no_stage_in_a_simulated_day():
    """A Critic turn is a nightly job, not a stage of the trading day
    (orchestrator/daily.py), so there is no slot to replay it into. Refuse
    loudly rather than run a day that silently ignores the file."""
    done = _run(str(RECORDINGS / "critic_g1_clear.jsonl"))
    assert done.returncode == 2
    assert "critic" in done.stderr


def test_make_replay_runs_the_script_with_rec():
    done = _make(f"REC={GOLDEN_EXEC}")
    assert done.returncode == 0, done.stderr
    assert "AUDIT CLEAN 2026-07-06" in done.stdout


def test_make_replay_without_rec_fails_before_running_anything():
    done = _make()
    assert done.returncode == 2
    assert "REC=<recording.jsonl> is required" in done.stderr
    assert "replay_day.py" not in done.stdout        # the recipe never ran
