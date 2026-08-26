# fund — see CLAUDE.md for what each mode means.

.PHONY: test lint sim-day replay live-day live-paper close-pnl resolve schema-pin surface-pin score-day preflight dev-status
.PHONY: staging-day staging-reset eval eval-report
.PHONY: eval-critic-dev eval-critic-holdout

# Bootstrap: plain `make test` works from a clean checkout or a fresh git
# worktree — .venv is created on first run, and deps re-sync whenever
# requirements.lock or pyproject.toml changes (the only steps that touch the
# network). Every host installs the lock, never the ranges, so local, the
# droplet and CI hold the same 52 versions. The sync is content-hash gated in
# scripts/sync_deps.py, NOT mtime-gated: Apple's GNU make 3.81 treats equal
# 1-second timestamps as up-to-date, which silently skips same-second edits.
# BOOT_PY picks a >=3.12 interpreter explicitly; macOS /usr/bin/python3 (3.9)
# won't do.
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

# Re-resolve pyproject.toml's ranges in a throwaway venv and rewrite the lock.
# The only supported way to change a pinned version — hand-editing the lock
# skips the resolver and can pin a set that does not install together. Run
# `make test` afterwards, then commit pyproject.toml and the lock together.
.PHONY: deps-relock
deps-relock:
	@$(BOOT_PY) scripts/relock.py

# Full offline suite: no network, no API keys. Must pass before every commit.
test: lint
	$(PYTHON) -m pytest tests/

# Purity lint: no LLM imports, no wall clock in business logic (CLAUDE.md invariant 3).
# Alert-code lint: every alert carries a stable code (docs/agents/devops.md).
lint: deps
	$(PYTHON) scripts/check_purity.py
	$(PYTHON) scripts/check_alert_codes.py

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

# Pin WHICH tools the broker exposes, as opposed to schema-pin's what-one-tool-
# takes. Same read-only introspection, different question.
#
# The exec seat's allow-array is `mcp__alpaca__*`, so the SERVER decides its
# capability surface. On 2026-08-20 four sessions enumerated that surface and
# got four different answers (4, 5, 7, 8 mutating verbs) while
# close_all_positions sat reachable in production with no gate ticket.
#
# DETECTION, not protection: _broker_verb_policy is deny-by-default, so a verb
# nobody has pinned is already denied. This says WHEN the surface moved, so a
# new mutating verb is a decision someone makes rather than a fact someone
# discovers. Run it after any alpaca-mcp-server bump or toolset change.
surface-pin: deps
	$(PYTHON) -m pytest -m live tests/test_live_smoke.py -k surface_pin -v

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
#
# Step 1 opens the LIVE database at $$FUND_DB and reports its schema state.
# Read-only: it never calls state.db.connect(), which would APPLY the pending
# migration it exists to report (#17). It runs FIRST because it is free and
# the eval suite is not — a live DB the code cannot run against should stop
# the deploy before ~$$0.31 of real LLM turns, not after. Same uid and
# EnvironmentFile as step 2: FUND_DB comes from /etc/fund/env, and the live DB
# is owned by `fund`.
#
# The STEP's exit codes are 0 ok, 1 migrations pending, 2 unexplained
# divergence, 3 cannot determine, and only 0 continues to step 2. They are not
# this target's: make collapses any failed recipe line to its own exit 2, so
# read which of the four it was off the step's stderr, never off `make`'s
# status — a target exit 2 is make reporting failure, not "unexplained
# divergence".
preflight:
	systemd-run --uid=fund --pipe --wait --quiet \
	  --property=WorkingDirectory=/opt/fund \
	  --property=EnvironmentFile=/etc/fund/env \
	  --property=Environment=PATH=/home/fund/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin \
	  --property=Environment=HOME=/home/fund \
	  --property=TimeoutStartSec=1min \
	  /opt/fund/.venv/bin/python3 scripts/preflight_schema.py
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

# The day's findings, worst first — what to read before anything else. run_day
# already appends this to #pnl; this target is for reading it again by hand, or
# for any past day. Stdlib only and never non-zero: audit_day owns the exit
# code, and a scorecard that could fail a day would invert what it is for.
score-day: deps
	$(PYTHON) scripts/score_day.py "$$FUND_DB" "$$(TZ=America/New_York date +%F)"

# Nightly reflection: decisions at their horizon -> resolutions (design §8).
# Rides the same 16:35 fire as close-pnl for the same SIP_DELAY reason, and is
# safe to re-run — a decision that already resolved is not selected again.
resolve: deps
	$(PYTHON) scripts/resolve_day.py

# Read-only production health check for developers: is every stated invariant
# and Phase 2 acceptance criterion still true on the box that trades?
#
# Reads the droplet over ssh, the broker, and the live DB with mode=ro. Writes
# NOTHING anywhere — no order, no deploy, no migration. Exit 0 always, so a
# check that cannot run renders as a finding instead of hiding the rest.
#
# Attended, not scheduled. Every finding is for a human to act on; this is
# developer context, not the fund's own alerting, which already works.
dev-status: deps
	$(PYTHON) scripts/dev_status.py

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

# Places no orders and touches no broker: the Critic seat is read-only
# (invariant 2) and its G1 turn reads only the fund DB.
#
# TWO targets, not one with a flag, because the difference is not a
# convenience. eval-critic-dev is the iteration loop and may be run as often
# as needed. eval-critic-holdout is the acceptance measurement and is run ONCE
# — its cases must never inform the charter, the same one-shot discipline
# specs/strategy.md invariant 6 puts on a strategy's own holdout. LABEL is
# required on both: traces are keyed by git sha, so an uncommitted charter
# edit would otherwise overwrite the baseline it is being compared against.
eval-critic-dev: deps
	$(PYTHON) scripts/eval_suite.py --seat critic --split dev --label $(LABEL)

eval-critic-holdout: deps
	$(PYTHON) scripts/eval_suite.py --seat critic --split holdout --label $(LABEL)

# Re-score recorded traces. Free and offline — never runs a turn.
#   make eval-report                            # every run
#   make eval-report RUN=control                # one run
#   make eval-report RUN=secondary BASELINE=control   # diff two runs
eval-report: deps
	$(PYTHON) -m evals.report_cli $(RUN) $(BASELINE)

# A COMPLETE trading day against a scratch Alpaca account — real seats, real
# gate, a real broker order — through the same systemd launch path the 09:35
# timer uses. ~4 minutes, ~$0.23. Droplet only; needs /etc/fund/staging-env
# (template: ops/staging-env.example).
#
# The fund's only end-to-end proof used to be a live fire, once per weekday. On
# 2026-08-18 that cost a trading day. This closes the loop to minutes while the
# market is open. ops/staging-day.sh REFUSES to run unless staging and
# production resolve to different Alpaca accounts and different databases.
staging-day:
	ops/staging-day.sh

# Flatten the scratch account and wipe the scratch DB so the next staging day
# starts clean — otherwise run two and the second sees the first's positions,
# changing what the gate allows. LIQUIDATES POSITIONS: reuses staging-day's
# guard and refuses if it cannot prove the account is the scratch one.
staging-reset:
	ops/staging-reset.sh
