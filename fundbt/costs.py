"""Cost floors by liquidity bucket (strategy.md §4). Agents cannot override.

Per-side bps. Derived from measured spread/impact evidence (see
strategy-research-report.md Pillar 1.4). Changed only by human commit.
"""

COST_FLOORS_BPS = {
    "mega_large": 5.0,
    "mid": 15.0,
    "small": 40.0,
    "micro": 100.0,
}

STRESS_MULTIPLIERS = (1.0, 2.0, 3.0)


def floor_for(bucket: str) -> float:
    """Per-side cost floor in bps. Unknown bucket -> KeyError -> run refused
    (invariant 7: default is REJECT, never a permissive fallback)."""
    return COST_FLOORS_BPS[bucket]
