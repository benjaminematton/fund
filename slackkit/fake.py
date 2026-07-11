"""In-memory Slack for offline tests (acceptance §0): records posts per
channel, queryable in asserts. Deterministic ts — no wall clock."""

from __future__ import annotations


class FakeSlack:
    def __init__(self) -> None:
        self.posts: dict[str, list[dict]] = {}
        self._ts = 0

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        self._ts += 1
        ts = f"{self._ts}.000000"
        self.posts.setdefault(channel, []).append(
            {"ts": ts, "text": text, "thread_ts": thread_ts})
        return ts
