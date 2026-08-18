#!/bin/sh
# Run a COMPLETE trading day against the scratch account, through the same
# systemd launch path the 09:35 timer uses.
#
# This exists because the fund's only end-to-end proof used to be a live fire,
# once per weekday. On 2026-08-18 that cost a full trading day: uvx was missing
# from systemd's PATH, and every check that could have caught it either ran in a
# login shell or exited early on the market clock. A staging day runs the real
# code, the real seats, the real gate and a real broker order in ~4 minutes.
#
# THE GUARD BELOW IS THE POINT. A rehearsal that shares production's Alpaca
# account is worse than no rehearsal: its orders change production's positions
# and buying power, and the next real day sizes against state its own database
# never recorded. Nothing downstream can detect that, so it is refused here,
# before a single seat starts.
set -eu

PROD_ENV="${FUND_PROD_ENV:-/etc/fund/env}"
STG_ENV="${FUND_STAGING_ENV:-/etc/fund/staging-env}"
CURL="${STAGING_CURL:-curl}"

for f in "$PROD_ENV" "$STG_ENV"; do
    [ -r "$f" ] || { echo "staging-day: cannot read $f" >&2; exit 1; }
done

# Read a key out of an env file without sourcing it into this shell — the two
# files define the SAME variable names, so sourcing both would silently leave
# whichever came last and defeat the comparison.
val() { sed -n "s/^$2=//p" "$1" | head -1; }

acct() {
    "$CURL" --silent --show-error --max-time 20 \
        -H "APCA-API-KEY-ID: $1" -H "APCA-API-SECRET-KEY: $2" \
        https://paper-api.alpaca.markets/v2/account | jq -r '.account_number // empty'
}

PROD_ACCT="$(acct "$(val "$PROD_ENV" ALPACA_API_KEY)" "$(val "$PROD_ENV" ALPACA_SECRET_KEY)")"
STG_ACCT="$(acct "$(val "$STG_ENV" ALPACA_API_KEY)" "$(val "$STG_ENV" ALPACA_SECRET_KEY)")"

[ -n "$PROD_ACCT" ] || { echo "staging-day: production credentials did not resolve to an account" >&2; exit 1; }
[ -n "$STG_ACCT" ]  || { echo "staging-day: staging credentials did not resolve to an account" >&2; exit 1; }

if [ "$PROD_ACCT" = "$STG_ACCT" ]; then
    echo "staging-day: REFUSING — staging and production are the same Alpaca account ($STG_ACCT)." >&2
    echo "  A rehearsal order would move production's positions and buying power," >&2
    echo "  and the next real day would size against state its DB never recorded." >&2
    echo "  Point $STG_ENV at a second paper account." >&2
    exit 1
fi

# Same reasoning one level down: a shared database would put rehearsal
# checkpoints, tickets and orders into production's source of truth.
PROD_DB="$(val "$PROD_ENV" FUND_DB)"
STG_DB="$(val "$STG_ENV" FUND_DB)"
if [ "$PROD_DB" = "$STG_DB" ]; then
    echo "staging-day: REFUSING — staging and production share FUND_DB ($STG_DB)." >&2
    exit 1
fi

# Invariant 1 holds in staging too. A scratch account is still a paper account.
[ "$(val "$STG_ENV" ALPACA_PAPER_TRADE)" = "true" ] || {
    echo "staging-day: REFUSING — ALPACA_PAPER_TRADE is not 'true' in $STG_ENV" >&2; exit 1; }

echo "staging-day: production=$PROD_ACCT staging=$STG_ACCT — distinct, proceeding"

[ -n "${STAGING_GUARD_ONLY:-}" ] && { echo "staging-day: guard-only, not running"; exit 0; }

# systemd-run, not a login shell: the launch path is what we are proving.
exec systemd-run --uid=fund --pipe --wait --quiet \
    --property=WorkingDirectory=/opt/fund \
    --property=EnvironmentFile="$STG_ENV" \
    --property=Environment=PATH=/home/fund/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin \
    --property=Environment=HOME=/home/fund \
    --property=TimeoutStartSec=30min \
    /opt/fund/.venv/bin/python3 scripts/run_day.py
