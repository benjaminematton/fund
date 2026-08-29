"""run_backtest wrapper tests: determinism, caching, budget, holdout single-touch,
cost-floor monotonicity. Offline — synthetic data, in-memory SQLite, no keys."""

import sqlite3

import numpy as np

import fundbt.rules  # noqa: F401  (registers dip_buyer)
from fundbt.run_backtest import (BacktestError, evaluate_holdout, run_backtest,
                                 snapshot_hash)
from state.db import connect
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_registry,
                             make_spec, seed_spec_row)

NOW = "2026-07-09T00:00:00Z"


def setup():
    spec = make_spec()
    return make_market(), spec, make_registry(spec)


def test_deterministic_and_cached():
    close, spec, reg = setup()
    r1 = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                      registry=reg, seat="quant", now_iso=NOW)
    r2 = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                      registry=reg, seat="quant", now_iso=NOW)
    assert not r1["cached"] and r2["cached"]
    assert r1["run_key"] == r2["run_key"]
    assert r1["net_sharpe"] == r2["net_sharpe"]
    assert reg.family_n("F1") == 1          # cache hit did NOT increment N


def test_snapshot_hash_ignores_platform_float_noise():
    """numpy's macOS wheel FMA-contracts the multiply-add inside
    Generator.uniform; the manylinux wheel rounds twice. That 1-ULP split at
    the first draw grows to ~5e-15 relative across the market, so hashing raw
    float text identifies the platform rather than the data. Perturb by an
    order of magnitude more than the observed drift: the hash must not move."""
    close = make_market()
    noise = 1.0 + 1e-14 * np.random.default_rng(0).standard_normal(close.shape)
    assert snapshot_hash(close * noise) == snapshot_hash(close)


def test_snapshot_hash_detects_a_real_data_change():
    """The tolerance above must not be so wide that a changed snapshot slips
    through — a pinned slice that moved is exactly what this hash is for."""
    close = make_market()
    changed = close.copy()
    changed.iloc[0, 0] *= 1.001
    assert snapshot_hash(changed) != snapshot_hash(close)


def test_param_range_enforced():
    close, spec, reg = setup()
    try:
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_pct": 0.20},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "param_out_of_range" in str(e)


def test_budget_exhaustion_is_logged():
    close, spec, reg = setup()
    spec["search_budget"] = 2
    for dd in (4, 5):
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_days": dd},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
    try:
        run_backtest(spec=spec, params={**GOLDEN_PARAMS, "dip_days": 6},
                     close=close, registry=reg, seat="quant", now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "budget_exhausted" in str(e)
    assert reg.family_n("F1") == 3          # the rejection itself was logged


def test_cost_floors_monotonic():
    close, spec, reg = setup()
    r = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)
    assert r["net_sharpe"] > r["net_sharpe_2x"] > r["net_sharpe_3x"]


def test_planted_edge_detected_and_sane():
    close, spec, reg = setup()
    r = run_backtest(spec=spec, params=GOLDEN_PARAMS, close=close,
                     registry=reg, seat="quant", now_iso=NOW)
    assert r["n_trades"] >= 100
    assert r["net_sharpe"] > 0.5            # planted reversion is detectable
    assert 0 < r["deflated_sharpe"] <= 1
    assert np.isfinite(r["wfe"])
    assert r["span_years"] > 7.5            # holdout removed ~18 months


def test_holdout_single_touch():
    close, spec, reg = setup()
    d1 = evaluate_holdout(spec=spec, params=GOLDEN_PARAMS, close=close,
                          registry=reg, now_iso=NOW)
    assert np.isfinite(d1["holdout_sharpe"])
    try:
        evaluate_holdout(spec=spec, params=GOLDEN_PARAMS, close=close,
                         registry=reg, now_iso=NOW)
        raise AssertionError("should have raised")
    except BacktestError as e:
        assert "holdout_already_consumed" in str(e)


def test_the_golden_spec_has_a_strategy_specs_row():
    """trial_registry.spec_id REFERENCES strategy_specs(spec_id), and
    state/db.py turns foreign keys ON, so registry.log() for the golden spec is
    only possible if this row exists. make_spec()'s id is baked into
    tests/test_golden.py's frozen hashes and cannot be changed, so the row is
    seeded to match the id rather than the other way round (issue #172).
    """
    spec = make_spec()
    conn = connect(":memory:")
    seed_spec_row(conn, spec)
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs WHERE spec_id = ?",
        (spec["spec_id"],)).fetchone()[0] == 1
    seed_spec_row(conn, spec)                    # idempotent: no PK explosion
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_specs").fetchone()[0] == 1


def test_the_registry_declares_no_ddl_of_its_own():
    """#172: one schema home. fundbt/registry.py used to carry a standalone
    DDL string with every REFERENCES clause stripped; that string existing at
    all is the defect, because it is a second source of truth for a schema
    specs/strategy-contracts.md §2 already declares."""
    import fundbt.registry as registry_module
    assert not hasattr(registry_module, "DDL"), (
        "fundbt/registry.py still declares DDL — state/schema.sql is the home")


def test_the_registry_writes_the_fund_dbs_tables_with_the_fk_live():
    """The registry writes state/schema.sql's tables, foreign keys and all.
    Reading PRAGMA foreign_key_list rather than trusting the DDL text: what
    matters is what the live database enforces."""
    reg = make_registry()
    fk = reg.conn.execute(
        "PRAGMA foreign_key_list(trial_registry)").fetchall()
    assert [(r["table"], r["from"], r["to"]) for r in fk] == [
        ("strategy_specs", "spec_id", "spec_id")]
    assert reg.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_logging_a_trial_for_an_unregistered_spec_is_refused():
    """The foreign key IS the rule that an unregistered spec cannot have
    trials. Loud, not swallowed: measured, SQLite's ON CONFLICT algorithms do
    not apply to foreign keys, so INSERT OR IGNORE still raises here."""
    reg = make_registry()
    try:
        reg.log(run_key="rk_orphan", spec_id="spec_neverregistered",
                family="F1", config_hash="c", data_snapshot_hash="d",
                engine_version="e", seed=0, seat="quant", stats={},
                is_holdout=False, created_at=NOW)
        raise AssertionError("should have raised")
    except sqlite3.IntegrityError as exc:
        assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert reg.family_n("F1") == 0


def test_holdout_single_touch_at_the_registry():
    """#172 done-means 4, pinned at the level that does not depend on #189.

    With the trial row present so the FK resolves, the FIRST consume_holdout
    writes and the SECOND hits holdout_evaluations' PRIMARY KEY and returns
    False. That False is the p-hacking alarm
    (specs/strategy-contracts.md:273) and it still means exactly what it
    always meant.
    """
    spec = make_spec()
    reg = make_registry(spec)
    reg.log(run_key="rk_h", spec_id=spec["spec_id"], family=spec["family"],
            config_hash="c", data_snapshot_hash="d",
            engine_version="e+holdout", seed=0, seat="quant", stats={},
            is_holdout=True, created_at=NOW)
    args = dict(spec_id=spec["spec_id"], run_key="rk_h", passed=True,
                detail={"holdout_sharpe": 1.0}, created_at=NOW)
    assert reg.consume_holdout(**args) is True
    assert reg.consume_holdout(**args) is False


def test_a_holdout_with_no_trial_row_is_a_wiring_error_not_a_p_hacking_alarm():
    """Issue #172, the narrowing.

    A foreign-key violation and a primary-key hit are the SAME exception class
    (sqlite3.IntegrityError). consume_holdout caught both and returned False,
    so a first-ever holdout against the fund DB — which fails the FK, because
    evaluate_holdout never logs its trial row (#189) — surfaced as
    holdout_already_consumed and paged #risk with "someone/something is
    p-hacking". A false positive on that alarm is its own incident class. The
    FK case escapes; only the PRIMARY KEY case still means consumed.
    """
    spec = make_spec()
    reg = make_registry(spec)
    try:
        reg.consume_holdout(spec_id=spec["spec_id"],
                            run_key="rk_never_logged", passed=True,
                            detail={}, created_at=NOW)
        raise AssertionError("should have raised")
    except sqlite3.IntegrityError as exc:
        assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY"
