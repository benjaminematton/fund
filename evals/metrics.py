"""Tier M metrics — measured every run, never blocking.

Why this is a metric and not an invariant: charters/pm.md:26 explicitly
PERMITS a non-price invalidation ("leave it unset for non-price conditions").
So a single vague invalidation breaks no rule. What matters is the RATE, and a
drop in it.

Measured 2026-08-17: deleting the pm.md:31 sizing line moved the price-level
rate from 6/6 to 2/6 while every case still passed 3/3. The PM kept applying
the stop rule correctly the whole time — it was the invalidation that went
vague, and the missing stops were the downstream symptom. An invalidation a
broker cannot enforce is a position with no real exit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A price LEVEL, deliberately narrow: a currency-marked number. Bare integers
# are rejected because invalidations routinely carry dates ("breaks the Aug 11
# swing low") and counts ("three hyperscalers"), and counting those as levels
# would score a vague invalidation as a specific one.
PRICE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def names_price_level(invalidation: str) -> bool:
    return bool(PRICE.search(invalidation or ""))


@dataclass
class StopDiscipline:
    buys: int
    priced: int        # invalidations naming an enforceable price level
    stopped: int       # buys carrying a stop_price

    def rate(self, n: int) -> str:
        return f"{n}/{self.buys}" if self.buys else "0/0"

    def __str__(self) -> str:
        return (f"buy decisions {self.buys} · enforceable invalidation"
                f" {self.rate(self.priced)} · stop attached"
                f" {self.rate(self.stopped)}")


def stop_discipline(traces) -> StopDiscipline:
    """Traces may be Trace objects or raw dicts — this reads only
    rows_written, so recorded JSON grades without rehydration."""
    buys = priced = stopped = 0
    for t in traces:
        rows = t["rows_written"] if isinstance(t, dict) else t.rows_written
        for row in (rows or {}).get("decisions") or []:
            if row["action"] != "buy":
                continue
            buys += 1
            priced += names_price_level(row.get("invalidation"))
            stopped += row.get("stop_price") is not None
    return StopDiscipline(buys=buys, priced=priced, stopped=stopped)
