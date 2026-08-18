"""pass^k reporting and baseline diffing.

pass^k, never pass@k: the fund fires once per market day, unattended, with no
retry and nobody picking best-of-three. pass@k describes a product that
retries; this one does not. tau-bench's illustration — 70% single-attempt
accuracy is ~6% across eight consecutive identical tasks.

Rendered as a FRACTION (2/3), never a percentage. n=3 gives almost no
resolution in a rate, and "66.7%" implies a precision the sample cannot
support.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb


def pass_k(c: int, n: int, k: int) -> float:
    """Finite-sample estimator: C(c,k)/C(n,k) for c passes in n runs."""
    if k > n:
        raise ValueError(f"pass^{k} undefined for {n} trial(s)")
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


@dataclass
class CaseReport:
    case: str
    trials: int
    passes: int
    inconclusive: int
    failures: list[str]

    @property
    def fraction(self) -> str:
        return f"{self.passes}/{self.trials}"

    @property
    def clean(self) -> bool:
        """Every trial passed outright. An INCONCLUSIVE trial is not a pass —
        it is a trial that produced no verdict, and calling it clean would
        hide API weather and grader bugs alike."""
        return self.passes == self.trials


def build_report(results) -> list[CaseReport]:
    by_case: dict[str, list] = {}
    for r in results:
        by_case.setdefault(r.case, []).append(r)
    out = []
    for case, rs in sorted(by_case.items()):
        failures = [f"{v.invariant}:{v.tag or v.outcome}"
                    for r in rs for v in r.verdicts if v.outcome == "FAIL"]
        out.append(CaseReport(
            case=case, trials=len(rs),
            passes=sum(1 for r in rs if r.passed),
            inconclusive=sum(1 for r in rs if r.inconclusive),
            failures=sorted(set(failures))))
    return out


def render(reports) -> str:
    lines = ["case    pass^3   status",
             "-----   ------   ------"]
    for r in reports:
        if r.failures:
            status = ", ".join(r.failures)
        elif r.inconclusive:
            status = f"INCONCLUSIVE x{r.inconclusive}"
        else:
            status = "OK"
        lines.append(f"{r.case:<7} {r.fraction:^6}   {status}")
    return "\n".join(lines)


def diff(current, baseline) -> str:
    """Only cases whose pass fraction MOVED. A diff that lists everything is
    a diff nobody reads."""
    base = {r.case: r for r in baseline}
    lines = []
    for r in current:
        b = base.get(r.case)
        if b is None:
            lines.append(f"{r.case}: NEW {r.fraction}")
        elif (b.passes, b.trials) != (r.passes, r.trials):
            lines.append(f"{r.case}: {b.fraction} -> {r.fraction}")
    for case in sorted(set(base) - {r.case for r in current}):
        lines.append(f"{case}: GONE (was {base[case].fraction})")
    return "\n".join(lines) or "no change vs baseline"
