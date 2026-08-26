"""Render and suppression.

Suppression must DOWNGRADE and annotate, never delete: a row that vanishes is
indistinguishable from a check that never ran, which is the failure this whole
package exists to prevent.
"""

from __future__ import annotations

from devcheck.model import Finding
from devcheck.render import render


def test_render_groups_by_severity_worst_first():
    out = render([
        Finding("a", "ok", "fine"),
        Finding("b", "alert", "broken"),
        Finding("c", "warn", "degraded"),
    ])
    assert out.index("broken") < out.index("degraded") < out.index("fine")


def test_render_names_every_check_id():
    out = render([Finding("position_coverage", "alert", "NVDA 0 of 40 covered")])
    assert "position_coverage" in out


def test_render_handles_no_findings():
    assert render([]).strip() != ""


def test_suppressed_finding_is_downgraded_not_dropped():
    from devcheck.evaluate import apply_suppression
    findings = [Finding("degradations", "warn", "degraded to default: model_fallback_used")]
    out = apply_suppression(findings, frozenset({"degradations"}))
    assert len(out) == 1
    assert out[0].severity == "ok"
    assert "suppressed" in out[0].detail


def test_unsuppressed_finding_is_untouched():
    """Negative control: same finding, empty suppression set, still warns."""
    from devcheck.evaluate import apply_suppression
    findings = [Finding("degradations", "warn", "degraded to default: model_fallback_used")]
    out = apply_suppression(findings, frozenset())
    assert out[0].severity == "warn"
