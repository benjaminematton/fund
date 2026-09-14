"""run_backtest handler — strategy-contracts.md §3.2, the wrapper enforcement
order. The engine (fundbt/run_backtest.py) does steps 2-6 and the trial
INSERT; what is under test here is what the HANDLER adds: step 1 (lifecycle
state), the rule pre-check, step 3's event, step 7's lifecycle move, the
close_provider seam, and every refusal path writing nothing.

THE HANDLER IS CALLED DIRECTLY, never through a built server: there is no
`@tool` and no cap (G-2(iii), same shape as submit_strategy_spec in #171),
so there is no MCP surface to reach. `test_no_shipped_seat_holds_the_cap`
pins that as a fact of the shipped table.

The cap is monkeypatched onto `quant` for the write-path tests. SEAT_CAPS on
disk is untouched, and the guard under test is the handler's own `_can`.
"""
import json

import pytest

from agents.tools import fund_server
from agents.tools.fund_server import (SEAT_CAPS, handle_run_backtest,
                                      handle_submit_strategy_spec)
from tests.synthetic import (GOLDEN_PARAMS, make_market, make_spec,
                             seed_spec_row, spec_payload)

NOW = "2026-09-13T14:00:00Z"
LATER = "2026-09-13T15:00:00Z"

# Built once: make_market() is deterministic (seeded) and the handler never
# mutates it. ~2520x20 floats; re-generating per test is pure waste.
CLOSE = make_market()


def _close():
    return CLOSE


def _no_data():
    """A provider with nothing to serve says so with LookupError — the one
    provider failure the handler reads as a refusal."""
    raise LookupError("no close-price data bound for this test")


@pytest.fixture
def granted(monkeypatch):
    """`quant` holds the cap for one test, and only there."""
    monkeypatch.setitem(fund_server.SEAT_CAPS, "quant",
                        SEAT_CAPS["quant"] | {"run_backtest"})


def _run(fund_db, args, seat="quant", now_iso=NOW, close_provider=_close):
    return handle_run_backtest(fund_db, seat=seat, args=args, now_iso=now_iso,
                               close_provider=close_provider)


def _count(fund_db, table):
    return fund_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _lifecycle(fund_db, sid):
    return fund_db.execute("SELECT state, state_version FROM strategies"
                           " WHERE strategy_id = ?", (sid,)).fetchone()


def _events(fund_db, kind):
    return [json.loads(r["payload"]) for r in fund_db.execute(
        "SELECT payload FROM events WHERE kind = ?", (kind,)).fetchall()]


def _golden(fund_db, **spec_overrides):
    """The dip_buyer spec tests/test_run_backtest.py runs, registered with
    its lifecycle row in SPEC. signal_rule.name == 'dip_buyer' is what makes
    it runnable through the handler."""
    return seed_spec_row(fund_db, make_spec() | spec_overrides)


def _assert_nothing_written(fund_db, sid):
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0
    assert tuple(_lifecycle(fund_db, sid)) == ("SPEC", 0)


# --- surface ---------------------------------------------------------------

def test_no_shipped_seat_holds_the_cap():
    """G-2(iii) again: handler, tests, `not served` row, no caller. A cap
    arrives with the charter and the schedule that justify it."""
    assert [s for s, caps in SEAT_CAPS.items() if "run_backtest" in caps] == []


def test_a_seat_without_the_cap_is_refused_and_nothing_is_written(fund_db):
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "not granted" in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_close_provider_is_a_required_keyword_and_build_fund_server_has_none(
        fund_db):
    """D1 as ruled: the seam is a REQUIRED handler parameter, bound by the
    future @tool closure — not a build_fund_server kwarg nothing reads."""
    import inspect

    from agents.tools.fund_server import build_fund_server

    param = inspect.signature(handle_run_backtest).parameters["close_provider"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
    assert "close_provider" not in inspect.signature(build_fund_server).parameters
    with pytest.raises(TypeError):
        handle_run_backtest(fund_db, seat="quant", args={}, now_iso=NOW)


def test_a_provider_with_no_data_is_a_tool_error_not_a_run(granted, fund_db):
    """No loader exists; a provider that has nothing says so with
    LookupError and the seat hears a refusal — never an empty frame the
    engine would refuse for a misleading reason (insufficient_data)."""
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
             close_provider=_no_data)
    assert r["ok"] is False and "no close-price data" in r["error"]
    _assert_nothing_written(fund_db, sid)


@pytest.mark.parametrize("exc", [RuntimeError("disk gone"),
                                 KeyError("SYN00"),
                                 IndexError("empty slice")])
def test_a_provider_that_fails_for_another_reason_is_not_swallowed(granted,
                                                                   fund_db,
                                                                   exc):
    """CLAUDE.md: fail fast, never swallow. Only a bare LookupError is a
    refusal. KeyError and IndexError are LookupError SUBCLASSES — the shape a
    buggy loader actually raises (a missing column, an empty slice) — and
    must propagate as bugs, not become a polite "no data" refusal."""
    sid = _golden(fund_db)

    def broken():
        raise exc

    with pytest.raises(type(exc)):
        _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
             close_provider=broken)
    _assert_nothing_written(fund_db, sid)


# --- input schema (§3.2 BacktestRequest) -------------------------------------

@pytest.mark.parametrize("args", [
    {"params": GOLDEN_PARAMS},                                   # no spec_id
    {"spec_id": "spec_golden000000f1"},                          # no params
    {"spec_id": "spec_golden000000f1", "params": GOLDEN_PARAMS,
     "seed": "zero"},                                            # bad seed
    {"spec_id": "spec_golden000000f1", "params": GOLDEN_PARAMS,
     "holdout_months": 0},                                       # extra=forbid
    {"spec_id": "spec_golden000000f1", "params": "dip_days=5"},  # not a dict
])
def test_a_malformed_request_is_refused_and_writes_nothing(granted, fund_db,
                                                           args):
    sid = _golden(fund_db)
    r = _run(fund_db, args)
    assert r["ok"] is False
    _assert_nothing_written(fund_db, sid)


# --- step 1: spec exists and strategies.state in {SPEC, BACKTEST} -----------

def test_an_unregistered_spec_is_refused(granted, fund_db):
    r = _run(fund_db, {"spec_id": "spec_neverregistered",
                       "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "not registered" in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0


def test_a_spec_with_no_lifecycle_row_is_refused_not_assumed_spec(granted,
                                                                  fund_db):
    """Invariant 4: a missing lifecycle row is ambiguity, and ambiguity is
    no action — the same posture as state/specs.py:_refuse_orphaned_specs."""
    sid = _golden(fund_db)
    fund_db.execute("DELETE FROM strategies WHERE strategy_id = ?", (sid,))
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and "lifecycle" in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0


@pytest.mark.parametrize("state", ["REJECTED", "VALIDATED", "RETIRED"])
def test_a_spec_outside_spec_or_backtest_is_refused(granted, fund_db, state):
    sid = _golden(fund_db)
    fund_db.execute("UPDATE strategies SET state = ? WHERE strategy_id = ?",
                    (state, sid))
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is False and state in r["error"]
    assert _count(fund_db, "trial_registry") == 0
    assert _count(fund_db, "events") == 0
    assert _lifecycle(fund_db, sid)["state"] == state


# --- D2: the rule pre-check ---------------------------------------------------

def test_a_spec_whose_rule_has_no_name_is_refused_as_unknown_rule(granted,
                                                                  fund_db):
    """spec_payload()'s signal_rule carries no `name` — a registered spec the
    engine would KeyError on (run_backtest.py:137). The handler pre-checks so
    the seat gets a refusal with a name, and nothing is written."""
    sid = handle_submit_strategy_spec(fund_db, seat="quant",
                                      args=spec_payload(), now_iso=NOW)["spec_id"]
    fund_db.execute("DELETE FROM events")            # the registration post
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": {"sigma": 1.5}})
    assert r["ok"] is False and "unknown_rule" in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_a_rule_name_the_engine_does_not_register_is_refused(granted, fund_db):
    sid = handle_submit_strategy_spec(
        fund_db, seat="quant",
        args=spec_payload(signal_rule={"name": "no_such_rule"}),
        now_iso=NOW)["spec_id"]
    fund_db.execute("DELETE FROM events")
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": {"sigma": 1.5}})
    assert r["ok"] is False and "unknown_rule" in r["error"]
    _assert_nothing_written(fund_db, sid)


# --- step 2: params declared, complete, typed, in range — HANDLER-side -------

@pytest.mark.parametrize("params,reason", [
    ({**GOLDEN_PARAMS, "dip_pct": 0.20}, "param_out_of_range:dip_pct"),
    ({**GOLDEN_PARAMS, "dip_days": 2}, "param_out_of_range:dip_days"),
    ({**GOLDEN_PARAMS, "lookback": 10}, "undeclared_param:lookback"),
    # Completeness: fundbt/rules.py:25-27 and _neighbor_params
    # (run_backtest.py:105-118) read EVERY declared param, so a missing one
    # is a raw KeyError inside the engine unless refused here.
    ({"dip_days": 5}, "missing_param:"),
    ({}, "missing_param:"),
    # §3.2 lets params carry str; a str against numeric bounds is a TYPE
    # refusal here, not the TypeError the engine's `lo <= v <= hi` would
    # raise (run_backtest.py:145). No bool case: pydantic coerces True to
    # 1.0 under `float | int | str` before the handler ever sees it
    # (measured), so a bool never reaches _check_params.
    ({**GOLDEN_PARAMS, "dip_days": "5"}, "param_type:dip_days"),
])
def test_a_bad_param_is_refused_by_the_handler_and_writes_nothing(
        granted, fund_db, params, reason):
    """The engine's spellings, reused, so a seat sees one vocabulary
    whichever layer refused. Refused BEFORE the engine, so nothing — not
    even a hash — is computed."""
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": params})
    assert r["ok"] is False and reason in r["error"]
    _assert_nothing_written(fund_db, sid)


def test_a_spec_with_a_malformed_range_is_refused_not_a_stack_trace(granted,
                                                                    fund_db):
    """StrategySpec.param_ranges is an unvalidated dict (state/models.py:142),
    so a range that is not [lo, hi, step] is registrable TODAY through the
    served submit_strategy_spec. Unpacking it is a ValueError/TypeError; the
    handler names it `bad_range:<p>` and writes nothing."""
    sid = handle_submit_strategy_spec(
        fund_db, seat="quant",
        args=spec_payload(signal_rule={"name": "dip_buyer"},
                          param_ranges={"sigma": "wide"}),
        now_iso=NOW)["spec_id"]
    fund_db.execute("DELETE FROM events")
    fund_db.commit()
    r = _run(fund_db, {"spec_id": sid, "params": {"sigma": 1.5}})
    assert r["ok"] is False and "bad_range:sigma" in r["error"]
    _assert_nothing_written(fund_db, sid)


# --- step 7: run, trial row, SPEC -> BACKTEST ----------------------------------

def test_the_first_run_returns_the_result_logs_one_trial_and_moves_to_backtest(
        granted, fund_db):
    sid = _golden(fund_db)
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is True
    res = r["result"]
    assert res["spec_id"] == sid and res["cached"] is False
    assert res["run_key"].startswith("run_")
    assert res["n_trials_family"] == 1
    # §3.2 BacktestResult, every field, no more and no less.
    assert set(res) == {
        "run_key", "spec_id", "config_hash", "data_snapshot_hash", "n_trades",
        "span_years", "net_sharpe", "net_sharpe_2x", "net_sharpe_3x",
        "per_period_sharpe", "deflated_sharpe", "n_trials_family", "wfe",
        "max_drawdown", "turnover_annual", "cost_share", "param_neighbors",
        "regime_sharpe", "cached"}
    assert _count(fund_db, "trial_registry") == 1
    trial = fund_db.execute("SELECT seat, created_at FROM trial_registry"
                            ).fetchone()
    assert (trial["seat"], trial["created_at"]) == ("quant", NOW)   # D8
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)
    assert _count(fund_db, "events") == 0                # no post on success


def test_a_spec_registered_through_the_production_write_path_backtests(
        granted, fund_db):
    """Ruling Q5: the golden fixture above seeds its rows by hand
    (tests/synthetic.py:74-77, frozen id). This one registers through
    handle_submit_strategy_spec -> state/specs.py:insert_strategy_spec, so
    the content-addressed id and the registration-written lifecycle row are
    on the tested route end to end. The rule library reads only `params`
    (fundbt/rules.py:25-27: dip_days, dip_pct, trend_days); the engine reads
    signal_rule.name, param_ranges, search_budget, family, liquidity_bucket
    and spec_id from the spec (run_backtest.py:137-176)."""
    payload = spec_payload(signal_rule={"name": "dip_buyer"},
                           param_ranges=make_spec()["param_ranges"])
    reg = handle_submit_strategy_spec(fund_db, seat="quant", args=payload,
                                      now_iso=NOW)
    assert reg["ok"] is True and reg["duplicate"] is False
    sid = reg["spec_id"]
    assert sid != make_spec()["spec_id"]                # content-addressed
    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    assert r["ok"] is True and r["result"]["spec_id"] == sid
    assert r["result"]["cached"] is False
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)
    kinds = [row[0] for row in fund_db.execute("SELECT kind FROM events")]
    assert kinds == ["strategy_spec"]                   # registration only


def test_the_same_run_again_is_cached_and_moves_nothing(granted, fund_db):
    """§1: identical run_key -> cached result, no new row. §3.2 step 7 on a
    row already in BACKTEST is a no-op, not an error."""
    sid = _golden(fund_db)
    first = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    second = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
                  now_iso=LATER)
    assert second["ok"] is True and second["result"]["cached"] is True
    assert second["result"]["run_key"] == first["result"]["run_key"]
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)


def test_a_second_config_runs_from_backtest_and_adds_a_trial(granted, fund_db):
    sid = _golden(fund_db)
    _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    r = _run(fund_db, {"spec_id": sid,
                       "params": {**GOLDEN_PARAMS, "dip_days": 6}})
    assert r["ok"] is True and r["result"]["n_trials_family"] == 2
    assert _count(fund_db, "trial_registry") == 2
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)


def test_a_token_that_moves_mid_run_is_a_tool_error_with_the_trial_standing(
        granted, fund_db):
    """Ruling Q7. The provider is called AFTER the handler reads
    state_version and BEFORE the engine runs, so a provider that bumps the
    token is exactly a concurrent writer in the window step 7's CAS guards.
    The engine's trial INSERT is its own irreversible commit, so the row
    stands; the lifecycle row is still SPEC (the CAS matched nothing); a
    plain re-run returns the cached result and re-attempts the edge."""
    sid = _golden(fund_db)

    def bumping():
        fund_db.execute("UPDATE strategies SET state_version = 1"
                        " WHERE strategy_id = ?", (sid,))
        fund_db.commit()
        return CLOSE

    r = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
             close_provider=bumping)
    assert r["ok"] is False and "is logged" in r["error"]
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("SPEC", 1)
    assert _count(fund_db, "events") == 0

    again = _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS},
                 now_iso=LATER)
    assert again["ok"] is True and again["result"]["cached"] is True
    assert _count(fund_db, "trial_registry") == 1
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 2)


# --- step 3: budget exhausted IS logged, and projects ------------------------

def test_budget_exhaustion_logs_the_rejection_and_appends_one_event(granted,
                                                                    fund_db):
    """§3.2 step 3: "exceeded -> REJECT and a budget_exhausted event; this
    rejection IS logged". The engine writes the trial row (a spent trial is
    a spent trial); the handler writes the event, through the outbox."""
    from slackkit.render import render

    sid = _golden(fund_db, search_budget=1)
    _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    r = _run(fund_db, {"spec_id": sid,
                       "params": {**GOLDEN_PARAMS, "dip_days": 6}},
             now_iso=LATER)
    assert r["ok"] is False and "budget_exhausted" in r["error"]
    assert _count(fund_db, "trial_registry") == 2
    rejected = fund_db.execute(
        "SELECT stats FROM trial_registry WHERE created_at = ?",
        (LATER,)).fetchone()
    assert json.loads(rejected["stats"]) == {"rejected": "budget_exhausted"}
    events = _events(fund_db, "budget_exhausted")
    assert events == [{"seat": "quant", "spec_id": sid, "family": "F1",
                       "search_budget": 1}]
    post = render("budget_exhausted", events[0])
    assert post.channel == "#research" and post.username is None
    # NOT this lane: §4's "SPEC/BACKTEST -> REJECTED (budget exhausted)"
    # edge belongs to stratgate / the orchestrator, not the handler. The
    # row stays where the first run left it.
    assert tuple(_lifecycle(fund_db, sid)) == ("BACKTEST", 1)


def test_a_retry_after_budget_exhaustion_is_refused_not_replayed(granted,
                                                                  fund_db):
    """fundbt/run_backtest.py:153-156 checks registry.get(rkey) BEFORE the
    budget check at :158-165. registry.get() (fundbt/registry.py:55-59)
    returns json.loads(stats), and for a logged budget rejection stats IS
    {"rejected": "budget_exhausted"} — so a retry of the identical
    (spec_id, params, seed) that already drew budget_exhausted hits the cache
    branch, not the budget branch: the engine returns
    {"rejected": "budget_exhausted", "cached": True} with no exception. The
    handler must not treat that as a success — no new trial row, no second
    #research post (the first refusal already posted one)."""
    sid = _golden(fund_db, search_budget=1)
    _run(fund_db, {"spec_id": sid, "params": GOLDEN_PARAMS})
    retry_args = {"spec_id": sid, "params": {**GOLDEN_PARAMS, "dip_days": 6}}
    first = _run(fund_db, retry_args, now_iso=LATER)
    assert first["ok"] is False and "budget_exhausted" in first["error"]
    trials = _count(fund_db, "trial_registry")
    events = _count(fund_db, "events")
    lifecycle = tuple(_lifecycle(fund_db, sid))

    retry = _run(fund_db, retry_args, now_iso=LATER)
    assert retry["ok"] is False and "budget_exhausted" in retry["error"]
    assert _count(fund_db, "trial_registry") == trials
    assert _count(fund_db, "events") == events == 1
    assert tuple(_lifecycle(fund_db, sid)) == lifecycle


def test_a_refused_run_before_the_engine_appends_no_event(granted, fund_db):
    """Only the budget rejection projects. Every other refusal is default
    HOLD: no row, no event."""
    sid = _golden(fund_db)
    _run(fund_db, {"spec_id": sid, "params": {**GOLDEN_PARAMS, "dip_pct": 9}})
    _run(fund_db, {"spec_id": "spec_nope", "params": GOLDEN_PARAMS})
    _run(fund_db, {"spec_id": sid}, seat="exec")
    assert _count(fund_db, "events") == 0
