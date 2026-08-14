"""Offline tests for the live composition root's decision seams (MVF T16).

scripts/run_day.py is the only place real clocks/Slack/Alpaca/LLM sessions are
constructed, so most of it can only be exercised live. What CAN be tested
offline is every place it decides something — the paper guard, env fail-fast,
the market-closed guard, the channel remap, and the committed watchlist — and
those are exactly the places a wrong answer trades against a shut market, an
unfunded account, or the wrong Slack channel.

Never calls main(): that builds real clients.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_day.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_day", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_day_script = _load()


# --- invariant 1: paper only -------------------------------------------------

@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_paper_guard_accepts_only_true(value):
    run_day_script.paper_guard({"ALPACA_PAPER_TRADE": value})


@pytest.mark.parametrize("value", ["false", "", "1", "yes", "paper"])
def test_paper_guard_refuses_anything_else(value):
    with pytest.raises(SystemExit) as e:
        run_day_script.paper_guard({"ALPACA_PAPER_TRADE": value})
    assert "invariant 1" in str(e.value)


def test_paper_guard_refuses_when_unset():
    """An unset var must stop the day, never default to trading."""
    with pytest.raises(SystemExit):
        run_day_script.paper_guard({})


# --- fail fast on env --------------------------------------------------------

def test_require_env_names_every_missing_var_at_once():
    with pytest.raises(SystemExit) as e:
        run_day_script.require_env(("FUND_DB", "SLACK_BOT_TOKEN", "ALPACA_API_KEY"),
                                   {"ALPACA_API_KEY": "PK1"})
    message = str(e.value)
    assert "FUND_DB" in message and "SLACK_BOT_TOKEN" in message
    assert "ALPACA_API_KEY" not in message
    assert ".env" in message


def test_require_env_treats_blank_as_missing_and_strips():
    with pytest.raises(SystemExit):
        run_day_script.require_env(("FUND_DB",), {"FUND_DB": "   "})
    assert run_day_script.require_env(
        ("FUND_DB",), {"FUND_DB": " state/fund.sqlite "}) == {
        "FUND_DB": "state/fund.sqlite"}


def test_required_env_covers_every_live_dependency():
    """A var the script reads but does not require would fail obscurely deep
    in a client constructor instead of on line one."""
    assert set(run_day_script.REQUIRED_ENV) == {
        "ANTHROPIC_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "FUND_DB", "SLACK_BOT_TOKEN"}


# --- market-closed guard (invariant 4) --------------------------------------

class _Source:
    def __init__(self, payload=None, raises=None):
        self._payload = payload
        self._raises = raises

    def market_clock(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


def test_market_is_open_when_the_broker_says_so():
    assert run_day_script.market_is_open(_Source({"is_open": True})) is True


def test_market_is_closed_when_the_broker_says_so():
    assert run_day_script.market_is_open(_Source({"is_open": False})) is False


def test_unreachable_broker_clock_reads_as_closed():
    """A day we could not verify is a day we do not trade."""
    assert run_day_script.market_is_open(
        _Source(raises=ConnectionError("boom"))) is False


def test_unparseable_clock_payload_reads_as_closed():
    assert run_day_script.market_is_open(_Source({})) is False
    assert run_day_script.market_is_open(_Source(None)) is False


# --- Slack channel remap -----------------------------------------------------

def test_parse_channel_overrides_empty_is_no_remap():
    assert run_day_script.parse_channel_overrides(None) == {}
    assert run_day_script.parse_channel_overrides("") == {}
    assert run_day_script.parse_channel_overrides("  , ") == {}


def test_parse_channel_overrides_pairs():
    assert run_day_script.parse_channel_overrides(
        "#pnl=#test-pnl, #risk=#test-risk") == {
        "#pnl": "#test-pnl", "#risk": "#test-risk"}


@pytest.mark.parametrize("raw", ["#pnl", "#pnl=", "=#test-pnl"])
def test_malformed_override_is_a_hard_stop(raw):
    """Half-parsed remaps must not silently post a rehearsal to the real
    channel."""
    with pytest.raises(SystemExit):
        run_day_script.parse_channel_overrides(raw)


class _RecordingSlack:
    def __init__(self):
        self.posts = []

    def post(self, channel, text, thread_ts=None):
        self.posts.append((channel, text, thread_ts))
        return "ts-1"


def test_remapped_slack_rewrites_listed_channels_only():
    inner = _RecordingSlack()
    slack = run_day_script.RemappedSlack(inner, {"#pnl": "#test-pnl"})
    assert slack.post("#pnl", "digest") == "ts-1"
    slack.post("#trade-log", "fill", "ts-0")
    assert inner.posts == [("#test-pnl", "digest", None),
                           ("#trade-log", "fill", "ts-0")]


# --- committed config --------------------------------------------------------

def test_watchlist_loads_from_yaml_not_from_code():
    tickers = run_day_script.load_watchlist(run_day_script.WATCHLIST_YAML)
    assert tickers and all(t == t.upper() for t in tickers)


def test_empty_watchlist_is_a_hard_stop(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers: []\n")
    with pytest.raises(SystemExit):
        run_day_script.load_watchlist(path)


def test_every_watchlist_ticker_has_a_sector():
    """A ticker with no sector is rejected fail-closed by the gate, so it
    would burn a seat turn for a guaranteed HOLD."""
    sectors = yaml.safe_load((ROOT / "config" / "sectors.yaml").read_text())
    missing = [t for t in run_day_script.load_watchlist(
        run_day_script.WATCHLIST_YAML) if t not in sectors]
    assert missing == [], f"no sector for {missing} in config/sectors.yaml"


def test_watchlist_stays_within_the_cost_bound():
    """Spec P1: watchlist <= 3 tickers is the fund's per-day cost bound."""
    assert len(run_day_script.load_watchlist(run_day_script.WATCHLIST_YAML)) <= 3


def test_every_stage_seat_has_a_committed_config():
    for seat in run_day_script.SEATS.values():
        assert (run_day_script.SEAT_CONFIG / f"{seat}.yaml").exists()
