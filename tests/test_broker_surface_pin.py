"""The pinned broker tool surface, checked offline against the gate policy.

The exec seat's allow-array is `mcp__alpaca__*`, so the SERVER decides what the
seat can reach. On 2026-08-20 four sessions enumerated that surface and got
four different answers — 4, 5, 7, then 8 mutating verbs — while
`close_all_positions` was reachable in production with no ticket.

Two layers, and keeping them distinct is the point:

  PROTECTION is `agents/runtime.py:_broker_verb_policy`, which is
  deny-by-default. It is why every one of those miscounts was harmless: a verb
  nobody enumerated is already denied.

  DETECTION is this file. It cannot make the fund safer; it makes a MOVED
  surface loud, so a new mutating verb is a red build rather than a discovery.

The offline tests here run in `make test`. The live introspection that refreshes
the pin is `tests/test_live_smoke.py` behind `-m live` — `make test` is offline
by contract, no network and no keys, and that contract outranks the
convenience of one test that would check the real server on every run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agents.runtime import _broker_verb_policy

PIN = Path(__file__).resolve().parents[1] / "config/broker_tool_surface.yaml"


@pytest.fixture(scope="module")
def pin() -> dict:
    return yaml.safe_load(PIN.read_text())


def _qualified(name: str) -> str:
    return f"mcp__alpaca__{name}"


def test_the_pin_records_what_the_surface_is_a_function_of(pin):
    """A tool list alone is not reproducible. The same package at a different
    toolset selection exposes a different surface, so a reader who cannot see
    both cannot tell whether the list is still true."""
    assert pin["spec"] == "alpaca-mcp-server@2.2.1"
    assert pin["toolsets"] == "account,trading,stock-data"


def test_every_gated_verb_routes_to_the_ticket_check(pin):
    for name in pin["gated"]:
        assert _broker_verb_policy(_qualified(name)) == "gated", name


def test_every_ungated_mutation_is_denied(pin):
    """The load-bearing one. A mutation that reaches the broker without a
    ticket has no max_qty, writes no `orders` row, and emits no gate event —
    reconciliation cannot even tell it happened."""
    for name in pin["mutating"]:
        assert _broker_verb_policy(_qualified(name)) == "deny", name


def test_every_read_is_allowed_including_the_four_that_break_the_prefix(pin):
    """`search_alpaca_docs`, `search_alpaca_api_specs`, `fetch_alpaca_doc` and
    `list_alpaca_api_endpoints` are reads with no `get_` prefix. The first
    version of the policy classified by prefix alone and denied all four —
    which is why this pin classifies by ENUMERATION and this test names them."""
    for name in pin["read"]:
        assert _broker_verb_policy(_qualified(name)) == "allow", name
    for odd in ("search_alpaca_docs", "search_alpaca_api_specs",
                "fetch_alpaca_doc", "list_alpaca_api_endpoints"):
        assert odd in pin["read"]
        assert _broker_verb_policy(_qualified(odd)) == "allow", odd


def test_the_three_buckets_are_a_partition(pin):
    """No tool in two buckets, and no bucket empty. A verb listed as both read
    and mutating would pass every test above while meaning nothing."""
    buckets = [set(pin[k]) for k in ("gated", "mutating", "read")]
    for a, b in ((0, 1), (0, 2), (1, 2)):
        assert not buckets[a] & buckets[b], buckets[a] & buckets[b]
    assert all(buckets)


def test_the_pin_matches_the_enumeration_it_was_built_from(pin):
    """38 tools: 3 gated, 8 mutating, 27 read. Asserted as a count as well as a
    list — a silent deletion from the list would otherwise pass everything."""
    assert (len(pin["gated"]), len(pin["mutating"]), len(pin["read"])) == (
        3, 8, 27)


def test_an_unpinned_verb_is_denied_rather_than_assumed_harmless(pin):
    """What happens when the surface moves and nobody has updated this file.

    There is deliberately no third 'harmless' bucket. Anything not explicitly
    gated or explicitly read is denied — so an unknown tool fails closed, and
    a settings-mutator cannot quietly join a bucket that waves it through."""
    assert _broker_verb_policy("mcp__alpaca__some_verb_from_the_next_release") \
        == "deny"
