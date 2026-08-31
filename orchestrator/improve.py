"""The improvement loop's Class A jobs (specs/improvement.md §2): the nightly
scoring job that turns graded signals into `weights` rows, and the read the
stage brief makes of them.

Rows in, rows out. No LLM, no wall clock, no file: the config arrives as a
dataclass the composition root (scripts/weights_day.py) built from
config/improvement.yaml, and time arrives as the injected Clock. Purity-linted
with the rest of orchestrator/, which is what lets the sim month drive it.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass

from calibration.rows import scoreboard_rows
from calibration.scoreboard import score_agents
from calibration.scoring import AgentScore
from orchestrator.clock import Clock, et_run_date, iso


@dataclass(frozen=True)
class WeightsConfig:
    """config/improvement.yaml, validated (improvement.md §2.1).

    window_days: trailing trading days the behavioural rates cover.
    horizon_days: what n_eff = n_graded / horizon_days divides by
    (calibration.md §4's overlap correction)."""
    window_days: int
    horizon_days: int

    def __post_init__(self) -> None:
        if self.window_days < 1 or self.horizon_days < 1:
            raise ValueError(
                "improvement config: window_days and horizon_days must be >= 1,"
                f" got window_days={self.window_days}"
                f" horizon_days={self.horizon_days}")


def window_dates(conn: sqlite3.Connection, as_of_date: str,
                 window_days: int) -> list[str]:
    """The trailing `window_days` trading days ending at `as_of_date`, oldest
    first. A trading day is a run_date with `signals` rows: run_research
    writes one per (seat, ticker) on every day with an active set, so the
    signals table is the fund's own trading calendar and the repo needs no
    other. Fewer days exist than asked for -> all of them."""
    rows = conn.execute(
        "SELECT DISTINCT run_date FROM signals WHERE run_date <= ?"
        " ORDER BY run_date DESC LIMIT ?", (as_of_date, window_days)).fetchall()
    return sorted(r["run_date"] for r in rows)


def behaviour(conn: sqlite3.Connection, seat: str, dates: list[str]) -> dict:
    """The window rates §3.3 grades against, for one seat over `dates`.

    Only rows the SEAT wrote count as signalled: charter_version = 'none'
    marks a row the orchestrator wrote because the seat was silent
    (orchestrator/daily.py run_research), and counting those would make
    coverage 1.0 by construction (improvement.md §2.1). Empty `dates` is a
    fund with no history yet: every count zero, every rate 0.0."""
    if not dates:
        return {"n_signalled": 0, "n_offered": 0, "n_distinct_conf": 0,
                "abstention_rate": 0.0, "coverage": 0.0, "cost_usd": 0.0}
    marks = ", ".join("?" * len(dates))
    spoke = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(direction = 'neutral'), 0) abstain,"
        f" COUNT(DISTINCT confidence) distinct_conf FROM signals"
        f" WHERE agent = ? AND charter_version <> 'none'"
        f" AND run_date IN ({marks})", (seat, *dates)).fetchone()
    n_offered = conn.execute(
        f"SELECT COUNT(*) n FROM offered WHERE run_date IN ({marks})",
        dates).fetchone()["n"]
    cost = conn.execute(
        f"SELECT COALESCE(SUM(usd_estimate), 0.0) c FROM costs"
        f" WHERE agent = ? AND run_date IN ({marks})", (seat, *dates)).fetchone()["c"]
    n = spoke["n"]
    return {"n_signalled": n,
            "n_offered": n_offered,
            "n_distinct_conf": spoke["distinct_conf"],
            "abstention_rate": spoke["abstain"] / n if n else 0.0,
            "coverage": n / n_offered if n_offered else 0.0,
            "cost_usd": float(cost)}


def inputs_hash(seat_rows: list[dict], beh: dict) -> str:
    """Everything that feeds one seat's row, hashed: its graded rows in
    grade order and its window rates. Equal to the seat's latest row's hash
    means nothing changed and nothing is written (improvement.md §2.1)."""
    blob = json.dumps({"rows": seat_rows, "window": beh}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
