"""Attribution columns, and the migration that gets them onto a live database.

state/db.py's connect() executes schema.sql only when the DB is absent, so a
column added to that file never reaches an existing database — including the
droplet's. Nothing in this repo migrated a column before; this is the first.

The three-value vocabulary is the point of most of these tests. A real version
means a seat produced the row under that charter. 'none' means the orchestrator
produced it because a seat was silent — no charter and no model were involved,
and claiming one would be a fabrication. 'unknown' means the row predates
attribution. Never NULL: a NULL drops silently out of a GROUP BY and out of
every `=`, which would make excluding defaulted rows from a charter comparison
an accident rather than a written clause.
"""

from __future__ import annotations

from datetime import datetime, timezone

from orchestrator.clock import SimClock
from slackkit.fake import FakeSlack
from state.db import connect
from state.migrations import apply, pending

RUN = "2026-08-18"


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _drop_attribution(conn, table: str) -> None:
    """Return a table to its pre-migration shape, the way a live DB looks."""
    for column in ("charter_version", "model_id"):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()


def test_an_existing_db_gains_the_columns(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    _drop_attribution(conn, "signals")

    assert apply(conn) == ["0001_attribution"]
    assert {"charter_version", "model_id"} <= _columns(conn, "signals")


def test_every_judgment_table_is_covered(tmp_path):
    """signals, decisions AND critiques. A table that records an agent's
    judgment and omits attribution is an exception, and exceptions erode the
    rule that made the columns worth adding."""
    conn = connect(tmp_path / "fund.sqlite")
    for table in ("signals", "decisions", "critiques"):
        assert {"charter_version", "model_id"} <= _columns(conn, table), table


def test_historical_rows_read_unknown_not_a_guessed_version(tmp_path):
    """pm.md is already at v6, so historical rows span versions with no record
    of which. A constant would be a fabrication; 'unknown' is the honest value
    and happens to be what SQLite's ADD COLUMN NOT NULL default gives us."""
    conn = connect(tmp_path / "fund.sqlite")
    _drop_attribution(conn, "signals")
    conn.execute(
        "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
        " summary, created_at) VALUES (?,'news','NVDA','bullish',60,'s',?)",
        (RUN, f"{RUN}T13:00:00Z"))
    conn.commit()

    apply(conn)
    row = conn.execute("SELECT charter_version, model_id FROM signals").fetchone()
    assert row["charter_version"] == "unknown"
    assert row["model_id"] == "unknown"


def test_apply_is_idempotent(tmp_path):
    conn = connect(tmp_path / "fund.sqlite")
    assert apply(conn) == []          # connect() already migrated it
    assert apply(conn) == []


def test_apply_reports_only_what_it_did(tmp_path):
    """A partially-migrated DB — one table done, one not — still completes,
    and says so once rather than per column."""
    conn = connect(tmp_path / "fund.sqlite")
    _drop_attribution(conn, "decisions")
    assert apply(conn) == ["0001_attribution"]
    assert apply(conn) == []


# --- the read-only seam ------------------------------------------------------

def test_pending_names_what_apply_would_do(tmp_path):
    """One list of what a migration adds, read by both sides. If pending()
    kept its own copy it could report current while apply() still had work —
    which is a green preflight against a database that is behind (#17)."""
    conn = connect(tmp_path / "fund.sqlite")
    _drop_attribution(conn, "signals")

    assert pending(conn) == ["0001_attribution"]
    assert apply(conn) == ["0001_attribution"]
    assert pending(conn) == []


def test_pending_writes_nothing(tmp_path):
    """The whole reason the seam exists: scripts/preflight_schema.py asks
    whether the live DB is behind and must not answer by fixing it."""
    conn = connect(tmp_path / "fund.sqlite")
    _drop_attribution(conn, "decisions")

    for _ in range(3):
        assert pending(conn) == ["0001_attribution"]
    assert not {"charter_version", "model_id"} & _columns(conn, "decisions")


def test_no_attribution_column_is_ever_nullable(tmp_path):
    """NOT NULL, deliberately — see the module docstring. This is the test that
    stops a future migration from 'simplifying' to a nullable column."""
    conn = connect(tmp_path / "fund.sqlite")
    for table in ("signals", "decisions", "critiques"):
        cols = {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}
        assert cols["charter_version"]["notnull"] == 1, table
        assert cols["model_id"]["notnull"] == 1, table


# --- the writers -------------------------------------------------------------

def _ctx(conn, **kw):
    from orchestrator.daily import StageCtx
    defaults = dict(conn=conn, run_date=RUN,
                    clock=SimClock(datetime(2026, 8, 18, 13, 0,
                                            tzinfo=timezone.utc)),
                    slack=FakeSlack(), research_seats=("news",),
                    market_inputs={})
    defaults.update(kw)
    return StageCtx(**defaults)


def test_a_defaulted_signal_reads_none_not_unknown(tmp_path):
    """The load-bearing one. schema.sql carries the same DEFAULT 'unknown' the
    migration needs, so a fresh DB has it too — meaning an orchestrator INSERT
    that does not bind explicitly produces a row that is distinguishable in the
    design and identical in practice."""
    from orchestrator.daily import run_research

    conn = connect(tmp_path / "fund.sqlite")
    run_research(_ctx(conn), active=["AMD"])

    row = conn.execute(
        "SELECT charter_version, model_id, summary FROM signals"
        " WHERE ticker = 'AMD'").fetchone()
    assert row["summary"] == "no report"          # it IS the defaulted row
    assert row["charter_version"] == "none"
    assert row["model_id"] == "none"


def test_a_defaulted_decision_reads_none_not_unknown(tmp_path):
    """run_decision's pm_timeout default. This writer has no owner on any other
    branch — it is not in the second-analyst diff — so if this task misses it,
    nothing else catches it."""
    from orchestrator.daily import run_decision

    conn = connect(tmp_path / "fund.sqlite")
    run_decision(_ctx(conn), active=["AMD"])

    row = conn.execute(
        "SELECT charter_version, model_id, action FROM decisions"
        " WHERE ticker = 'AMD'").fetchone()
    assert row["action"] == "hold"                # the pm_timeout default
    assert row["charter_version"] == "none"
    assert row["model_id"] == "none"


def test_a_defaulted_critique_reads_none(tmp_path):
    """critiques rows are today written entirely by the orchestrator's
    no_critic_seat placeholder. When a real Critic seat lands, its handler
    binds real values with no schema change — and charter_version then
    separates real critiques from the placeholder era without parsing `note`."""
    from orchestrator.daily import run_decision

    conn = connect(tmp_path / "fund.sqlite")
    run_decision(_ctx(conn), active=["AMD"])

    row = conn.execute(
        "SELECT charter_version, model_id, note FROM critiques"
        " WHERE ticker = 'AMD'").fetchone()
    assert row["charter_version"] == "none"
    assert row["model_id"] == "none"


def test_a_seat_written_signal_carries_its_real_charter_version(tmp_path):
    """The agent path. submit_signal binds what the seat is actually running,
    so a score delta can be attributed to the charter that produced it."""
    from agents.tools.fund_server import handle_submit_signal

    conn = connect(tmp_path / "fund.sqlite")
    args = {"ticker": "NVDA", "direction": "bullish", "confidence": 60,
            "summary": "s"}
    assert handle_submit_signal(
        conn, seat="news", args=args, run_date=RUN,
        now_iso=f"{RUN}T13:00:00Z", charter_version="v1",
        model_id="claude-haiku-4-5-20251001")["ok"]

    row = conn.execute("SELECT charter_version, model_id FROM signals").fetchone()
    assert row["charter_version"] == "v1"
    assert row["model_id"] == "claude-haiku-4-5-20251001"


def test_resubmission_updates_attribution_too(tmp_path):
    """submit_signal is an UPSERT. A seat re-submitting under a new charter
    must not leave the old version on the row — that would attribute the new
    call to the old prompt, which is the exact confusion these columns exist
    to prevent."""
    from agents.tools.fund_server import handle_submit_signal

    conn = connect(tmp_path / "fund.sqlite")
    args = {"ticker": "NVDA", "direction": "bullish", "confidence": 60,
            "summary": "s"}
    handle_submit_signal(conn, seat="news", args=args, run_date=RUN,
                         now_iso=f"{RUN}T13:00:00Z", charter_version="v1",
                         model_id="m1")
    handle_submit_signal(conn, seat="news", args=args, run_date=RUN,
                         now_iso=f"{RUN}T13:05:00Z", charter_version="v2",
                         model_id="m2")

    rows = conn.execute("SELECT charter_version, model_id FROM signals").fetchall()
    assert len(rows) == 1
    assert rows[0]["charter_version"] == "v2"
    assert rows[0]["model_id"] == "m2"


# --- charter version parsing -------------------------------------------------

def test_charter_version_comes_from_the_header():
    """charters/_template.md: 'bump the header on any change'. The header is
    therefore the source of truth, not a second field to keep in sync."""
    from agents.seats import charter_version_for

    assert charter_version_for({"seat": "pm"}) == "v6"
    assert charter_version_for({"seat": "news"}) == "v3"


def test_an_unparseable_header_is_unknown_not_a_crash():
    """A charter whose header stops matching must not take the trading day
    down. 'unknown' is honest and the audit surfaces it; a raise here would
    fail the day over a docstring (invariant 4)."""
    from agents.seats import _parse_charter_version

    assert _parse_charter_version("# Portfolio Manager\n") == "unknown"
    assert _parse_charter_version("") == "unknown"
    assert _parse_charter_version("# Quant — v12\n") == "v12"
