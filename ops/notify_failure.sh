#!/bin/sh
# Post a failed unit's status to Slack. Invoked by fund-alert@.service via
# OnFailure=. Deliberately dependency-free of the fund itself: no DB
# connection, no python, no fund imports — the alert path must not share a
# failure mode with the thing it is watching.
#
# Reads SLACK_BOT_TOKEN and FUND_ALERT_CHANNEL from /etc/fund/alert-env, NOT
# from /etc/fund/env. A missing or unreadable job env file is the most likely
# fresh-host failure; if the alert read the same file it would die identically.
set -eu

UNIT="${1:?usage: notify_failure.sh <unit-name>}"
: "${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN not set (is /etc/fund/alert-env loaded?)}"
: "${FUND_ALERT_CHANNEL:?FUND_ALERT_CHANNEL not set}"

CURL="${FUND_ALERT_CURL:-curl}"
JOURNALCTL="${FUND_ALERT_JOURNALCTL:-journalctl}"

# Redact anything shaped like a credential before it leaves the box. A
# traceback that dumps os.environ must not publish broker keys to Slack.
# Prefix rules catch known token shapes; the name-based rule catches
# secrets with no recognizable prefix (e.g. ALPACA_SECRET_KEY), whether
# written as NAME=VALUE, NAME: VALUE, or quoted python/JSON dict dumps
# ('NAME': 'VALUE', "NAME": "VALUE").
redact() {
    sed -E \
        -e 's/sk-ant-[A-Za-z0-9_-]+/sk-ant-REDACTED/g' \
        -e 's/xoxb-[A-Za-z0-9-]+/xoxb-REDACTED/g' \
        -e 's/xapp-[A-Za-z0-9-]+/xapp-REDACTED/g' \
        -e 's/PK[A-Z0-9]{16,}/PK-REDACTED/g' \
        -e "s/['\"]?([A-Z][A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)['\"]?[[:space:]]*[=:][[:space:]]*['\"]?[^[:space:]]+/\1=REDACTED/g"
}

STATUS="$(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)"
CODE="$(systemctl show -p ExecMainStatus --value "$UNIT" 2>/dev/null || echo '?')"
TAIL="$("$JOURNALCTL" -u "$UNIT" -n 20 --no-pager -o cat 2>/dev/null | redact || echo '(journal unavailable)')"

TEXT="$(printf ':rotating_light: *%s* failed\nresult=%s exit=%s\n```\n%s\n```' \
        "$UNIT" "$STATUS" "$CODE" "$TAIL")"

# jq builds the payload so quotes, backslashes and newlines in the journal
# tail cannot produce malformed JSON.
PAYLOAD="$(jq -n --arg channel "$FUND_ALERT_CHANNEL" --arg text "$TEXT" \
           '{channel: $channel, text: $text}')"

RESPONSE="$("$CURL" --silent --show-error --max-time 20 \
    -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H 'Content-Type: application/json; charset=utf-8' \
    -d "$PAYLOAD")"

# chat.postMessage returns HTTP 200 with {"ok":false} on auth/scope errors, so
# curl --fail sees success. Check the body or the alert is silently lost.
if [ "$(printf %s "$RESPONSE" | jq -r '.ok')" != "true" ]; then
    echo "notify_failure: slack rejected the post: $(printf %s "$RESPONSE" | jq -r '.error // .')" >&2
    exit 1
fi
