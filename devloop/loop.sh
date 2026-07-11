#!/usr/bin/env bash
# Devloop — Ralph-style build loop for Phase 1, gated by the repo's own checks.
#
#   while not done: fresh `claude -p` picks ONE unchecked acceptance item,
#   implements it TDD, then an external gate (tamper guard + make test) and a
#   second-agent invariant review decide whether the work survives. The harness
#   commits on green and discards red. Stops on: plan complete, N consecutive
#   failures, iteration cap, or cost cap.
#
# State lives in the repo (acceptance checkboxes, git, devloop/NOTES.md) —
# never in conversation context. Kill it and rerun any time; it resumes.
#
# Usage:  ./devloop/loop.sh            (commit devloop/ first; needs clean tree)
# Knobs:  MAX_ITER, MAX_COST_USD, FAIL_LIMIT, PHASE (env vars)
set -uo pipefail
cd "$(dirname "$0")/.."

MAX_ITER="${MAX_ITER:-20}"
MAX_COST_USD="${MAX_COST_USD:-15}"   # ResultMessage cost is a client-side ESTIMATE (CLAUDE.md)
FAIL_LIMIT="${FAIL_LIMIT:-3}"
PHASE="${PHASE:-Phase 1}"
LOGDIR="devloop/logs"; mkdir -p "$LOGDIR"

# Refuse to start dirty: red iterations are discarded with `git reset --hard`,
# and we will not eat uncommitted human work.
if [ -n "$(git status --porcelain)" ]; then
  echo "HALT: working tree not clean — commit or stash first" >&2; exit 2
fi

# Baseline = the commit whose tests/fixtures are immutable for this whole run.
[ -f devloop/.baseline ] || git rev-parse HEAD > devloop/.baseline
BASELINE="$(cat devloop/.baseline)"

open_items() {  # unchecked, non-@live boxes in this phase's section
  python3 - "$PHASE" <<'EOF'
import re, sys
text = open("specs/acceptance.md").read()
m = re.search(rf"^## {re.escape(sys.argv[1])}\b.*?(?=^## |\Z)", text, re.S | re.M)
sec = m.group(0) if m else ""
print(sum(1 for l in sec.splitlines()
          if l.strip().startswith("- [ ]") and "@live" not in l))
EOF
}

json_cost() { python3 -c 'import json,sys; print(json.load(sys.stdin).get("total_cost_usd") or 0)'; }

total_cost=0; fails=0
for i in $(seq 1 "$MAX_ITER"); do
  left="$(open_items)"
  if [ "$left" -eq 0 ]; then
    echo "DONE: $PHASE complete after $((i-1)) iterations (~\$$total_cost est.)"
    echo "Remaining manual work: any @live items in specs/acceptance.md."
    exit 0
  fi
  echo "== iter $i/$MAX_ITER — $left item(s) open, est. \$$total_cost spent =="

  # Worker: fresh process, fixed prompt. Deliberately NOT --bare: the repo
  # requires CLAUDE.md (invariants) loaded for every session.
  worker_json="$LOGDIR/iter-$i-worker.json"
  claude -p "$(cat devloop/PROMPT.md)" \
    --allowedTools "Read,Glob,Grep,Edit,Write,Bash(python3 *),Bash(make test*),Bash(make lint*),Bash(pytest *),Bash(git status*),Bash(git diff*),Bash(git log*),Bash(ls *),Bash(mkdir *)" \
    --output-format json > "$worker_json" 2>"$LOGDIR/iter-$i-worker.err"
  total_cost=$(python3 -c "print(round($total_cost + $(json_cost < "$worker_json" || echo 0), 4))")

  # Gate 1: external checker (tamper guard + make test)
  if ! ./devloop/check.sh > "$LOGDIR/iter-$i-check.log" 2>&1; then
    fails=$((fails+1))
    echo "iter $i: RED (checker) — discarding ($fails/$FAIL_LIMIT). See $LOGDIR/iter-$i-check.log"
    git reset --hard -q; git clean -fdq -e devloop/
  else
    # Gate 2: second-agent invariant review of the diff (maker != checker)
    review_json="$LOGDIR/iter-$i-review.json"
    claude -p "$(cat devloop/REVIEWER.md)

DIFF UNDER REVIEW:
$(git diff HEAD)" \
      --allowedTools "Read,Glob,Grep" \
      --output-format json \
      --json-schema '{"type":"object","properties":{"verdict":{"enum":["pass","block"]},"reasons":{"type":"array","items":{"type":"string"}}},"required":["verdict","reasons"]}' \
      > "$review_json" 2>"$LOGDIR/iter-$i-review.err"
    total_cost=$(python3 -c "print(round($total_cost + $(json_cost < "$review_json" || echo 0), 4))")
    verdict=$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("structured_output") or {}).get("verdict","block"))' < "$review_json")

    if [ "$verdict" = "pass" ]; then
      git add -A
      git commit -qm "devloop($PHASE): iter $i green — $((left-1)) item(s) remaining" \
                 -m "checker: make test + tamper guard vs $BASELINE; reviewer: pass"
      fails=0
      echo "iter $i: GREEN — committed"
    else
      fails=$((fails+1))
      echo "iter $i: RED (reviewer block) — discarding ($fails/$FAIL_LIMIT). See $review_json"
      git reset --hard -q; git clean -fdq -e devloop/
    fi
  fi

  # Circuit breaker + budget cap — stuck loops summon the human, never guess on.
  if [ "$fails" -ge "$FAIL_LIMIT" ]; then
    echo "HALT: $FAIL_LIMIT consecutive red iterations — read $LOGDIR and devloop/NOTES.md" >&2
    exit 1
  fi
  if python3 -c "import sys; sys.exit(0 if $total_cost > $MAX_COST_USD else 1)"; then
    echo "HALT: est. cost \$$total_cost exceeds \$$MAX_COST_USD" >&2
    exit 1
  fi
done
echo "HALT: iteration cap $MAX_ITER reached, $(open_items) item(s) still open (~\$$total_cost est.)" >&2
exit 1
