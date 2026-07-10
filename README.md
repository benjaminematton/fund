# fund — research stack (backtest starter)

> Repo entry points: `CLAUDE.md` (rules), `specs/design.md` (architecture), `KICKOFF.md` (how to start). This README covers only the pre-built research stack: `fundbt/`, `stratgate/`, `calibration/`.

The `run_backtest` tool and deterministic strategy gate from `specs/strategy.md`
/ `specs/strategy-contracts.md`, as working, tested code. Designed to merge into
the fund repo as `fundbt/` + `stratgate/` (+ the golden fixture and tests).

## Layout

```
stratgate/            pure validation code — NO LLM IMPORTS (CI-enforced)
  stats.py            DSR, PSR, MinTRL, WFE, drawdown (numpy + stdlib only;
                      DSR verified against the Bailey–López de Prado 2014
                      paper's own numerical example)
  gate.py             G2/G3 thresholds + evaluators; malformed input -> gate_error
fundbt/
  run_backtest.py     the wrapper: spec/range/budget checks, holdout quarantine,
                      cost floors + 2x/3x stress, DSR with family-wide N,
                      neighbor perturbation, registry logging, run_key caching
  engine_pandas.py    default engine: pure pandas, next-bar execution, no RNG
  engine_vbt.py       optional vectorbt 1.0 adapter (same semantics; see
                      docstring for the verified gotcha list)
  rules.py            registered signal rules (starter: F1 dip_buyer)
  registry.py         SQLite trial registry + holdout single-touch
  costs.py            cost floors by liquidity bucket (human-commit only)
  hashing.py          canonical content-addressed ids (the ONLY hashing module)
calibration/
  scoring.py          proper scoring: Brier/BSS, Murphy decomposition, ECE,
                      batting/slugging, EB shrinkage, recency decay
  scoreboard.py       resolutions -> AgentScores -> PM weights -> #pnl markdown
fixtures/
  golden-strategy.md  worked PASS + FAIL example with exact numbers
tests/                32 tests, all offline, no keys, no network
```

## Run

```
python3 tests/run_tests.py     # zero-dep runner
pytest tests/                  # if pytest installed
```

Requires numpy + pandas only. `pip install .[vbt]` for the vectorbt engine.

## Design rules inherited from the fund

- LLMs hypothesize; code validates. Nothing in here imports LLM code.
- Default is REJECT: NaN/missing/malformed anywhere -> strategy does not advance.
- Every backtest logs a trial row; family-wide N feeds the deflated Sharpe.
- Identical config -> cached result, N unchanged (content-addressed run_key).
- The holdout is consumed exactly once per spec — enforced by PRIMARY KEY.
- Cost floors are constants, not parameters.
- Clock is injected (`now_iso`); no wall-clock reads in business logic.

## Wiring into the agent runtime

Expose `run_backtest` via `create_sdk_mcp_server` next to `submit_signal`
(strict schemas from specs/strategy-contracts.md §3). `evaluate_holdout` and all
gate evaluation (G2/G3/G4) are orchestrator-invoked via direct import — never
registered as agent tools (contracts §3.3: an agent-callable holdout would let a
seat burn the one-touch evaluation). Production data replaces
`tests/synthetic.py` with pinned Parquet snapshots (Norgate/Sharadar) hashed by
`snapshot_hash` — same code path, real prices.
