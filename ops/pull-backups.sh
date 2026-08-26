#!/bin/sh
# Mac-side: pull the droplet's snapshots down. Opportunistic — the launchd
# agent fires daily and this simply fails fast when the Mac is asleep or off
# the network, which is not an error worth alerting on.
#
# Pull, not push: the droplet then needs no credential for any other system.
# It holds keys only for the APIs it must reach.
set -eu

: "${FUND_DROPLET:?FUND_DROPLET not set (e.g. fund@203.0.113.10)}"
LOCAL="${FUND_LOCAL_BACKUPS:?FUND_LOCAL_BACKUPS not set}"
RSYNC="${FUND_RSYNC:-rsync}"

mkdir -p "$LOCAL"
"$RSYNC" -az --ignore-existing \
      -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
      "${FUND_DROPLET}:/var/lib/fund/backups/" "$LOCAL/"
echo "pull-backups: $(ls -1 "$LOCAL" | wc -l | tr -d ' ') files in $LOCAL"

# File the day's alerts as GitHub issues (docs/agents/devops.md).
#
# Chained here rather than given its own timer: this is the one job that runs
# daily on this machine and knows a fresh snapshot just landed, so "backup
# pulled" implies "filer ran" with no clock to drift between the two. It runs
# here rather than on the droplet because `gh` is not installed there, and a
# repo-write token does not belong on the box that holds the broker keys.
#
# Unset FUND_FILER disables all of it. A machine that only mirrors backups
# must not acquire a new way to fail.
FILER="${FUND_FILER:-}"
if [ -n "$FILER" ]; then
    # Newest dated snapshot. `fund-predeploy-<ts>.sqlite` sorts after every
    # `fund-<date>.sqlite`, so a plain `tail -1` would hand the filer an
    # arbitrary-age preflight copy. The date-shaped glob excludes it, and
    # excludes the `-wal`/`-shm` sidecars in the mirror for the same reason.
    SNAPSHOT="$(ls -1 "$LOCAL"/fund-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].sqlite 2>/dev/null | tail -1 || true)"
    if [ -z "$SNAPSHOT" ]; then
        echo "pull-backups: no snapshot to file from"
    else
        # The window is the snapshot's own date, taken from the filename
        # rather than a clock: the run covers exactly the day it pulled, and
        # the script stays testable. A day the pull misses is a day nothing
        # files — deliberate, so the case for a wider window is observed
        # rather than assumed.
        DAY="$(basename "$SNAPSHOT" .sqlite | sed 's/^fund-//')"
        # Named, not counted. `--ignore-existing` means a stalled droplet
        # backup transfers nothing and still exits 0, so the file count above
        # cannot tell stale from fresh (#110). The snapshot's name can.
        echo "pull-backups: filing from $(basename "$SNAPSHOT") (day $DAY)"
        # `set -e` is in force and the filer exits 1 on a malformed payload —
        # a routine data condition, not a failed pull. Capture the code and
        # report it: a blanket `|| true` would swallow the tracker-unavailable
        # case too, which is the one worth seeing.
        RC=0
        "$FILER" "$SNAPSHOT" --since "$DAY" --apply || RC=$?
        [ "$RC" -eq 0 ] || echo "pull-backups: filer exited $RC"
    fi
fi
