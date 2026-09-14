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

import os
from pathlib import Path

import pytest

from scripts.eval_one import EVAL_KEYS, _primary_checkout, load_env
from scripts.run_day import REQUIRED_ENV

ROOT = Path(__file__).resolve().parents[1]
# Every .env.eval eval_one can load from here: this checkout's, and the
# primary's found the way eval_one finds it, so `make test` in a worktree
# polices the file the Mac actually holds instead of skipping (#135 rider,
# 2026-09-13). Deduped by path: in the primary checkout the two are one file.
# Keyed by label, not path, so no home directory lands in -v output.
_PRIMARY = _primary_checkout()
_CANDIDATES: dict[str, Path] = {}
for _label, _path in (("own", ROOT / ".env.eval"),
                      ("primary", (_PRIMARY or ROOT) / ".env.eval")):
    if _path.exists() and _path not in _CANDIDATES.values():
        _CANDIDATES[_label] = _path

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


@pytest.mark.skipif(not _CANDIDATES, reason="no .env.eval on this host")
@pytest.mark.parametrize("env_eval", list(_CANDIDATES.values()),
                         ids=list(_CANDIDATES))
def test_the_real_env_eval_file_holds_nothing_it_should_not(env_eval,
                                                            monkeypatch):
    """Runs against every .env.eval reachable from this checkout (its own and
    the primary's); skips only where none exists (CI). This is the one that
    catches a full `.env` pasted in wholesale."""
    keys = {line.split("=", 1)[0].strip()
            for line in env_eval.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
            and "=" in line}
    assert keys <= set(EVAL_KEYS), (
        f".env.eval carries {sorted(keys - set(EVAL_KEYS))}, which is outside"
        f" the eval contract {EVAL_KEYS}. A file with FUND_DB and a Slack"
        " token is a trading credential, whatever it is named.")
    monkeypatch.delenv("ALPACA_PAPER_TRADE", raising=False)
    # load_env setdefault-writes every EVAL_KEY; monkeypatch only restores keys
    # it was told about, and delenv(raising=False) records nothing for an
    # absent key, so the keys we add are removed by hand.
    added = [k for k in EVAL_KEYS if k not in os.environ]
    try:
        load_env(env_eval)
        assert os.environ["ALPACA_PAPER_TRADE"] == "true"    # invariant 1
    finally:
        for k in added:             # real values must not outlive this test
            os.environ.pop(k, None)
