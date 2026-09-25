"""The Alpaca MCP subprocess inherits ONLY an allow-list of the parent
environment (#128).

That subprocess sits DOWNSTREAM of the PreToolUse order gate and holds the raw
broker credentials; a hook in the SDK process cannot constrain what a
separate OS process does with what it was handed. And it was handed
everything: both spawning hops are additive. The SDK starts the CLI with
{**os.environ, **options.env} (claude_agent_sdk/_internal/transport/
subprocess_cli.py, connect()), and the CLI starts a stdio MCP server with
{...process.env, ...config.env} — which is how the server authenticates today
although agents/seats.py never hands it a key. On the droplet that parent
environment is /etc/fund/env entire: ANTHROPIC_API_KEY, the Slack tokens,
HC_PING_URL.

So the `env` dict on the server config is an OVERLAY, not a scope, and an
assertion on that dict pins nothing about what the child received. The one
lever this repo owns is the command it launches: a POSIX-sh prologue that
unsets every name not on the allow-list and then exec's uvx. These tests RUN
that prologue under a deliberately polluted environment and read back what
the program at the end of it saw.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

from agents.seats import ALPACA_MCP_SPEC, build_seat_options, load_seat_config
from orchestrator.clock import SimClock

ALL_SEATS = ("exec", "analyst", "news", "pm", "critic", "reflect", "quant")

# What /etc/fund/env and the two spawning hops put in the parent environment
# (.env.example, ops/fund-daily.service, subprocess_cli.py, the CLI's spawn).
# Every value is a sentinel that must NOT reach the child.
POLLUTED = {
    "PATH": os.environ["PATH"],
    "HOME": "/home/fund",
    "ALPACA_API_KEY": "PKtest",
    "ALPACA_SECRET_KEY": "sekrit",
    "ALPACA_PAPER_TRADE": "true",
    "ANTHROPIC_API_KEY": "sk-ant-LEAK",
    "SLACK_BOT_TOKEN": "xoxb-LEAK",
    "SLACK_BOT_TOKEN_EXEC": "xoxb-LEAK-exec",
    "SLACK_CHANNEL_OVERRIDES": "",
    "HC_PING_URL": "https://hc/LEAK",
    "FUND_DB": "/var/lib/fund/fund.sqlite",
    "FUND_JOURNALS": "/var/lib/fund/journals",
    "FUND_TRACES": "/var/lib/fund/traces",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    "CLAUDE_CODE_SESSION_ID": "sess",
    "CLAUDE_PROJECT_DIR": "/opt/fund",
    "PWD": "/opt/fund",
    "USER": "fund",
    "LOGNAME": "fund",
    "SHELL": "/bin/bash",
    "TERM": "xterm",
}

ALLOWED = {"PATH", "HOME", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
           "ALPACA_PAPER_TRADE", "ALPACA_TOOLSETS"}


def _alpaca(seat: str, tmp_path) -> dict:
    cfg = load_seat_config(f"agents/config/{seat}.yaml")
    clock = SimClock(datetime(2026, 7, 6, 15, 30, tzinfo=timezone.utc))
    return build_seat_options(cfg, tmp_path / "fund.sqlite",
                              clock).mcp_servers["alpaca"]


def _child_env(alpaca: dict, parent: dict, *, overlay: bool = True) -> dict:
    """Run the seat's real launch with `parent` as the whole environment and
    return what the program at the end of it saw.

    The last two words of the launch are the real program, `uvx <spec>`;
    they are swapped for `env -0`, which prints its environment. `overlay`
    mimics the CLI merging the config's `env` dict over the parent."""
    cmd = [alpaca["command"], *alpaca["args"]]
    assert cmd[-2:] == ["uvx", ALPACA_MCP_SPEC], cmd
    cmd[-2:] = ["/usr/bin/env", "-0"]
    env = {**parent, **(alpaca["env"] if overlay else {})}
    done = subprocess.run(cmd, env=env, capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr.decode()
    child = dict(item.split("=", 1)
                 for item in done.stdout.decode().split("\0") if item)
    # bash's own shell-level counter, which its `exec` builtin re-exports
    # AFTER the prologue's unset (macOS /bin/sh is bash). Not inherited from
    # the parent, and dash — the droplet's /bin/sh — never sets it.
    child.pop("SHLVL", None)
    return child


@pytest.mark.parametrize("seat", ALL_SEATS)
def test_the_subprocess_sees_exactly_the_allow_list(seat, tmp_path):
    """EVERY seat, because every seat launches this server (issue #108's
    unconditional wiring), so every seat's launch is a place the parent
    environment could leak from."""
    alpaca = _alpaca(seat, tmp_path)
    child = _child_env(alpaca, POLLUTED)

    assert set(child) == ALLOWED, sorted(set(child) ^ ALLOWED)
    assert child["PATH"] == POLLUTED["PATH"]
    assert child["HOME"] == POLLUTED["HOME"]
    assert child["ALPACA_API_KEY"] == "PKtest"
    assert child["ALPACA_SECRET_KEY"] == "sekrit"
    assert child["ALPACA_PAPER_TRADE"] == "true"
    assert child["ALPACA_TOOLSETS"] == alpaca["env"]["ALPACA_TOOLSETS"]


def test_paper_trade_is_set_by_the_launch_not_inherited(tmp_path):
    """Invariant 1 is a property of the launch, not of the overlay: with no
    overlay at all and a parent that says otherwise, the child still trades
    paper."""
    parent = {**POLLUTED, "ALPACA_PAPER_TRADE": "false"}
    child = _child_env(_alpaca("exec", tmp_path), parent, overlay=False)
    assert child["ALPACA_PAPER_TRADE"] == "true"


UV_SETTINGS = {
    "UV_CACHE_DIR": "/srv/uv-cache",
    "UV_PYTHON_INSTALL_DIR": "/srv/uv-python",
    "XDG_CACHE_HOME": "/srv/cache",
    "XDG_DATA_HOME": "/srv/data",
    "TMPDIR": "/srv/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


def test_uv_cache_settings_pass_through_only_when_set(tmp_path):
    """ops/README.md's pre-warm and the 09:35 launch must resolve the SAME uv
    cache, or the first tool call downloads on a trading morning. None of
    these is set on the droplet today (ops/fund-daily.service sets only
    PATH), which is why the base case above shows them absent — absent, not
    empty: `UV_CACHE_DIR=` is a different setting from unset."""
    alpaca = _alpaca("exec", tmp_path)
    child = _child_env(alpaca, {**POLLUTED, **UV_SETTINGS})

    assert {k: child.get(k) for k in UV_SETTINGS} == UV_SETTINGS
    assert set(child) == ALLOWED | set(UV_SETTINGS)


def test_no_secret_value_is_baked_into_the_launch(monkeypatch, tmp_path):
    """The prologue names variables; it never carries their values. A launch
    that read os.environ at build time would put the broker key into the
    CLI's --mcp-config argv, readable by every user on the host via ps —
    worse than the inheritance it replaces."""
    monkeypatch.setenv("ALPACA_API_KEY", "PKbaked")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "sekrit-baked")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-baked")

    launch = json.dumps(_alpaca("exec", tmp_path))

    assert "PKbaked" not in launch
    assert "sekrit-baked" not in launch
    assert "xoxb-baked" not in launch
