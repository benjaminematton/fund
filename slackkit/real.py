"""Real Slack port (live-paper + @live smoke only). Import via
slackkit.real explicitly — slackkit/__init__.py must stay empty so the
purity-linted orchestrator can import slackkit.outbox."""

from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .port import PermanentPostError

# Slack error codes that no retry can fix: the bot was never invited, the
# channel is gone or archived, the token is bad. Everything else (rate limits,
# 5xx, network) is transient and must keep its retry semantics.
PERMANENT_ERRORS = frozenset({"not_in_channel", "channel_not_found",
                              "invalid_auth", "is_archived"})


class RealSlack:
    def __init__(self, token: str) -> None:
        self._client = WebClient(token=token)

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        try:
            resp = self._client.chat_postMessage(channel=channel, text=text,
                                                 thread_ts=thread_ts)
        except SlackApiError as exc:
            code = (exc.response or {}).get("error")
            if code in PERMANENT_ERRORS:
                raise PermanentPostError(f"{channel}: {code}") from exc
            raise
        return resp["ts"]
