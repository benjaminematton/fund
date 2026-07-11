"""Proves the offline-by-default marker config (acceptance §0): a live-marked
test that always fails must never run under `make test`."""

import pytest


@pytest.mark.live
def test_live_canary_is_excluded_from_default_run():
    raise AssertionError(
        "live-marked test executed in an offline run — pytest marker config is broken"
    )
