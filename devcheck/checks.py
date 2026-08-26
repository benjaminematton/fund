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
