"""Append-only trial registry (strategy-contracts.md §2). SQLite, no LLM imports.

Every backtest by any seat lands here — including rejected/abandoned ones.
Family-wide N feeds the deflated Sharpe. An unlogged trial cannot exist because
run_backtest logs inside the same code path that returns the result.

THE SCHEMA LIVES IN state/schema.sql, NOT HERE (issue #172, closing #50's
Group 2). This module owns queries and owns no DDL, no database and no path.
It is handed a connection someone else opened — build it with
state.db.connect(), which is what sets PRAGMA foreign_keys = ON and therefore
what makes trial_registry.spec_id -> strategy_specs(spec_id) and
holdout_evaluations.run_key -> trial_registry(run_key) real rather than
decorative. The standalone DDL this file used to carry stripped every
REFERENCES clause, which is how the missing holdout trial row (#189) stayed
invisible for as long as it did.

Nothing here imports state/. That is deliberate: state/specs.py already imports
fundbt.hashing, so an import back would close a package cycle that is currently
survivable only because fundbt/__init__.py happens to be a bare docstring.
fundbt stays a leaf.
"""

from __future__ import annotations

import json
import sqlite3

# Named, not positional. The old INSERT was `VALUES (?,?,?,?,?,?,?,?,?,?,?)`
# against a DDL string in this same file, so the two could not drift. The DDL
# now lives in state/schema.sql, which other lanes edit: a column inserted
# there would silently shift every value one place to the left.
_TRIAL_COLUMNS = (
    "run_key", "spec_id", "family", "config_hash", "data_snapshot_hash",
    "engine_version", "seed", "seat", "stats", "is_holdout", "created_at")

_HOLDOUT_COLUMNS = ("spec_id", "run_key", "passed", "detail", "created_at")


class TrialRegistry:
    """Query surface over an already-open fund-DB connection.

    Takes the connection rather than a path so the database's identity, its
    lifetime and its PRAGMAs stay with whoever opened it — the posture every
    other writer in this repo already has (state/specs.py, every fund MCP tool
    handler, agents/seats.py's conn_factory). state/db.py's connect() is called
    once per tool call specifically so nothing holds a write lock across turns;
    a registry that opened and kept its own connection could not honour that.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- trials ------------------------------------------------------------

    def get(self, run_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT stats FROM trial_registry WHERE run_key = ?", (run_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def log(self, *, run_key: str, spec_id: str, family: str, config_hash: str,
            data_snapshot_hash: str, engine_version: str, seed: int, seat: str,
            stats: dict, is_holdout: bool, created_at: str) -> None:
        """Append one trial. Re-logging the same run_key is a no-op.

        OR IGNORE covers the PRIMARY KEY only. It does NOT cover the foreign
        key on spec_id: SQLite's ON CONFLICT algorithms do not apply to foreign
        keys (measured), so logging a trial for a spec with no strategy_specs
        row raises SQLITE_CONSTRAINT_FOREIGNKEY. That is the schema saying an
        unregistered spec cannot have trials, and it is meant to be loud.
        """
        self.conn.execute(
            f"INSERT OR IGNORE INTO trial_registry"
            f" ({', '.join(_TRIAL_COLUMNS)})"
            f" VALUES ({', '.join(['?'] * len(_TRIAL_COLUMNS))})",
            (run_key, spec_id, family, config_hash, data_snapshot_hash,
             engine_version, seed, seat, json.dumps(stats), int(is_holdout),
             created_at),
        )
        self.conn.commit()

    def family_n(self, family: str) -> int:
        """N for the DSR: every trial ever run in this family, all seats, all specs.

        `family` is denormalized onto every row, so this one COUNT sweeps the
        whole lineage — including REJECTED ancestors — with no traversal
        (strategy-contracts.md:260, which specifies it unfiltered).
        """
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
        for row in rows:
            s = json.loads(row[0]).get("per_period_sharpe")
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
        A False here is a p-hacking alarm: project to #risk.

        A FOREIGN KEY violation is not that, and must never be reported as it.
        Both arrive as sqlite3.IntegrityError, so the blanket catch this
        replaced could not tell them apart. Under state/db.py's
        PRAGMA foreign_keys = ON, holdout_evaluations.run_key REFERENCES
        trial_registry(run_key); evaluate_holdout now logs its own trial row
        for that run_key before calling this method (#189, landed in this same
        lane), so in the one production call path that FK always resolves.
        This branch is therefore not reachable through evaluate_holdout today
        — it guards a future wiring regression (that insert removed,
        reordered, or a caller other than evaluate_holdout that skips it), not
        a live defect. It IS exercised directly, by a test that calls this
        method without logging a trial row first
        (test_a_holdout_with_no_trial_row_is_a_wiring_error_not_a_p_hacking_alarm),
        which is how the guard stays pinned. If it ever does fire in
        production it means SQLITE_CONSTRAINT_FOREIGNKEY, returned False, and
        surfaced to the operator as holdout_already_consumed, which
        specs/strategy-contracts.md:273 routes to #risk as
        "someone/something is p-hacking". A false positive on that alarm is its
        own incident class. The FK case is a wiring error and is re-raised
        untouched; the PRIMARY KEY path keeps its exact previous meaning.

        Discrimination is on sqlite_errorname (Python 3.11+; pyproject pins
        >=3.12), never on the message text.
        """
        try:
            self.conn.execute(
                f"INSERT INTO holdout_evaluations"
                f" ({', '.join(_HOLDOUT_COLUMNS)})"
                f" VALUES ({', '.join(['?'] * len(_HOLDOUT_COLUMNS))})",
                (spec_id, run_key, int(passed), json.dumps(detail), created_at),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
                raise
            return False
