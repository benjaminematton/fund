from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Finding:
    check: str        # stable id, greppable, never localised
    severity: str     # "ok" | "warn" | "alert"
    detail: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    covering_qty: float      # shares covered by live stops, per design.md §5


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    side: str
    qty: float
    type: str
    status: str


@dataclass(frozen=True)
class OrderRow:
    client_order_id: str
    symbol: str


@dataclass(frozen=True)
class ServiceResult:
    unit: str
    result: str          # "success" | "exit-code" | "unreachable" | ...
    last_run: str        # ISO or "" when never


@dataclass(frozen=True)
class Snapshot:
    """One complete read of production. Every field is data; nothing here
    computes. Built by scripts/dev_status.py, consumed by evaluate()."""

    droplet_env: Mapping[str, str]
    seat_trading_toolsets: Mapping[str, bool]
    orders: Sequence[OrderRow]
    tickets: Mapping[str, str]          # ticket id -> symbol
    events_unposted: int
    broker_fill_count: int
    checkpoints: Sequence[tuple[str, str, str]]   # (run_date, stage, status)
    journals_written: frozenset[str] | set[str]
    seats_participating: frozenset[str] | set[str]
    scorecard_codes: Sequence[str]
    positions: Sequence[Position]
    open_orders: Sequence[OpenOrder]
    due_unresolved: Sequence[int]       # decision ids past horizon with no resolution
    droplet_head: str
    origin_master: str
    commits_behind: int
    services: Mapping[str, ServiceResult]
    suppressed: frozenset[str] = field(default_factory=frozenset)
