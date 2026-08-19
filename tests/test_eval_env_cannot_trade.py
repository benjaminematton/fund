"""A host credentialled for `make eval` must still be unable to trade.

The Mac was taken out of the trading path on 2026-08-18 by two independent
barriers (PROGRESS.md "The Mac after cutover"): com.fund.daily.plist moved out
of ~/Library/LaunchAgents, and `.env` renamed to `.env.MIGRATED-TO-VM`. Evals
are a development loop and want to run there anyway, so `.env.eval` carries
the eval keys — and the second barrier survives only because that file is
missing what a trading day requires.

This pins the gap. Without it the barrier is a naming convention, and the
first person who copies `.env.MIGRATED-TO-VM` to `.env.eval` to "just get the
eval running" silently re-arms the Mac.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval_one import EVAL_KEYS, load_env
from scripts.run_day import REQUIRED_ENV

ROOT = Path(__file__).resolve().parents[1]
EVAL_ENV = ROOT / ".env.eval"

# What a trading day needs and an eval turn must never be handed.
TRADING_ONLY = {"FUND_DB", "SLACK_BOT_TOKEN"}


def test_eval_keys_cannot_satisfy_a_trading_day():
    """The load-bearing assertion: run_day.py refuses on its own REQUIRED_ENV
    check when only the eval keys are present."""
    missing = set(REQUIRED_ENV) - set(EVAL_KEYS)
    assert missing >= TRADING_ONLY, (
        f"the eval key set now satisfies all but {missing} of run_day.py's"
        f" REQUIRED_ENV {REQUIRED_ENV}. If a trading requirement was dropped,"
        " a .env.eval host can start a trading day — re-establish the gap"
        " before relaxing this test.")


def test_eval_keys_carry_no_trading_credential():
    assert not (set(EVAL_KEYS) & TRADING_ONLY)


def test_paper_trading_is_part_of_the_eval_contract():
    """Invariant 1 travels with the credentials, not just with the code:
    eval_suite.py refuses unless this resolves to 'true'."""
    assert "ALPACA_PAPER_TRADE" in EVAL_KEYS


@pytest.mark.skipif(not EVAL_ENV.exists(), reason="no .env.eval on this host")
def test_the_real_env_eval_file_holds_nothing_it_should_not(monkeypatch):
    """Runs only where the file exists. This is the one that catches a full
    `.env` pasted in wholesale."""
    keys = {line.split("=", 1)[0].strip()
            for line in EVAL_ENV.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
            and "=" in line}
    assert keys <= set(EVAL_KEYS), (
        f".env.eval carries {sorted(keys - set(EVAL_KEYS))}, which is outside"
        f" the eval contract {EVAL_KEYS}. A file with FUND_DB and a Slack"
        " token is a trading credential, whatever it is named.")
    monkeypatch.delenv("ALPACA_PAPER_TRADE", raising=False)
    load_env(EVAL_ENV)
    import os
    assert os.environ["ALPACA_PAPER_TRADE"] == "true"    # invariant 1
