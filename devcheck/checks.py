from __future__ import annotations

from devcheck.model import Finding, Snapshot


def check_paper_trading(s: Snapshot) -> Finding:
    """Invariant 1 — paper only, everywhere.

    Nothing else in the fund asserts this against the running host.
    scripts/resolve_day.py guards its own startup, which protects that job
    and says nothing about the box.
    """
    value = s.droplet_env.get("ALPACA_PAPER_TRADE")
    if value == "true":
        return Finding("paper_trading", "ok", "ALPACA_PAPER_TRADE=true on the droplet")
    return Finding(
        "paper_trading",
        "alert",
        f"ALPACA_PAPER_TRADE={value!r} on the droplet — invariant 1 requires 'true'",
    )


def check_trading_toolset(s: Snapshot) -> Finding:
    """Invariant 2 — only the Execution Trader holds the `trading` toolset."""
    holders = sorted(seat for seat, has in s.seat_trading_toolsets.items() if has)
    if holders == ["exec"]:
        return Finding("trading_toolset", "ok", "only exec holds `trading`")
    extra = [h for h in holders if h != "exec"]
    if extra:
        return Finding(
            "trading_toolset",
            "alert",
            f"seats other than exec hold `trading`: {', '.join(extra)} — invariant 2",
        )
    return Finding(
        "trading_toolset",
        "alert",
        "exec does not hold `trading` — no order can ever be placed",
    )


def check_order_idempotency(s: Snapshot) -> Finding:
    """Invariant 5 — client_order_id is always a gate ticket id."""
    orphans = [o.client_order_id for o in s.orders if o.client_order_id not in s.tickets]
    if not orphans:
        return Finding(
            "order_idempotency",
            "ok",
            f"{len(s.orders)} order(s), every client_order_id is a ticket id",
        )
    return Finding(
        "order_idempotency",
        "alert",
        f"client_order_id not matching any ticket: {', '.join(sorted(orphans))} — invariant 5",
    )


def check_outbox(s: Snapshot) -> Finding:
    """Invariant 6 — Slack is a projection of SQLite. An undrained outbox
    means the projection is silently behind the truth."""
    if s.events_unposted == 0:
        return Finding("outbox", "ok", "events outbox fully drained")
    return Finding(
        "outbox",
        "alert",
        f"{s.events_unposted} event(s) with posted_at IS NULL — Slack is stale",
    )


def check_db_broker_agreement(s: Snapshot) -> Finding:
    """Invariant 6 — the DB is the source of truth, so the broker having
    seen more fills than the DB has rows is a divergence, not a rounding
    difference. Manual out-of-gate orders produce exactly this."""
    rows = len(s.orders)
    if rows == s.broker_fill_count:
        return Finding("db_broker_agreement", "ok", f"orders rows == broker fills ({rows})")
    return Finding(
        "db_broker_agreement",
        "alert",
        f"orders has {rows} row(s); broker has seen {s.broker_fill_count} fill(s) — "
        "the fund's record disagrees with the broker",
    )
