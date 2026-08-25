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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from gate.risk import Approved, Rejected, size
from gate.tickets import (create_ticket, expire_open_tickets, open_tickets,
                          open_tickets_without_orders)
from orchestrator.clock import Clock, et_hhmm, iso
from orchestrator.protection import (assert_positions_accounted,
                                     assert_positions_protected)
from orchestrator.reconcile import reconcile_orders
from slackkit.outbox import append_alert, append_event, drain
from state.critiques import insert_default_critiques
from state.journal import append_entry
from state.transition import try_transition

# NOTE: NEVER import from agents/ here — turns arrive injected via StageCtx.
# That seam is what keeps orchestrator/ purity-lintable and sim-day possible.

TICKET_TTL_MIN = 45          # gate default expiry (contracts §2)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class StageCtx:
    conn: sqlite3.Connection
    run_date: str
    clock: Clock
    slack: object
    # Seats owing a signal per active ticker today. REQUIRED and no default:
    # a default would need manual syncing with production config, and getting
    # it wrong fails SILENTLY (a seat quietly stops being graded). Execution-
    # only contexts pass () — run_research raises rather than skipping.
    research_seats: tuple[str, ...]
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


def allowed_actions(market_inputs: dict) -> dict[str, dict[str, int]]:
    """The gate's ADVISORY allowed-actions snapshot: `{ticker: {buy, sell}}` in
    shares, the PM's sizing budget for the day (charters/pm.md Inputs).

    Computed by the same size() code path the enforcement pass uses (design
    §5, invariant §3.9), which is what makes "what the PM was shown is what
    the gate enforces" a fact rather than a hope — an unreachable shape is 0,
    and a ticker where BOTH shapes are 0 is absent entirely, so the key set of
    this dict IS run_pre_gate's active set. Pure: no writes, no clock, no
    agent input."""
    snapshot: dict[str, dict[str, int]] = {}
    for ticker, inputs in market_inputs.items():
        shapes = {}
        for side in ("buy", "sell"):
            result = _sized(inputs, side, "advisory")
            shapes[side] = result.max_qty if isinstance(result, Approved) else 0
        if shapes["buy"] or shapes["sell"]:
            snapshot[ticker] = shapes
    return snapshot


def run_pre_gate(ctx: StageCtx) -> list[str]:
    """Allowed-actions pass (design §3, 08:45): a ticker is active if either
    the buy shape or the sell shape is approvable. Tickers where nothing is
    possible ({buy:0, sell:0}) are dropped and never reach an LLM. Pure — no
    writes, so run_day can recompute it on a crash resume. Derived from
    allowed_actions so the active set and the snapshot the PM is shown can
    never drift apart."""
    return list(allowed_actions(ctx.market_inputs))


def _pre_gate_stage(ctx: StageCtx) -> list[str]:
    """The pre_gate stage body: run_pre_gate's pure computation, plus one
    alert per ticker dropped because BOTH shapes came back gate_error — a
    malformed/NaN feed, not the legitimate no_headroom/nothing_held skip,
    which must stay silent (review Important 5). Only called from inside
    run_stage, never from run_day's pure recompute-on-done branch, so a
    resumed day never re-posts these alerts."""
    active: list[str] = []
    now = None
    for ticker, inputs in ctx.market_inputs.items():
        results = [_sized(inputs, side, "advisory") for side in ("buy", "sell")]
        if any(isinstance(r, Approved) for r in results):
            active.append(ticker)
        elif all(isinstance(r, Rejected) and r.reason == "gate_error" for r in results):
            if now is None:
                now = iso(ctx.clock.now())
            append_alert(ctx.conn, "gate_error",
                         f"gate_error {ticker} — dropped from"
                         " today's universe (malformed feed)",
                         now_iso=now, ticker=ticker)
    return active


def _covered(ctx: StageCtx) -> set[tuple[str, str]]:
    """Every (agent, ticker) pair holding a signal row today. ONE predicate:
    the skip-on-resume decision and the defaults loop must never disagree
    about what "covered" means, and a COUNT-based version would miscount if
    signals ever holds a row for a seat outside research_seats."""
    return {(r["agent"], r["ticker"]) for r in ctx.conn.execute(
        "SELECT agent, ticker FROM signals WHERE run_date = ?",
        (ctx.run_date,))}


def run_research(ctx: StageCtx, active: list[str]) -> None:
    """Analyst turns, then the default for anything they missed: neutral/0/
    "no report" (contracts §6).

    The default is per SEAT per ticker. A ticker-level guard would let one
    seat's row mask another seat's silence, and calibration/rows.py grades per
    s.agent — so the silent seat would be scored only on days it reported.

    Idempotent per seat: when every (seat, ticker) pair is already covered the
    turn is SKIPPED, so a crash-resume does not pay for LLM calls it already
    paid for."""
    if not ctx.research_seats:
        raise ValueError(
            "run_research: research_seats is empty — refusing to run a research"
            " stage that can never insert its neutral/0 defaults (invariant 4)")
    wanted = [(seat, ticker)
              for seat in ctx.research_seats for ticker in active]
    turn = ctx.run_turn.get("research")
    if turn is not None and not set(wanted) <= _covered(ctx):
        turn()
    now = iso(ctx.clock.now())
    covered = _covered(ctx)          # re-read: the turn just inserted rows
    for seat, ticker in wanted:
        if (seat, ticker) in covered:
            continue
        ctx.conn.execute(
            # 'none', not the column default 'unknown': the orchestrator
            # wrote this row because the seat was silent, so no charter and no
            # model produced it. 'unknown' means "predates attribution" and
            # collapsing the two would hide which rows measure seat
            # RELIABILITY rather than charter JUDGMENT — the distinction that
            # keeps defaulted rows out of charter comparisons.
            "INSERT OR IGNORE INTO signals (run_date, agent, ticker, direction,"
            " confidence, summary, created_at, charter_version, model_id)"
            " VALUES (?, ?, ?, 'neutral', 0, 'no report', ?, 'none', 'none')",
            (ctx.run_date, seat, ticker, now))
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
        # Event BEFORE the row commit (review Minor 7): if a crash lands
        # between the two, the resume guard in this loop only ever sees the
        # decisions table, so once that INSERT commits the ticker is
        # skipped forever. Committing the alert first means the only
        # crash window left re-runs both (a harmless duplicate alert),
        # never loses the alert outright.
        append_alert(ctx.conn, "pm_timeout",
                     f"pm_timeout {ticker} — defaulted to hold", now_iso=now)
        ctx.conn.execute(
            # 'none' for the same reason as the defaulted signal above: a
            # pm_timeout hold is the orchestrator recording silence, not a
            # charter's judgment. This writer is owned by no other branch, so
            # nothing else would catch it being missed.
            "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
            " invalidation, status, created_at, charter_version, model_id)"
            " VALUES (?, ?, 'hold', 0, ?, 'n/a', 'submitted', ?, 'none',"
            " 'none')",
            (ctx.run_date, ticker, "no decision by the deadline (pm_timeout)",
             now))
        ctx.conn.commit()


def _gate_reject(ctx: StageCtx, d: sqlite3.Row, reason: str, now: str) -> None:
    """Reject path (review Critical 1): CAS any existing OPEN ticket for
    this decision closed FIRST, so a crash-resumed snapshot that now
    rejects can never leave a live ticket behind for validate_order to
    still authorize. Looked up fresh (not reused from the caller) so this
    is also the recovery path for Important 4's per-decision except."""
    existing = ctx.conn.execute(
        "SELECT id FROM tickets WHERE decision_id = ? AND status = 'open'",
        (d["id"],)).fetchone()
    if existing is not None:
        try_transition(ctx.conn, "tickets", {"id": existing["id"]},
                       "open", "expired", now)
    if try_transition(ctx.conn, "decisions", {"id": d["id"]},
                      "submitted", "rejected", now):
        append_event(ctx.conn, "gate_rejected",
                     {"ticker": d["ticker"], "side": d["action"],
                      "reason": reason}, now)


def _gate_handle(ctx: StageCtx, d: sqlite3.Row, now: str, expires_at: str,
                 expires_dt: datetime) -> None:
    if d["action"] == "hold":
        try_transition(ctx.conn, "decisions", {"id": d["id"]},
                       "submitted", "held", now)
        return
    # Existing-ticket lookup BEFORE sizing settles reject vs. approve (review
    # Criticals 1 & 2): a crash-resumed decision must reconcile whatever
    # ticket is already there, not just mint-or-skip on the approve branch.
    existing = ctx.conn.execute("SELECT * FROM tickets WHERE decision_id = ?",
                                (d["id"],)).fetchone()
    result = _sized(ctx.market_inputs.get(d["ticker"]) or {}, d["action"],
                    "enforce")
    # The gate CAPS the PM's ask; it never sizes UP a smaller one
    # (golden day: min(80, 66) = 66).
    max_qty = min(d["qty"], result.max_qty) if isinstance(result, Approved) else 0
    if max_qty < 1:
        reason = result.reason if not isinstance(result, Approved) else "zero_qty"
        _gate_reject(ctx, d, reason, now)
        return
    ticket_id = existing["id"] if existing is not None else ctx.id_factory()
    if existing is None:
        create_ticket(ctx.conn, id=ticket_id, decision_id=d["id"],
                      ticker=d["ticker"], side=d["action"], max_qty=max_qty,
                      stop_price=d["stop_price"], expires_at_iso=expires_at,
                      now_iso=now)
    else:
        # Reused ticket, reconciled to the freshly computed cap (review
        # Critical 2) — UPDATE in place, never expire-and-remint: the
        # ticket id IS the client_order_id (invariant 5), so a new id would
        # break order idempotency. If the trader already placed an order
        # against the old cap, that order is untouched — a lower max_qty
        # here cannot unplace it, it only stops a NEW order from being
        # authorized above the current cap. Guarded to 'open' so a ticket
        # some other path already closed is left alone.
        ctx.conn.execute(
            "UPDATE tickets SET max_qty = ?, expires_at = ? WHERE id = ?"
            " AND status = 'open'", (max_qty, expires_at, ticket_id))
        ctx.conn.commit()
    if try_transition(ctx.conn, "decisions", {"id": d["id"]},
                      "submitted", "approved", now):
        append_event(ctx.conn, "gate_approved",
                     {"ticket_id": ticket_id, "ticker": d["ticker"],
                      "side": d["action"], "max_qty": max_qty,
                      "expires_hhmm": et_hhmm(expires_dt)}, now)


def run_gate(ctx: StageCtx) -> None:
    """Enforcement pass (design §3, 11:15). Every submitted decision settles
    today: hold -> held; buy/sell -> a ticket capped at the gate's max_qty, or
    rejected with a reason. Re-runnable: only 'submitted' rows are touched and
    an already-minted ticket is reused (and reconciled) rather than
    duplicated. Each decision is isolated (review Important 4): a raise
    handling one ticker rejects only that ticker with 'gate_error' and the
    rest of the stage still runs. That matches the fail-closed posture size()
    already has, so one bad row can never sink the whole day's execution,
    reconciliation, and digest."""
    now_dt = ctx.clock.now()
    now = iso(now_dt)
    expires_dt = now_dt + timedelta(minutes=TICKET_TTL_MIN)
    expires_at = iso(expires_dt)
    rows = ctx.conn.execute(
        "SELECT * FROM decisions WHERE run_date = ? AND status = 'submitted'"
        " ORDER BY id", (ctx.run_date,)).fetchall()
    for d in rows:
        try:
            _gate_handle(ctx, d, now, expires_at, expires_dt)
        except Exception:
            _gate_reject(ctx, d, "gate_error", now)


def _alert_unexecuted_tickets(ctx: StageCtx) -> None:
    """D2: a ticket the gate approved that is STILL open after the trader
    turn, with no order row keyed by it (invariant 5: orders.client_order_id
    IS the ticket id), means the turn produced nothing. That is exactly the
    2026-08-17 failure — turn billed, no order placed, stage 'done', day
    reported a success. Alerting (not raising) is deliberate: default HOLD
    stands, the day still finishes, and the alert plus the reddened audit are
    the signal. Zero open tickets stays silent — a hold day is normal.

    Keyed on STATUS, never on the clock (issue #40). Expiry sweeps at the
    start of the body, before the turn, so the only way a past-TTL ticket is
    still 'open' here is that its TTL passed DURING the turn — a turn that
    overran its whole 45-minute budget and placed nothing. That is the loudest
    case there is, and the clock filter in open_tickets silently deleted
    exactly it from the answer."""
    now = iso(ctx.clock.now())
    for ticket in open_tickets_without_orders(ctx.conn):
        append_alert(ctx.conn, "ticket_open_after_exec",
                     f"ticket {ticket['id'][:8]} open after exec"
                     " turn — no order", now_iso=now)


def run_execution(ctx: StageCtx, run_trader_turn: Callable[[], None] | None) -> str:
    """Trader stage. Zero open tickets -> no turn at all (no LLM spend on a
    hold day); the stage still drains and still checkpoints done."""
    now = iso(ctx.clock.now())
    expire_open_tickets(ctx.conn, now)   # clock-injected expiry (acceptance §0)

    def body() -> None:
        if run_trader_turn is not None and open_tickets(ctx.conn, iso(ctx.clock.now())):
            run_trader_turn()
        _alert_unexecuted_tickets(ctx)

    run_stage(ctx, "execution", body)
    return "done"


def _decision_rows(conn: sqlite3.Connection, run_date: str) -> list[dict]:
    """The digest's decisions as fields, not prose. slackkit/render.py lays
    the digest out from these: it must not parse them back out of the text
    (free text is never parsed) nor read the DB itself (invariant 6)."""
    return [{"ticker": r["ticker"], "action": r["action"], "qty": r["qty"],
             "status": r["status"]}
            for r in conn.execute(
                "SELECT ticker, action, qty, status FROM decisions"
                " WHERE run_date = ? ORDER BY ticker", (run_date,)).fetchall()]


def _decision_line(rows: list[dict]) -> str:
    if not rows:
        return "decisions: none"
    return "decisions: " + ", ".join(
        f"{d['ticker']} {d['action']} {d['qty']} ({d['status']})" for d in rows)


def _fill_rows(conn: sqlite3.Connection, run_date: str) -> list[dict]:
    """Partially-filled orders count (review Fix 7): real shares changed hands,
    so a digest reading `fills: none` beside them is untrue — and the digest is
    cited as acceptance evidence (HANDOFF-LIVE §5). Marked `partial` so the
    word keeps meaning something on a complete fill."""
    return [{"symbol": r["symbol"], "side": r["side"],
             "filled_qty": r["filled_qty"],
             "filled_avg_price": r["filled_avg_price"],
             "partial": r["status"] == "partially_filled"}
            for r in conn.execute(
                "SELECT o.symbol, o.side, o.filled_qty, o.filled_avg_price,"
                " o.status FROM orders o JOIN tickets t ON t.id = o.client_order_id"
                " JOIN decisions d ON d.id = t.decision_id"
                " WHERE d.run_date = ? AND o.status IN ('filled', 'partially_filled')"
                " ORDER BY o.submitted_at", (run_date,)).fetchall()]


def _fill_line(rows: list[dict]) -> str:
    if not rows:
        return "fills: none"
    return "fills: " + ", ".join(
        f"{f['symbol']} {f['side']} {f['filled_qty']}@{f['filled_avg_price']:.2f}"
        + (" (partial)" if f["partial"] else "") for f in rows)


def _signal_line(conn: sqlite3.Connection, run_date: str, agent: str) -> str:
    rows = conn.execute(
        "SELECT ticker, direction, confidence FROM signals"
        " WHERE run_date = ? AND agent = ? ORDER BY ticker",
        (run_date, agent)).fetchall()
    return "signals: " + (", ".join(
        f"{r['ticker']} {r['direction']} ({r['confidence']}/100)" for r in rows)
        or "none")


def _append_entry_once(root, seat: str, run_date: str, text: str) -> None:
    """append_entry is append-only, not idempotent by itself; this makes it
    so by checking the run_date header is not already in the file (review
    Minor 6)."""
    path = Path(root) / f"{seat}.md"
    header = f"## {run_date}\n"
    if path.exists() and header in path.read_text():
        return
    append_entry(root, seat, run_date, text)


def run_close(ctx: StageCtx) -> None:
    """EOD digest to #pnl + one journal line per participating seat. Posts
    even on a full-HOLD day. Idempotent per-piece (review Minor 6): the
    digest-exists check only guards the digest post, and each journal write
    is independently guarded by _append_entry_once, so an exit between the
    digest commit and the journal writes still gets the journals written on
    resume instead of losing them forever."""
    conn, run_date = ctx.conn, ctx.run_date
    marker = f'"run_date": "{run_date}"'
    digest_posted = conn.execute(
        "SELECT 1 FROM events WHERE kind = 'digest' AND payload LIKE ?",
        (f"%{marker}%",)).fetchone() is not None
    now = iso(ctx.clock.now())
    decision_rows, fill_rows = (_decision_rows(conn, run_date),
                                _fill_rows(conn, run_date))
    decisions, fills = _decision_line(decision_rows), _fill_line(fill_rows)
    if not digest_posted:
        cost = conn.execute(
            "SELECT COALESCE(SUM(usd_estimate), 0) s FROM costs WHERE run_date = ?",
            (run_date,)).fetchone()["s"]
        # total_cost_usd is a client-side estimate — always labeled "est." (CLAUDE.md)
        text = (f"{run_date} close\n{decisions}\n{fills}\n"
                f"est. inference cost ${cost:.2f}")
        # text AND fields: text is Slack's notification fallback and what rows
        # written before Block Kit carry; the rows are what render.py lays out.
        append_event(conn, "digest",
                     {"text": text, "run_date": run_date,
                      "decisions": decision_rows, "fills": fill_rows,
                      "cost_usd": cost}, now)
    if ctx.journals_root is None:
        return
    for agent in conn.execute("SELECT DISTINCT agent FROM signals"
                              " WHERE run_date = ? ORDER BY agent",
                              (run_date,)).fetchall():
        _append_entry_once(ctx.journals_root, agent["agent"], run_date,
                           _signal_line(conn, run_date, agent["agent"]))
    _append_entry_once(ctx.journals_root, "pm", run_date, decisions)
    if not fills.endswith("none"):
        _append_entry_once(ctx.journals_root, "exec", run_date, fills)


def run_day(ctx: StageCtx, *, execution_turn: Callable[[], None] | None = None,
            broker=None, sleep: Callable[[float], None] | None = None) -> None:
    """One trading day, sequential (design §3, compressed for MVF). Each stage
    is wrapped in its own checkpoint, so a re-fire resumes rather than
    repeats."""
    active = run_stage(ctx, "pre_gate", lambda: _pre_gate_stage(ctx))
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
    # NOT a stage: an assertion must re-check on a resumed day, never be
    # skipped as 'done'. Drained explicitly because a resumed day can find
    # close already 'done', and run_stage returns before draining — which
    # would leave a naked-position alert sitting in the outbox until the next
    # run. `sleep` is threaded through for the one short re-read a
    # just-created OTO leg needs to become visible.
    now = iso(ctx.clock.now())
    # Two directions, deliberately separate calls: protection asks whether what
    # the broker holds is covered, accounting asks whether what the fund's own
    # orders claim is still held. A position that CLOSED produces no broker
    # position, so the first is structurally silent on exactly the case the
    # second exists to catch.
    findings = assert_positions_protected(ctx.conn, broker=broker,
                                          now_iso=now, sleep=sleep)
    findings += assert_positions_accounted(ctx.conn, broker=broker,
                                           now_iso=now, sleep=sleep)
    if findings:
        drain(ctx.conn, ctx.slack, now)
    run_stage(ctx, "close", lambda: run_close(ctx))
