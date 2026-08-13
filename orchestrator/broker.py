from __future__ import annotations

from typing import Protocol


class BrokerPort(Protocol):
    """Read-side broker access for deterministic code (mirrors SlackPort).
    Order PLACEMENT stays agent-side behind the hook — this port never places."""
    def get_order_by_client_order_id(self, coid: str) -> dict | None: ...
