"""orchestrator/improve.py — the nightly scoring job (specs/improvement.md
§2.1) and the reads the briefs make of the `weights` table.

Pure by construction: an injected SimClock, an injected WeightsConfig, a temp
SQLite. The fixture below is built so every number in the scoreboard row can
be checked by hand — see test_the_weights_row_carries_the_calibration_values
for the arithmetic.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from orchestrator.clock import SimClock, iso
from orchestrator.improve import (WeightsConfig, behaviour, inputs_hash,
                                  window_dates)
from state.db import connect

NIGHTLY = datetime(2026, 7, 13, 20, 35, tzinfo=timezone.utc)   # 16:35 ET
AS_OF = "2026-07-13"
CFG = WeightsConfig(window_days=20, horizon_days=5)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fund.sqlite")
    yield c
    c.close()


@pytest.fixture
def clock():
    return SimClock(NIGHTLY)


def _dates(n: int, start: date = date(2026, 4, 1)) -> list[str]:
    """`n` consecutive run_dates, oldest first, all before AS_OF."""
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _day(conn, run_date: str, alpha: float, signals: dict, *,
         ticker: str = "NVDA", offered: tuple[str, ...] = ("NVDA",),
         charter_version: str = "v1", cost: dict | None = None) -> None:
    """One graded trading day: `offered` rows, one signal per seat in
    `signals` ({seat: (direction, confidence)}), one held PM decision on
    `ticker`, and its resolution at `alpha`. calibration/rows.py fans the
    resolution back out to every seat's signal on (run_date, ticker)."""
    stamp = f"{run_date}T15:00:00+00:00"
    for t in offered:
        conn.execute("INSERT OR IGNORE INTO offered (run_date, ticker, created_at)"
                     " VALUES (?, ?, ?)", (run_date, t, stamp))
    for seat, (direction, confidence) in signals.items():
        conn.execute(
            "INSERT INTO signals (run_date, agent, ticker, direction, confidence,"
            " summary, created_at, charter_version, model_id)"
            " VALUES (?, ?, ?, ?, ?, 's', ?, ?, 'm')",
            (run_date, seat, ticker, direction, confidence, stamp, charter_version))
    cur = conn.execute(
        "INSERT INTO decisions (run_date, ticker, action, qty, thesis,"
        " invalidation, status, created_at) VALUES (?, ?, 'hold', 0, 't', 'i',"
        " 'held', ?)", (run_date, ticker, stamp))
    conn.execute(
        "INSERT INTO resolutions (decision_id, horizon_days, realized_return,"
        " alpha_vs_spy, invalidated, resolved_at) VALUES (?, 5, ?, ?, 0, ?)",
        (cur.lastrowid, alpha, alpha, stamp))
    for seat, usd in (cost or {}).items():
        conn.execute(
            "INSERT INTO costs (run_date, agent, session_id, usd_estimate,"
            " recorded_at) VALUES (?, ?, 's', ?, ?)", (run_date, seat, usd, stamp))
    conn.commit()


def _two_seat_history(conn, n_days: int = 60) -> list[str]:
    """Seat `a` calls every day right at 80 (bullish on an up day, bearish on
    a down day); seat `b` abstains every day at 50. Alternating ±1% alpha,
    so outcomes are not degenerate. Seat `a` costs $0.05 a day."""
    dates = _dates(n_days)
    for i, d in enumerate(dates):
        alpha = 0.01 if i % 2 == 0 else -0.01
        _day(conn, d, alpha,
             {"a": ("bullish" if alpha > 0 else "bearish", 80),
              "b": ("neutral", 50)},
             cost={"a": 0.05})
    return dates


# --- config ---------------------------------------------------------------

def test_config_rejects_a_window_or_horizon_below_one():
    for bad in (dict(window_days=0, horizon_days=5),
                dict(window_days=20, horizon_days=0)):
        with pytest.raises(ValueError, match="must be >= 1"):
            WeightsConfig(**bad)


# --- windows --------------------------------------------------------------

def test_window_dates_are_the_trailing_trading_days_oldest_first(conn):
    dates = _two_seat_history(conn)
    assert window_dates(conn, AS_OF, 20) == dates[-20:]
    assert window_dates(conn, AS_OF, 100) == dates          # fewer than asked: all
    assert window_dates(conn, dates[9], 5) == dates[5:10]   # bounded by as_of
    assert window_dates(conn, "2026-01-01", 5) == []


def test_behaviour_counts_the_window_only(conn):
    dates = _two_seat_history(conn)
    window = dates[-20:]
    a = behaviour(conn, "a", window)
    assert a == {"n_signalled": 20, "n_offered": 20, "n_distinct_conf": 1,
                 "abstention_rate": 0.0, "coverage": 1.0,
                 "cost_usd": pytest.approx(1.0)}
    b = behaviour(conn, "b", window)
    assert (b["n_signalled"], b["abstention_rate"], b["cost_usd"]) == (20, 1.0, 0.0)


def test_behaviour_over_no_dates_is_all_zero(conn):
    assert behaviour(conn, "a", []) == {
        "n_signalled": 0, "n_offered": 0, "n_distinct_conf": 0,
        "abstention_rate": 0.0, "coverage": 0.0, "cost_usd": 0.0}


def test_defaulted_rows_are_offered_but_not_signalled(conn):
    """run_research writes neutral/0 rows with charter_version='none' for a
    silent seat. They are graded (calibration invariant 2) but the seat did
    not speak, so they count toward n_offered and not toward n_signalled —
    otherwise coverage is 1.0 by construction (improvement.md §2.1)."""
    dates = _dates(3)
    for d in dates:
        _day(conn, d, 0.01, {"c": ("neutral", 0)}, charter_version="none")
    c = behaviour(conn, "c", dates)
    assert (c["n_offered"], c["n_signalled"]) == (3, 0)
    assert (c["coverage"], c["abstention_rate"], c["n_distinct_conf"]) == (0.0, 0.0, 0)


def test_coverage_is_signalled_over_offered(conn):
    """Two tickers offered, the seat spoke on one."""
    d = _dates(1)[0]
    _day(conn, d, 0.01, {"a": ("bullish", 70)}, offered=("NVDA", "MSFT"))
    assert behaviour(conn, "a", [d])["coverage"] == 0.5


def test_coverage_is_zero_when_signals_predate_the_offered_table(conn):
    """`offered` is a brand-new table; production `signals` rows go back
    before it existed. The first nightly run's trailing window can therefore
    span a date with `signals` rows and zero `offered` rows: the seat spoke
    (n_signalled > 0) yet n_offered == 0. coverage must degrade to 0.0, not
    divide by zero (improvement.md §2.1)."""
    d = _dates(1)[0]
    _day(conn, d, 0.01, {"a": ("bullish", 70)}, offered=())
    b = behaviour(conn, "a", [d])
    assert (b["n_signalled"], b["n_offered"]) == (1, 0)
    assert b["coverage"] == 0.0


# --- hash -----------------------------------------------------------------

def test_inputs_hash_is_stable_and_sensitive():
    rows = [{"seat": "a", "direction": "long", "confidence": 80, "alpha": 0.01}]
    beh = {"n_signalled": 1, "n_offered": 1, "n_distinct_conf": 1,
           "abstention_rate": 0.0, "coverage": 1.0, "cost_usd": 0.0}
    h = inputs_hash(rows, beh)
    assert h == inputs_hash(list(rows), dict(beh))
    assert h != inputs_hash(rows, {**beh, "cost_usd": 0.01})
    assert h != inputs_hash(rows + rows, beh)


# --- the job --------------------------------------------------------------

from orchestrator import improve                                   # noqa: E402
from orchestrator.improve import latest_weights, write_weights     # noqa: E402


def _rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM weights ORDER BY as_of_date, agent")]


def test_the_weights_row_carries_the_calibration_values(conn, clock):
    """Every number below is hand-computed from calibration.md §1–§2 over the
    two-seat fixture (60 graded calls each, alternating ±1% alpha).

    Seat a: p = 0.8 on every up day and 0.2 on every down day, so every
    squared error is 0.04 -> brier 0.04. Base rate 0.5, reference Brier 0.25
    -> BSS 1 - 0.04/0.25 = 0.84 (recency weights cancel on identical errors).
    Pool: 60 rows at 0.04 + 60 at 0.25 -> 0.145 -> pool BSS 0.42.
    Shrink w = 60/(60+30) = 2/3 -> 2/3*0.84 + 1/3*0.42 = 0.70; total 42.0.
    Murphy (exact, two forecast values, n_bins = 3): reliability 0.04,
    resolution 0.25, ECE 0.20. Batting 1.0 (every directional call won);
    slugging is undefined with no loss -> NULL.
    Seat b: brier 0.25, BSS 0.0, shrunk 1/3*0.42 = 0.14, total 8.4; one
    forecast value -> reliability 0, resolution 0, ECE 0; no directional call
    -> batting and slugging NULL. n_abstain 60.
    PM weights: raw 0.70 / 0.14, floor 0.5 * 0.42 = 0.21 lifts b, then
    normalise over 0.91 -> a 0.769231, b 0.230769.
    n_eff = 60 / 5 = 12. Window (20 days): a spoke 20 times at one
    confidence, $1.00 est.; b abstained 20 of 20.
    """
    _two_seat_history(conn)

    out = write_weights(conn, clock, CFG)

    assert out == {"as_of_date": AS_OF, "written": ["a", "b"],
                   "unchanged": [], "skipped": []}
    a, b = _rows(conn)
    assert (a["agent"], a["as_of_date"], a["created_at"]) == ("a", AS_OF, iso(NIGHTLY))
    assert (a["n_graded"], a["n_abstain"], a["n_eff"]) == (60, 0, 12.0)
    assert a["brier"] == pytest.approx(0.04)
    assert a["bss"] == pytest.approx(0.84)
    assert a["bss_shrunk"] == pytest.approx(0.70)
    assert a["total_skill"] == pytest.approx(42.0)
    assert (a["reliability"], a["resolution"], a["ece"]) == (
        pytest.approx(0.04), pytest.approx(0.25), pytest.approx(0.20))
    assert a["batting"] == 1.0 and a["slugging"] is None
    assert (a["n_signalled"], a["n_offered"], a["n_distinct_conf"]) == (20, 20, 1)
    assert (a["abstention_rate"], a["coverage"]) == (0.0, 1.0)
    assert a["cost_usd"] == pytest.approx(1.0)
    assert a["weight"] == pytest.approx(0.70 / 0.91)
    assert (a["narrowed"], len(a["inputs_hash"])) == (0, 64)

    assert (b["n_graded"], b["n_abstain"]) == (60, 60)
    assert b["brier"] == pytest.approx(0.25) and b["bss"] == pytest.approx(0.0)
    assert b["bss_shrunk"] == pytest.approx(0.14)
    assert b["total_skill"] == pytest.approx(8.4)
    assert (b["reliability"], b["resolution"], b["ece"]) == (0.0, 0.0, 0.0)
    assert (b["batting"], b["slugging"]) == (None, None)
    assert (b["abstention_rate"], b["cost_usd"]) == (1.0, 0.0)
    assert b["weight"] == pytest.approx(0.21 / 0.91)
    assert a["weight"] + b["weight"] == pytest.approx(1.0)


def test_a_second_run_on_unchanged_data_writes_nothing(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    before = _rows(conn)

    out = write_weights(conn, clock, CFG)

    assert out["written"] == [] and out["unchanged"] == ["a", "b"]
    assert _rows(conn) == before


def test_a_same_night_rerun_on_changed_data_replaces_that_nights_row(conn, clock):
    """resolve_day re-fired after a failed drain resolves more; the night's
    scoreboard is recomputed. UNIQUE (as_of_date, agent): still one row."""
    dates = _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    first = {r["agent"]: r["inputs_hash"] for r in _rows(conn)}
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})

    out = write_weights(conn, clock, CFG)

    assert out["written"] == ["a", "b"]
    rows = _rows(conn)
    assert len(rows) == 2 and all(r["as_of_date"] == AS_OF for r in rows)
    assert all(r["inputs_hash"] != first[r["agent"]] for r in rows)
    assert rows[0]["n_graded"] == 61


def test_the_next_night_keeps_the_old_row_beside_the_new(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})
    clock.advance(days=1)

    write_weights(conn, clock, CFG)

    assert [(r["as_of_date"], r["agent"]) for r in _rows(conn)] == [
        (AS_OF, "a"), (AS_OF, "b"), ("2026-07-14", "a"), ("2026-07-14", "b")]


@pytest.mark.parametrize("field, poison", [("brier", math.nan),
                                            ("total_skill", math.inf)])
def test_a_non_finite_load_bearing_value_skips_that_seat_and_names_it(
        conn, clock, monkeypatch, field, poison):
    """improvement.md §2.1 "Two kinds of column": a non-finite load-bearing
    value is no row for that seat, never a placeholder (invariant 4). NaN and
    inf both qualify, and only NaN is backstopped elsewhere — SQLite itself
    binds it as NULL, which NOT NULL would catch even unguarded. inf binds as
    a real float and stores silently: none of n_eff/brier/bss_shrunk/
    total_skill carry a CHECK (only `weight` does), so total_skill — the PM's
    ranking column — is what gets poisoned for the inf case here, the column
    with no backstop and the worst consequence. The other seat is still
    written."""
    from calibration.scoring import AgentScore

    _two_seat_history(conn)
    real = improve.score_agents

    def poisoned(rows):
        scores, weights = real(rows)
        broken = [AgentScore(**{**vars(s), field: poison}) if s.seat == "a"
                  else s for s in scores]
        return broken, weights
    monkeypatch.setattr(improve, "score_agents", poisoned)

    out = write_weights(conn, clock, CFG)

    assert out["skipped"] == ["a"] and out["written"] == ["b"]
    assert [r["agent"] for r in _rows(conn)] == ["b"]


def test_a_raise_mid_job_writes_no_row_at_all(conn, clock, monkeypatch):
    """All-or-nothing (improvement.md §6, "no row for any seat"): the rows are
    computed before any is written and land in one transaction."""
    _two_seat_history(conn)
    real = improve.behaviour

    def boom(c, seat, dates):
        if seat == "b":
            raise sqlite3.OperationalError("disk I/O error")
        return real(c, seat, dates)
    monkeypatch.setattr(improve, "behaviour", boom)

    with pytest.raises(sqlite3.OperationalError):
        write_weights(conn, clock, CFG)
    assert _rows(conn) == []


def test_a_raise_mid_write_loop_rolls_back_and_writes_no_row_at_all(
        tmp_path, clock):
    """The test above raises during compute, before any INSERT runs. A raise
    can also land in the write loop, with a row already staged in the open
    transaction — that is what `conn.rollback()` in write_weights' except
    clause is actually for. Without it, the pending row survives the raise
    (a connection reads its own uncommitted writes) and a later commit on
    this same connection would land a one-seat scoreboard: exactly the
    partial write invariant 4 forbids. `in_transaction is False` is the
    assertion that tells a real rollback apart from a table that merely
    never got written to.

    A plain sqlite3.Connection subclass stands in for a disk-I/O failure on
    the second seat's INSERT, since monkeypatching a function (as above)
    can't land the raise inside the write loop itself."""
    class _BoomOnSecondInsert(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO weights"):
                self._n = getattr(self, "_n", 0) + 1
                if self._n == 2:
                    raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, parameters)

    db_path = tmp_path / "fund.sqlite"
    seed = connect(db_path)
    _two_seat_history(seed)
    seed.close()
    conn = sqlite3.connect(str(db_path), factory=_BoomOnSecondInsert)
    conn.row_factory = sqlite3.Row

    with pytest.raises(sqlite3.OperationalError):
        write_weights(conn, clock, CFG)

    assert _rows(conn) == []
    assert conn.in_transaction is False


def test_no_graded_seat_writes_nothing_and_says_so(conn, clock):
    assert write_weights(conn, clock, CFG) == {
        "as_of_date": AS_OF, "written": [], "unchanged": [], "skipped": []}
    assert _rows(conn) == []


# --- the read -------------------------------------------------------------

def test_latest_weights_returns_each_seats_newest_row(conn, clock):
    _two_seat_history(conn)
    write_weights(conn, clock, CFG)
    _day(conn, "2026-07-10", 0.02, {"a": ("bullish", 60), "b": ("neutral", 50)})
    clock.advance(days=1)
    write_weights(conn, clock, CFG)

    rows = latest_weights(conn)
    assert [(r["agent"], r["as_of_date"]) for r in rows] == [
        ("a", "2026-07-14"), ("b", "2026-07-14")]
    assert set(rows[0]) == {c[1] for c in conn.execute("PRAGMA table_info(weights)")}
    assert rows[1]["batting"] is None                    # NULL survives as None
    assert [r["agent"] for r in latest_weights(conn, agent="b")] == ["b"]
    assert latest_weights(conn, agent="nobody") == []
