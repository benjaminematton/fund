"""Event kind -> (channel, Slack mrkdwn text) per contracts.md §8. Unknown
kind = raise: an unrenderable event is a bug, not something to guess at
(invariant 4 is about trading defaults; projection failures must fail fast).

Rendering is a projection and nothing more (invariant 6): every word below
comes from the event payload or is a constant of this module. No renderer
reads the database."""

from __future__ import annotations

from typing import Callable, NamedTuple


class Post(NamedTuple):
    """What one event projects to. `text` is always populated — Slack renders
    it, not `blocks`, in push notifications and to screen readers, so blocks
    without text arrive blank there. `blocks` is None for kinds whose text is
    already prose composed elsewhere."""
    channel: str
    text: str
    blocks: list[dict] | None = None


# Slack rejects a message whose section/field/context text exceeds this, with
# msg_blocks_too_long — a PERMANENT error, so an over-long thesis would be
# dead-lettered and lost from the projection instead of retried. Clip instead.
TEXT_LIMIT = 3000


def _md(text: str) -> dict:
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT - 1] + "…"
    return {"type": "mrkdwn", "text": text}


def _section(text: str) -> dict:
    return {"type": "section", "text": _md(text)}


def _fields(*pairs: tuple[str, str]) -> dict:
    return {"type": "section",
            "fields": [_md(f"*{label}*\n{value}") for label, value in pairs]}


def _context(*parts: str) -> dict:
    return {"type": "context", "elements": [_md(" · ".join(parts))]}

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


def _render_fill(payload: dict) -> Post:
    qty, price, ticker = (payload["filled_qty"], payload["filled_avg_price"],
                          payload["ticker"])
    verb = "bought" if payload["side"] == "buy" else "sold"
    ticket = payload["ticket_id"][:8]
    return Post("#trade-log",
                f"*{SEATS['exec']}* · 🧾 {verb} *{qty} {ticker}* at "
                f"*${price:.2f}* — ${qty * price:,.2f}\nTicket `{ticket}`",
                [_section(f"🧾 {verb} *{qty} {ticker}*"),
                 _fields(("Price", f"${price:.2f}"),
                         ("Notional", f"${qty * price:,.2f}")),
                 _context(SEATS["exec"], f"Ticket `{ticket}`")])


def _render_signal(payload: dict) -> Post:
    seat, ticker = _seat(payload["agent"]), payload["ticker"]
    headline = (f"{payload['direction']}, conviction "
                f"{payload['confidence']}/100")
    return Post("#research",
                f"*{seat}* · *{ticker}* · {headline}\n> {payload['summary']}",
                [_section(f"*{ticker}* · {headline}"),
                 _section(f"> {payload['summary']}"),
                 _context(seat)])


def _render_decision(payload: dict) -> Post:
    ticker = payload["ticker"]
    order = _order(payload["action"], payload["qty"])
    return Post("#trading-floor",
                f"*{SEATS['pm']}* · *{ticker}* — {order}\n"
                f"> {payload['thesis']}",
                [_section(f"*{ticker}* — {order}"),
                 _section(f"> {payload['thesis']}"),
                 _context(SEATS["pm"])])


def _render_gate_approved(payload: dict) -> Post:
    side, ticker = payload["side"], payload["ticker"]
    ticket, expires = payload["ticket_id"][:8], payload["expires_hhmm"]
    return Post("#risk",
                f"*Risk Gate* · ✅ *{side} {ticker}* approved for up to "
                f"*{payload['max_qty']} shares*\n"
                f"Ticket `{ticket}` · expires {expires} ET",
                [_section(f"✅ *{side} {ticker}* approved"),
                 _fields(("Up to", f"{payload['max_qty']} shares"),
                         ("Expires", f"{expires} ET")),
                 _context("Risk Gate", f"Ticket `{ticket}`")])


def _render_gate_rejected(payload: dict) -> Post:
    side, ticker, reason = payload["side"], payload["ticker"], payload["reason"]
    gloss = REASONS.get(reason, "")
    why = f"> {gloss + ' ' if gloss else ''}(`{reason}`)"
    return Post("#risk",
                f"*Risk Gate* · ⛔ *{side} {ticker}* blocked\n{why}",
                [_section(f"⛔ *{side} {ticker}* blocked"),
                 _section(why),
                 _context("Risk Gate")])


def _render_digest(payload: dict) -> Post:
    """The day's decisions and fills as a layout. Rows written before Block
    Kit carry only `text` and keep posting as text — a dead-lettered digest
    is a lost day of acceptance evidence (HANDOFF-LIVE §5)."""
    text = payload["text"]
    if "decisions" not in payload:
        return Post("#pnl", text)
    decisions, fills = payload["decisions"], payload["fills"]
    blocks = [
        _section(f"*{payload['run_date']} close*"),
        _fields(("Decisions", str(len(decisions))), ("Fills", str(len(fills))),
                # a client-side estimate, always labeled est. (CLAUDE.md)
                ("Est. cost", f"${payload['cost_usd']:.2f}")),
        _section("> " + (", ".join(
            f"{d['ticker']} {d['action']} {d['qty']} ({d['status']})"
            for d in decisions) or "no decisions")),
        _section("> " + (", ".join(
            f"{f['symbol']} {f['side']} {f['filled_qty']}"
            f"@{f['filled_avg_price']:.2f}"
            + (" (partial)" if f["partial"] else "") for f in fills)
            or "no fills")),
        _context("Inference cost is a client-side estimate"),
    ]
    return Post("#pnl", text, blocks)


def _signed_usd(usd: float) -> str:
    """`+$500.00`, not `$+500.00` — the sign goes outside (orchestrator/pnl)."""
    return f"{'-' if usd < 0 else '+'}${abs(usd):,.2f}"


def _render_pnl(payload: dict) -> Post:
    """Post-close P&L vs SPY. Same channel as the digest, deliberately a
    different kind: run_close's already-posted guard matches on kind='digest',
    so sharing the kind would make a re-fired close skip its own digest.

    Every figure keeps its explicit sign: a losing day and a winning one must
    not differ by a character someone can miss while skimming."""
    text = payload["text"]
    if "pnl_usd" not in payload:
        return Post("#pnl", text)
    return Post("#pnl", text, [
        _section(f"*{payload['run_date']} close*"),
        _fields(("P&L", f"{_signed_usd(payload['pnl_usd'])}"
                        f" ({payload['pnl_pct']:+.2%})"),
                ("vs SPY", f"{payload['spy_pct']:+.2%}"),
                ("Alpha", f"{payload['alpha']:+.2%}"),
                ("Equity", f"${payload['equity']:,.2f}")),
    ])


def _render_alert(payload: dict) -> Post:
    """Labelled so an alert is not mistaken for a gate post: #risk carries
    both, and they demand different reactions."""
    text = f"⚠️ *Alert* · {payload['text']}"
    return Post("#risk", text, [_section(text)])


def _render_projection_error(payload: dict) -> Post:
    return Post("#risk",
                f"⚠️ projection error: event {payload['event_id']} "
                f"kind {payload['kind']} could not render")


RENDERERS: dict[str, Callable[[dict], Post]] = {
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


def render(kind: str, payload: dict) -> Post:
    """unknown kind raises here; drain() dead-letters it so one bad event
    cannot jam the queue (MVF review C2)."""
    renderer = RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"no renderer for event kind {kind!r}")
    return renderer(payload)
