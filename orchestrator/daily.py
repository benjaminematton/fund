"""MVF daily stages + sequential runner (design §3, review P4). No LLM imports,
no wall clock — agent turns and market inputs arrive injected via StageCtx.
Every stage body is wrapped by run_stage (checkpoint CAS, outbox drain) —
the generalization of Phase 1's run_execution_stage.

Default is HOLD everywhere (invariant 4): a missing analyst signal becomes
neutral/0, a missing PM decision becomes hold/0 + a pm_timeout alert, and any
gate error rejects. A full-HOLD day still completes every stage and posts the
digest."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from gate.risk import Approved, size
from gate.tickets import create_ticket, expire_open_tickets, open_tickets
from orchestrator.clock import Clock, iso
from orchestrator.reconcile import reconcile_orders
from slackkit.outbox import append_event, drain
from state.critiques import insert_default_critiques
from state.journal import append_entry
from state.transition import try_transition

# NOTE: NEVER import from agents/ here — turns arrive injected via StageCtx.
# That seam is what keeps orchestrator/ purity-lintable and sim-day possible.

TICKET_TTL_MIN = 45          # gate default expiry (contracts §2)
DEFAULT_ANALYST = "analyst"  # seat that owns a defaulted "no report" signal


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class StageCtx:
    conn: sqlite3.Connection
    run_date: str
    clock: Clock
    slack: object
    market_inputs: dict = field(default_factory=dict)   # ticker -> gate-inputs dict
    run_turn: dict = field(default_factory=dict)        # stage -> zero-arg callable
    id_factory: Callable[[], str] = _new_id             # uuid4 live; fixed in tests
    journals_root: object = None                        # Path | None


def run_stage(ctx: StageCtx, stage: str, body: Callable[[], object]):
    """Checkpoint CAS + outbox drain around one stage body (contracts §5.2):
      done    -> skip, return None (stages 'done' never re-run, contracts §6)
      pending -> CAS to running, run
      running -> crash resume: re-run the idempotent body
    Returns the body's value, or None when the stage was already done."""
    now = iso(ctx.clock.now())
    key = {"run_date": ctx.run_date, "stage": stage, "ticker": "*"}
    ctx.conn.execute(
        "INSERT OR IGNORE INTO checkpoints (run_date, stage, ticker, status,"
        " updated_at) VALUES (?, ?, '*', 'pending', ?)",
        (ctx.run_date, stage, now))
    ctx.conn.commit()
    status = ctx.conn.execute(
        "SELECT status FROM checkpoints WHERE run_date = ? AND stage = ?"
        " AND ticker = '*'", (ctx.run_date, stage)).fetchone()["status"]
    if status == "done":
        return None
    if status == "pending":
        try_transition(ctx.conn, "checkpoints", key, "pending", "running", now)
    result = body()
    now2 = iso(ctx.clock.now())
    drain(ctx.conn, ctx.slack, now2)
    try_transition(ctx.conn, "checkpoints", key, "running", "done", now2)
    return result


def _sized(inputs, side: str, mode: str):
    """size() over a PLAIN DICT — never a constructed GateInputs (review C3).
    Garbage (NaN vol, missing sector) must reach the gate's own validator and
    come back as Rejected('gate_error'), not raise inside the orchestrator."""
    return size({**dict(inputs), "side": side}, mode)


def run_pre_gate(ctx: StageCtx) -> list[str]:
    """Allowed-actions pass (design §3, 08:45): a ticker is active if either
    the buy shape or the sell shape is approvable. Tickers where nothing is
    possible ({buy:0, sell:0}) are dropped and never reach an LLM. Pure — no
    writes, so run_day can recompute it on a crash resume."""
    return [ticker for ticker, inputs in ctx.market_inputs.items()
            if any(isinstance(_sized(inputs, side, "advisory"), Approved)
                   for side in ("buy", "sell"))]


def run_research(ctx: StageCtx, active: list[str]) -> None:
    """Analyst turn, then the default for anything it missed: neutral/0/
    "no report" (contracts §6). Idempotent: a ticker that already has a
    signal today is left alone."""
    turn = ctx.run_turn.get("research")
    if turn is not None:
        turn()
    now = iso(ctx.clock.now())
    for ticker in active:
        covered = ctx.conn.execute(
            "SELECT 1 FROM signals WHERE run_date = ? AND ticker = ?",
            (ctx.run_date, ticker)).fetchone()
        if covered is not None:
            continue
        ctx.conn.execute(
            "INSERT OR IGNORE INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at)"
            " VALUES (?, ?, ?, 'neutral', 0, 'no report', ?)",
            (ctx.run_date, DEFAULT_ANALYST, ticker, now))
    ctx.conn.commit()


def run_decision(ctx: StageCtx, active: list[str]) -> None:
    """Default critiques FIRST (MVF has no Critic seat; without a critique row
    every submit_decision call errors), then the PM turn, then hold/0 +
    pm_timeout alert for anything the PM did not decide (contracts §6)."""
    now = iso(ctx.clock.now())
    insert_default_critiques(ctx.conn, ctx.run_date, active, "no_critic_seat", now)
    turn = ctx.run_turn.get("decision")
    if turn is not None:
        turn()
    now = iso(ctx.clock.now())
    for ticker in active:
        decided = ctx.conn.execute(
            "SELECT 1 FROM decisions WHERE run_date = ? AND ticker = ?",
            (ctx.run_date, ticker)).fetchone()
        if decided is not None:
            continue
        ctx.conn.execute(
            "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
            " invalidation, status, created_at)"
            " VALUES (?, ?, 'hold', 0, ?, 'n/a', 'submitted', ?)",
            (ctx.run_date, ticker, "no decision by the deadline (pm_timeout)",
             now))
        ctx.conn.commit()
        append_event(ctx.conn, "alert",
                     {"text": f"pm_timeout {ticker} — defaulted to hold"}, now)


def run_gate(ctx: StageCtx) -> None:
    """Enforcement pass (design §3, 11:15). Every submitted decision settles
    today: hold -> held; buy/sell -> a ticket capped at the gate's max_qty, or
    rejected with a reason. Re-runnable: only 'submitted' rows are touched and
    an already-minted ticket is reused rather than duplicated."""
    now_dt = ctx.clock.now()
    now = iso(now_dt)
    expires_at = iso(now_dt + timedelta(minutes=TICKET_TTL_MIN))
    rows = ctx.conn.execute(
        "SELECT * FROM decisions WHERE run_date = ? AND status = 'submitted'"
        " ORDER BY id", (ctx.run_date,)).fetchall()
    for d in rows:
        if d["action"] == "hold":
            try_transition(ctx.conn, "decisions", {"id": d["id"]},
                           "submitted", "held", now)
            continue
        result = _sized(ctx.market_inputs.get(d["ticker"]) or {}, d["action"],
                        "enforce")
        # The gate CAPS the PM's ask; it never sizes UP a smaller one
        # (golden day: min(80, 66) = 66).
        max_qty = min(d["qty"], result.max_qty) if isinstance(result, Approved) else 0
        if max_qty < 1:
            reason = result.reason if not isinstance(result, Approved) else "zero_qty"
            if try_transition(ctx.conn, "decisions", {"id": d["id"]},
                              "submitted", "rejected", now):
                append_event(ctx.conn, "gate_rejected",
                             {"ticker": d["ticker"], "side": d["action"],
                              "reason": reason}, now)
            continue
        existing = ctx.conn.execute("SELECT id FROM tickets WHERE decision_id = ?",
                                    (d["id"],)).fetchone()
        ticket_id = existing["id"] if existing is not None else ctx.id_factory()
        if existing is None:
            create_ticket(ctx.conn, id=ticket_id, decision_id=d["id"],
                          ticker=d["ticker"], side=d["action"], max_qty=max_qty,
                          stop_price=d["stop_price"], expires_at_iso=expires_at,
                          now_iso=now)
        if try_transition(ctx.conn, "decisions", {"id": d["id"]},
                          "submitted", "approved", now):
            append_event(ctx.conn, "gate_approved",
                         {"ticket_id": ticket_id, "ticker": d["ticker"],
                          "side": d["action"], "max_qty": max_qty,
                          "expires_hhmm": expires_at[11:16] + "Z"}, now)


def run_execution(ctx: StageCtx, run_trader_turn: Callable[[], None] | None) -> str:
    """Trader stage. Zero open tickets -> no turn at all (no LLM spend on a
    hold day); the stage still drains and still checkpoints done."""
    now = iso(ctx.clock.now())
    expire_open_tickets(ctx.conn, now)   # gate expiry is clock-injected (§0)

    def body() -> None:
        if run_trader_turn is None:
            return
        if not open_tickets(ctx.conn, iso(ctx.clock.now())):
            return
        run_trader_turn()

    run_stage(ctx, "execution", body)
    return "done"


def _decision_line(conn: sqlite3.Connection, run_date: str) -> str:
    rows = conn.execute(
        "SELECT ticker, action, qty, status FROM decisions WHERE run_date = ?"
        " ORDER BY ticker", (run_date,)).fetchall()
    if not rows:
        return "decisions: none"
    return "decisions: " + ", ".join(
        f"{r['ticker']} {r['action']} {r['qty']} ({r['status']})" for r in rows)


def _fill_line(conn: sqlite3.Connection, run_date: str) -> str:
    rows = conn.execute(
        "SELECT o.symbol, o.side, o.filled_qty, o.filled_avg_price"
        " FROM orders o JOIN tickets t ON t.id = o.client_order_id"
        " JOIN decisions d ON d.id = t.decision_id"
        " WHERE d.run_date = ? AND o.status = 'filled'"
        " ORDER BY o.submitted_at", (run_date,)).fetchall()
    if not rows:
        return "fills: none"
    return "fills: " + ", ".join(
        f"{r['symbol']} {r['side']} {r['filled_qty']}@{r['filled_avg_price']:.2f}"
        for r in rows)


def _signal_line(conn: sqlite3.Connection, run_date: str, agent: str) -> str:
    rows = conn.execute(
        "SELECT ticker, direction, confidence FROM signals"
        " WHERE run_date = ? AND agent = ? ORDER BY ticker",
        (run_date, agent)).fetchall()
    return "signals: " + (", ".join(
        f"{r['ticker']} {r['direction']} ({r['confidence']}/100)" for r in rows)
        or "none")


def run_close(ctx: StageCtx) -> None:
    """EOD digest to #pnl + one journal line per participating seat. Posts
    even on a full-HOLD day. Idempotent: a digest already written for this
    run_date short-circuits the whole body (no second digest, no second
    journal entry)."""
    conn, run_date = ctx.conn, ctx.run_date
    marker = f'"run_date": "{run_date}"'
    if conn.execute("SELECT 1 FROM events WHERE kind = 'digest' AND payload LIKE ?",
                    (f"%{marker}%",)).fetchone() is not None:
        return
    now = iso(ctx.clock.now())
    decisions, fills = _decision_line(conn, run_date), _fill_line(conn, run_date)
    cost = conn.execute(
        "SELECT COALESCE(SUM(usd_estimate), 0) s FROM costs WHERE run_date = ?",
        (run_date,)).fetchone()["s"]
    # total_cost_usd is a client-side estimate — always labeled "est." (CLAUDE.md)
    text = (f"{run_date} close\n{decisions}\n{fills}\n"
            f"est. inference cost ${cost:.2f}")
    append_event(conn, "digest", {"text": text, "run_date": run_date}, now)
    if ctx.journals_root is None:
        return
    for agent in conn.execute("SELECT DISTINCT agent FROM signals"
                              " WHERE run_date = ? ORDER BY agent",
                              (run_date,)).fetchall():
        append_entry(ctx.journals_root, agent["agent"], run_date,
                     _signal_line(conn, run_date, agent["agent"]))
    append_entry(ctx.journals_root, "pm", run_date, decisions)
    if not fills.endswith("none"):
        append_entry(ctx.journals_root, "exec", run_date, fills)


def run_day(ctx: StageCtx, *, execution_turn: Callable[[], None] | None = None,
            broker=None, sleep: Callable[[float], None] | None = None) -> None:
    """One trading day, sequential (design §3, compressed for MVF). Each stage
    is wrapped in its own checkpoint, so a re-fire resumes rather than
    repeats."""
    active = run_stage(ctx, "pre_gate", lambda: run_pre_gate(ctx))
    if active is None:               # stage already done: recompute (pure, no writes)
        active = run_pre_gate(ctx)
    run_stage(ctx, "research", lambda: run_research(ctx, active))
    run_stage(ctx, "decision", lambda: run_decision(ctx, active))
    run_stage(ctx, "gate", lambda: run_gate(ctx))
    run_execution(ctx, execution_turn if execution_turn is not None
                  else ctx.run_turn.get("execution"))
    run_stage(ctx, "reconciliation",
              lambda: reconcile_orders(ctx.conn, clock=ctx.clock, broker=broker,
                                       sleep=sleep or (lambda _s: None)))
    run_stage(ctx, "close", lambda: run_close(ctx))
