"""Append-only trial registry (strategy-contracts.md §2). SQLite, no LLM imports.

Every backtest by any seat lands here — including rejected/abandoned ones.
Family-wide N feeds the deflated Sharpe. An unlogged trial cannot exist because
run_backtest logs inside the same code path that returns the result.
"""

from __future__ import annotations

import json
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS trial_registry (
  run_key            TEXT PRIMARY KEY,
  spec_id            TEXT NOT NULL,
  family             TEXT NOT NULL,
  config_hash        TEXT NOT NULL,
  data_snapshot_hash TEXT NOT NULL,
  engine_version     TEXT NOT NULL,
  seed               INTEGER NOT NULL,
  seat               TEXT NOT NULL,
  stats              TEXT NOT NULL,
  is_holdout         INTEGER NOT NULL DEFAULT 0,
  created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trials_family ON trial_registry(family);
CREATE INDEX IF NOT EXISTS idx_trials_spec   ON trial_registry(spec_id);

CREATE TABLE IF NOT EXISTS holdout_evaluations (
  spec_id    TEXT PRIMARY KEY,
  run_key    TEXT NOT NULL,
  passed     INTEGER NOT NULL,
  detail     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class TrialRegistry:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(DDL)

    # -- trials ------------------------------------------------------------

    def get(self, run_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT stats FROM trial_registry WHERE run_key = ?", (run_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def log(self, *, run_key: str, spec_id: str, family: str, config_hash: str,
            data_snapshot_hash: str, engine_version: str, seed: int, seat: str,
            stats: dict, is_holdout: bool, created_at: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO trial_registry VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_key, spec_id, family, config_hash, data_snapshot_hash,
             engine_version, seed, seat, json.dumps(stats), int(is_holdout), created_at),
        )
        self.conn.commit()

    def family_n(self, family: str) -> int:
        """N for the DSR: every trial ever run in this family, all seats, all specs."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM trial_registry WHERE family = ?", (family,)
        ).fetchone()[0]

    def spec_trial_count(self, spec_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM trial_registry WHERE spec_id = ?", (spec_id,)
        ).fetchone()[0]

    def family_sharpe_variance(self, family: str) -> float:
        """Variance of per-period Sharpes across the family's logged trials —
        the V[{SR_n}] input to SR0. Requires >= 2 trials; else 0 (SR0 degenerates)."""
        rows = self.conn.execute(
            "SELECT stats FROM trial_registry WHERE family = ?", (family,)
        ).fetchall()
        srs = []
        for (blob,) in rows:
            s = json.loads(blob).get("per_period_sharpe")
            if s is not None and isinstance(s, (int, float)):
                srs.append(float(s))
        if len(srs) < 2:
            return 0.0
        import numpy as np
        return float(np.var(np.asarray(srs), ddof=1))

    # -- holdout single-touch (invariant 6) ---------------------------------

    def consume_holdout(self, *, spec_id: str, run_key: str, passed: bool,
                        detail: dict, created_at: str) -> bool:
        """Returns False if the holdout was already consumed (PRIMARY KEY hit).
        A False here is a p-hacking alarm: project to #risk."""
        try:
            self.conn.execute(
                "INSERT INTO holdout_evaluations VALUES (?,?,?,?,?)",
                (spec_id, run_key, int(passed), json.dumps(detail), created_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
