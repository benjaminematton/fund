"""Event kind -> (channel, Slack mrkdwn text) per contracts.md §8. Unknown
kind raises: an unrenderable event is a bug, not something to guess at
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
    already prose composed elsewhere.

    `username`/`icon_emoji` override the sender Slack shows. Setting them
    needs the token's chat:write.customize scope. Both None — every kind but
    `signal` and `decision` — posts under the app's own identity."""
    channel: str
    text: str
    blocks: list[dict] | None = None
    username: str | None = None
    icon_emoji: str | None = None


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

# The seat that emitted the post, in the words a human uses for it: a name so
# a channel reads as people talking, the role in parentheses so nobody has to
# memorize the mapping and a second analyst stays unambiguous. One dict, used
# both as the Slack sender name and as the in-message label — two spellings of
# one seat is worse than seeing the name twice, and the label is what survives
# if chat:write.customize is ever revoked. An unmapped seat falls back to its
# raw name rather than raising — a new seat must not take the projection down.
SEATS = {"analyst": "Nora (Analyst)", "quant": "Kai (Quant)",
         "critic": "Ida (Critic)", "pm": "Vic (PM)",
         "exec": "Dash (Execution)"}

# The face beside the name. Only seats with a model behind them get one:
# machinery (the gate, fills, digests, P&L, alerts) posts as the fund itself,
# so a reader can always tell a model's words from code that cannot be argued
# with. An unmapped seat gets no face rather than borrowing another's.
ICONS = {"analyst": "🔎", "quant": "📐", "critic": "🧪", "pm": "🎯",
         "exec": "⚡"}

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


def _persona(agent: str) -> tuple[str, str | None]:
    """The sender Slack shows for a seat: (username, icon_emoji)."""
    return _seat(agent), ICONS.get(agent)


def _order(side: str, qty: int) -> str:
    """`buy 80 shares` or `hold` — a hold has no share count to claim."""
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
    username, icon = _persona(payload["agent"])
    headline = (f"{payload['direction']}, conviction "
                f"{payload['confidence']}/100")
    return Post("#research",
                f"*{seat}* · *{ticker}* · {headline}\n> {payload['summary']}",
                [_section(f"*{ticker}* · {headline}"),
                 _section(f"> {payload['summary']}"),
                 _context(seat)],
                username, icon)


def _render_decision(payload: dict) -> Post:
    # Rows written before the projection carried a seat can only have come
    # from the PM — it was the sole seat allowed to submit — so they keep
    # posting as the PM instead of raising on a field they never had.
    slug = payload.get("seat", "pm")
    seat, (username, icon) = _seat(slug), _persona(slug)
    ticker = payload["ticker"]
    order = _order(payload["action"], payload["qty"])
    return Post("#trading-floor",
                f"*{seat}* · *{ticker}* — {order}\n"
                f"> {payload['thesis']}",
                [_section(f"*{ticker}* — {order}"),
                 _section(f"> {payload['thesis']}"),
                 _context(seat)],
                username, icon)


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
    """Labeled so an alert is not mistaken for a gate post: #risk carries
    both, and they demand different reactions."""
    text = f"⚠️ *Alert* · {payload['text']}"
    return Post("#risk", text, [_section(text)])


# A day with thirty findings must not post a wall nobody reads. The rest are
# counted, never dropped silently — the DB has them all, and `make score-day`
# prints the lot.
SCORECARD_LINES = 5


def _render_scorecard(payload: dict) -> Post:
    """The day's findings, worst first, as scripts/score_day.py ranked them.

    Posts on EVERY day, clean ones included: silence would be ambiguous
    between a quiet day and a job that never ran. Nothing here re-ranks or
    re-judges — the order arrives already decided."""
    rows, run_date = payload["rows"], payload["run_date"]
    shown = rows[:SCORECARD_LINES]
    lines = [f"`{r['severity']}` *{r['kind']}* — {r['detail']}" for r in shown]
    if len(rows) > len(shown):
        lines.append(f"_…and {len(rows) - len(shown)} more_")
    body = "\n".join(lines) or "_nothing flagged_"
    head = f"*{run_date} scorecard* · {len(rows)} finding(s), worst first"
    return Post("#pnl", f"{head}\n{body}",
                [_section(head), _section(body),
                 _context("Ranked by fixed severity, never a tuned score")])


def _render_model_fallback_used(payload: dict) -> Post:
    """A fallback served part or all of a seat turn, so today's signals and
    decisions from that seat name a model that did not produce them.

    Posted as machinery, not as the seat: the seat did not say this, code
    noticed it. Deliberately NOT an `alert` — scripts/audit_day.py fails the
    day on any alert, and a fallback is not a failed day."""
    seat = _seat(payload["seat"])
    served = ", ".join(payload["served"])
    text = (f"*Model fallback* · {seat} ran *{served}*, configured "
            f"*{payload['configured']}*\n"
            "> Today's rows from this seat carry the configured model, not"
            " the one that served them.")
    return Post("#risk", text, [
        _section(f"🔀 *{seat}* ran a model it was not configured to run"),
        _fields(("Served", served), ("Configured", payload["configured"])),
        _context("Rows from this seat name the configured model")])


def _render_spec_critique(payload: dict) -> Post:
    """The Critic's G1 verdict on one strategy spec.

    Carries a face, unlike `scorecard` and `model_fallback_used`. Those are
    machinery reporting on models; `objections` is the model's own prose, the
    same class of content as a signal's summary or a decision's thesis — and
    the rule ICONS states is that a reader must be able to tell a model's
    words from code that cannot be argued with.

    Posts to #research, not #risk: at G1 nothing has been risked yet. A clear
    verdict is announced as flatly as an objecting one — the seat's value
    depends on objections being rare, so dramatizing them invites the
    manufactured ones its charter forbids."""
    spec_id, verdict = payload["spec_id"], payload["verdict"]
    seat = _seat(payload.get("seat", "critic"))
    username, icon = _persona(payload.get("seat", "critic"))
    objections = payload.get("objections") or []
    headline = (f"G1 *{verdict}* · `{spec_id}`" if verdict == "clear"
                else f"G1 *{verdict}* ({len(objections)}) · `{spec_id}`")
    body = "\n".join(f"> {o}" for o in objections)
    blocks = [_section(headline)]
    if body:
        blocks.append(_section(body))
    blocks.append(_context(seat, "mechanism alignment"))
    return Post("#research", f"*{seat}* · {headline}" + (f"\n{body}" if body
                                                         else ""),
                blocks, username, icon)


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
    "model_fallback_used": _render_model_fallback_used,
    "scorecard": _render_scorecard,
    "spec_critique": _render_spec_critique,
    "projection_error": _render_projection_error,
}


def render(kind: str, payload: dict) -> Post:
    """An unknown kind raises here; drain() dead-letters it so one bad event
    cannot jam the queue (MVF review C2)."""
    renderer = RENDERERS.get(kind)
    if renderer is None:
        raise ValueError(f"no renderer for event kind {kind!r}")
    return renderer(payload)
