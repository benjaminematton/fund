# fund — see CLAUDE.md for what each mode means.

.PHONY: test lint sim-day replay live-day live-paper close-pnl schema-pin preflight eval eval-report

# Bootstrap: plain `make test` works from a clean checkout or a fresh git
# worktree — .venv is created on first run, and deps re-sync whenever
# pyproject.toml changes (the only steps that touch the network). The sync is
# content-hash gated in scripts/sync_deps.py, NOT mtime-gated: Apple's GNU
# make 3.81 treats equal 1-second timestamps as up-to-date, which silently
# skips same-second pyproject edits. BOOT_PY picks a >=3.12 interpreter
# explicitly; macOS /usr/bin/python3 (3.9) won't do.
PYTHON := .venv/bin/python3
BOOT_PY := $(shell command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)

.PHONY: deps
deps:
	@test -x $(PYTHON) || { \
	    $(BOOT_PY) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
	        || { echo "fund: need python3 >= 3.12 on PATH, found $$($(BOOT_PY) --version 2>&1)" >&2; exit 1; }; \
	    $(BOOT_PY) -m venv .venv && $(PYTHON) -m pip install --quiet --upgrade pip; \
	}
	@$(PYTHON) scripts/sync_deps.py

# Full offline suite: no network, no API keys. Must pass before every commit.
test: lint
	$(PYTHON) -m pytest tests/

# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
lint: deps
	$(PYTHON) scripts/check_purity.py

# Full simulated trading day: injected clock, FakeSlack, recorded LLM decisions,
# real tool/gate/DB execution. No network, no API keys, no LLM cost.
sim-day: deps
	$(PYTHON) -m pytest tests/test_sim_day.py -v

# Replay a recorded day's LLM decisions against current code.
replay:
	@echo "replay: not implemented yet — requires the recorder/replayer (Phase 1," >&2
	@echo "see specs/acceptance.md §0). Usage when built: make replay REC=<recording>" >&2
	@exit 2

# Pin the broker's REAL tool schema. Read-only: initialize + tools/list, no
# order is ever placed. Needs .env loaded and uvx on PATH.
#
# This is the ONLY guard against the 2026-08-17 outage class, where the gate
# validated a stop-leg shape the broker had never exposed and every offline
# test agreed with the gate because the fixtures encoded the same assumption.
# Offline tests cannot catch that by construction — run this before a live day.
schema-pin: deps
	$(PYTHON) -m pytest -m live tests/test_live_smoke.py -k schema_pin -v

# Host preflight: exercises uvx -> MCP connect -> Anthropic -> a real seat turn
# under the EXACT environment systemd will use. Run after ANY host, unit, or
# environment change. Droplet-only; the paths below are hardcoded on purpose.
#
# It must go through systemd-run, NOT `su - fund`. A login shell sources the
# profile and puts ~/.local/bin on PATH; systemd's default PATH does not have
# it. Every manual check on 2026-08-18 used `su - fund`, so all of them passed
# while the real launch path was broken — that is the day the fund lost. The
# market-closed timer rehearsal missed it too: run_day.py exited on the broker
# clock before any seat started, so it went green without proving anything.
# Running this from the current shell reproduces exactly that blind spot.
#
# ~$$0.31 and hits the network. Never wire into `make test`.
#
# Places no orders: the seats under eval are the analyst and PM, whose Alpaca
# toolsets are read-only and whose deny list blocks mcp__alpaca__place_*.
#
# --label is required. Traces are keyed by git sha, so an unlabelled probe run
# silently overwrites the control baseline (see scripts/eval_suite.py). The
# label's traces are removed afterwards whether or not the run passed —
# evals/traces/ is tracked in git, so leftovers dirty the droplet checkout.
# No `deps` prerequisite: this runs /opt/fund/.venv as the fund user, and
# `deps` would sync the invoking user's checkout instead.
preflight:
	systemd-run --uid=fund --pipe --wait --quiet \
	  --property=WorkingDirectory=/opt/fund \
	  --property=EnvironmentFile=/etc/fund/env \
	  --property=Environment=PATH=/home/fund/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin \
	  --property=Environment=HOME=/home/fund \
	  --property=TimeoutStartSec=10min \
	  /opt/fund/.venv/bin/python3 scripts/eval_suite.py --label vmcheck a01; \
	  ret=$$?; rm -rf evals/traces/vmcheck; exit $$ret

# One live trading day: real clock, real Slack, real Alpaca paper, real LLM
# seats. Needs .env loaded (`set -a; source .env; set +a`). Manual/launchd
# only, NEVER CI. Exits 0 without trading when the market is closed.
# See HANDOFF-LIVE.md before the first supervised run.
live-day: deps
	$(PYTHON) scripts/run_day.py

# Alias kept for the specs/acceptance.md name.
live-paper: live-day

# The day's second fire: P&L $ and % vs SPY to #pnl, after the close has
# settled. Real Alpaca paper + real Slack, no LLM seats. Must run at/after
# 16:16 ET — close_frame's SIP_DELAY shift means an earlier run asks for a bar
# the closing auction has not written, and the job correctly posts nothing.
close-pnl: deps
	$(PYTHON) scripts/close_pnl.py

# Eval suite: REAL LLM turns against the REAL charters, 6 cases x 3 trials.
# Needs .env loaded. MEASURED 2026-08-17: 18 trials, $0.81 est., ~7 minutes.
# Never CI-on-commit — the code invariants already run against recorded traces
# inside `make test` for $0.
#
# Places no orders and touches no broker: the seats under eval are the analyst
# and PM, whose Alpaca toolsets are read-only and whose deny list blocks
# mcp__alpaca__place_*.
eval: deps
	$(PYTHON) scripts/eval_suite.py $(CASES)

# Re-score recorded traces. Free and offline — never runs a turn.
#   make eval-report                            # every run
#   make eval-report RUN=control                # one run
#   make eval-report RUN=secondary BASELINE=control   # diff two runs
eval-report: deps
	$(PYTHON) -m evals.report_cli $(RUN) $(BASELINE)
