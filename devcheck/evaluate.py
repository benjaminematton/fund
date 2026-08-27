from __future__ import annotations

from dataclasses import replace

from devcheck import checks
from devcheck.model import Finding, Snapshot

# Ordered registry. Adding a check means adding it here and nowhere else.
# Order is display order: invariants first, acceptance criteria second,
# deployment state last.
CHECKS = (
    checks.check_paper_trading,
    checks.check_trading_toolset,
    checks.check_order_idempotency,
    checks.check_outbox,
    checks.check_db_broker_agreement,
    checks.check_degradations,
    checks.check_checkpoints,
    checks.check_journals,
    checks.check_reflection,
    checks.check_position_coverage,
    checks.check_deploy_state,
    checks.check_services,
    checks.check_database,
)


def evaluate(snapshot: Snapshot) -> list[Finding]:
    """Run every check against one snapshot. Pure.

    A check returning None means "nothing to say"; it is omitted rather than
    rendered as an empty row.
    """
    out: list[Finding] = []
    for check in CHECKS:
        result = check(snapshot)
        if result is None:
            continue
        if isinstance(result, Finding):
            out.append(result)
        else:
            out.extend(result)
    out = _starve_db_derived(out, snapshot.db_read_ok)
    # Runs last: it reads the other checks' output, not just the snapshot.
    out.append(checks.check_issue_coverage(snapshot, out))
    return out


# Checks whose entire input is the droplet's SQLite database. When that read
# fails there is nothing true to say about them, and "ok" would be the false
# green this package exists to remove. They warn rather than alert so one root
# cause stays loud instead of five copies of it.
DB_DERIVED = ("order_idempotency", "outbox", "checkpoints", "journals", "reflection")


def _starve_db_derived(findings: list[Finding], db_read_ok: bool) -> list[Finding]:
    """Rewrite DB-sourced verdicts to "unknown" when the database was unread."""
    if db_read_ok:
        return findings
    return [
        replace(f, severity="warn",
                detail="the fund database was not read, so this was never checked")
        if f.check in DB_DERIVED else f
        for f in findings
    ]


def apply_suppression(findings: list[Finding], suppressed: frozenset[str]) -> list[Finding]:
    """Downgrade suppressed checks to ok and say so.

    Never drops the row. A vanished row is indistinguishable from a check
    that never ran, and the whole package exists to remove that ambiguity.
    """
    out = []
    for f in findings:
        if f.check in suppressed and f.severity != "ok":
            out.append(replace(f, severity="ok", detail=f"{f.detail} [suppressed by .claude/health.md]"))
        else:
            out.append(f)
    return out
