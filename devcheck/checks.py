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
    difference. Manual out-of-gate orders produce exactly this.

    An unread count is reported as unread. AlpacaSource exposes no fill
    history, so this is a live state rather than a hypothetical: rendering it
    as agreement would print a green row for a comparison nobody performed.
    """
    rows = len(s.orders)
    if s.broker_fill_count is None:
        return Finding(
            "db_broker_agreement",
            "warn",
            f"orders has {rows} row(s); the broker's fill count was not read, so the "
            "two were never compared — AlpacaSource exposes no fill history",
        )
    if rows == s.broker_fill_count:
        return Finding("db_broker_agreement", "ok", f"orders rows == broker fills ({rows})")
    return Finding(
        "db_broker_agreement",
        "alert",
        f"orders has {rows} row(s); broker has seen {s.broker_fill_count} fill(s) — "
        "the fund's record disagrees with the broker",
    )


# Codes that mean "the pipeline degraded to its default and said so".
# Invariant 4 makes these correct behaviour, not bugs — they warn so a
# permanently degraded day cannot quietly become the normal one.
_DEGRADATION_CODES = ("gate_error", "pm_timeout", "critic_timeout", "missing_signal")


def check_degradations(s: Snapshot) -> Finding:
    """Invariant 4 — every error resolves to HOLD. Correct, and worth seeing."""
    seen = [c for c in s.scorecard_codes if c in _DEGRADATION_CODES]
    if not seen:
        return Finding("degradations", "ok", "no stage degraded to its default")
    return Finding(
        "degradations",
        "warn",
        f"degraded to default: {', '.join(sorted(set(seen)))} — correct per invariant 4, "
        "but the day did not run clean",
    )


def check_checkpoints(s: Snapshot) -> Finding:
    """Phase 2 acceptance — every checkpoint reaches `done`."""
    unfinished = sorted({stage for _, stage, status in s.checkpoints if status != "done"})
    if not unfinished:
        return Finding("checkpoints", "ok", f"{len(s.checkpoints)} checkpoint(s), all done")
    return Finding(
        "checkpoints",
        "alert",
        f"stage(s) not done: {', '.join(unfinished)}",
    )


def check_journals(s: Snapshot) -> Finding:
    """Phase 2 acceptance — each participating seat writes a journal entry.
    design.md §7 makes memory load-bearing in this phase."""
    missing = sorted(set(s.seats_participating) - set(s.journals_written))
    if not missing:
        return Finding("journals", "ok", "every participating seat wrote a journal entry")
    return Finding(
        "journals",
        "warn",
        f"participated but wrote no journal entry: {', '.join(missing)}",
    )


def check_reflection(s: Snapshot) -> Finding:
    """Phase 2 acceptance — the nightly job writes `resolutions` at horizon.

    An empty resolutions table is correct until a decision passes its horizon
    and a dead job afterwards. The snapshot carries the decisions that are
    already due, so the two cases cannot be confused: this is the shape that
    fooled a session on 2026-08-21, which read the empty table as a failure.
    """
    if not s.due_unresolved:
        return Finding("reflection", "ok", "no decision is past its horizon and unresolved")
    ids = ", ".join(str(i) for i in s.due_unresolved)
    return Finding(
        "reflection",
        "alert",
        f"{len(s.due_unresolved)} decision(s) past horizon with no resolutions row "
        f"(ids: {ids}) — the nightly reflection job is not landing",
    )


def check_position_coverage(s: Snapshot) -> Finding:
    """design.md §5 — a ticket carrying a stop_price becomes a broker-side
    stop leg, so a held position is expected to be covered.

    Coverage is AGGREGATE: N shares covered by one or more live stops.
    Partial cover is exposure; the uncovered remainder has no code path that
    will protect it.

    An unread book is an alert, not a green row. "0 position(s), every share
    covered" is what an unreachable broker would otherwise print, and it is
    indistinguishable from a genuinely flat account.
    """
    if s.positions is None:
        why = f" ({s.broker_error})" if s.broker_error else ""
        return Finding(
            "position_coverage",
            "alert",
            f"the broker's position book was not read{why}, so live exposure is "
            "unknown — spec §4 forbids inferring position state from the database",
        )
    naked = [p for p in s.positions if p.covering_qty < p.qty]
    if not naked:
        return Finding(
            "position_coverage",
            "ok",
            f"{len(s.positions)} position(s), every share covered",
        )
    parts = [
        f"{p.symbol} {p.covering_qty:g} of {p.qty:g} covered" for p in naked
    ]
    return Finding(
        "position_coverage",
        "alert",
        "; ".join(parts) + " — the uncovered shares have no code path that will protect them",
    )


def check_deploy_state(s: Snapshot) -> Finding:
    """Deployment state — is the code under test the code that is running.

    Warn, not alert: being behind is the normal state between a merge and a
    deploy. It is worth seeing because a green suite says nothing about the
    box, and on 2026-08-21 four sessions each held a different answer.
    """
    if s.commits_behind == 0:
        return Finding("deploy_state", "ok", f"droplet level with origin/master ({s.origin_master})")
    return Finding(
        "deploy_state",
        "warn",
        f"droplet at {s.droplet_head}, origin/master at {s.origin_master} — "
        f"{s.commits_behind} commit(s) behind",
    )


def check_services(s: Snapshot) -> Finding:
    """The scheduled units that constitute the fund actually running."""
    bad = [r for r in s.services.values() if r.result != "success"]
    if not bad:
        names = ", ".join(sorted(s.services))
        return Finding("services", "ok", f"last run succeeded: {names}" if names else "no units read")
    parts = [f"{r.unit}: {r.result}" + (f" at {r.last_run}" if r.last_run else "") for r in bad]
    return Finding("services", "alert", "; ".join(sorted(parts)))


def check_database(s: Snapshot) -> Finding:
    """Invariant 6 — SQLite is the source of truth, so a database nobody
    could read means the day's record was never inspected.

    This is the root cause the DB-derived checks defer to. It exists because
    a query against a wrong-but-present path returns no rows with exit 0, and
    "no rows" is how a healthy empty table also looks.
    """
    if s.db_read_ok:
        return Finding("database", "ok", "the fund database was read")
    return Finding(
        "database",
        "alert",
        "the fund database could not be read — every check sourced from it is "
        "reported unknown rather than healthy",
    )


def check_issue_coverage(s: Snapshot, findings: list[Finding]) -> Finding:
    """docs/agents/issue-tracker.md — work in this repo lives as GitHub issues.

    An alert nobody files disappears when the window closes. Only `alert`
    participates: a `warn` that nagged every day would train the reader to
    skip the report, which is the failure suppression exists to prevent.

    A suppressed check is excluded for that same reason — it is declared
    known noise, and this runs before apply_suppression() has downgraded it,
    so the severity seen here is the raw one.
    """
    untracked = sorted(
        f.check
        for f in findings
        if f.severity == "alert"
        and f.check != "issue_coverage"
        and f.check not in s.tracked_checks
        and f.check not in s.suppressed
    )
    if not untracked:
        return Finding("issue_coverage", "ok", "every alert is tracked by an issue")
    hint = " ".join(f'gh issue create --label "check:{c}"' for c in untracked[:2])
    return Finding(
        "issue_coverage",
        "alert",
        f"alert(s) with no open issue: {', '.join(untracked)} — these die with this "
        f"window. File them: {hint}",
    )
