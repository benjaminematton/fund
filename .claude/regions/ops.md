---
paths:
  - ops/
  - tests/test_ops*.py
---
# ops — standing

Keeping the fund running on the VM host: `systemctl start fund-daily.service`
runs one trading day; schedule, cutover, and rollback live in `ops/README.md`.
Devops is a separate loop from the fund's own feedback loop — conflating them
wastes sessions; detection is already built, do not add a second checker
(`docs/agents/devops.md`). Findings reach the tracker per
`docs/agents/issue-tracker.md`. Broker mutations, droplet deploys, and gate
thresholds are Benjamin's, in his own window.

# Journal
