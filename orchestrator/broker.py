from __future__ import annotations

from typing import Protocol


class BrokerPort(Protocol):
    """Broker access for deterministic code (mirrors SlackPort): read the
    state of an order the fund already placed, cancel one that is still
    working past the fill-poll's cap, and read what the ACCOUNT actually
    holds — positions and live orders — so a position with no protective
    order cannot go unnoticed. Order PLACEMENT stays agent-side behind
    the PreToolUse gate hook (invariant 2) — this port never places, and must
    never grow a method that does.

    `cancel_order` REQUESTS a cancel; it does not assert the outcome. The
    caller re-queries and records only what the broker confirms — a cancel
    can lose the race to a fill (reconcile.py).

    `open_positions` and `open_orders` RAISE on failure rather than returning
    empty. They have no retry behind them, and an empty list would read as
    "nothing held" / "nothing protecting it" — a silent pass on the exact
    condition orchestrator/protection.py exists to catch."""
    def get_order_by_client_order_id(self, coid: str) -> dict | None: ...
    def cancel_order(self, coid: str) -> None: ...
    def open_positions(self) -> list[dict]: ...
    def open_orders(self) -> list[dict]: ...
