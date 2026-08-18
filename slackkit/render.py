"""Event kind -> (channel, Slack mrkdwn text) per contracts.md §8. Unknown
kind = raise: an unrenderable event is a bug, not something to guess at
(invariant 4 is about trading defaults; projection failures must fail fast).

Rendering is a projection and nothing more (invariant 6): every word below
comes from the event payload or is a constant of this module. No renderer
reads the database."""

from __future__ import annotations

from typing import Callable

# The seat that emitted the post, in the words a human uses for it. An
# unmapped seat falls back to its raw name rather than raising — a new seat
# must not take the projection down.
SEATS = {"analyst": "Research Analyst", "quant": "Quant", "critic": "Critic",
         "pm": "Portfolio Manager", "exec": "Execution Trader"}

# gate.risk reason codes in English. Unglossed codes degrade to the bare
# code; tests/test_slackkit.py statically guards that every Rejected()
# literal in gate/ appears here.
REASONS = {
    "no_headroom": "Sector exposure is already at its cap — no room for"
                   " another share.",
    "circuit_breaker": "The daily-loss circuit breaker is tripped — no new"
                       " buys today.",
    "position_count": "The fund already holds its maximum number of"
                      " positions.",
    "nothing_held": "There is no position to sell.",
    "zero_qty": "Risk sizing came out below one share.",
    "gate_error": "The gate could not size this trade from the inputs it was"
                  " given.",
}


def _seat(agent: str) -> str:
    return SEATS.get(agent, agent)


def _order(side: str, qty: int) -> str:
    """'buy 80 shares' / 'hold' — a hold has no share count to claim."""
    return "hold" if side == "hold" else f"{side} {qty} shares"


def _render_fill(payload: dict) -> tuple[str, str]:
    qty, price = payload["filled_qty"], payload["filled_avg_price"]
    verb = "bought" if payload["side"] == "buy" else "sold"
    return ("#trade-log",
            f"*{SEATS['exec']}* · 🧾 {verb} *{qty} {payload['ticker']}* at "
            f"*${price:.2f}* — ${qty * price:,.2f}\n"
            f"Ticket `{payload['ticket_id'][:8]}`")


def _render_signal(payload: dict) -> tuple[str, str]:
    return ("#research",
            f"*{_seat(payload['agent'])}* · *{payload['ticker']}* · "
            f"{payload['direction']}, conviction {payload['confidence']}/100\n"
            f"> {payload['summary']}")


def _render_decision(payload: dict) -> tuple[str, str]:
    return ("#trading-floor",
            f"*{SEATS['pm']}* · *{payload['ticker']}* — "
            f"{_order(payload['action'], payload['qty'])}\n"
            f"> {payload['thesis']}")


def _render_gate_approved(payload: dict) -> tuple[str, str]:
    return ("#risk",
            f"*Risk Gate* · ✅ *{payload['side']} {payload['ticker']}* "
            f"approved for up to *{payload['max_qty']} shares*\n"
            f"Ticket `{payload['ticket_id'][:8]}` · "
            f"expires {payload['expires_hhmm']} ET")


def _render_gate_rejected(payload: dict) -> tuple[str, str]:
    reason = payload["reason"]
    gloss = REASONS.get(reason, "")
    return ("#risk",
            f"*Risk Gate* · ⛔ *{payload['side']} {payload['ticker']}* blocked\n"
            f"> {gloss + ' ' if gloss else ''}(`{reason}`)")


def _render_digest(payload: dict) -> tuple[str, str]:
    return ("#pnl", payload["text"])


def _render_pnl(payload: dict) -> tuple[str, str]:
    """Post-close P&L vs SPY. Same channel as the digest, deliberately a
    different kind: run_close's already-posted guard matches on kind='digest',
    so sharing the kind would make a re-fired close skip its own digest."""
    return ("#pnl", payload["text"])


def _render_alert(payload: dict) -> tuple[str, str]:
    """Labelled so an alert is not mistaken for a gate post: #risk carries
    both, and they demand different reactions."""
    return ("#risk", f"⚠️ *Alert* · {payload['text']}")


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
    "pnl": _render_pnl,
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
