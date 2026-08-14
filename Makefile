# fund — see CLAUDE.md for what each mode means.

.PHONY: test lint sim-day replay live-day live-paper

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

# One live trading day: real clock, real Slack, real Alpaca paper, real LLM
# seats. Needs .env loaded (`set -a; source .env; set +a`). Manual/launchd
# only, NEVER CI. Exits 0 without trading when the market is closed.
# See HANDOFF-LIVE.md before the first supervised run.
live-day: deps
	$(PYTHON) scripts/run_day.py

# Alias kept for the specs/acceptance.md name.
live-paper: live-day
