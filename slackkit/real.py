"""Real Slack port (live-paper + @live smoke only). Import from
slackkit.real explicitly — slackkit/__init__.py must stay empty so the
purity-linted orchestrator can import slackkit.outbox."""

from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .port import PermanentPostError

# Slack error codes that no retry can fix: the bot was never invited, the
# channel is gone or archived, the token is bad, the Block Kit payload is
# malformed or oversized, or the token cannot set a sender identity — Slack
# rejects that message identically forever, so it must dead-letter rather
# than stop the drain for a retry that cannot help. Everything else (rate
# limits, 5xx, network) is transient and must keep its retry semantics.
# Codes per api.slack.com/methods/chat.postMessage.
#
# missing_scope / not_allowed_token_type are the persona pair: they answer a
# username/icon_emoji the token is not allowed to set, and stay refused until
# a human changes the app's scopes. Left transient, they would stop the drain
# on the day's first signal — the analyst's — and queue every gate post, fill
# and digest behind it for the rest of the day.
PERMANENT_ERRORS = frozenset({"not_in_channel", "channel_not_found",
                              "invalid_auth", "is_archived",
                              "invalid_blocks", "invalid_blocks_format",
                              "msg_blocks_too_long",
                              "missing_scope", "not_allowed_token_type"})


class RealSlack:
    def __init__(self, token: str) -> None:
        self._client = WebClient(token=token)

    def post(self, channel: str, text: str, thread_ts: str | None = None,
             blocks: list[dict] | None = None, username: str | None = None,
             icon_emoji: str | None = None) -> str:
        # blocks is omitted rather than sent as None: Slack treats an explicit
        # null as a payload to validate and rejects it. username/icon_emoji
        # are omitted for the same reason, and need chat:write.customize.
        extra = {"blocks": blocks} if blocks else {}
        if username:
            extra["username"] = username
        if icon_emoji:
            extra["icon_emoji"] = icon_emoji
        try:
            resp = self._client.chat_postMessage(channel=channel, text=text,
                                                 thread_ts=thread_ts, **extra)
        except SlackApiError as exc:
            code = (exc.response or {}).get("error")
            if code in PERMANENT_ERRORS:
                raise PermanentPostError(f"{channel}: {code}") from exc
            raise
        return resp["ts"]
