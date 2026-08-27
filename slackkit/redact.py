"""Strip credential-shaped text out of anything bound for an egress.

Three sites in scripts/run_day.py interpolate a raw exception into an alert
(`seat_turn_failed`, `exec_turn_violation`, `run_day_failed`), and a traceback
that touches os.environ carries broker keys, Slack tokens or the Anthropic key
with it. Alert text reaches TWO egresses — Slack, via outbox.drain(), and
GitHub, via scripts/file_alert_issues.py, which writes it into an issue title
and body. Redacting in append_alert covers both, because both read the stored
row.

SCOPE — this covers alert TEXT only, and nothing else claims to be clean.
append_alert's `**payload` extras are stored unredacted, and one site relies on
that: orchestrator/preconditions.py:77-78 caps the text at 120 chars and keeps
the FULL uncapped exception in `payload["error"]` on purpose, so the cap never
costs the only diagnostic there is. No renderer or filer egresses payload
extras today. Separately, scripts/run_day.py:350 logs the RAW text before
append_alert ever sees it, so logs/run_day.err.log keeps an unredacted copy on
the box. Neither is in scope here; do not read this module as a promise that a
credential cannot reach disk.

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

# Longest input the rules run over; the rest is dropped, not passed through
# unredacted. The three run_day.py sites interpolate `{exc}` with no length
# cap of their own (unlike orchestrator/protection.py:203 and
# preconditions.py:77, which use str(e)[:120]), and an attacker-influenced
# exception message is the ordinary shape here — the analyst seat reads
# attacker-authorable news (issue #42).
#
# The bound is what keeps redaction from becoming the outage. This module's
# whole thesis is that it must never cost an alert, and a regex that runs for
# minutes is strictly worse than one that raises: a raise is caught and logged
# at run_day.py:471, while a hang inside guarded() is a dead trading day with
# no signal at all (invariant 4, violated in effect rather than in form).
#
# DIVERGENCE from the shell twin, deliberate: over this length the two
# implementations no longer agree, because sed is fed a bounded journal tail
# (-n 20) and has no equivalent exposure. Under it they agree byte for byte.
#
# Raising it is not free and not linear-cheap: with the cap disabled, cost runs
# ~22 us/char on the worst shape below — 1MB takes 23s and 4MB takes 97s,
# inside an `except` on the trading path.
_MAX_CHARS = 8192

# Truncation cuts back to the last of these, so the cap can never bisect a
# token. It otherwise can: PK[A-Z0-9]{16,} needs 16 characters after its
# prefix, so a PK token cut short by the cap is not a match and its first
# 1-17 characters were emitted in the clear — the cap introducing a leak the
# uncapped scan did not have. The `+` rules (sk-ant-, xoxb-, xapp-) never had
# it; they match any fragment and give up only the non-secret prefix.
#
# The lookback is deliberately UNBOUNDED, unlike the shell-style bounded
# window: a bound reopens exactly this class, because a non-whitespace run
# longer than the window falls back to the hard cut and bisects a token again.
# The cost is that a truncated alert ending in one huge unbroken blob loses
# that blob — and a whitespace-free 8KB run carries no readable diagnosis
# anyway. If the kept region holds no whitespace at all, all of it is dropped.
_WHITESPACE = " \t\n\r\f\v"

# Whitespace except newline. sed is line-oriented, so a shell match can never
# span log lines; \s here would let one run off the end of a line and eat the
# next one, destroying the diagnosis that follows. Pinned by
# test_a_match_cannot_run_across_a_newline.
_SP = r"[^\S\n]"

# The two halves of a NAME, either side of its keyword. The unbounded `*` these
# replace was the cubic term: two of them straddling the alternation made 8KB
# of `("A"*40 + "KEY" + "A"*40)` take ~20 seconds and 40KB roughly 40 minutes.
#
# HEAD must still backtrack — the keyword follows it, so it has to give back to
# let KEY|TOKEN|... match. Its bound costs no fidelity: on a name with more
# than 64 characters before its keyword the match simply starts further right,
# the skipped characters are copied through verbatim, and the rendered line is
# byte-identical.
#
# TAIL is possessive, which is both exact and 11x faster (215ms -> 19ms on the
# worst input found). Giving back cannot rescue a failed match: everything that
# may follow the name is `['"]?`, horizontal space or `[=:]`, and none of those
# match a [A-Z0-9_] character.
#
# TAIL's bound is the one real divergence from the shell twin. sed redacts
# `AKEY` + 70 more capitals + `=v`; this does not. Real credential names run
# under 30 characters, and an unbounded-but-possessive tail measured 3.5x
# SLOWER than the plain bound, so the bound stays and the gap is documented
# rather than closed.
_NAME_HEAD = r"[A-Z0-9_]{0,63}"
_NAME_TAIL = r"[A-Z0-9_]{0,63}+"

# COST CENTRE, for whoever edits these next: it is the TAIL of the name rule,
# `_SP*[=:]_SP*['"]?\S+`, not the name halves above. The worst input found is a
# long capitals run, a delimiter, then thousands of spaces ended by a newline,
# so `\S+` fails at every backtrack position of the second `_SP*`: ~0.19s at
# and above the cap. The name halves, having been bounded, no longer dominate.
# Anything added to this half needs the same adversarial measurement.
#
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
    (re.compile(r"""['"]?([A-Z]""" + _NAME_HEAD
                + r"""(?:KEY|TOKEN|SECRET|PASSWORD)""" + _NAME_TAIL
                + r""")['"]?""" + _SP + r"""*[=:]""" + _SP
                + r"""*['"]?\S+"""), r"\1=REDACTED"),
)


def redact(text: str) -> str:
    """Return `text` with credential-shaped substrings replaced.

    Never raises, and never runs long. This is on the alert path, inside
    `except` blocks, where a raise would turn "something needs review" into a
    dead trading day (invariant 4; slackkit/outbox.py:44-47) — and where a
    regex that grinds for minutes would do the same thing, silently. A
    redactor that can lose an alert is worse than the leak it fixes, so any
    failure passes the ORIGINAL text through untouched, and input past
    _MAX_CHARS is dropped rather than scanned.

    Truncation drops the tail; it never passes it through. The cut lands on a
    whitespace boundary, so every token the scan sees is whole: a value cut in
    half is dropped rather than emitted as a prefix of itself, and a value that
    survives is still preceded by its NAME= and still redacted.

    The `…[N chars truncated]` marker reaches the GitHub egress but not the
    Slack one: slackkit/render.py:33-38 clips section text at 3000 characters,
    so anything long enough to have been truncated here is clipped again well
    before the marker. The operator sees render's own `…` and learns the size
    of the drop only from the issue body.
    """
    try:
        out = text
        dropped = 0
        if len(out) > _MAX_CHARS:
            head = out[:_MAX_CHARS]
            boundary = max(head.rfind(ws) for ws in _WHITESPACE)
            head = head[:boundary + 1]          # -1 -> drop the lot
            dropped = len(out) - len(head)
            out = head
        for pattern, replacement in _RULES:
            out = pattern.sub(replacement, out)
        if dropped:
            out += f"…[{dropped} chars truncated before redaction]"
        return out
    except Exception:
        log.exception("redact: failed; alert text passes through unredacted")
        return text
