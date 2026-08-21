"""Assertion: the broker's account settings still match the pinned baseline
(2026-08-20 design).

Invariant 3 locks the gate's thresholds, but they stand on account settings
`gate/risk.py` never reads. No LLM seat can move them — agents/runtime.py's
_broker_verb_policy denies update_account_config — but a human at the Alpaca
dashboard or Alpaca itself still can.

This detects, it does not control. Every limb of the envelope already fails
closed: sizing is bounded by min(dollar, cash) rather than buying power, and
the sell branch caps at held_qty so the gate cannot open a short. What was
missing is ATTRIBUTION — a cleared no_shorting surfaces as `gate_error`, which
names nothing. So drift alerts and the day continues.

The whole payload is diffed, not a list of interesting settings. Enumeration is
the failure mode this repo keeps hitting: the exec verb surface was counted
4 -> 5 -> 7 -> 8 by four sessions in one afternoon, and the hand-written
setting list in config/broker_tool_surface.yaml is already wrong against pinned
alpaca-py 0.44.0.

The pin is against alpaca-py's AccountConfiguration model, not Alpaca's raw
API response — that model's extra='ignore' default drops a field it does not
declare before account_config() ever hands it a dict, so a field Alpaca adds
to the wire is unreachable here until an alpaca-py upgrade declares it. That
is the right boundary, not a gap: a field the fund cannot see cannot affect
the fund, and the upgrade that makes it visible is itself a human commit —
invariant 3's own mechanism. The check reddens the first day the fund can
actually observe the field.

Not a stage: an assertion must re-check on a resumed day rather than be skipped
as 'done', and a duplicate alert is the safe direction.

Every ambiguity alerts (invariant 4) — an unreachable broker, an unreadable
payload, an empty baseline. A check that can pass while lying is worse than no
check at all."""
from __future__ import annotations

import sqlite3

from slackkit.outbox import append_event


def assert_account_config_unchanged(conn: sqlite3.Connection, *, broker,
                                    baseline: dict, now_iso: str) -> int:
    """Alert on every difference between `baseline` and the broker's account
    configuration. Returns the number of alerts appended.

    Never raises and never blocks: this runs on the critical path of a live
    trading day, and a precondition that is merely *different* is not grounds
    to refuse to trade."""

    def alert(text: str, drift: dict) -> int:
        append_event(conn, "alert", {"text": text, "drift": drift}, now_iso)
        return 1

    if not baseline:
        # Distinct from "nothing drifted". An unloadable or empty baseline
        # would otherwise make this function silently unable to fail.
        return alert(
            "account config: baseline is empty — the drift check could not"
            " run, so today's account settings are unverified",
            {"reason": "empty_baseline"})

    try:
        actual = broker.account_config()
    except Exception as e:                       # noqa: BLE001 — every failure alerts
        # The Slack text is capped at 120 chars for readability — a
        # multi-field pydantic ValidationError would run the distinguishing
        # field names past that cap. The payload keeps the FULL error so the
        # text's cap never costs the only diagnostic that exists.
        return alert(
            f"account config: could not read from the broker —"
            f" {type(e).__name__}: {str(e)[:120]}",
            {"reason": "unreadable", "error": str(e)})

    if not isinstance(actual, dict):
        return alert(
            f"account config: broker returned {type(actual).__name__},"
            " expected a mapping — settings are unverified",
            {"reason": "malformed"})

    n = 0
    for field in sorted(set(baseline) | set(actual)):
        if field not in actual:
            n += alert(
                f"account config: `{field}` is pinned to"
                f" {baseline[field]!r} but the broker no longer reports it",
                {"field": field, "expected": baseline[field], "actual": None})
        elif field not in baseline:
            n += alert(
                f"account config: broker reports `{field}` ="
                f" {actual[field]!r}, which the baseline does not pin",
                {"field": field, "expected": None, "actual": actual[field]})
        elif actual[field] != baseline[field]:
            n += alert(
                f"account config: `{field}` changed —"
                f" expected {baseline[field]!r}, broker reports"
                f" {actual[field]!r}",
                {"field": field, "expected": baseline[field],
                 "actual": actual[field]})
    return n
