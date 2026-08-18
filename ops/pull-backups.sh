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
