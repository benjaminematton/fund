"""Cross-engine parity: engine_pandas vs engine_vbt.

The two engines do NOT share sizing semantics — engine_pandas equal-weights
across ACTIVE positions (re-splits daily), engine_vbt allocates a fixed 1/N
percent sleeve per asset with shared cash. Exact return equality is therefore
not expected and not asserted. Parity here means the same signals must produce
the same trading story and, above all, the same gate verdict:

  1. identical output shape/index (drop-in contract),
  2. entry counts within 10% (same signals, same next-bar execution),
  3. daily net returns correlated > 0.90,
  4. annualized net Sharpe within 0.30 absolute — smaller than any G2
     threshold step, so an engine swap cannot flip a verdict silently.

If either engine drifts past these bands, either fix the adapter or split
ENGINE_VERSION semantics explicitly — do not widen the bands (same rule as
golden numbers). Skips (passes trivially, with a notice) when vectorbt is not
installed: the offline suite must not require it.
"""

from __future__ import annotations

import numpy as np

import fundbt.rules  # noqa: F401  (registers dip_buyer into RULES)
from fundbt.engine_pandas import run as run_pandas
from fundbt.run_backtest import RULES
from stratgate import stats as S
from tests.synthetic import GOLDEN_PARAMS, make_market

COST_BPS = 5.0
MAX_TRADE_COUNT_DRIFT = 0.10
MIN_RETURNS_CORR = 0.90
MAX_SHARPE_DIFF = 0.30


def _vbt_available() -> bool:
    try:
        import vectorbt  # noqa: F401
        return True
    except Exception:
        return False


def test_engine_parity_on_synthetic_market():
    if not _vbt_available():
        print("  (skipped: vectorbt not installed — parity runs only with [vbt] extra)")
        return
    from fundbt.engine_vbt import run as run_vbt

    close = make_market()
    entries, exits = RULES["dip_buyer"](close, GOLDEN_PARAMS)

    a = run_pandas(close, entries, exits, cost_bps_per_side=COST_BPS)
    b = run_vbt(close, entries, exits, cost_bps_per_side=COST_BPS)

    # 1. drop-in contract: same shape and index
    assert len(a.returns) == len(b.returns) == len(close)
    assert a.returns.index.equals(close.index)
    assert b.returns.index.equals(close.index)

    # 2. same signals -> same number of executed entries (within drift band)
    assert a.n_trades > 0 and b.n_trades > 0, "planted edge must trade in both engines"
    drift = abs(a.n_trades - b.n_trades) / max(a.n_trades, b.n_trades)
    assert drift <= MAX_TRADE_COUNT_DRIFT, (
        f"trade-count drift {drift:.1%} (pandas {a.n_trades} vs vbt {b.n_trades})")

    # 3. the daily P&L stories must agree
    corr = float(np.corrcoef(a.returns.to_numpy(), b.returns.to_numpy())[0, 1])
    assert corr >= MIN_RETURNS_CORR, f"returns correlation {corr:.3f} < {MIN_RETURNS_CORR}"

    # 4. gate-verdict safety: Sharpe gap smaller than any G2 threshold step
    sr_a = S.annualized_sharpe(a.returns.to_numpy())
    sr_b = S.annualized_sharpe(b.returns.to_numpy())
    assert abs(sr_a - sr_b) <= MAX_SHARPE_DIFF, (
        f"annualized Sharpe diverges: pandas {sr_a:.3f} vs vbt {sr_b:.3f}")


def test_pandas_engine_is_deterministic():
    """Cheap invariant that runs offline: same inputs, twice, bit-identical."""
    close = make_market()
    entries, exits = RULES["dip_buyer"](close, GOLDEN_PARAMS)
    a = run_pandas(close, entries, exits, cost_bps_per_side=COST_BPS)
    b = run_pandas(close, entries, exits, cost_bps_per_side=COST_BPS)
    assert a.n_trades == b.n_trades
    assert (a.returns.to_numpy() == b.returns.to_numpy()).all()
    assert a.total_costs == b.total_costs
