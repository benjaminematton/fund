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
    fund with no history yet: every count zero, every rate 0.0.

    `coverage`'s numerator is signals on (run_date, ticker) pairs that are
    also in `offered`, not `n_signalled` itself: `signals` predates `offered`
    by years in production, so a date-window ratio of the two counts reads
    far above 1 for the fund's first ~20 nights, and nothing stops a seat
    signalling a ticker nobody offered even once the calendars align.
    `signals` UNIQUE (run_date, agent, ticker) caps this seat at one row per
    pair and `offered` PRIMARY KEY (run_date, ticker) caps the join at one
    match per pair, so the pair-joined count can never exceed n_offered —
    coverage is bounded at 1 structurally, not by convention."""
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
    n_covered = conn.execute(
        f"SELECT COUNT(*) n FROM signals s JOIN offered o"
        f" ON o.run_date = s.run_date AND o.ticker = s.ticker"
        f" WHERE s.agent = ? AND s.charter_version <> 'none'"
        f" AND s.run_date IN ({marks})", (seat, *dates)).fetchone()["n"]
    cost = conn.execute(
        f"SELECT COALESCE(SUM(usd_estimate), 0.0) c FROM costs"
        f" WHERE agent = ? AND run_date IN ({marks})", (seat, *dates)).fetchone()["c"]
    n = spoke["n"]
    return {"n_signalled": n,
            "n_offered": n_offered,
            "n_distinct_conf": spoke["distinct_conf"],
            "abstention_rate": spoke["abstain"] / n if n else 0.0,
            "coverage": n_covered / n_offered if n_offered else 0.0,
            "cost_usd": float(cost)}


def inputs_hash(seat_rows: list[dict], beh: dict) -> str:
    """Everything that feeds one seat's row, hashed: its graded rows in
    grade order and its window rates. Equal to the seat's latest row's hash
    means nothing changed and nothing is written (improvement.md §2.1)."""
    blob = json.dumps({"rows": seat_rows, "window": beh}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


# --- the `weights` row --------------------------------------------------------

# Columns whose value the PM acts on. A non-finite value here is "no row for
# this seat tonight" (§2.1, §6), never a placeholder. Every other REAL column
# is descriptive and stores NULL where the sample cannot define it.
LOAD_BEARING = ("n_eff", "brier", "bss_shrunk", "total_skill", "weight")

_COLS = ("as_of_date", "agent", "n_graded", "n_abstain", "n_eff", "brier", "bss",
         "bss_shrunk", "total_skill", "reliability", "resolution", "ece",
         "batting", "slugging", "n_signalled", "n_offered", "abstention_rate",
         "n_distinct_conf", "coverage", "cost_usd", "weight", "narrowed",
         "inputs_hash", "created_at")

# Same night, changed inputs: replace that night's row. UNIQUE (as_of_date,
# agent) is what makes this a replacement and not a second row.
_UPSERT = (f"INSERT INTO weights ({', '.join(_COLS)})"
           f" VALUES ({', '.join(':' + c for c in _COLS)})"
           " ON CONFLICT(as_of_date, agent) DO UPDATE SET "
           + ", ".join(f"{c} = excluded.{c}" for c in _COLS
                       if c not in ("as_of_date", "agent")))

# Each seat's newest row. §4: "latest row per seat" = MAX(as_of_date) per
# agent, and the UNIQUE makes it one.
_LATEST = """
SELECT w.* FROM weights w
  JOIN (SELECT agent, MAX(as_of_date) AS d FROM weights GROUP BY agent) m
    ON m.agent = w.agent AND m.d = w.as_of_date
"""


def _descriptive(value: float | None) -> float | None:
    """NULL for a value the sample cannot define: Murphy terms under 20
    calls, batting with no directional call, slugging with no loss (inf),
    BSS on degenerate outcomes. Python's sqlite3 would bind NaN as NULL
    anyway; this makes it a decision rather than an accident, and turns inf
    — which SQLite would store — into the same NULL."""
    return float(value) if value is not None and math.isfinite(value) else None


def _row(score: AgentScore, weight: float, beh: dict, cfg: WeightsConfig,
         digest: str, as_of_date: str, now_iso: str) -> dict:
    return {
        "as_of_date": as_of_date, "agent": score.seat,
        "n_graded": score.n_graded, "n_abstain": score.n_abstain,
        "n_eff": score.n_graded / cfg.horizon_days,
        "brier": score.brier,
        "bss": _descriptive(score.bss),
        "bss_shrunk": score.bss_shrunk,
        "total_skill": score.total_skill,
        "reliability": _descriptive(score.reliability),
        "resolution": _descriptive(score.resolution),
        "ece": _descriptive(score.ece),
        "batting": _descriptive(score.batting),
        "slugging": _descriptive(score.slugging),
        **beh,
        "weight": weight, "narrowed": 0,
        "inputs_hash": digest, "created_at": now_iso,
    }


def write_weights(conn: sqlite3.Connection, clock: Clock,
                  cfg: WeightsConfig) -> dict:
    """One `weights` row per graded seat for tonight (improvement.md §2.1).
    Returns {"as_of_date", "written", "unchanged", "skipped"}, each list in
    seat order, for the job log.

    All-or-nothing: every row is computed before any is written and one
    commit lands them, so a raise anywhere leaves the table exactly as it
    was (invariant 7 — no-change) and the caller alerts once. A seat whose
    load-bearing values are not finite is skipped and named, never written
    with a placeholder (invariant 4). A seat whose inputs hash to its latest
    row's hash is unchanged and not rewritten; a changed seat the same night
    replaces that night's row.
    """
    as_of_date = et_run_date(clock.now())
    now_iso = iso(clock.now())
    rows = scoreboard_rows(conn)
    scores, weights = score_agents(rows)
    dates = window_dates(conn, as_of_date, cfg.window_days)
    latest = {r["agent"]: r["inputs_hash"] for r in latest_weights(conn)}
    out = {"as_of_date": as_of_date, "written": [], "unchanged": [], "skipped": []}
    pending: list[dict] = []
    try:
        for score in scores:
            beh = behaviour(conn, score.seat, dates)
            seat_rows = [r for r in rows if r["seat"] == score.seat]
            digest = inputs_hash(seat_rows, beh)
            if latest.get(score.seat) == digest:
                out["unchanged"].append(score.seat)
                continue
            row = _row(score, weights[score.seat], beh, cfg, digest,
                       as_of_date, now_iso)
            if any(not math.isfinite(row[k]) for k in LOAD_BEARING):
                out["skipped"].append(score.seat)
                continue
            pending.append(row)
        for row in pending:
            conn.execute(_UPSERT, row)
            out["written"].append(row["agent"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return out


def latest_weights(conn: sqlite3.Connection,
                   agent: str | None = None) -> list[dict]:
    """Every seat's newest row — or one seat's — as plain dicts carrying every
    `weights` column, in agent order. A NULL descriptive column comes back
    None. The brief's `weights` section reads this; so does the job, for the
    no-op check."""
    sql = _LATEST + (" WHERE w.agent = ?" if agent is not None else "") \
        + " ORDER BY w.agent"
    params = (agent,) if agent is not None else ()
    return [dict(r) for r in conn.execute(sql, params)]
