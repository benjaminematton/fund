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
    condition orchestrator/protection.py exists to catch.

    `open_orders` returns one dict per working order, legs FLATTENED, with
    eight keys: `symbol`, `side`, `qty`, `type`, `status`, `id`,
    `client_order_id`, `stop_price`, `expires_at`. `id` is the broker's UUID
    and `client_order_id` is the string whoever placed it chose — the two are
    unrelated, and for an OTO leg the client id is Alpaca-generated. Both
    `stop_price` and `expires_at` are None on orders that carry none, so a
    trailing stop has no stop price and a DAY order has no expiry.

    `expires_at` is an ISO STRING in the repo's canonical form (a `T`
    separator, as orchestrator.clock.iso() produces), never a datetime.
    Everything else in the database is written that way, and a value with a
    space separator sorts and compares against none of it.

    HELD orders are EXCLUDED, because QueryOrderStatus.OPEN excludes them at
    the real broker. A held OTO leg is protection that does not exist yet: its
    parent has not filled, so there is no position to protect."""
    def get_order_by_client_order_id(self, coid: str) -> dict | None: ...
    def cancel_order(self, coid: str) -> None: ...
    def open_positions(self) -> list[dict]: ...
    def open_orders(self) -> list[dict]: ...
