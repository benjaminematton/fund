# fund — see CLAUDE.md for what each mode means.

.PHONY: test lint sim-day replay live-paper

# Bootstrap: plain `make test` works from a clean checkout or a fresh git
# worktree — .venv is created on first run and re-synced from pyproject.toml
# whenever it changes (the only step that touches the network). BOOT_PY picks
# a >=3.12 interpreter explicitly; macOS /usr/bin/python3 (3.9) won't do.
PYTHON := .venv/bin/python3
DEPS_STAMP := .venv/.deps-synced
BOOT_PY := $(shell command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)

$(DEPS_STAMP): pyproject.toml
	@$(BOOT_PY) -c "import sys; ok = sys.version_info >= (3, 12); \
	    ok or print('fund: need python3 >= 3.12 on PATH, found ' + sys.version.split()[0], file=sys.stderr); \
	    sys.exit(0 if ok else 1)"
	$(BOOT_PY) -m venv .venv
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -c "import subprocess, sys, tomllib; \
	    p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
	    deps = p['dependencies'] + p.get('optional-dependencies', {}).get('dev', []); \
	    sys.exit(subprocess.call([sys.executable, '-m', 'pip', 'install', '--quiet', *deps]))"
	touch $(DEPS_STAMP)

# Full offline suite: no network, no API keys. Must pass before every commit.
test: lint
	$(PYTHON) tests/run_tests.py

# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
lint: $(DEPS_STAMP)
	$(PYTHON) scripts/check_purity.py

# Full simulated trading day: injected clock, FakeSlack, recorded LLM decisions,
# real tool/gate/DB execution. Lands with Phase 1–2 (orchestrator + gate).
sim-day:
	@echo "sim-day: not implemented yet — requires orchestrator/ and gate/ (Phase 1–2," >&2
	@echo "see specs/acceptance.md). 'make test' is the current offline check." >&2
	@exit 2

# Replay a recorded day's LLM decisions against current code.
replay:
	@echo "replay: not implemented yet — requires the recorder/replayer (Phase 1," >&2
	@echo "see specs/acceptance.md §0). Usage when built: make replay REC=<recording>" >&2
	@exit 2

# Real Slack + Alpaca paper + real LLM calls. Needs .env. Manual only, never CI.
live-paper:
	@echo "live-paper: not implemented yet — requires agents/ runtime (Phase 1–3)." >&2
	@exit 2
