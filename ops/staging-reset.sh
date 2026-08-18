#!/bin/sh
# Flatten the scratch account and wipe the scratch database, so the next
# staging day starts from a known state.
#
# Without this, rehearsals drift: run two and the second sees the first's
# positions, so the gate's `sell` allowances differ and you are no longer
# testing the same thing twice. Useful sometimes — that is how you exercise the
# sell path — but it should be deliberate, not accidental.
#
# THIS SCRIPT LIQUIDATES POSITIONS. Pointed at the wrong account it would close
# the real fund's book. It therefore refuses to do anything until
# ops/staging-day.sh's guard confirms staging and production are distinct — the
# same check, reused rather than copied, because a divergent copy of a safety
# check is worse than no copy.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
STG_ENV="${FUND_STAGING_ENV:-/etc/fund/staging-env}"
CURL="${STAGING_CURL:-curl}"

STAGING_GUARD_ONLY=1 "$HERE/staging-day.sh" >/dev/null || {
    echo "staging-reset: REFUSING — the staging/production guard did not pass." >&2
    echo "  Run ops/staging-day.sh to see why. Nothing was liquidated." >&2
    exit 1
}

val() { sed -n "s/^$2=//p" "$1" | head -1; }
KEY="$(val "$STG_ENV" ALPACA_API_KEY)"
SEC="$(val "$STG_ENV" ALPACA_SECRET_KEY)"
DB="$(val "$STG_ENV" FUND_DB)"
JOURNALS="$(val "$STG_ENV" FUND_JOURNALS)"

echo "staging-reset: closing all positions on the scratch account"
"$CURL" --silent --show-error --max-time 30 -X DELETE \
    -H "APCA-API-KEY-ID: $KEY" -H "APCA-API-SECRET-KEY: $SEC" \
    "https://paper-api.alpaca.markets/v2/positions?cancel_orders=true" >/dev/null

echo "staging-reset: removing $DB and $JOURNALS"
rm -f "$DB" "$DB-wal" "$DB-shm" "$(dirname "$DB")/run_day.lock"
rm -rf "$JOURNALS"
mkdir -p "$JOURNALS"

# This script usually runs as root (via `make staging-reset`), but the day runs
# as the unprivileged service user. A root-owned journals directory makes
# run_day.py die with EACCES writing analyst.md — and it dies AFTER the analyst
# and PM have already spent their turns, so the failure costs real money and
# tells you nothing until the traceback. Restore the parent's ownership rather
# than hardcoding a user, so this holds on any host.
OWNER="$(stat -c '%U:%G' "$(dirname "$JOURNALS")" 2>/dev/null || stat -f '%Su:%Sg' "$(dirname "$JOURNALS")")"
chown -R "$OWNER" "$JOURNALS"

echo "staging-reset: done — next staging day starts clean"
