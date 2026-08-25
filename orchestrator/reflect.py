"""The factual frame a seat reflects ON — computed, never narrated.

orchestrator/resolve.py writes the outcome of every decision and leaves
`reflection` NULL on purpose: "it is an agent write, and this job holds no
LLM." This module is that follow-on, and it inverts the usual shape. Instead of
asking the seat what happened and hoping it remembers correctly, the facts are
rendered from rows and handed to it; the seat writes only interpretation.

WHY, because it is not obvious and the cheaper design is the wrong one. A
re-analysis of Reflexion's own logs (134 ALFWorld environments, 15 trials)
found 32% of them developed frozen reflective memory in which ZERO of 121
stored reflections named the correct target; those environments took 7.6 trials
to solve against 1.5 for the rest. The mechanism is an information vacuum —
given a coarse outcome signal and no step-level detail, the reflection
generator emits a plausible but causally wrong diagnosis, which then persists
precisely BECAUSE it reads as credible on retrieval. Programmatic extraction of
the facts beat prompting the model for evidence-grounded reflection, 86% to
~0%. Removing the vacuum is cheaper and more reliable than asking the model to
be careful inside one.

The fund's signal is richer than that study's binary pass/fail — realized
return and alpha are continuous — so the effect here should be weaker. The
change still costs nothing, which is why it stands.

Pure: rows in, string out. No clock (every value comes from a row), no network,
no LLM. Purity-linted with the rest of orchestrator/.
"""

from __future__ import annotations

# One row per decision, with its outcome. Signals are fetched separately: a
# decision has many, and joining them here would multiply the outcome row.
_CALL = """
SELECT d.run_date, d.ticker, d.action, d.qty, d.status,
       r.horizon_days, r.realized_return, r.alpha_vs_spy
  FROM decisions d
  JOIN resolutions r ON r.decision_id = d.id
 WHERE d.id = ?
"""

# What the seats said that day about that ticker, in the order they are graded.
_SIGNALS = """
SELECT agent, direction, confidence
  FROM signals
 WHERE run_date = ? AND ticker = ?
 ORDER BY agent
"""


def _pct(value: float) -> str:
    """+6.14% — always signed, because an unsigned loss reads as a gain at a
    glance and this text is the one thing the seat is asked to trust."""
    return f"{value * 100:+.2f}%"


def _pp(value: float) -> str:
    """+5.04pp. Percentage POINTS against the benchmark, not percent: alpha is
    a difference of two returns, and calling it a percentage invites the reader
    to compound it."""
    return f"{value * 100:+.2f}pp"


def reflection_frame(conn, decision_id: int) -> str | None:
    """The facts of one resolved decision, or None when it has not resolved.

    None rather than a placeholder: an unmeasured call and a call that exactly
    matched SPY are the same row in a scoreboard and mean opposite things
    (invariant 4, and resolve.py's own posture). A seat asked to reflect on a
    call with no outcome would be reflecting on nothing.

    Deliberately says NOTHING about invalidation. resolve.py writes that column
    as a constant 0 because neither signal the fund has — the broker's stop
    leg, Ops' watch on the free-text condition — is readable from that job.
    Rendering it would state "not invalidated" as fact on every row, which is
    exactly the confident-but-wrong input this frame exists to eliminate. The
    field re-enters when something actually writes it.
    """
    call = conn.execute(_CALL, (decision_id,)).fetchone()
    if call is None:
        return None

    signals = conn.execute(
        _SIGNALS, (call["run_date"], call["ticker"])).fetchall()
    said = " · ".join(f"{s['agent']} {s['direction']} {s['confidence']}"
                      for s in signals) or "no signals"

    return (
        f"{call['ticker']} · {call['run_date']} · "
        f"{call['action']} {call['qty']} ({call['status']})\n"
        f"signals: {said}\n"
        f"after {call['horizon_days']} sessions: "
        f"{_pct(call['realized_return'])} realized · "
        f"{_pp(call['alpha_vs_spy'])} vs SPY"
    )


def store_reflection(conn, decision_id: int, frame: str,
                     prose: str = "") -> bool:
    """Write frame + the seat's interpretation into `resolutions.reflection`.

    Stored together, facts first, so a later reader sees the numbers beside the
    claim without trusting the seat to have cited them — the auditable-trail
    property achieved by storage rather than by asking the model to comply. A
    seat that writes nothing still leaves the frame: a silent turn produces a
    record, not a blank.

    First write wins: the row is claimed only while `reflection` IS NULL, so a
    repeat call for the same decision leaves the stored text alone. The stage
    that drives this re-runs its whole body from the top on crash-resume, and
    without the guard a crash partway through replaces considered reflections
    with the re-run's. The guard is on the column, not on a memory of what this
    process did, so a write from another session is stopped the same way.

    What the guard does NOT do is save the money. `prose` is a parameter: the
    seat has already been asked and the turn already paid for by the time this
    function is reached, and all a blocked write saves is the overwrite. A
    stage that calls the seat inside its loop therefore pays for all fifty
    decisions again on resume and discards forty of the answers here. Skipping
    that cost is the CALLER's job and must happen BEFORE the turn: select the
    decisions whose `reflection` IS NULL and reflect only on those.

    Returns True when this call wrote the row. False has two meanings and
    neither is an error worth raising on: a reflection is already stored (the
    ordinary resumed-stage path), or `decision_id` has no `resolutions` row at
    all — resolve.py has not run, and that decision is absent from the audit
    trail this module exists to guarantee. A caller must not log "reflected" on
    a False; it tells the two apart by the frame it already holds, since
    reflection_frame returns None on exactly the same missing row — a real
    frame plus a False is the already-reflected case.

    A consequence worth stating, and why both halves must keep going in
    together: a caller that stored the frame before the turn and the prose
    after it would find the second write blocked and the row factual-only
    forever. One call per decision.
    """
    text = f"{frame}\n\n{prose}".rstrip() if prose else frame
    wrote = conn.execute("UPDATE resolutions SET reflection = ?"
                         " WHERE decision_id = ? AND reflection IS NULL",
                         (text, decision_id)).rowcount == 1
    conn.commit()
    return wrote
