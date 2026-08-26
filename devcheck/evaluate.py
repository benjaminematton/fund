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
    # Runs last: it reads the other checks' output, not just the snapshot.
    out.append(checks.check_issue_coverage(snapshot, out))
    return out


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
