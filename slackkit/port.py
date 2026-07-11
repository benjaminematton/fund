from __future__ import annotations

from typing import Protocol


class SlackPort(Protocol):
    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str: ...
