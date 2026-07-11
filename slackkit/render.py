"""Event kind -> (channel, text) per contracts.md §8. Unknown kind = raise:
an unrenderable event is a bug, not something to guess at (invariant 4 is
about trading defaults; projection failures must fail fast)."""

from __future__ import annotations


def render(kind: str, payload: dict) -> tuple[str, str]:
    if kind == "fill":
        return ("#trade-log",
                f"🧾 {payload['ticker']} {payload['side']} "
                f"{payload['filled_qty']}@{payload['filled_avg_price']:.2f} "
                f"(ticket {payload['ticket_id'][:8]})")
    raise ValueError(f"no renderer for event kind {kind!r}")
