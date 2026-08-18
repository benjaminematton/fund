from __future__ import annotations

from typing import Protocol


class SlackPort(Protocol):
    def post(self, channel: str, text: str, thread_ts: str | None = None,
             blocks: list[dict] | None = None) -> str:
        """`blocks` is Block Kit layout; `text` is NOT optional alongside it.
        Slack renders text — not blocks — in push notifications and to screen
        readers, so a blocks-only message arrives blank there. Every caller
        passes both; `blocks=None` posts text alone."""
        ...


class PermanentPostError(Exception):
    """A post that will fail identically forever (the bot is not in the
    channel, the channel is gone/archived, the token is invalid). drain()
    dead-letters that one event and keeps going; every other post failure
    stays transient and stops the drain for retry.

    Lives here, not in real.py, so outbox.py can import it without pulling in
    slack_sdk (slackkit.outbox is imported by the purity-linted orchestrator)."""
