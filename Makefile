# fund — see CLAUDE.md for what each mode means.

.PHONY: test lint sim-day replay live-paper

# Full offline suite: no network, no API keys. Must pass before every commit.
test: lint
	python3 tests/run_tests.py

# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
lint:
	python3 scripts/check_purity.py

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
