from __future__ import annotations

from typing import Sequence

from devcheck.model import Finding

_ORDER = {"alert": 0, "warn": 1, "ok": 2}
_MARK = {"alert": "🔴", "warn": "🟡", "ok": "🟢"}


def render(findings: Sequence[Finding]) -> str:
    """Markdown, worst first. Every check appears, including the healthy ones:
    a reader must be able to tell "checked and fine" from "not checked"."""
    if not findings:
        return "_no checks ran — this is itself a finding_\n"
    lines = ["| | check | detail |", "|---|---|---|"]
    for f in sorted(findings, key=lambda f: (_ORDER.get(f.severity, 3), f.check)):
        lines.append(f"| {_MARK.get(f.severity, '⚪')} | `{f.check}` | {f.detail} |")
    return "\n".join(lines) + "\n"
