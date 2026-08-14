"""In-memory Slack for offline tests (acceptance §0): records posts per
channel, queryable in asserts. Deterministic ts — no wall clock."""

from __future__ import annotations

from .port import PermanentPostError


class FakeSlack:
    def __init__(self, permanent_failures: set[str] | None = None) -> None:
        """permanent_failures: channels the bot is not in — every post there
        raises PermanentPostError, the way RealSlack does on not_in_channel."""
        self.posts: dict[str, list[dict]] = {}
        self.permanent_failures = permanent_failures or set()
        self._ts = 0

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        if channel in self.permanent_failures:
            raise PermanentPostError(f"{channel}: not_in_channel")
        self._ts += 1
        ts = f"{self._ts}.000000"
        self.posts.setdefault(channel, []).append(
            {"ts": ts, "text": text, "thread_ts": thread_ts})
        return ts
