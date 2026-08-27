"""Strip credential-shaped text out of anything bound for an egress.

Three sites in scripts/run_day.py interpolate a raw exception into an alert
(`seat_turn_failed`, `exec_turn_violation`, `run_day_failed`), and a traceback
that touches os.environ carries broker keys, Slack tokens or the Anthropic key
with it. Alert text reaches TWO egresses — Slack, via outbox.drain(), and
GitHub, via scripts/file_alert_issues.py, which writes it into an issue title
and body. Redacting in append_alert covers both, because both read the stored
row.

TWIN: the `redact()` shell function in ops/notify_failure.sh:25-32 applies
these same five rules to the journal tail. That script is deliberately
dependency-free of the fund (its header: "the alert path must not share a
failure mode with the thing it is watching"), so the duplication is permanent
and intentional — change the two together, and keep tests/test_ops_notify.py
and tests/test_redact.py in step.

The rules cut in both directions on purpose. Over-redaction guts an alert's
usefulness: `KeyError: 'ALPACA_SECRET_KEY'` names the ONE fact the operator
needs, so the name pattern is anchored to ALL_CAPS env-var shape throughout
and leaves a mixed-case `KeyError` alone.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Whitespace except newline. sed is line-oriented, so a shell match can never
# span log lines; \s alone would let one.
_SP = r"[^\S\n]"

# Applied in order, exactly as ops/notify_failure.sh chains its -e expressions:
# prefix rules catch known token shapes, then the name rule catches secrets
# with no recognizable prefix (e.g. ALPACA_SECRET_KEY), written as NAME=VALUE,
# NAME: VALUE, or quoted python/JSON dict dumps ('NAME': 'VALUE').
#
# The name rule's replacement keeps the NAME and normalises `:` to `=`, so the
# operator still learns which credential the line was about.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-REDACTED"),
    (re.compile(r"xoxb-[A-Za-z0-9-]+"), "xoxb-REDACTED"),
    (re.compile(r"xapp-[A-Za-z0-9-]+"), "xapp-REDACTED"),
    (re.compile(r"PK[A-Z0-9]{16,}"), "PK-REDACTED"),
    (re.compile(r"""['"]?([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)"""
                r"""[A-Z0-9_]*)['"]?""" + _SP + r"""*[=:]""" + _SP
                + r"""*['"]?\S+"""), r"\1=REDACTED"),
)


def redact(text: str) -> str:
    """Return `text` with credential-shaped substrings replaced.

    Never raises. This runs on the alert path, inside `except` blocks, where a
    raise would turn "something needs review" into a dead trading day
    (invariant 4; slackkit/outbox.py:44-47). A redactor that can lose an alert
    is worse than the leak it fixes, so any failure passes the ORIGINAL text
    through untouched.
    """
    try:
        out = text
        for pattern, replacement in _RULES:
            out = pattern.sub(replacement, out)
        return out
    except Exception:
        log.exception("redact: failed; alert text passes through unredacted")
        return text
