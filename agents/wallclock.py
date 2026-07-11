"""The one real-clock implementation. Lives outside the purity-linted
packages (orchestrator/ may not call datetime.now); injected at composition
roots only — live-paper mode and the @live smoke test."""

from datetime import datetime, timezone


class WallClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
