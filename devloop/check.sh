#!/usr/bin/env bash
# Devloop checker gate: external verification, never the agent's own claim.
# 1. tamper guard  — structural rules (protected paths, immutable tests)
# 2. make test     — purity lint + full offline suite (the repo's backpressure)
set -uo pipefail
cd "$(dirname "$0")/.."

BASELINE="$(cat devloop/.baseline)"

python3 devloop/tamper_guard.py "$BASELINE" || exit 1
make test || { echo "CHECK: make test red" >&2; exit 1; }
echo "CHECK: green"
