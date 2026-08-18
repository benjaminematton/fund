"""EOD P&L vs SPY (contracts §8) — the digest's two missing fields.

No new storage: equity and last_equity already arrive on every account_state
read (the gate's circuit breaker consumes the same pair), and SPY's closes
already arrive through close_frame. This module is arithmetic over sources the
day already touches.

Purity-linted like the rest of orchestrator/: the broker source and the clock
arrive injected, so nothing here reaches the network or the wall clock.
"""

from __future__ import annotations

import math

from orchestrator.clock import et_run_date


class PnlUnavailable(RuntimeError):
    """Today's P&L cannot be computed from data the fund trusts, so there is no
    number to post. Never a guess and never a zero (invariant 4) — a flat day
    and an unmeasurable one look identical in a digest and mean opposite
    things."""


def eod_pnl(source, clock) -> dict:
    """`{run_date, equity, pnl_usd, pnl_pct, spy_pct, alpha}` for the ET
    session `clock.now()` falls in.

    MUST run after the close has settled — 16:35 ET is the scheduled time.
    close_frame shifts its end back SIP_DELAY (16 min), so an earlier call asks
    for a bar the session has not finished writing; the same-session guard
    on the bar date is what turns that into no post rather than a wrong one.
    Raises PnlUnavailable when the broker equity or the SPY closes cannot
    support a number.
    """
    now = clock.now()
    run_date = et_run_date(now)

    account = source.account_state()
    equity = _finite(account.get("equity"))
    last_equity = _finite(account.get("last_equity"))
    if equity is None or last_equity is None or last_equity <= 0:
        raise PnlUnavailable(
            f"{run_date}: broker equity unusable"
            f" (equity={account.get('equity')!r},"
            f" last_equity={account.get('last_equity')!r}) — a fresh paper"
            " account reports last_equity '0', which must not read as an"
            " infinite return")

    spy_prev, spy_last, bar_date = _spy_closes(source, now)
    if bar_date != run_date:
        raise PnlUnavailable(
            f"{run_date}: SPY's last daily bar is {bar_date}, not today — this"
            " session never traded (holiday), or the close has not settled"
            " yet. The broker clock cannot tell those apart at 16:35; the feed"
            " can.")

    pnl_pct = (equity - last_equity) / last_equity
    spy_pct = (spy_last - spy_prev) / spy_prev
    return {"run_date": run_date, "equity": equity,
            "pnl_usd": equity - last_equity, "pnl_pct": pnl_pct,
            "spy_pct": spy_pct, "alpha": pnl_pct - spy_pct}


def format_line(pnl: dict) -> str:
    """One Slack line. Every figure carries an explicit sign: a losing day and
    a winning one must not differ by a character someone can miss while
    skimming a channel. The dollar sign goes inside the sign (`+$500.00`, not
    `$+500.00`)."""
    usd = pnl["pnl_usd"]
    return (f"P&L {'-' if usd < 0 else '+'}${abs(usd):,.2f}"
            f" ({pnl['pnl_pct']:+.2%}) · SPY {pnl['spy_pct']:+.2%} ·"
            f" alpha {pnl['alpha']:+.2%} · equity ${pnl['equity']:,.2f}")


def _spy_closes(source, end) -> tuple[float, float, str]:
    """SPY's last two daily closes, and the ET date of the last one.

    Routed through close_frame, never a hand-rolled bars request: close_frame
    owns the SIP_DELAY shift that the free Alpaca plan rejects with a 403 when
    it is missing, and applies it to the `end` passed in rather than to a
    wall-clock read of its own.
    """
    frame = source.close_frame(["SPY"], end=end)
    if "SPY" not in frame.columns or len(frame.index) < 2:
        raise PnlUnavailable(
            "SPY: a benchmark return needs two daily closes, got"
            f" {len(frame.index)}")
    prev, last = _finite(frame["SPY"].iloc[-2]), _finite(frame["SPY"].iloc[-1])
    if prev is None or last is None or prev <= 0:
        raise PnlUnavailable(
            f"SPY: closes unusable ({frame['SPY'].iloc[-2]!r},"
            f" {frame['SPY'].iloc[-1]!r}) — a ticker with no bars in the"
            " window arrives as a NaN column rather than an error")
    return prev, last, et_run_date(frame.index[-1])


def _finite(v) -> float | None:
    """`float(v)`, or None if it is missing, unparseable or non-finite. NaN
    arrives here as an ordinary value — both `_safe_float` and
    `_reshape_close_frame` produce it instead of raising."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
