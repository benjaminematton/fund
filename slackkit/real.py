"""Real Slack port (live-paper + @live smoke only). Import via
slackkit.real explicitly — slackkit/__init__.py must stay empty so the
purity-linted orchestrator can import slackkit.outbox."""

from __future__ import annotations

from slack_sdk import WebClient


class RealSlack:
    def __init__(self, token: str) -> None:
        self._client = WebClient(token=token)

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        resp = self._client.chat_postMessage(channel=channel, text=text,
                                             thread_ts=thread_ts)
        return resp["ts"]
