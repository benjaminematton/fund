"""Event kind -> (channel, text) per contracts.md §8. Unknown kind = raise:
an unrenderable event is a bug, not something to guess at (invariant 4 is
about trading defaults; projection failures must fail fast)."""

from __future__ import annotations

from typing import Callable


def _render_fill(payload: dict) -> tuple[str, str]:
    return ("#trade-log",
            f"🧾 {payload['ticker']} {payload['side']} "
            f"{payload['filled_qty']}@{payload['filled_avg_price']:.2f} "
            f"(ticket {payload['ticket_id'][:8]})")


def _render_signal(payload: dict) -> tuple[str, str]:
    return ("#research",
            f"[{payload['agent']}] {payload['ticker']} — "
            f"{payload['direction']} ({payload['confidence']}/100): "
            f"{payload['summary']}")


def _render_decision(payload: dict) -> tuple[str, str]:
    return ("#trading-floor",
            f"VERDICT {payload['ticker']}: {payload['action']} {payload['qty']}\n"
            f"{payload['thesis']}")


def _render_gate_approved(payload: dict) -> tuple[str, str]:
    return ("#risk",
            f"✅ TICKET {payload['ticket_id'][:8]} {payload['side']} "
            f"{payload['ticker']} ≤{payload['max_qty']} "
            f"expires {payload['expires_hhmm']}")


def _render_gate_rejected(payload: dict) -> tuple[str, str]:
    return ("#risk",
            f"⛔ {payload['ticker']} {payload['side']} — {payload['reason']}")


def _render_digest(payload: dict) -> tuple[str, str]:
    return ("#pnl", payload["text"])


def _render_alert(payload: dict) -> tuple[str, str]:
    return ("#risk", payload["text"])


def _render_projection_error(payload: dict) -> tuple[str, str]:
    return ("#risk",
            f"⚠️ projection error: event {payload['event_id']} "
            f"kind {payload['kind']} could not render")


RENDERERS: dict[str, Callable[[dict], tuple[str, str]]] = {
    "fill": _render_fill,
    "signal": _render_signal,
    "decision": _render_decision,
    "gate_approved": _render_gate_approved,
    "gate_rejected": _render_gate_rejected,
    "digest": _render_digest,
    "alert": _render_alert,
    "projection_error": _render_projection_error,
}


def render(kind: str, payload: dict) -> tuple[str, str]:
    """unknown kind raises here; drain() dead-letters it so one bad event
    cannot jam the queue (MVF review C2)."""
    renderer = RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"no renderer for event kind {kind!r}")
    return renderer(payload)
