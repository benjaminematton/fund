from __future__ import annotations

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
    return out
